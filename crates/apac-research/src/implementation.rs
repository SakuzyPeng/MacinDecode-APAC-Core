//! Report identifiers of the independent decoder: backend, support scope and
//! state, numeric and layout profiles. They name the path a configuration
//! selected in `decode-sq` reports; the core decoder does not carry them.
use crate::{identity, inspect};
use apac_core::{Decoder, StreamKind};

/// Stereo SQ with neutral scene metadata and ASP.
pub const BACKEND: &str = "rust_sq_cac_tns_bwe2_drc_off_f64_fft_v11";
pub const QUALIFICATION: &str = "independent_math_reference";
pub const ACCESS_PROFILE: &str = "apac-sq-access-v1";
pub const HOA_ACCESS_PROFILE: &str = "apac-hoa-access-v1";
const CHANNELS_BACKEND: &str = "rust_channel_sq_cac_tns_bwe2_drc_off_f64_fft_v2";
const COMPOSITE_BACKEND: &str = "rust_hoa_multiple_asc_sq_drc_off_f64_fft_v2";
const HOA_EXPANDED_BACKEND: &str = "rust_hoa_expanded_orders_sq_drc_off_f64_fft_v2";
const HOA_PARTIAL_BACKEND: &str = "rust_hoa_partial_domain_sq_drc_off_f64_fft_v2";
const HOA_CONTROLS_BACKEND: &str = "rust_hoa_spatial_controls_sq_drc_off_f64_fft_v3";
const HOA_FRAME_CONTROLS_BACKEND: &str = "rust_hoa_spatial_controls_sq_drc_off_f64_fft_v4";
const HOA_TRANSPORT_BACKEND: &str = "rust_hoa_transports_sq_cac_tns_bwe2_drc_off_f64_fft_v2";
const HOA_QUANTIZATION_BACKEND: &str = "rust_hoa_salient_quantization_sq_drc_off_f64_fft_v2";
const HOA_AMBIENT_COUNTS_BACKEND: &str = "rust_hoa_ambient_counts_sq_drc_off_f64_fft_v2";
const HOA_COUNTS_BACKEND: &str = "rust_hoa_salient_counts_sq_drc_off_f64_fft_v2";
const HOA_COMPONENT_ORDERS_BACKEND: &str = "rust_hoa_component_orders_sq_drc_off_f64_fft_v2";
const HOA_AMBIENT_BACKEND: &str = "rust_hoa_ambient_sq_drc_off_f64_fft_v2";
const HOA_SALIENT_BACKEND: &str = "rust_hoa_salient_sq_drc_off_f64_fft_v2";
const HOA_MIXED_BACKEND: &str = "rust_hoa_mixed_sq_drc_off_f64_fft_v2";
const HOA_STATIC_AMBIENT_BACKEND: &str = "rust_hoa_static_ambient_sq_drc_off_f64_fft_v2";
const HOA_ADDITIVE_BACKEND: &str = "rust_hoa_additive_sq_drc_off_f64_fft_v2";
const HOA_DYNAMIC_BACKEND: &str = "rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v2";
const HOA_DYNAMIC_DOMAINS_BACKEND: &str = "rust_hoa_dynamic_domains_sq_drc_off_f64_fft_v2";

pub fn channel_layout_profile(decoder: &Decoder) -> Option<&'static str> {
    // A component's discrete profile does not describe the whole HOA stream.
    if decoder.composite().is_some() {
        return None;
    }
    decoder.transport().channel_layout_profile()
}
pub fn backend(decoder: &Decoder) -> &'static str {
    if decoder.composite().is_some() {
        return COMPOSITE_BACKEND;
    }
    if let Some(context) = decoder.hoa() {
        if context.shared_configuration_enabled() {
            return "rust_hoa_shared_configuration_sq_drc_off_f64_fft_v2";
        }
        if context.static_remapping().is_some() {
            return "rust_hoa_static_remapping_sq_drc_off_f64_fft_v2";
        }
        if context.source_layout_enabled() {
            return "rust_hoa_source_layout_sq_drc_off_f64_fft_v2";
        }
        return if context.dynamic_domains_extended() {
            HOA_DYNAMIC_DOMAINS_BACKEND
        } else if context.controls_extended() {
            if context.spatial_controls().flag_b {
                HOA_FRAME_CONTROLS_BACKEND
            } else {
                HOA_CONTROLS_BACKEND
            }
        } else if !context.full_order() {
            HOA_PARTIAL_BACKEND
        } else if context.transport_extended() {
            HOA_TRANSPORT_BACKEND
        } else if context.expanded_orders() {
            HOA_EXPANDED_BACKEND
        } else if context.quantization_extended() {
            HOA_QUANTIZATION_BACKEND
        } else if context.ambient_count_extended() {
            HOA_AMBIENT_COUNTS_BACKEND
        } else if context.salient_components() != 0 && context.salient_components() != 5 {
            HOA_COUNTS_BACKEND
        } else if context.component_orders_extended() {
            HOA_COMPONENT_ORDERS_BACKEND
        } else if context.ambient_combination() == inspect::AmbientCombination::Add {
            HOA_ADDITIVE_BACKEND
        } else if context.dynamic_selection_enabled() {
            HOA_DYNAMIC_BACKEND
        } else if context.static_ambient_enabled() {
            HOA_STATIC_AMBIENT_BACKEND
        } else if context.salient_components() != 0 && context.ambient_components() != 0 {
            HOA_MIXED_BACKEND
        } else if context.salient_components() != 0 {
            HOA_SALIENT_BACKEND
        } else {
            HOA_AMBIENT_BACKEND
        };
    }
    if decoder.info().kind == StreamKind::Channels {
        CHANNELS_BACKEND
    } else {
        BACKEND
    }
}
pub fn state_profile(decoder: &Decoder) -> &'static str {
    if decoder.composite().is_some() {
        return identity::STREAM_STATE_PROFILE;
    }
    if let Some(context) = decoder.hoa() {
        return context.state_profile();
    }
    if decoder.info().kind == StreamKind::Channels {
        identity::CHANNEL_STATE_PROFILE
    } else {
        identity::PACKET_STATE_PROFILE
    }
}
pub fn support_scope(decoder: &Decoder) -> &'static str {
    if decoder.composite().is_some() {
        return "hoa_multiple_asc_sq_drc_off";
    }
    if let Some(context) = decoder.hoa() {
        if context.shared_configuration_enabled() {
            return "hoa_shared_configuration_sq_drc_off";
        }
        if context.static_remapping().is_some() {
            return "hoa_static_remapping_sq_drc_off";
        }
        if context.source_layout_enabled() {
            return "hoa_source_layout_sq_drc_off";
        }
        if context.dynamic_domains_extended() {
            return "hoa_dynamic_actual_domains_sq_drc_off";
        }
        if context.controls_extended() {
            return "hoa_spatial_controls_sq_drc_off";
        }
        if !context.full_order() {
            return "hoa_partial_domain_sq_drc_off";
        }
        if context.transport_extended() {
            return "hoa_sq_transport_compositions_drc_off";
        }
        if context.expanded_orders() {
            return "hoa_orders_zero_to_ten_sq_drc_off";
        }
        if context.quantization_extended() {
            return "hoa_salient_6_to9_bits_sq_drc_off";
        }
        if context.ambient_count_extended() {
            return "hoa_variable_ambient_counts_sq_drc_off";
        }
        if context.salient_components() != 0 && context.salient_components() != 5 {
            return "hoa_variable_salient_counts_sq_drc_off";
        }
        if context.ambient_combination() == inspect::AmbientCombination::Add {
            return if context.dynamic_selection_enabled() {
                "hoa_dynamic9_to16_additive_sq_drc_off"
            } else if context.order() == 2 {
                "hoa2_additive_sq_drc_off"
            } else {
                "hoa3_additive_sq_drc_off"
            };
        }
        if context.dynamic_selection_enabled() {
            return if context.ambient_components() == 0 {
                "hoa_dynamic9_to16_salient_sq_drc_off"
            } else {
                "hoa_dynamic9_to16_mixed_sq_drc_off"
            };
        }
        if context.static_ambient_enabled() {
            return match (context.order(), context.salient_components()) {
                (1, 0) => "hoa1_static_ambient_sq_drc_off",
                (2, 5) => "hoa2_mixed_static_ambient_sq_drc_off",
                (3, 5) => "hoa3_mixed_static_ambient_sq_drc_off",
                _ => "hoa3_static_ambient_sq_drc_off",
            };
        }
        return match (
            context.order(),
            context.salient_components(),
            context.ambient_components(),
        ) {
            (1, 0, _) => "hoa1_ambient4_sq_drc_off",
            (2, 5, 4) => "hoa2_salient5_ambient4_sq_drc_off",
            (3, 5, 4) => "hoa3_salient5_ambient4_sq_drc_off",
            (2, 5, 0) => "hoa2_salient5_sq_drc_off",
            (3, 5, 0) => "hoa3_salient5_sq_drc_off",
            _ => "hoa3_ambient16_sq_drc_off",
        };
    }
    if matches!(decoder.info().channel_count, 12 | 24) {
        "single_asc_714_222_sq_drc_off"
    } else if decoder.info().kind == StreamKind::Channels {
        "single_asc_mono_51_71_sq_drc_off"
    } else {
        "stereo_sq_drc_off_neutral_scene_asp"
    }
}
pub fn hoa_numeric_profile(decoder: &Decoder) -> Option<&'static str> {
    if decoder.composite().is_some() {
        return Some("apac-hoa-shared-configuration-math-v1");
    }
    decoder.hoa().map(|c| c.numeric_profile())
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn cookies(file: &str) -> Vec<Vec<u8>> {
        let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("../../data")
            .join(file);
        let data: Value = serde_json::from_str(&std::fs::read_to_string(path).unwrap()).unwrap();
        let hex = |v: &Value| -> Vec<u8> {
            let s = v.as_str().unwrap();
            (0..s.len())
                .step_by(2)
                .map(|i| u8::from_str_radix(&s[i..i + 2], 16).unwrap())
                .collect()
        };
        match data["fixtures"].as_array() {
            Some(rows) => rows.iter().map(|row| hex(&row["cookie"])).collect(),
            None => vec![hex(&data["cookie"])],
        }
    }

    /// The identifiers the HOA state fixtures pin for every cookie they hold.
    #[test]
    fn hoa_fixtures_select_their_backend_and_profiles() {
        // (fixture file, backend, state profile, HOA numeric profile)
        type Case = (
            &'static str,
            Option<&'static str>,
            Option<&'static str>,
            Option<&'static str>,
        );
        let cases: [Case; 7] = [
            (
                "hoa-salient-state-v1.json",
                None,
                None,
                Some("apac-hoa-salient-math-v1"),
            ),
            (
                "hoa-mixed-state-v1.json",
                Some("rust_hoa_mixed_sq_drc_off_f64_fft_v2"),
                None,
                None,
            ),
            (
                "hoa-static-ambient-state-v1.json",
                Some("rust_hoa_static_ambient_sq_drc_off_f64_fft_v2"),
                None,
                Some("apac-hoa-static-ambient-math-v1"),
            ),
            (
                "hoa-dynamic-state-v1.json",
                Some("rust_hoa_dynamic_selection_sq_drc_off_f64_fft_v2"),
                None,
                None,
            ),
            (
                "hoa-additive-state-v1.json",
                Some("rust_hoa_additive_sq_drc_off_f64_fft_v2"),
                Some("apac-hoa-additive-state-v1"),
                None,
            ),
            (
                "hoa-component-orders-state-v1.json",
                Some("rust_hoa_component_orders_sq_drc_off_f64_fft_v2"),
                Some("apac-hoa-component-orders-state-v1"),
                None,
            ),
            (
                "hoa-order1-state-v1.json",
                Some("rust_hoa_component_orders_sq_drc_off_f64_fft_v2"),
                Some("apac-hoa-component-orders-state-v1"),
                None,
            ),
        ];
        for (file, expected_backend, state, numeric) in cases {
            for cookie in cookies(file) {
                let decoder = Decoder::from_cookie(&cookie).unwrap();
                if let Some(expected) = expected_backend {
                    assert_eq!(backend(&decoder), expected, "{file}");
                }
                if let Some(expected) = state {
                    assert_eq!(state_profile(&decoder), expected, "{file}");
                }
                if let Some(expected) = numeric {
                    assert_eq!(hoa_numeric_profile(&decoder), Some(expected), "{file}");
                }
            }
        }
    }
}
