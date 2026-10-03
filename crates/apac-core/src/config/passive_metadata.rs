//! Bounded renderer declarations retained for raw PCM output. No renderer runs.
use super::{
    parser::{PResult, Parser},
    passive::PositionSyntax,
};
use crate::prelude::*;

impl Parser<'_> {
    pub(super) fn passive_values(&mut self, p: &str, widths: &[usize]) -> PResult<()> {
        for (i, &width) in widths.iter().enumerate() {
            self.take(&format!("{p}.encoded[{i}]"), width)?;
        }
        Ok(())
    }
    fn metadata_esc(&mut self, p: &str) -> PResult<u64> {
        self.esc(p, [4, 7, 10])
    }
    pub(super) fn metadata_configuration(&mut self) -> PResult<()> {
        let p = "ancillary.metadata";
        if self.flag(&format!("{p}.compression_present"))? {
            let kind = self.take(&format!("{p}.compression.type"), 3)?;
            let bytes = self.esc(&format!("{p}.compression.bytes"), [4, 8, 16])? as usize;
            self.metadata_compression(&format!("{p}.compression"), kind, bytes)?;
        }
        if !self.flag(&format!("{p}.configuration_present"))? {
            return self.invalid(
                "metadata-presence",
                "renderer configuration presence disagrees with its outer declaration",
            );
        }
        if self.flag(&format!("{p}.parameters_present"))? {
            let count = self.metadata_esc(&format!("{p}.parameter_count"))?;
            if count > 128 {
                return self.invalid(
                    "metadata-parameter-count",
                    "renderer configuration permits at most 128 parameters",
                );
            }
            for i in 0..count {
                let q = format!("{p}.parameters[{i}]");
                let kind = self.metadata_esc(&format!("{q}.id"))?;
                self.metadata_global_parameter(&q, kind)?;
            }
        }
        let count = self.metadata_esc(&format!("{p}.group_count"))?;
        let count = self.count(count, 6)?;
        for i in 0..count {
            let q = format!("{p}.groups[{i}]");
            self.metadata_esc(&format!("{q}.id"))?;
            if !self.flag(&format!("{q}.default"))? {
                if self.flag(&format!("{q}.component_reference"))? {
                    self.metadata_esc(&format!("{q}.component_index"))?;
                    match self.take(&format!("{q}.component_type"), 3)? {
                        0 | 2 | 3 => {
                            self.metadata_esc(&format!("{q}.parameter_0"))?;
                        }
                        1 | 4 => {
                            self.metadata_esc(&format!("{q}.parameter_0"))?;
                            if self.flag(&format!("{q}.parameter_1_present"))? {
                                self.metadata_esc(&format!("{q}.parameter_1"))?;
                            }
                        }
                        _ => {
                            return self.invalid(
                                "metadata-component-type",
                                "reserved renderer component type",
                            );
                        }
                    }
                } else {
                    let channels = self.metadata_esc(&format!("{q}.channel_count_minus_one"))? + 1;
                    if self.flag(&format!("{q}.contiguous_channels"))? {
                        self.metadata_esc(&format!("{q}.first_channel"))?;
                    } else {
                        let channels = self.count(channels, 4)?;
                        for c in 0..channels {
                            self.metadata_esc(&format!("{q}.channel_indices[{c}]"))?;
                        }
                    }
                }
            }
            if self.flag(&format!("{q}.data_present"))? {
                self.renderer_data(&format!("{q}.data"))?;
            }
        }
        Ok(())
    }
    fn metadata_global_parameter(&mut self, p: &str, kind: u64) -> PResult<()> {
        match kind {
            0..=2 => {
                self.take(&format!("{p}.encoded"), 1)?;
            }
            3 => {
                self.take(&format!("{p}.encoded"), 5)?;
            }
            4 => self.passive_values(p, &[3, 8, 8, 8, 3, 8, 8, 8])?,
            5 => {
                self.take(&format!("{p}.gain_encoded"), 8)?;
                if !self.flag(&format!("{p}.unit_range"))? {
                    self.take(&format!("{p}.range_encoded"), 4)?;
                }
                if self.flag(&format!("{p}.cartesian"))? {
                    self.passive_values(p, &[8, 8, 8, 7])?;
                } else {
                    self.passive_values(p, &[9, 8, 7, 8])?;
                }
            }
            6 => {
                self.passive_values(p, &[2, 2, 3])?;
                for i in 0..9 {
                    let q = format!("{p}.items[{i}]");
                    if !self.flag(&format!("{q}.default"))? {
                        self.passive_values(&q, &[7; 5])?;
                    }
                }
                let bits = self.take(&format!("{p}.mask_bits_minus_one"), 7)? as usize + 1;
                self.passive_bits(&format!("{p}.mask"), bits)?;
            }
            7 => {
                if self.flag(&format!("{p}.value_present"))? {
                    self.take(&format!("{p}.value_encoded"), 32)?;
                }
                match self.take(&format!("{p}.type"), 3)? {
                    0 => {}
                    1 => {
                        self.take(&format!("{p}.index"), 8)?;
                    }
                    2 => {
                        self.metadata_resource(&format!("{p}.resource"))?;
                        self.take(&format!("{p}.mode"), 3)?;
                        return self.stop("bound reference reader and writer reject resource-based HRTF declarations");
                    }
                    _ => return self.stop("reference renderer does not implement this HRTF type"),
                }
            }
            8 => self.metadata_resource(p)?,
            9 => {
                self.metadata_resource(&format!("{p}.resource"))?;
                let start = self.pos();
                let mut text = Vec::new();
                loop {
                    let byte = self.bits.read(8)? as u8;
                    text.push(byte);
                    if byte == 0 {
                        break;
                    }
                    if text.len() > 4096 {
                        return self.invalid(
                            "metadata-string-limit",
                            "renderer label exceeds resource limit",
                        );
                    }
                }
                self.record(
                    &format!("{p}.label"),
                    start,
                    serde_json::json!({"bytes":text.len(),"sha256":crate::model::sha256(&text)}),
                )?;
                self.take(&format!("{p}.layout_encoded"), 4)?;
                let count = self.drc_count(&format!("{p}.speaker_count"), 8, 33)?;
                for i in 0..count {
                    let q = format!("{p}.speakers[{i}]");
                    self.passive_values(&q, &[4, 9, 8])?;
                    if !self.flag(&format!("{q}.default_distance"))? {
                        self.take(&format!("{q}.distance_encoded"), 4)?;
                    }
                    self.take(&format!("{q}.value_encoded"), 7)?;
                    self.take(&format!("{q}.type"), 3)?;
                }
            }
            10 => self.passive_values(p, &[3, 32, 32, 32, 1])?,
            11 => self.position(p, &mut PositionSyntax::default(), true)?,
            12 => {
                if self.flag(&format!("{p}.predefined"))? {
                    self.esc(&format!("{p}.index"), [3, 6, 10])?;
                } else {
                    self.metadata_resource(&format!("{p}.resource"))?;
                    self.esc(&format!("{p}.parameter_0"), [3, 6, 10])?;
                    self.esc(&format!("{p}.parameter_1"), [4, 8, 16])?;
                }
            }
            13 => {
                self.take(&format!("{p}.encoded"), 6)?;
            }
            14 => self.passive_values(p, &[4, 8])?,
            15 => {
                let bytes = self.take(&format!("{p}.bytes_minus_one"), 4)? as usize + 1;
                let start = self.pos();
                let count = self.take(&format!("{p}.channels_minus_one"), 8)? as usize + 1;
                if count * 3 + 8 > bytes * 8 {
                    return self.invalid(
                        "metadata-headphone-size",
                        "headphone metadata channels exceed its byte length",
                    );
                }
                for c in 0..count {
                    self.take(&format!("{p}.channels[{c}]"), 3)?;
                }
                self.passive_bits(&format!("{p}.padding"), start + bytes * 8 - self.pos())?;
            }
            _ => {
                let bytes = self.esc(&format!("{p}.bytes_minus_one"), [4, 8, 16])? as usize + 1;
                self.passive_bits(&format!("{p}.payload"), bytes * 8)?;
            }
        }
        Ok(())
    }
    pub(super) fn metadata_resource(&mut self, p: &str) -> PResult<()> {
        self.take(&format!("{p}.type"), 2)?;
        self.esc(&format!("{p}.parameter_0"), [1, 3, 8])?;
        self.esc(&format!("{p}.parameter_1"), [1, 3, 8])?;
        Ok(())
    }
    pub(super) fn metadata_reverb(&mut self, p: &str) -> PResult<()> {
        for (i, width) in [10, 10, 8, 9, 12].into_iter().enumerate() {
            if self.flag(&format!("{p}.parameters[{i}].present"))? {
                self.take(&format!("{p}.parameters[{i}].encoded"), width)?;
            }
        }
        self.flag(&format!("{p}.flag"))?;
        for i in 5..8 {
            if self.flag(&format!("{p}.parameters[{i}].present"))? {
                self.take(&format!("{p}.parameters[{i}].encoded"), 7)?;
            }
        }
        Ok(())
    }
}
