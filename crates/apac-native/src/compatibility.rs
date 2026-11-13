//! Optional explanations for observed native failures, never input eligibility.
use crate::error::Error;
use apac_core::config::parse_recorded;
use sha2::{Digest, Sha256};
use std::{fs::File, io::Read};

const COMPONENT: &str =
    "/System/Library/Components/AudioCodecs.component/Contents/MacOS/AudioCodecs";
const COMPONENT_SHA256: &str = "5bd433b1cd97b1a93d1b5c389c1b46314757045a51c9e51d3cbf5212cab294b2";
const RUNTIME_UUID: [u8; 16] = [
    0x61, 0xc4, 0x4b, 0x37, 0x3b, 0x5d, 0x33, 0x76, 0x90, 0xac, 0x8e, 0x17, 0x06, 0xff, 0x3b, 0x20,
];
const INVALID_DATA: i32 = i32::from_be_bytes(*b"!dat");

unsafe extern "C" {
    fn apac_loaded_codec_identity(uuid: *mut u8, cpu: *mut u32) -> i32;
}

fn qualified_slice(uuid: [u8; 16], cpu: [u32; 2]) -> bool {
    uuid == RUNTIME_UUID && cpu == [0x0100_000c, 0x8000_0002]
}

fn qualified_component() -> bool {
    let mut uuid = [0; 16];
    let mut cpu = [0; 2];
    // Both buffers have the exact lengths required by the bridge.
    if unsafe { apac_loaded_codec_identity(uuid.as_mut_ptr(), cpu.as_mut_ptr()) } != 1
        || !qualified_slice(uuid, cpu)
    {
        return false;
    }
    let Ok(mut file) = File::open(COMPONENT) else {
        return false;
    };
    let mut digest = Sha256::new();
    let mut buffer = [0u8; 64 * 1024];
    loop {
        match file.read(&mut buffer) {
            Ok(0) => break,
            Ok(count) => digest.update(&buffer[..count]),
            Err(_) => return false,
        }
    }
    format!("{:x}", digest.finalize()) == COMPONENT_SHA256
}

fn decode_operation(operation: &str) -> bool {
    [
        "AudioConverterSetProperty(DecompressionMagicCookie) (",
        "AudioConverterSetProperty(DecompressionMagicCookie/replay) (",
        "ExtAudioFileWrapAudioFileID(read) (",
        "ExtAudioFileSetProperty(ClientDataFormat) (",
        "ExtAudioFileSetProperty(ClientChannelLayout) (",
        "ExtAudioFileGetProperty(FileLengthFrames) (",
        "ExtAudioFileSeek (",
        "ExtAudioFileRead (",
    ]
    .iter()
    .any(|prefix| operation.starts_with(prefix))
}

fn dynamic_coefficients(cookie: &[u8]) -> Option<u64> {
    let (config, recording) = parse_recorded(cookie).ok()?;
    if !config.is_complete() {
        return None;
    }
    recording.fields.iter().find_map(|field| {
        let prefix = field
            .name
            .strip_suffix(".hoa.dynamic_selection_config_present")?;
        if !prefix.starts_with("components[") || field.value.as_bool() != Some(true) {
            return None;
        }
        let count = recording
            .derived
            .get(&format!("{prefix}.hoa.coefficient_count"))?
            .as_u64()?;
        (count > 36).then_some(count)
    })
}

fn explain(mut error: Error, cookie: &[u8], qualified: impl FnOnce() -> bool) -> Error {
    if error.os_status != Some(INVALID_DATA) || !decode_operation(&error.operation) {
        return error;
    }
    let Some(count) = dynamic_coefficients(cookie) else {
        return error;
    };
    if qualified() {
        error.message.push_str(&format!(
            "; this configuration uses HOA dynamic selection with {count} internal coefficients, \
             which exceeds the limit of 36 in the verified macOS 27.2 (26B123) AudioCodecs component"
        ));
    }
    error
}

pub(crate) fn explain_decode_error(error: Error, cookie: &[u8]) -> Error {
    explain(error, cookie, qualified_component)
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::Value;

    fn fixtures() -> Vec<Value> {
        let data: Value =
            serde_json::from_str(include_str!("../tests/data/hoa-native-boundary.json")).unwrap();
        data["cases"].as_array().unwrap().clone()
    }

    fn cookie(case: &Value) -> Vec<u8> {
        case["cookie_hex"]
            .as_str()
            .unwrap()
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| u8::from_str_radix(std::str::from_utf8(pair).unwrap(), 16).unwrap())
            .collect()
    }

    fn native_error() -> Error {
        Error::native(
            "AudioConverterSetProperty(DecompressionMagicCookie) (create packet decoder)",
            INVALID_DATA,
        )
    }

    #[test]
    fn explanation_requires_dynamic_selection_above_36_and_preserves_status() {
        for case in fixtures() {
            let original = native_error();
            let expected = case["dynamic_selection"] == true
                && case["internal_coefficients"].as_u64().unwrap() > 36;
            let error = explain(original.clone(), &cookie(&case), || {
                assert!(expected, "identity should only be read for matching input");
                true
            });
            assert_eq!(error.message.contains("limit of 36"), expected);
            let mut value = serde_json::to_value(error).unwrap();
            value["message"] = serde_json::json!(original.message);
            assert_eq!(value, serde_json::to_value(original).unwrap());
        }
    }

    #[test]
    fn unqualified_component_bad_cookie_and_unrelated_errors_remain_original() {
        let raw = cookie(&fixtures()[1]);
        let expected = serde_json::to_value(native_error()).unwrap();
        assert_eq!(
            serde_json::to_value(explain(native_error(), &raw, || false)).unwrap(),
            expected
        );
        for bad in [&[][..], &raw[..raw.len() - 1]] {
            assert_eq!(
                serde_json::to_value(explain(native_error(), bad, || panic!("identity read")))
                    .unwrap(),
                expected
            );
        }
        for original in [
            Error::native("AudioConverterFillComplexBuffer (replay)", INVALID_DATA),
            Error::native(
                "ExtAudioFileSetProperty(ClientDataFormat/encode) (encode)",
                INVALID_DATA,
            ),
            Error::native(
                "AudioConverterSetProperty(DecompressionMagicCookie) (create packet decoder)",
                -50,
            ),
        ] {
            let expected = serde_json::to_value(&original).unwrap();
            assert_eq!(
                serde_json::to_value(explain(original, &raw, || panic!("identity read"))).unwrap(),
                expected
            );
        }
    }

    #[test]
    fn both_replay_policies_and_file_initialization_can_explain_the_same_limit() {
        let raw = cookie(&fixtures()[1]);
        for operation in [
            "AudioConverterSetProperty(DecompressionMagicCookie) (create packet decoder)",
            "AudioConverterSetProperty(DecompressionMagicCookie/replay) (set cookie after processing policy)",
            "ExtAudioFileSetProperty(ClientDataFormat) (prepare reference decode)",
            "ExtAudioFileRead (reference decode)",
        ] {
            let result = explain(Error::native(operation, INVALID_DATA), &raw, || true);
            assert!(result.message.contains("37 internal coefficients"));
        }
    }

    #[test]
    fn runtime_identity_must_match_architecture_and_uuid() {
        assert!(qualified_slice(RUNTIME_UUID, [0x0100_000c, 0x8000_0002]));
        assert!(!qualified_slice([0; 16], [0x0100_000c, 0x8000_0002]));
        assert!(!qualified_slice(RUNTIME_UUID, [0x0100_0007, 3]));
        assert!(!qualified_slice(RUNTIME_UUID, [0x0100_000c, 0x8000_000c]));
    }
}
