//! Spatial control fields, bounded in-frame shapes, and immutable format tables.
use super::{
    Parser,
    hoa::{HoaConfiguration, HoaPath, HoaState, SalientComponentConfiguration},
};
use crate::config::{CookieReport, ParseError};
use serde::{Deserialize, Serialize};
use std::sync::OnceLock;

pub const PROFILE: &str = "apac-hoa-spatial-controls-v1";
pub const NUMERIC_PROFILE: &str = "apac-hoa-spatial-controls-math-v1";
pub const STATE_PROFILE: &str = "apac-hoa-spatial-controls-state-v1";
pub const FRAME_NUMERIC_PROFILE: &str = "apac-hoa-spatial-controls-math-v2";
pub const FRAME_STATE_PROFILE: &str = "apac-hoa-spatial-controls-state-v2";
pub(super) fn numeric_profile(controls: HoaSpatialControls) -> &'static str {
    if controls.flag_b {
        FRAME_NUMERIC_PROFILE
    } else {
        NUMERIC_PROFILE
    }
}
pub(super) fn state_profile(controls: HoaSpatialControls) -> &'static str {
    if controls.flag_b {
        FRAME_STATE_PROFILE
    } else {
        STATE_PROFILE
    }
}
#[cfg(test)]
#[path = "hoa_controls_tests.rs"]
mod tests;

#[derive(Debug, Clone, Copy, PartialEq, Eq, Serialize, Deserialize)]
pub struct HoaSpatialControls {
    pub flag_a: bool,
    pub flag_b: bool,
    pub flag_c: bool,
    pub flag_d: bool,
    pub flag_e: bool,
    pub flag_f: bool,
    pub parameter_0: u8,
}
impl Default for HoaSpatialControls {
    fn default() -> Self {
        Self {
            flag_a: true,
            flag_b: false,
            flag_c: false,
            flag_d: false,
            flag_e: true,
            flag_f: true,
            parameter_0: 1,
        }
    }
}
impl HoaSpatialControls {
    pub(super) fn from_cookie(parsed: &CookieReport) -> Self {
        let field = |name: &str| {
            parsed
                .fields
                .iter()
                .find(|f| f.name == format!("components[0].hoa.{name}"))
        };
        let flag = |name| field(name).and_then(|f| f.value.as_bool()).unwrap_or(false);
        Self {
            flag_a: flag("flag_a"),
            flag_b: flag("flag_b"),
            flag_c: flag("flag_c"),
            flag_d: flag("flag_d"),
            flag_e: flag("flag_e"),
            flag_f: flag("flag_f"),
            parameter_0: field("parameter_0")
                .and_then(|f| f.value.as_u64())
                .unwrap_or(3)
                .min(3) as u8,
        }
    }
    pub(super) fn extended(self, mixed: bool) -> bool {
        let mut normalized = self;
        normalized.flag_d = false;
        normalized != Self::default() || (self.flag_d && !mixed)
    }
}

#[derive(Deserialize)]
struct Grid {
    method: usize,
    subbands: usize,
    long_ends: Vec<usize>,
}
#[derive(Deserialize)]
struct Format {
    format_profile: String,
    format_sha256: String,
    mean_coefficients_f32: Vec<u32>,
    tables: Vec<Grid>,
}
fn format() -> &'static Format {
    static DATA: OnceLock<Format> = OnceLock::new();
    DATA.get_or_init(|| {
        let f: Format = serde_json::from_str(include_str!(
            "../../../../data/hoa-spatial-controls-format-v1.json"
        ))
        .expect("spatial control tables");
        assert_eq!(f.format_profile, "apac-hoa-spatial-controls-format-v1");
        assert_eq!(f.mean_coefficients_f32.len(), 121);
        assert_eq!(f.tables.len(), 48);
        assert!(
            f.mean_coefficients_f32
                .iter()
                .all(|&w| f32::from_bits(w).is_finite())
        );
        for (i, g) in f.tables.iter().enumerate() {
            assert_eq!((g.method, g.subbands), (i / 16, i % 16 + 1));
            assert_eq!(g.long_ends.len(), g.subbands);
            assert_eq!(g.long_ends.last(), Some(&1024));
            assert!(g.long_ends[0] > 0 && g.long_ends.windows(2).all(|w| w[0] < w[1]));
        }
        f
    })
}
pub fn format_sha256() -> &'static str {
    &format().format_sha256
}
pub fn frame_state_sha256() -> &'static str {
    static HASH: OnceLock<String> = OnceLock::new();
    HASH.get_or_init(|| {
        crate::model::sha256(include_bytes!(
            "../../../../data/hoa-frame-configuration-state-v2.json"
        ))
    })
}
pub(super) fn mean(index: usize) -> f64 {
    f64::from(f32::from_bits(format().mean_coefficients_f32[index]))
}
pub(super) fn boundaries(count: usize, method: usize) -> &'static [usize] {
    &format().tables[method * 16 + count - 1].long_ends
}

/// Retain inactive component descriptors so flag_c=false can restore their shape.
#[derive(Debug, Clone, PartialEq, Serialize, Deserialize)]
pub struct HoaFrameConfiguration {
    pub salient_components: usize,
    pub component_configurations: Vec<SalientComponentConfiguration>,
    pub ambient_indices: Vec<u8>,
    pub explicit_ambient_selection: bool,
}
#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct HoaFrameConfigurationReport {
    pub present: bool,
    pub start_bit_offset: usize,
    pub end_bit_offset: usize,
    pub salient_components: usize,
    pub ambient_components: usize,
    pub component_configurations: Vec<SalientComponentConfiguration>,
    pub ambient_indices: Vec<u8>,
}
fn width(count: usize) -> usize {
    usize::BITS as usize - count.saturating_sub(1).leading_zeros() as usize
}
fn escaped(parser: &mut Parser<'_>, name: &str) -> Result<usize, ParseError> {
    let mut value = 0;
    for (i, w) in [4, 6, 8].into_iter().enumerate() {
        let v = parser.take(&format!("{name}[{i}]"), w)? as usize;
        value += v;
        if v < (1 << w) - 1 {
            break;
        }
    }
    Ok(value)
}

pub(super) fn effective_configuration(
    parser: &mut Parser<'_>,
    state: &mut HoaState,
    cookie: &HoaConfiguration,
    frame_type: u64,
) -> Result<(HoaConfiguration, Option<HoaFrameConfigurationReport>), ParseError> {
    let mut shape = cookie.clone();
    if !cookie.controls.flag_b {
        return Ok((shape, None));
    }
    let mut active = state
        .frame_configuration
        .clone()
        .unwrap_or_else(|| HoaFrameConfiguration {
            salient_components: usize::from(cookie.salient_components),
            component_configurations: cookie.salient_configurations.clone(),
            ambient_indices: cookie.ambient_indices().to_vec(),
            explicit_ambient_selection: cookie.explicit_ambient_selection,
        });
    let start = parser.bits.position();
    let present = parser.flag("hoa.frame_configuration.present")?;
    // Independence belongs to the current core frame. The bound decoder sets
    // this flag after Deserialize and therefore checks the preceding frame;
    // the format requirement must not inherit that one-frame delay.
    if !present && matches!(frame_type, 1 | 2) {
        return Err(ParseError::new(
            start,
            "hoa-frame-configuration",
            "independent frames require a spatial configuration restatement",
        ));
    }
    if present {
        let salient = escaped(parser, "hoa.frame_configuration.salient_components")?;
        let n = usize::from(cookie.recovery_slots);
        let ambient = parser.take(
            "hoa.frame_configuration.ambient_components_encoded",
            width(n),
        )? as usize
            + usize::from(salient == 0);
        if salient > usize::from(cookie.salient_components)
            || ambient > n
            || salient + ambient > usize::from(cookie.transport_channels)
        {
            return Err(ParseError::new(
                parser.bits.position(),
                "hoa-frame-configuration",
                "active components exceed cookie or transport capacity",
            ));
        }
        active.salient_components = salient;
        if cookie.controls.flag_c {
            let maximum = cookie
                .salient_configurations
                .iter()
                .map(|s| s.subband_count)
                .max()
                .unwrap_or(0);
            for (i, c) in active
                .component_configurations
                .iter_mut()
                .take(salient)
                .enumerate()
            {
                let bands = escaped(
                    parser,
                    &format!("hoa.frame_configuration.components[{i}].subbands_minus_one"),
                )? + 1;
                let order = if cookie.full_order {
                    parser.take(
                        &format!("hoa.frame_configuration.components[{i}].order"),
                        width(usize::from(cookie.order) + 1),
                    )? as u8
                } else {
                    cookie.order
                };
                let coefficients = if cookie.full_order {
                    (usize::from(order) + 1).pow(2)
                } else {
                    n
                };
                if bands > maximum || coefficients > n || coefficients < 2 {
                    return Err(ParseError::new(
                        parser.bits.position(),
                        "hoa-frame-configuration",
                        "active descriptor exceeds cookie dimensions or subband capacity",
                    ));
                }
                *c = SalientComponentConfiguration {
                    order,
                    subband_count: bands,
                    coefficient_count: coefficients,
                };
            }
        }
        active.explicit_ambient_selection =
            parser.flag("hoa.frame_configuration.ambient_selection_present")?;
        active.ambient_indices = (0..ambient).map(|i| i as u8).collect();
        if active.explicit_ambient_selection {
            let mut limit = n;
            for i in (0..ambient).rev() {
                let value = parser.take(
                    &format!("hoa.frame_configuration.ambient_selection[{i}]"),
                    width(limit),
                )? as usize;
                if value >= limit
                    || value < i
                    || (i + 1 < ambient && value >= usize::from(active.ambient_indices[i + 1]))
                {
                    return Err(ParseError::new(
                        parser.bits.position(),
                        "hoa-frame-configuration",
                        "ambient indices must be distinct increasing indices inside the recovery domain",
                    ));
                }
                active.ambient_indices[i] = value as u8;
                if value == i {
                    break;
                }
                limit = value + 1;
            }
        }
    }
    shape.salient_components = active.salient_components as u8;
    shape.salient_configurations =
        active.component_configurations[..active.salient_components].to_vec();
    shape.ambient_components = active.ambient_indices.len() as u8;
    shape.core_channels = shape.salient_components + shape.ambient_components;
    shape.ambient_selection = active.ambient_indices.clone();
    shape.explicit_ambient_selection = active.explicit_ambient_selection;
    shape.path = if shape.salient_components == 0 {
        HoaPath::Ambient
    } else if shape.ambient_components == 0 {
        HoaPath::Salient
    } else {
        HoaPath::Mixed
    };
    shape.static_ambient = true;
    if shape.ambient_components < 4 {
        shape.ambient_transform = super::hoa_ambient::AmbientTransform::Disabled;
    }
    let report = HoaFrameConfigurationReport {
        present,
        start_bit_offset: start,
        end_bit_offset: parser.bits.position(),
        salient_components: active.salient_components,
        ambient_components: active.ambient_indices.len(),
        component_configurations: shape.salient_configurations.clone(),
        ambient_indices: active.ambient_indices.clone(),
    };
    state.frame_configuration = Some(active);
    if cookie.salient_components > 0 && state.salient.is_none() {
        let maximum = cookie
            .salient_configurations
            .iter()
            .map(|c| c.subband_count)
            .max()
            .unwrap();
        state.salient = Some(Box::new(super::hoa_salient::SalientState::with_dimensions(
            vec![usize::from(cookie.recovery_slots); usize::from(cookie.salient_components)],
            vec![maximum; usize::from(cookie.salient_components)],
        )));
    }
    Ok((shape, Some(report)))
}
