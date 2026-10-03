use super::{
    AdditionalComponent, AudioScenes, Component, Config, ConfigField, CookieReport, Diagnostic,
    DrcDeclaration, Extension, Field, HoaAsc, Located, ParseError, ParseStatus, UnknownRange,
    bits::BitReader,
};
use crate::prelude::*;
use crate::record::{DigestUnit, FieldValue};

pub(super) enum Stop {
    Invalid(ParseError),
    Unsupported {
        position: usize,
        reason: String,
        whole: bool,
    },
}
impl From<ParseError> for Stop {
    fn from(e: ParseError) -> Self {
        Self::Invalid(e)
    }
}
pub(super) type PResult<T> = Result<T, Stop>;

/// Recorded fields go to `report`; the values decoding reads are also stored,
/// typed and located, in `config` as the syntax reaches them.
pub(super) struct Parser<'a> {
    pub data: &'a [u8],
    pub bits: BitReader<'a>,
    pub report: CookieReport,
    pub config: Config,
}
impl<'a> Parser<'a> {
    pub(super) fn new(data: &'a [u8]) -> Self {
        let report = CookieReport::new(data);
        Self {
            data,
            bits: BitReader::new(data),
            config: Config::empty(&report),
            report,
        }
    }
}

/// The cookie's recorded report and its typed configuration from one pass.
pub(super) fn parse(data: &[u8]) -> Result<(CookieReport, Config), ParseError> {
    let mut p = Parser::new(data);
    match p.cookie() {
        Ok(()) => {}
        Err(Stop::Invalid(error)) => return Err(error),
        Err(Stop::Unsupported {
            position,
            reason,
            whole,
        }) => {
            p.report.status = if whole {
                ParseStatus::Unsupported
            } else {
                ParseStatus::Partial
            };
            p.unknown(position, data.len() * 8, reason.clone());
            p.report.diagnostics.push(Diagnostic {
                bit_offset: position,
                message: reason,
            });
        }
    }
    let mut config = p.config;
    config.finish(&p.report);
    Ok((p.report, config))
}

/// Reuse the same bounded scene grammar for an explicitly present frame update.
/// Field coordinates remain relative to the supplied packet, not a fake cookie.
pub(crate) fn parse_scene_at(
    data: &[u8],
    offset: usize,
) -> Result<(CookieReport, AudioScenes, usize), ParseError> {
    let mut p = Parser::new(data);
    p.bits.skip(offset)?;
    match p.audio_scenes() {
        Ok(()) => {}
        Err(Stop::Invalid(error)) => return Err(error),
        Err(Stop::Unsupported {
            position, reason, ..
        }) => {
            p.report.status = ParseStatus::Partial;
            p.report.diagnostics.push(Diagnostic {
                bit_offset: position,
                message: reason,
            });
        }
    }
    let end = p.pos();
    let scenes = p.config.ancillary.audio_scenes;
    #[cfg(test)]
    assert_eq!(scenes, AudioScenes::from_fields(&p.report.fields));
    Ok((p.report, scenes, end))
}

/// Header payload type 0 uses the same version-8 header as the cookie, but a
/// missing configuration can reuse an explicitly supplied previous context.
pub(crate) fn parse_drc_header_at(
    data: &[u8],
    offset: usize,
    rate: u64,
    channels: u64,
) -> Result<(CookieReport, DrcDeclaration, usize), ParseError> {
    let mut p = Parser::new(data);
    p.bits.skip(offset)?;
    p.report
        .derived
        .insert("sample_rate_hz".into(), FieldValue::from(rate));
    match p.drc_header(channels, true) {
        Ok(()) => {}
        Err(Stop::Invalid(error)) => return Err(error),
        Err(Stop::Unsupported {
            position, reason, ..
        }) => {
            p.report.status = ParseStatus::Partial;
            p.report.diagnostics.push(Diagnostic {
                bit_offset: position,
                message: reason,
            });
        }
    }
    let end = p.pos();
    let mut drc = p.config.ancillary.drc;
    drc.finish(&p.report);
    #[cfg(test)]
    assert_eq!(drc, DrcDeclaration::from_report(&p.report));
    Ok((p.report, drc, end))
}

fn at<T>(value: T, bit_offset: usize) -> Field<T> {
    Some(Located { value, bit_offset })
}

impl Parser<'_> {
    /// `take`, keeping the position for the typed configuration.
    pub fn take_at(&mut self, name: &str, width: usize) -> PResult<Located<u64>> {
        let bit_offset = self.pos();
        let value = self.take(name, width)?;
        Ok(Located { value, bit_offset })
    }
    pub fn flag_at(&mut self, name: &str) -> PResult<Located<bool>> {
        let bit_offset = self.pos();
        let value = self.flag(name)?;
        Ok(Located { value, bit_offset })
    }
    pub fn esc_at(&mut self, name: &str, widths: [usize; 3]) -> PResult<Located<u64>> {
        let bit_offset = self.pos();
        let value = self.esc(name, widths)?;
        Ok(Located { value, bit_offset })
    }
    /// The ASC whose syntax is being read.
    pub fn component_mut(&mut self) -> &mut Component {
        self.config
            .components
            .last_mut()
            .expect("component declared")
    }
    pub fn hoa_mut(&mut self) -> &mut HoaAsc {
        &mut self.component_mut().hoa
    }
    fn extension_mut(&mut self) -> &mut Extension {
        self.config
            .extensions
            .last_mut()
            .expect("extension declared")
    }
    pub fn pos(&self) -> usize {
        self.bits.position()
    }
    pub fn invalid<T>(&self, name: &str, message: impl Into<String>) -> PResult<T> {
        Err(ParseError::new(self.pos(), name, message).into())
    }
    pub fn stop<T>(&self, reason: impl Into<String>) -> PResult<T> {
        Err(Stop::Unsupported {
            position: self.pos(),
            reason: reason.into(),
            whole: false,
        })
    }
    pub fn unknown(&mut self, start: usize, end: usize, reason: String) {
        if start >= end {
            return;
        }
        let raw_hex = self.data[start / 8..end.div_ceil(8)]
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        self.report.unknown_ranges.push(UnknownRange {
            bit_offset: start,
            bit_length: end - start,
            first_byte_skip_bits: start % 8,
            raw_hex,
            reason,
        });
    }
    pub fn record(&mut self, name: &str, start: usize, value: FieldValue) -> PResult<()> {
        if self.report.fields.len() >= 16384 {
            return self.invalid("field-limit", "more than 16384 fields");
        }
        self.report.fields.push(ConfigField {
            name: name.into(),
            bit_offset: start,
            bit_length: self.pos() - start,
            value,
        });
        Ok(())
    }
    pub fn take(&mut self, name: &str, width: usize) -> PResult<u64> {
        let start = self.pos();
        let value = self.bits.read(width)?;
        self.record(name, start, FieldValue::from(value))?;
        Ok(value)
    }
    pub fn flag(&mut self, name: &str) -> PResult<bool> {
        let start = self.pos();
        let value = self.bits.read(1)? != 0;
        self.record(name, start, FieldValue::from(value))?;
        Ok(value)
    }
    pub fn esc(&mut self, name: &str, widths: [usize; 3]) -> PResult<u64> {
        self.escaped(name, widths, u64::from(u32::MAX))
    }
    fn escaped(&mut self, name: &str, widths: [usize; 3], maximum: u64) -> PResult<u64> {
        let start = self.pos();
        let mut value = 0u64;
        for width in widths {
            let part = self.bits.read(width)?;
            value = value
                .checked_add(part)
                .ok_or_else(|| ParseError::new(start, "overflow", "escaped integer overflow"))?;
            if value > maximum {
                return Err(ParseError::new(
                    start,
                    "overflow",
                    "escaped integer exceeds the 32-bit format range",
                )
                .into());
            }
            if part != (1u64 << width) - 1 {
                break;
            }
        }
        self.record(name, start, FieldValue::from(value))?;
        Ok(value)
    }
    pub fn count(&self, value: u64, minimum_bits: usize) -> PResult<usize> {
        let count = usize::try_from(value)
            .map_err(|_| ParseError::new(self.pos(), "overflow", "count exceeds usize"))?;
        if count > 4096
            || count
                .checked_mul(minimum_bits)
                .is_none_or(|n| n > self.bits.remaining())
        {
            return self.invalid(
                "count-range",
                "count exceeds parser limit or remaining input",
            );
        }
        Ok(count)
    }
    pub fn zero_padding(&mut self, name: &str, width: usize) -> PResult<()> {
        let start = self.pos();
        let value = self.take(name, width)?;
        if value != 0 {
            return Err(Stop::Unsupported {
                position: start,
                reason: format!("nonzero {name} has not been verified"),
                whole: false,
            });
        }
        Ok(())
    }
    fn cookie(&mut self) -> PResult<()> {
        let size = self.take("box.size_bytes", 32)?;
        let kind = self.take("box.type", 32)?;
        if kind != u64::from(u32::from_be_bytes(*b"dapa")) {
            self.report.fields.clear();
            return Err(Stop::Unsupported {
                position: 0,
                reason: "only standalone dapa cookies are supported".into(),
                whole: true,
            });
        }
        if size < 12 {
            return self.invalid(
                "box-length",
                "standalone cookie box must contain its 12-byte header",
            );
        }
        if size as usize != self.data.len() {
            return self.invalid(
                "box-length",
                "declared box length differs from input length",
            );
        }
        let version_flags = self.take_at("box.version_flags", 32)?;
        self.config.version_flags = Some(version_flags);
        if version_flags.value != 0 {
            return Err(Stop::Unsupported {
                position: self.pos(),
                reason: "unverified dapa version/flags".into(),
                whole: true,
            });
        }
        let version = self.take_at("bitstream_version", 16)?;
        self.config.bitstream_version = Some(version);
        if version.value != 0x0800 {
            return Err(Stop::Unsupported {
                position: self.pos(),
                reason: "unverified APAC bitstream version".into(),
                whole: true,
            });
        }
        self.global()?;
        let padding = (8 - self.pos() % 8) % 8;
        if self.bits.remaining() != padding {
            return self.stop("additional trailing bits outside the known configuration syntax");
        }
        self.zero_padding("alignment_padding", padding)?;
        Ok(())
    }
    fn global(&mut self) -> PResult<()> {
        let profile = self.take_at("global.profile_id", 6)?;
        self.config.global.profile_id = Some(profile);
        let level = self.take_at("global.level_id", 4)?;
        self.config.global.level_id = Some(level);
        let (profile, level) = (profile.value, level.value);
        self.report
            .derived
            .insert("profile_id".into(), FieldValue::from(profile));
        self.report
            .derived
            .insert("level_id".into(), FieldValue::from(level));
        self.config.global.flag_a = Some(self.flag_at("global.flag_a")?);
        let sr = self.take_at("global.sample_rate_index", 6)?;
        self.config.global.sample_rate_index = Some(sr);
        let sr = sr.value;
        const RATES: [u32; 13] = [
            96000, 88200, 64000, 48000, 44100, 32000, 24000, 22050, 16000, 12000, 11025, 8000, 7350,
        ];
        if let Some(&rate) = RATES.get(sr as usize) {
            self.report
                .derived
                .insert("sample_rate_hz".into(), FieldValue::from(rate));
            self.config.global.sample_rate_hz = Some(u64::from(rate));
        } else if sr <= 15 {
            return self.stop("sample-rate index retains prior decoder state; a standalone cookie does not supply that rate context");
        } else {
            return self.invalid(
                "sample-rate-index",
                "sample-rate indices 16 through 63 are invalid in this APAC configuration",
            );
        }
        let frame_size = self.take_at("global.frame_size_index", 6)?;
        self.config.global.frame_size_index = Some(frame_size);
        if frame_size.value != 0 {
            return self
                .stop("bound reference codec only implements frame-size index 0 (1024 samples)");
        }
        self.report
            .derived
            .insert("frame_samples".into(), FieldValue::from(1024));
        self.config.global.frame_samples = Some(1024);
        let channels = self.take_at("global.channel_count", 8)?;
        self.config.global.channel_count = Some(channels);
        let channels = channels.value;
        if channels == 0 {
            return self.invalid("channel-count", "zero declared channels");
        }
        self.report
            .derived
            .insert("channels".into(), FieldValue::from(channels));
        self.config.global.channels = Some(channels);
        // These wire fields have confirmed boundaries; their operational names remain unassigned.
        self.config.global.parameter_b = Some(self.take_at("global.parameter_b", 8)?);
        let flag_c = self.flag_at("global.flag_c")?;
        self.config.global.flag_c = Some(flag_c);
        if flag_c.value {
            return self.stop("metadata-output mode is outside the raw PCM decoder profile");
        }
        let n = self.esc_at("global.component_count", [3, 6, 12])?;
        self.config.global.component_count = Some(n);
        let n = n.value;
        if n == 0 {
            return self.invalid("component-count", "no audio scene component");
        }
        let n = self.count(n, 12)?;
        let mut total = 0u64;
        let mut occupied = vec![false; channels as usize];
        let mut effective = Vec::<(u64, u64, u64, usize)>::new();
        for i in 0..n {
            let prefix = format!("components[{i}]");
            let start = self.take_at(&format!("{prefix}.lowest_channel_index"), 8)?;
            self.config.components.push(Component {
                lowest_channel_index: Some(start),
                ..Component::default()
            });
            let start = start.value;
            let kind = self.take_at(&format!("{prefix}.type"), 3)?;
            self.component_mut().kind = Some(kind);
            let kind = kind.value;
            let component_channels = match kind {
                0 => self.lbr_component(&prefix, start, channels)?,
                2 => self.hoa_component(&prefix, start, channels)?,
                _ => return self.stop(format!("ASC type {kind} is not implemented")),
            };
            if let Some(&(_, count, previous_kind, first)) =
                effective.iter().find(|&&(s, _, _, _)| s == start)
            {
                if count != component_channels || kind != previous_kind {
                    return self.invalid(
                        "channel-range",
                        "duplicate component start has a different type or extent",
                    );
                }
                self.report.derived.insert(
                    format!("{prefix}.effective_component_index"),
                    FieldValue::from(first),
                );
                self.component_mut().effective_component_index = Some(first as u64);
            } else {
                for c in start..start + component_channels {
                    if std::mem::replace(&mut occupied[c as usize], true) {
                        return self
                            .invalid("channel-range", "overlapping component channel ranges");
                    }
                }
                effective.push((start, component_channels, kind, i));
                total = total.checked_add(component_channels).ok_or_else(|| {
                    ParseError::new(self.pos(), "overflow", "component channel sum overflow")
                })?;
            }
            self.report.derived.insert(
                format!("{prefix}.channels"),
                FieldValue::from(component_channels),
            );
            self.component_mut().channels = Some(component_channels);
        }
        if total != channels {
            return self.invalid(
                "channel-count",
                "component channels do not cover declared channels",
            );
        }
        let additional = self.flag_at("global.additional_asc_present")?;
        self.config.global.additional_asc_present = Some(additional);
        let additional = additional.value;
        let parameter_roots = if additional {
            let count = self.esc_at("global.additional_component_count", [3, 6, 12])?;
            self.config.global.additional_component_count = Some(count);
            let count = count.value;
            if count == 0 || count > channels {
                return self.invalid(
                    "additional-component-count",
                    "additional component count exceeds its nonempty channel ranges",
                );
            }
            let count = self.count(count, 11)?;
            let mut starts = Vec::with_capacity(count);
            for i in 0..count {
                let start = self.take_at(
                    &format!("additional_components[{i}].lowest_channel_index"),
                    8,
                )?;
                self.config.additional_components.push(AdditionalComponent {
                    lowest_channel_index: Some(start),
                    ..AdditionalComponent::default()
                });
                let start = start.value;
                if start >= channels || starts.contains(&start) {
                    return self.invalid(
                        "additional-channel-range",
                        "invalid or duplicate additional component start",
                    );
                }
                starts.push(start);
                let kind = self.take_at(&format!("additional_components[{i}].type"), 3)?;
                self.config.additional_components[i].kind = Some(kind);
                if kind.value > 5 {
                    return self.invalid(
                        "additional-component-type",
                        "additional ASC type 6 or 7 is rejected by the reference configuration reader",
                    );
                }
            }
            for (i, &start) in starts.iter().enumerate() {
                let end = starts
                    .iter()
                    .copied()
                    .filter(|&n| n > start)
                    .min()
                    .unwrap_or(channels);
                let span = if count == 1 { channels } else { end - start };
                self.report.derived.insert(
                    format!("additional_components[{i}].channels"),
                    FieldValue::from(span),
                );
                self.config.additional_components[i].channels = Some(span);
            }
            (0..count)
                .map(|i| format!("additional_components[{i}]"))
                .collect::<Vec<_>>()
        } else {
            (0..n).map(|i| format!("components[{i}]")).collect()
        };
        for (index, root) in parameter_roots.into_iter().enumerate() {
            let parameter_0 = self.esc_at(&format!("{root}.parameter_0"), [3, 6, 9])?;
            // This declaration is not an allocation length. Its final 32-bit
            // segment permits the escaped sum to exceed u32; retain the full
            // wire value and report the reference's narrowed storage separately.
            let bit_offset = self.pos();
            let value = self.escaped(&format!("{root}.parameter_1"), [2, 8, 32], u64::MAX)?;
            let parameter_1 = at(value, bit_offset);
            if additional {
                let declared = &mut self.config.additional_components[index];
                (declared.parameter_0, declared.parameter_1) = (Some(parameter_0), parameter_1);
            } else {
                let declared = &mut self.config.components[index];
                (declared.parameter_0, declared.parameter_1) = (Some(parameter_0), parameter_1);
            }
            if value > u64::from(u32::MAX) {
                self.report.derived.insert(
                    format!("{root}.parameter_1_reference_u32"),
                    FieldValue::from(value as u32),
                );
            }
        }
        let present = self.flag_at("ancillary.scene_graph_present")?;
        self.config.ancillary.scene_graph_present = Some(present);
        if present.value {
            self.scene_graph()?;
        }
        let present = self.flag_at("ancillary.audio_scenes_present")?;
        self.config.ancillary.audio_scenes_present = Some(present);
        if present.value {
            self.audio_scenes()?;
        }
        let present = self.flag_at("ancillary.loudness_drc_present")?;
        self.config.ancillary.loudness_drc_present = Some(present);
        if present.value {
            self.loudness_drc(channels)?;
        }
        let present = self.flag_at("ancillary.metadata_present")?;
        self.config.ancillary.metadata_present = Some(present);
        if present.value {
            self.metadata_configuration()?;
        }
        let present = self.flag_at("ancillary.custom_data_present")?;
        self.config.ancillary.custom_data_present = Some(present);
        if present.value {
            self.custom_data()?;
        }
        self.extensions()?;
        Ok(())
    }
    fn opaque_bytes(&mut self, name: &str, bytes: usize) -> PResult<()> {
        let start = self.pos();
        let mut data = Vec::with_capacity(bytes);
        for _ in 0..bytes {
            data.push(self.bits.read(8)? as u8);
        }
        self.record(
            name,
            start,
            FieldValue::digest(DigestUnit::Bytes, bytes, &data),
        )
    }
    fn custom_data(&mut self) -> PResult<()> {
        let root = "ancillary.custom_data";
        let bytes = self.esc(&format!("{root}.bytes_minus_one"), [4, 8, 16])? as usize + 1;
        let start = self.pos();
        let end = start
            .checked_add(bytes * 8)
            .ok_or_else(|| ParseError::new(start, "overflow", "custom data length overflow"))?;
        if !(3..=4100).contains(&bytes) {
            return self.invalid(
                "custom-data-length",
                "custom configuration exceeds its bounded header and 4096-byte payload",
            );
        }
        let previous = self.bits.set_end(end)?;
        if self.take(&format!("{root}.parameter_0"), 16)? == 0 {
            return self.invalid(
                "custom-data-parameter",
                "custom configuration parameter_0 must be nonzero",
            );
        }
        self.esc(&format!("{root}.parameter_1_minus_one"), [4, 8, 0])?;
        self.config.ancillary.custom_data_flag_a = Some(self.flag_at(&format!("{root}.flag_a"))?);
        self.take(
            &format!("{root}.header_padding"),
            (8 - (self.pos() - start) % 8) % 8,
        )?;
        let payload = (end - self.pos()) / 8;
        if payload > 4096 {
            return self.invalid(
                "custom-data-length",
                "custom configuration payload exceeds 4096 bytes",
            );
        }
        self.opaque_bytes(&format!("{root}.payload"), payload)?;
        self.bits.set_end(previous)?;
        Ok(())
    }
    pub(super) fn component_range(&self, start: u64, count: u64, total: u64) -> PResult<()> {
        if count == 0 || start.checked_add(count).is_none_or(|end| end > total) {
            return self.invalid(
                "channel-range",
                "component channels exceed the declared layout",
            );
        }
        Ok(())
    }
    fn lbr_component(&mut self, prefix: &str, start: u64, total: u64) -> PResult<u64> {
        self.component_mut().lbr_flag = Some(self.flag_at(&format!("{prefix}.lbr_flag"))?);
        let count = self.esc_at(&format!("{prefix}.tce_count"), [5, 10, 16])?;
        self.component_mut().tce_count = Some(count);
        let count = self.count(count.value, 3)?;
        let mut channels = 0u64;
        for t in 0..count {
            let value = self.take_at(&format!("{prefix}.tce[{t}].type"), 3)?;
            self.component_mut().tce_types.push(value);
            let value = value.value;
            channels += match value {
                0 | 3 | 4 => 1,
                1 => 2,
                _ => return self.stop(format!("unverified TCE type {value}")),
            };
        }
        self.component_range(start, channels, total)?;
        let family = self.take_at(&format!("{prefix}.layout_family"), 16)?;
        self.component_mut().layout_family = Some(family);
        let family = family.value;
        if family == 0 {
            for c in 0..channels {
                self.take(&format!("{prefix}.channel_labels[{c}]"), 7)?;
            }
        } else if family == 1 {
            self.take(&format!("{prefix}.channel_bitmap"), 27)?;
        } else {
            self.report.derived.insert(
                format!("{prefix}.layout_tag"),
                FieldValue::from((family << 16) | channels),
            );
            self.component_mut().layout_tag = Some((family << 16) | channels);
        }
        let remapping = self.flag_at(&format!("{prefix}.remapping_present"))?;
        self.component_mut().remapping_present = Some(remapping);
        if remapping.value {
            let width = (64 - (channels - 1).leading_zeros()) as usize;
            for c in 0..channels {
                if self.take(&format!("{prefix}.remapping[{c}]"), width)? >= channels {
                    return self.invalid("channel-remapping", "remapping index out of range");
                }
            }
        }
        Ok(channels)
    }
    fn extensions(&mut self) -> PResult<()> {
        let mut index = 0;
        while self.flag(&format!("extensions[{index}].present"))? {
            let prefix = format!("extensions[{index}]");
            let kind = self.esc_at(&format!("{prefix}.type"), [4, 8, 16])?;
            self.config.extensions.push(Extension {
                kind: Some(kind),
                ..Extension::default()
            });
            let kind = kind.value;
            let bytes = self
                .esc(&format!("{prefix}.bytes_minus_one"), [4, 8, 16])?
                .checked_add(1)
                .ok_or_else(|| {
                    ParseError::new(self.pos(), "overflow", "extension length overflow")
                })?;
            let length = usize::try_from(bytes)
                .ok()
                .and_then(|n| n.checked_mul(8))
                .ok_or_else(|| {
                    ParseError::new(self.pos(), "overflow", "extension bit length overflow")
                })?;
            let start = self.pos();
            let end = start
                .checked_add(length)
                .ok_or_else(|| ParseError::new(start, "overflow", "extension end overflow"))?;
            if length > self.bits.remaining() {
                return self.invalid("truncated", "extension exceeds remaining input");
            }
            if kind != 3 {
                self.opaque_bytes(&format!("{prefix}.opaque_payload"), bytes as usize)?;
                self.extension_mut().opaque_payload = true;
                index += 1;
                if index > 256 {
                    return self.invalid("count-range", "too many extension elements");
                }
                continue;
            }
            let previous = self.bits.set_end(end)?;
            // ContentOrigin stores five bounded numeric fields using a +1 sentinel convention.
            for (field, width) in [8, 3, 10, 10, 8].into_iter().enumerate() {
                let raw = self.take(
                    &format!("{prefix}.content_origin.values[{field}].encoded"),
                    width,
                )?;
                self.report.derived.insert(
                    format!("{prefix}.content_origin.values[{field}]"),
                    FieldValue::from(raw as i64 - 1),
                );
            }
            let padding = end - self.pos();
            let value = self.take(&format!("{prefix}.padding"), padding % 8)?;
            self.extension_mut().padding = Some(value);
            if padding > 7 {
                self.opaque_bytes(&format!("{prefix}.extra_payload"), padding / 8)?;
                self.extension_mut().extra_payload = true;
            }
            self.bits.set_end(previous)?;
            index += 1;
            if index > 256 {
                return self.invalid("count-range", "too many extension elements");
            }
        }
        Ok(())
    }
}
