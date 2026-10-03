//! Compression configuration boundaries only. Renderer payloads are not decoded.
use super::parser::{PResult, Parser};
use crate::prelude::*;
impl Parser<'_> {
    pub(super) fn metadata_compression(&mut self, p: &str, kind: u64, bytes: usize) -> PResult<()> {
        let start = self.pos();
        let bits = bytes * 8;
        if bits > self.bits.remaining() {
            return self.invalid(
                "metadata-compression-size",
                "compression configuration exceeds its parent input",
            );
        }
        let previous = self.bits.set_end(start + bits)?;
        match kind {
            0 => {
                let initial =
                    self.esc(format_args!("{p}.initial_width_minus_one"), [5, 8, 16])? + 1;
                let maximum =
                    initial + self.esc(format_args!("{p}.maximum_width_delta"), [3, 8, 16])?;
                if initial > 15 || maximum > 16 {
                    return self.stop("bound LZW metadata reader supports initial widths through 15 and maximum widths through 16");
                }
            }
            1..=3 => {
                let count = self.esc(format_args!("{p}.table_count_minus_one"), [3, 8, 16])? + 1;
                let count = self.count(count, 5)?;
                for i in 0..count {
                    let q = format!("{p}.tables[{i}]");
                    let width =
                        self.esc(format_args!("{q}.symbol_width_minus_one"), [5, 8, 16])? + 1;
                    if width > 12 {
                        return self.invalid(
                            "metadata-table-resource-limit",
                            "metadata alphabet exceeds the 4096-symbol resource limit",
                        );
                    }
                    let entries = 1usize << width;
                    if kind == 3 {
                        for j in 0..entries {
                            let r = format!("{q}.codes[{j}]");
                            let bits =
                                self.esc(format_args!("{r}.width_minus_one"), [5, 8, 16])? + 1;
                            if bits > 64 {
                                return self.invalid(
                                    "metadata-codeword-width",
                                    "metadata Huffman codeword exceeds 64 bits",
                                );
                            }
                            self.take(format_args!("{r}.code"), bits as usize)?;
                        }
                    } else {
                        let precision =
                            self.take(format_args!("{q}.value_width_minus_one"), 5)? as usize + 1;
                        let mut float_sum = 0f32;
                        let mut integer_sum = 0u64;
                        for j in 0..entries {
                            let raw =
                                self.take(format_args!("{q}.values_encoded[{j}]"), precision)?;
                            if precision == 32 {
                                let value = f32::from_bits(raw as u32);
                                if !value.is_finite() || value < 0. {
                                    return self.invalid(
                                        "metadata-probability",
                                        "nonfinite or negative metadata probability",
                                    );
                                }
                                float_sum += value;
                            } else {
                                integer_sum += raw;
                            }
                        }
                        if (precision == 32 && float_sum != 1.)
                            || (precision != 32 && integer_sum == 0)
                        {
                            return self.invalid(
                                "metadata-probability",
                                "invalid metadata probability normalization",
                            );
                        }
                    }
                }
                if kind != 3 && self.flag(format_args!("{p}.parameter_present"))? {
                    let value = self.take(format_args!("{p}.parameter_bits"), 32)? as u32;
                    if !f32::from_bits(value).is_finite() {
                        return self.invalid(
                            "metadata-probability",
                            "nonfinite metadata probability parameter",
                        );
                    }
                }
                let padding = (8 - (self.pos() - start) % 8) % 8;
                self.passive_bits(format_args!("{p}.padding"), padding)?;
            }
            _ => self.passive_bits(format_args!("{p}.payload"), bits)?,
        }
        if self.pos() != start + bits {
            return self.invalid(
                "metadata-compression-size",
                "compression configuration does not consume its declared bytes",
            );
        }
        self.bits.set_end(previous)?;
        Ok(())
    }
}
