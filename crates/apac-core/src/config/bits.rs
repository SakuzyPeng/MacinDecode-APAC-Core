use super::ParseError;

/// MSB-first bounded reader; positions always refer to the original input.
pub struct BitReader<'a> {
    data: &'a [u8],
    position: usize,
    end: usize,
}
impl<'a> BitReader<'a> {
    pub fn new(data: &'a [u8]) -> Self {
        Self {
            data,
            position: 0,
            end: data.len().saturating_mul(8),
        }
    }
    pub(crate) fn data(&self) -> &'a [u8] {
        self.data
    }
    pub fn position(&self) -> usize {
        self.position
    }
    pub fn remaining(&self) -> usize {
        self.end - self.position
    }
    pub fn skip(&mut self, width: usize) -> Result<(), ParseError> {
        if width > self.remaining() {
            return Err(ParseError::new(
                self.position,
                "truncated",
                "skip exceeds input bounds",
            ));
        }
        self.position += width;
        Ok(())
    }
    pub fn read(&mut self, width: usize) -> Result<u64, ParseError> {
        if width > 64 {
            return Err(ParseError::new(
                self.position,
                "invalid-width",
                "bit read exceeds 64 bits",
            ));
        }
        if width > self.remaining() {
            return Err(ParseError::new(
                self.position,
                "truncated",
                format!("need {width} bits, only {} remain", self.remaining()),
            ));
        }
        let mut value = 0u64;
        for _ in 0..width {
            value = (value << 1)
                | u64::from((self.data[self.position / 8] >> (7 - self.position % 8)) & 1);
            self.position += 1;
        }
        Ok(value)
    }
    pub fn set_end(&mut self, end: usize) -> Result<usize, ParseError> {
        if end < self.position || end > self.data.len().saturating_mul(8) {
            return Err(ParseError::new(
                self.position,
                "truncated",
                "substructure exceeds input bounds",
            ));
        }
        let previous = self.end;
        self.end = end;
        Ok(previous)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    #[test]
    fn cross_byte_and_full_width_reads() {
        let mut r = BitReader::new(&[0b10110110, 0b01101001, 0xff]);
        assert_eq!(r.read(3).unwrap(), 5);
        assert_eq!(r.read(9).unwrap(), 0b101100110);
        assert_eq!(r.read(12).unwrap(), 0x9ff);
        let before = r.position();
        assert!(r.read(1).is_err());
        assert_eq!(before, r.position());
        assert!(r.skip(usize::MAX).is_err());
        assert_eq!(before, r.position());
        r.skip(0).unwrap();
        assert_eq!(BitReader::new(&[0xff; 8]).read(64).unwrap(), u64::MAX);
        assert!(BitReader::new(&[0; 16]).read(65).is_err());
    }
}
