//! Recorded syntax: field events and derived values for research reports.
//!
//! Values render exactly as the former `serde_json::Value` did. Structured
//! values go through `serde_json::to_value`, whose maps sort their keys.
use crate::config::passive::PositionSyntax;
use crate::frame::{Bwe2Data, ElementBwe2Data, TnsChannel};
use crate::prelude::*;
use core::fmt::{self, Write};
use serde::{Serialize, Serializer, ser::SerializeMap};

/// One recorded syntax element. Coordinates are bits from the start of the
/// cookie or packet that was parsed.
#[derive(Debug, Clone, PartialEq, Serialize)]
pub struct ConfigField {
    pub name: String,
    pub bit_offset: usize,
    pub bit_length: usize,
    pub value: FieldValue,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum DigestUnit {
    Bits,
    Bytes,
}

#[derive(Debug, Clone, PartialEq)]
pub enum FieldValue {
    Bool(bool),
    U64(u64),
    I64(i64),
    Str(Cow<'static, str>),
    U64s(Vec<u64>),
    I64s(Vec<i64>),
    /// `{"bits"|"bytes": count, "sha256": digest}` for opaque payloads.
    Digest {
        unit: DigestUnit,
        count: usize,
        sha256: String,
    },
    /// `{"ordering": …}`.
    Ordering(&'static str),
    Positions(Vec<PositionSyntax>),
    Tns(Box<TnsChannel>),
    Bwe2(Box<Bwe2Data>),
    ElementBwe2(Box<ElementBwe2Data>),
}

impl FieldValue {
    pub fn digest(unit: DigestUnit, count: usize, data: &[u8]) -> Self {
        Self::Digest {
            unit,
            count,
            sha256: crate::model::sha256(data),
        }
    }
    pub fn as_u64(&self) -> Option<u64> {
        match *self {
            Self::U64(v) => Some(v),
            Self::I64(v) => u64::try_from(v).ok(),
            _ => None,
        }
    }
    pub fn as_bool(&self) -> Option<bool> {
        match *self {
            Self::Bool(v) => Some(v),
            _ => None,
        }
    }
    pub fn as_str(&self) -> Option<&str> {
        match self {
            Self::Str(v) => Some(v),
            _ => None,
        }
    }
    pub fn as_u64s(&self) -> Option<&[u64]> {
        match self {
            Self::U64s(v) => Some(v),
            _ => None,
        }
    }
    /// Compact JSON identical to serde_json's rendering, for the scalar and
    /// digest values that cookie syntax records. Structured values are `None`.
    pub fn write_json(&self, out: &mut impl Write) -> Option<fmt::Result> {
        Some(match self {
            Self::Bool(v) => write!(out, "{v}"),
            Self::U64(v) => write!(out, "{v}"),
            Self::I64(v) => write!(out, "{v}"),
            Self::Str(v) => write_json_str(out, v),
            Self::U64s(values) => write_json_list(out, values),
            Self::I64s(values) => write_json_list(out, values),
            Self::Digest {
                unit,
                count,
                sha256,
            } => (|| {
                write!(out, "{{\"{}\":{count},\"sha256\":", unit.key())?;
                write_json_str(out, sha256)?;
                out.write_char('}')
            })(),
            Self::Ordering(v) => (|| {
                out.write_str("{\"ordering\":")?;
                write_json_str(out, v)?;
                out.write_char('}')
            })(),
            Self::Positions(_) | Self::Tns(_) | Self::Bwe2(_) | Self::ElementBwe2(_) => {
                return None;
            }
        })
    }
}

/// The compact JSON of a scalar or digest value; structured values do not
/// implement it and fail to format.
impl fmt::Display for FieldValue {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        self.write_json(f).unwrap_or(Err(fmt::Error))
    }
}

impl DigestUnit {
    fn key(self) -> &'static str {
        match self {
            Self::Bits => "bits",
            Self::Bytes => "bytes",
        }
    }
}

impl ConfigField {
    /// `{"name":…,"bit_offset":…,"bit_length":…,"value":…}` as serde_json
    /// writes it; `None` for structured values.
    pub fn write_json(&self, out: &mut impl Write) -> Option<fmt::Result> {
        let head = (|| {
            out.write_str("{\"name\":")?;
            write_json_str(out, &self.name)?;
            write!(
                out,
                ",\"bit_offset\":{},\"bit_length\":{},\"value\":",
                self.bit_offset, self.bit_length
            )
        })();
        if let Err(error) = head {
            return Some(Err(error));
        }
        Some(
            self.value
                .write_json(out)?
                .and_then(|()| out.write_char('}')),
        )
    }
}

fn write_json_list(out: &mut impl Write, values: &[impl fmt::Display]) -> fmt::Result {
    out.write_char('[')?;
    for (i, v) in values.iter().enumerate() {
        if i > 0 {
            out.write_char(',')?;
        }
        write!(out, "{v}")?;
    }
    out.write_char(']')
}

/// serde_json's string escaping: quote, backslash and C0 controls only.
fn write_json_str(out: &mut impl Write, text: &str) -> fmt::Result {
    out.write_char('"')?;
    for c in text.chars() {
        match c {
            '"' => out.write_str("\\\"")?,
            '\\' => out.write_str("\\\\")?,
            '\u{8}' => out.write_str("\\b")?,
            '\u{c}' => out.write_str("\\f")?,
            '\n' => out.write_str("\\n")?,
            '\r' => out.write_str("\\r")?,
            '\t' => out.write_str("\\t")?,
            c if u32::from(c) < 0x20 => write!(out, "\\u{:04x}", u32::from(c))?,
            c => out.write_char(c)?,
        }
    }
    out.write_char('"')
}

impl Serialize for FieldValue {
    fn serialize<S: Serializer>(&self, serializer: S) -> Result<S::Ok, S::Error> {
        fn sorted<T: Serialize, S: Serializer>(
            value: &T,
            serializer: S,
        ) -> Result<S::Ok, S::Error> {
            serde_json::to_value(value)
                .map_err(serde::ser::Error::custom)?
                .serialize(serializer)
        }
        match self {
            Self::Bool(v) => serializer.serialize_bool(*v),
            Self::U64(v) => serializer.serialize_u64(*v),
            Self::I64(v) => serializer.serialize_i64(*v),
            Self::Str(v) => serializer.serialize_str(v),
            Self::U64s(v) => v.serialize(serializer),
            Self::I64s(v) => v.serialize(serializer),
            Self::Digest {
                unit,
                count,
                sha256,
            } => {
                let mut map = serializer.serialize_map(Some(2))?;
                map.serialize_entry(unit.key(), count)?;
                map.serialize_entry("sha256", sha256)?;
                map.end()
            }
            Self::Ordering(v) => {
                let mut map = serializer.serialize_map(Some(1))?;
                map.serialize_entry("ordering", v)?;
                map.end()
            }
            Self::Positions(v) => sorted(v, serializer),
            Self::Tns(v) => sorted(v, serializer),
            Self::Bwe2(v) => sorted(v, serializer),
            Self::ElementBwe2(v) => sorted(v, serializer),
        }
    }
}

/// Compare with plain integers and booleans as `serde_json::Value` did.
macro_rules! eq_integer {
    ($($t:ty),*) => {$(
        impl PartialEq<$t> for FieldValue {
            fn eq(&self, other: &$t) -> bool {
                match *self {
                    Self::U64(v) => i128::from(v) == *other as i128,
                    Self::I64(v) => i128::from(v) == *other as i128,
                    _ => false,
                }
            }
        }
    )*};
}
eq_integer!(u8, u16, u32, u64, usize, i32, i64);
/// Compare by rendering, as the recorded value would print.
impl PartialEq<serde_json::Value> for FieldValue {
    fn eq(&self, other: &serde_json::Value) -> bool {
        serde_json::to_value(self).is_ok_and(|value| value == *other)
    }
}
impl PartialEq<bool> for FieldValue {
    fn eq(&self, other: &bool) -> bool {
        self.as_bool() == Some(*other)
    }
}
impl PartialEq<&str> for FieldValue {
    fn eq(&self, other: &&str) -> bool {
        self.as_str() == Some(*other)
    }
}

macro_rules! from_unsigned {
    ($($t:ty),*) => {$(
        impl From<$t> for FieldValue {
            fn from(value: $t) -> Self {
                Self::U64(value as u64)
            }
        }
    )*};
}
from_unsigned!(u8, u16, u32, u64, usize);
macro_rules! from_signed {
    ($($t:ty),*) => {$(
        impl From<$t> for FieldValue {
            fn from(value: $t) -> Self {
                Self::I64(value as i64)
            }
        }
    )*};
}
from_signed!(i8, i16, i32, i64);
impl From<Vec<i64>> for FieldValue {
    fn from(values: Vec<i64>) -> Self {
        Self::I64s(values)
    }
}
impl From<bool> for FieldValue {
    fn from(value: bool) -> Self {
        Self::Bool(value)
    }
}
impl From<&'static str> for FieldValue {
    fn from(value: &'static str) -> Self {
        Self::Str(Cow::Borrowed(value))
    }
}
impl From<String> for FieldValue {
    fn from(value: String) -> Self {
        Self::Str(Cow::Owned(value))
    }
}
macro_rules! from_unsigned_list {
    ($($t:ty),*) => {$(
        impl From<Vec<$t>> for FieldValue {
            fn from(values: Vec<$t>) -> Self {
                Self::U64s(values.into_iter().map(|v| v as u64).collect())
            }
        }
        impl From<&[$t]> for FieldValue {
            fn from(values: &[$t]) -> Self {
                Self::U64s(values.iter().map(|&v| v as u64).collect())
            }
        }
    )*};
}
from_unsigned_list!(u8, u16, u32, u64, usize);

#[cfg(test)]
mod tests {
    use super::*;

    fn json(value: &FieldValue) -> String {
        let mut out = String::new();
        value.write_json(&mut out).unwrap().unwrap();
        assert_eq!(out, serde_json::to_string(value).unwrap());
        out
    }

    #[test]
    fn scalar_values_render_as_serde_json_values() {
        let cases = [
            (FieldValue::from(true), serde_json::json!(true)),
            (FieldValue::from(u64::MAX), serde_json::json!(u64::MAX)),
            (FieldValue::from(-5i64), serde_json::json!(-5)),
            (FieldValue::from(7i64), serde_json::json!(7)),
            (
                FieldValue::from(String::from("a\"\\\u{1}\n\té/")),
                serde_json::json!("a\"\\\u{1}\n\té/"),
            ),
            (
                FieldValue::from(vec![1u32, 2, 3]),
                serde_json::json!([1, 2, 3]),
            ),
            (FieldValue::from(Vec::<usize>::new()), serde_json::json!([])),
            (FieldValue::from(vec![-1i64, 3]), serde_json::json!([-1, 3])),
            (
                FieldValue::digest(DigestUnit::Bits, 9, b"ab"),
                serde_json::json!({"bits": 9, "sha256": crate::model::sha256(b"ab")}),
            ),
            (
                FieldValue::digest(DigestUnit::Bytes, 2, b"ab"),
                serde_json::json!({"bytes": 2, "sha256": crate::model::sha256(b"ab")}),
            ),
            (
                FieldValue::Ordering("group_band_window_line"),
                serde_json::json!({"ordering": "group_band_window_line"}),
            ),
        ];
        for (value, expected) in cases {
            assert_eq!(json(&value), serde_json::to_string(&expected).unwrap());
        }
    }

    #[test]
    fn position_syntax_renders_with_sorted_keys() {
        let positions = vec![PositionSyntax {
            parent_dynamic: true,
            position_precision: 4,
            ..Default::default()
        }];
        let value = FieldValue::Positions(positions.clone());
        assert!(value.write_json(&mut String::new()).is_none());
        assert_eq!(
            serde_json::to_string(&value).unwrap(),
            serde_json::to_value(&positions).unwrap().to_string()
        );
    }

    /// Every field the cookie corpus records renders identically through the
    /// core writer and serde_json.
    #[test]
    fn corpus_fields_render_as_serde_json() {
        let mut fields = 0usize;
        for cookie in crate::config::model_tests::corpus() {
            let Ok((_, report)) = crate::config::parse_recorded(&cookie) else {
                continue;
            };
            for field in &report.fields {
                let mut out = String::new();
                field.write_json(&mut out).unwrap().unwrap();
                assert_eq!(out, serde_json::to_string(field).unwrap());
                fields += 1;
            }
        }
        assert!(fields > 10_000, "{fields} fields");
    }
}
