//! Production admission is narrower than the syntactically recognized HOA range.
use super::{HoaFrameContext, StreamFrameContext};
use crate::prelude::*;
use crate::synthesis::Decoder;

fn third_order_fixture() -> (crate::Config, crate::prelude::Vec<u8>) {
    let value: serde_json::Value = serde_json::from_str(include_str!(
        "../../../../data/hoa-expanded-orders-state-v1.json"
    ))
    .unwrap();
    let row = value["boundaries"]
        .as_array()
        .unwrap()
        .iter()
        .find(|row| row["order"] == 3)
        .unwrap();
    let bytes = |key: &str| {
        row[key]
            .as_str()
            .unwrap()
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|s| u8::from_str_radix(std::str::from_utf8(s).unwrap(), 16).unwrap())
            .collect::<crate::prelude::Vec<_>>()
    };
    (
        crate::Config::parse(&bytes("cookie")).unwrap(),
        bytes("packet"),
    )
}

pub(crate) fn supported_or_order_limit(cookie: &[u8], context: &HoaFrameContext) -> bool {
    if context.is_supported() {
        return true;
    }
    let reason = context.rejection().expect("unsupported reason");
    assert!(
        reason.contains("HOA implementation supports orders 0..3")
            || reason.contains("order-3 implementation limit"),
        "unexpected rejection: {reason}"
    );
    assert!(Decoder::from_cookie(cookie).is_err());
    let stream = StreamFrameContext::from_cookie(cookie).unwrap();
    assert!(!stream.is_supported());
    // Diagnostic getters must not try to load an excluded high-order dictionary.
    let _ = context.component_order_info();
    let _ = context.descriptor_numeric_profile();
    false
}

pub(crate) fn decoder_or_order_limit(cookie: &[u8]) -> Option<Decoder> {
    match Decoder::from_cookie(cookie) {
        Ok(decoder) => Some(decoder),
        Err(error) => {
            let reason = error.to_string();
            assert!(
                reason.contains("HOA implementation supports orders 0..3")
                    || reason.contains("order-3 implementation limit"),
                "unexpected rejection: {reason}"
            );
            assert!(
                !StreamFrameContext::from_cookie(cookie)
                    .unwrap()
                    .is_supported()
            );
            None
        }
    }
}

#[test]
fn production_dictionary_set_ends_at_third_order() {
    assert_eq!(crate::tables::HOA_MAX_SUPPORTED_ORDER, 3);
    assert_eq!(crate::tables::HOA_MAX_SUPPORTED_COEFFICIENTS, 16);
    assert_eq!(crate::tables::SALIENT_CONSTANTS.len(), 12);
    for order in 1usize..=3 {
        for precision in 6u8..=9 {
            let entry =
                &crate::tables::SALIENT_CONSTANTS[(order - 1) * 4 + usize::from(precision - 6)];
            assert_eq!(entry.coefficients, (order + 1).pow(2));
            assert_eq!(entry.precision, precision);
        }
    }
}

#[test]
fn explicit_high_acn_labels_cannot_hide_in_a_small_output_layout() {
    let (mut config, _) = third_order_fixture();
    let component = &mut config.components[0];
    component.layout_tag = Some(0);
    component.hoa.channel_labels = Some((0..16).map(|i| (2 << 16) | i).collect());
    assert!(HoaFrameContext::from_config(&config).is_supported());
    config.components[0].hoa.channel_labels.as_mut().unwrap()[15] = (2 << 16) | 16;
    let context = HoaFrameContext::from_config(&config);
    assert!(!context.is_supported());
    assert!(
        context
            .rejection()
            .unwrap()
            .contains("HOA ACN source label")
    );
    assert!(Decoder::new(&config).is_err());
}

#[test]
fn low_order_discrete_source_output_is_not_a_global_sixteen_channel_limit() {
    let (mut config, packet) = third_order_fixture();
    config.global.profile_id.as_mut().unwrap().value = 0;
    config.global.level_id.as_mut().unwrap().value = 0;
    config.global.channel_count.as_mut().unwrap().value = 32;
    config.global.channels = Some(32);
    config.components[0].channels = Some(32);
    config.components[0].layout_tag = Some((147 << 16) | 32);
    let context = HoaFrameContext::from_config(&config);
    assert!(context.is_supported(), "{:?}", context.rejection());
    assert_eq!(context.recovery_slot_count(), 16);
    let mut decoder = Decoder::new(&config).unwrap();
    assert_eq!(decoder.info().channel_count, 32);
    assert_eq!(decoder.decode_vec(&packet).unwrap().len(), 32 * 1024);
}

#[test]
fn independent_third_order_components_can_still_form_a_larger_stream() {
    let (mut config, _) = third_order_fixture();
    config.global.profile_id.as_mut().unwrap().value = 0;
    config.global.level_id.as_mut().unwrap().value = 0;
    config.global.channel_count.as_mut().unwrap().value = 32;
    config.global.channels = Some(32);
    config.global.component_count.as_mut().unwrap().value = 2;
    let mut second = config.components[0].clone();
    second.lowest_channel_index.as_mut().unwrap().value = 16;
    config.components.push(second);
    let context = StreamFrameContext::from_config(&config).unwrap();
    assert!(context.is_supported(), "{:?}", context.rejection());
    assert_eq!(Decoder::new(&config).unwrap().info().channel_count, 32);
}
