//! Frozen identifiers of published results.
//!
//! Profile strings name a syntax area, state model or arithmetic order; the
//! digest functions return the SHA-256 of the data or format description a
//! profile was qualified against. Reports carry both, so they never change
//! for an existing profile: new behavior gets a new identifier.

pub use crate::bwe2_math::{format_sha256 as bwe2_format_sha256, math_sha256 as bwe2_math_sha256};
pub use crate::frame::stream::{PROFILE as STREAM_PROFILE, STATE_PROFILE as STREAM_STATE_PROFILE};
pub use crate::frame::{
    BWE2_NUMERIC_PROFILE, CAC_NUMERIC_PROFILE, CHANNEL_STATE_PROFILE, DRC_RULES_VERSION,
    HOA_DYNAMIC_DOMAINS_PROFILE, HOA_DYNAMIC_SUBBAND_PROFILE, HOA_EXPANDED_ORDERS_PROFILE,
    HOA_NUMERIC_PROFILE, HOA_PARTIAL_PROFILE, HOA_SALIENT_ORDER1_PROFILE,
    HOA_SALIENT_PARTITION_PROFILE, HOA_SALIENT_SUBBAND_PROFILE, HOA_SHARED_CONFIG_PROFILE,
    HOA_SHARED_DRC_PROFILE, HOA_SOURCE_LAYOUT_PROFILE, HOA_SPATIAL_CONTROLS_PROFILE,
    HOA_STATE_PROFILE, HOA_STATIC_REMAPPING_PROFILE, HOA_TRANSPORT_PROFILE,
    STATE_PROFILE as PACKET_STATE_PROFILE, TNS_NUMERIC_PROFILE, cac_math_sha256,
    drc_codebook_sha256, hoa_ambient_format_sha256, hoa_ambient_math_sha256,
    hoa_dynamic_domains_format_sha256, hoa_dynamic_format_sha256, hoa_expanded_math_sha256,
    hoa_frame_configuration_state_sha256, hoa_salient_format_sha256, hoa_salient_math_sha256,
    hoa_salient_subbands_format_sha256, hoa_shared_config_format_sha256,
    hoa_shared_drc_format_sha256, hoa_source_layout_format_sha256,
    hoa_spatial_controls_format_sha256, hoa_static_remapping_format_sha256,
    hoa_transport_format_sha256, tns_math_sha256,
};
pub use crate::numeric::{PROFILE as NUMERIC_PROFILE, tables_sha256 as numeric_tables_sha256};
