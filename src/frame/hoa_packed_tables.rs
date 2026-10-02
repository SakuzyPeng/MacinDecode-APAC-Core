//! Lossless storage decoding. These are table encodings, not APAC payload syntax.
pub(super) const CODEBOOK_ENCODING: &str = "preorder-tree-msb-hex-v1";
pub(super) const MATRIX_ENCODING: &str = "micro21-msb-hex-v1";

struct Bits {
    data: Vec<u8>,
    position: usize,
    end: usize,
}

impl Bits {
    fn new(hex: &str, end: usize) -> Result<Self, &'static str> {
        if end.div_ceil(8).checked_mul(2) != Some(hex.len()) {
            return Err("packed table length differs");
        }
        let digit = |c| match c {
            b'0'..=b'9' => Ok(c - b'0'),
            b'a'..=b'f' => Ok(c - b'a' + 10),
            _ => Err("invalid packed table hex"),
        };
        let data: Vec<u8> = hex
            .as_bytes()
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| Ok((digit(pair[0])? << 4) | digit(pair[1])?))
            .collect::<Result<_, &'static str>>()?;
        let padding = (8 - end % 8) % 8;
        if padding != 0
            && data
                .last()
                .is_some_and(|byte| byte & ((1 << padding) - 1) != 0)
        {
            return Err("nonzero packed table padding");
        }
        Ok(Self {
            data,
            position: 0,
            end,
        })
    }

    fn take(&mut self, width: usize) -> Result<u32, &'static str> {
        if self.end - self.position < width {
            return Err("truncated packed table");
        }
        let mut value = 0;
        for _ in 0..width {
            value = (value << 1)
                | u32::from((self.data[self.position / 8] >> (7 - self.position % 8)) & 1);
            self.position += 1;
        }
        Ok(value)
    }

    fn finish(&self) -> Result<(), &'static str> {
        if self.position != self.end {
            return Err("unused packed table bits");
        }
        Ok(())
    }
}

pub(super) fn codebook(hex: &str, precision: u8) -> Result<Vec<(usize, u32)>, &'static str> {
    if !(6..=9).contains(&precision) {
        return Err("packed codebook precision must be 6..9");
    }
    let count = 1usize << precision;
    let mut bits = Bits::new(hex, count * (usize::from(precision) + 2) - 1)?;
    let mut book = vec![(0, 0); count];
    fn visit(
        bits: &mut Bits,
        book: &mut [(usize, u32)],
        precision: u8,
        code: u32,
        depth: usize,
    ) -> Result<(), &'static str> {
        if bits.take(1)? != 0 {
            if depth == 0 {
                return Err("zero-length Huffman codeword");
            }
            let symbol = bits.take(usize::from(precision))? as usize;
            if book[symbol].0 != 0 {
                return Err("duplicate Huffman symbol");
            }
            book[symbol] = (depth, code);
        } else {
            if depth >= 32 {
                return Err("Huffman depth exceeds 32");
            }
            visit(bits, book, precision, code << 1, depth + 1)?;
            visit(bits, book, precision, (code << 1) | 1, depth + 1)?;
        }
        Ok(())
    }
    visit(&mut bits, &mut book, precision, 0, 0)?;
    bits.finish()?;
    if book.iter().any(|word| word.0 == 0) {
        return Err("missing Huffman symbol");
    }
    Ok(book)
}

pub(super) fn matrix(hex: &str, count: usize) -> Result<Vec<u32>, &'static str> {
    let mut bits = Bits::new(hex, count.checked_mul(21).ok_or("matrix size overflow")?)?;
    let mut words = Vec::with_capacity(count);
    for _ in 0..count {
        let packed = bits.take(21)?;
        // Recreate the original rounding from six decimal places to Float32.
        // Apply the sign to the bits so that negative zero survives as well.
        let magnitude = f64::from(packed & 0xfffff) / 1_000_000.;
        words.push((magnitude as f32).to_bits() | ((packed >> 20) << 31));
    }
    bits.finish()?;
    Ok(words)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn matrix_words_preserve_sign_bits_and_reject_wrong_sizes_and_padding() {
        // +0 and -0, each encoded as a sign bit followed by 20 magnitude bits.
        assert_eq!(matrix("000004000000", 2).unwrap(), [0, 0x8000_0000]);
        assert!(matrix("000004000001", 2).is_err());
        assert!(matrix("0000040000", 2).is_err());
        assert!(matrix("00000400000000", 2).is_err());
        assert!(matrix("gggggg", 1).is_err());
    }

    #[test]
    fn corrupted_tree_depth_length_and_empty_codewords_are_rejected() {
        assert_eq!(
            codebook(&"00".repeat(64), 6).unwrap_err(),
            "Huffman depth exceeds 32"
        );
        let mut empty_word = "80".to_owned();
        empty_word.push_str(&"00".repeat(63));
        assert_eq!(
            codebook(&empty_word, 6).unwrap_err(),
            "zero-length Huffman codeword"
        );
        assert!(codebook(&"00".repeat(63), 6).is_err());
        assert!(codebook(&"00".repeat(65), 6).is_err());
        assert!(codebook(&"ff".repeat(64), 6).is_err());
    }
}
