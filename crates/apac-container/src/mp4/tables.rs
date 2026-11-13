//! Sequential run/table cursors, independent of packet count in memory usage.
use super::boxes::{Atom, MediaCursor, Structure, invalid, u32be, u64be};
use crate::{OpenMode, Result, Source};
use apac_core::MAX_PACKET_BUFFER;

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Table {
    atom: Atom,
    count: u32,
    width: u64,
}
impl Table {
    fn open(file: &mut impl Source, atom: Atom, width: u64, versions: &[u8]) -> Result<Self> {
        atom.full(file, versions)?;
        let count = u32be(&atom.take::<4>(file, 4)?);
        atom.exact(8 + u64::from(count) * width)?;
        Ok(Self { atom, count, width })
    }
    fn row<const N: usize>(self, file: &mut impl Source, index: u32) -> Result<[u8; N]> {
        if index >= self.count || N as u64 != self.width {
            return Err(self.atom.error("table exhausted or entry width mismatch"));
        }
        self.atom.take(file, 8 + u64::from(index) * self.width)
    }
}

/// The optional composition table must describe exactly one zero offset
/// for each sample. Playback checks runs as samples are reached.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct Composition {
    table: Table,
    run: u32,
    total: u64,
}
impl Composition {
    fn read_run(&mut self, file: &mut impl Source) -> Result<()> {
        let raw = self.table.row::<8>(file, self.run)?;
        let n = u32be(&raw);
        if n == 0 || u32be(&raw[4..]) != 0 {
            return Err(self
                .table
                .atom
                .error("zero run or unsupported nonzero composition offset"));
        }
        self.total += u64::from(n);
        self.run += 1;
        Ok(())
    }
    fn check(&mut self, file: &mut impl Source, sample: u64, count: u32) -> Result<()> {
        let eof = sample == u64::from(count);
        while self.run < self.table.count && (eof || self.total <= sample) {
            self.read_run(file)?;
        }
        if (eof && self.total != u64::from(count)) || (!eof && self.total <= sample) {
            return Err(self
                .table
                .atom
                .error("composition count differs from sample count"));
        }
        Ok(())
    }
}

/// A single lookahead entry suffices to validate the ordered sync samples.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct SyncSamples {
    table: Table,
    row: u32,
    previous: u32,
}
impl SyncSamples {
    fn check(&mut self, file: &mut impl Source, sample: u64, count: u32) -> Result<()> {
        while self.row < self.table.count && u64::from(self.previous) <= sample {
            let value = u32be(&self.table.row::<4>(file, self.row)?);
            if value <= self.previous || value > count {
                return Err(self
                    .table
                    .atom
                    .error("sync sample index out of range or not increasing"));
            }
            self.previous = value;
            self.row += 1;
        }
        Ok(())
    }
}

/// A sample cursor: where the next sample's size, chunk and duration are
/// read. It is small and `Copy`, so a position can be saved and restored.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) struct Index {
    sizes: Atom,
    fixed_size: u32,
    pub count: u32,
    offsets: Table,
    chunks: Table,
    times: Table,
    pub next: u64,
    chunk_index: u32,
    chunk_left: u32,
    chunk_offset: u64,
    previous_end: u64,
    chunk_run: u32,
    current_run: Option<(u32, u32)>,
    next_run: Option<(u32, u32)>,
    time_run: u32,
    time_left: u32,
    composition: Option<Composition>,
    sync: Option<SyncSamples>,
    media: MediaCursor,
}
impl Index {
    pub(super) fn open(file: &mut impl Source, s: &Structure, mode: OpenMode) -> Result<Self> {
        let sizes = s.get(b"stsz")?;
        sizes.full(file, &[0])?;
        let raw = sizes.take::<8>(file, 4)?;
        let fixed_size = u32be(&raw);
        let count = u32be(&raw[4..]);
        sizes.exact(
            12 + if fixed_size == 0 {
                u64::from(count) * 4
            } else {
                0
            },
        )?;
        if fixed_size as u64 > MAX_PACKET_BUFFER as u64 {
            return Err(sizes.error("sample size exceeds 16 MiB"));
        }
        let wide = s.boxes.contains_key(b"co64");
        let offsets = Table::open(
            file,
            s.get(if wide { b"co64" } else { b"stco" })?,
            if wide { 8 } else { 4 },
            &[0],
        )?;
        let chunks = Table::open(file, s.get(b"stsc")?, 12, &[0])?;
        let times = Table::open(file, s.get(b"stts")?, 8, &[0])?;
        if (count == 0) != (offsets.count == 0)
            || (count == 0) != (chunks.count == 0)
            || (count == 0) != (times.count == 0)
        {
            return Err(sizes.error("empty sample/chunk/time tables disagree"));
        }
        let composition = if let Some(&a) = s.boxes.get(b"ctts") {
            let mut cursor = Composition {
                table: Table::open(file, a, 8, &[0, 1])?,
                run: 0,
                total: 0,
            };
            if mode == OpenMode::Verified {
                cursor.check(file, u64::from(count), count)?;
                None
            } else {
                Some(cursor)
            }
        } else {
            None
        };
        let sync = if let Some(&a) = s.boxes.get(b"stss") {
            let mut cursor = SyncSamples {
                table: Table::open(file, a, 4, &[0])?,
                row: 0,
                previous: 0,
            };
            if mode == OpenMode::Verified {
                cursor.check(file, u64::from(count), count)?;
                None
            } else {
                Some(cursor)
            }
        } else {
            None
        };
        let mut out = Self {
            sizes,
            fixed_size,
            count,
            offsets,
            chunks,
            times,
            next: 0,
            chunk_index: 0,
            chunk_left: 0,
            chunk_offset: 0,
            previous_end: 0,
            chunk_run: 0,
            current_run: None,
            next_run: None,
            time_run: 0,
            time_left: 0,
            composition,
            sync,
            media: MediaCursor::default(),
        };
        if count != 0 && mode == OpenMode::Verified {
            out.start_chunks(file)?;
        }
        Ok(out)
    }
    fn start_chunks(&mut self, file: &mut impl Source) -> Result<()> {
        let first = self.run(file, 0)?;
        if first.0 != 1 {
            return Err(self.chunks.atom.error("first chunk must be one"));
        }
        self.current_run = Some(first);
        self.chunk_run = 1;
        self.load_next_run(file)
    }
    fn run(&self, file: &mut impl Source, index: u32) -> Result<(u32, u32)> {
        let raw = self.chunks.row::<12>(file, index)?;
        let first = u32be(&raw);
        let samples = u32be(&raw[4..]);
        let description = u32be(&raw[8..]);
        if first == 0 || first > self.offsets.count || samples == 0 || description != 1 {
            return Err(self
                .chunks
                .atom
                .error("invalid first chunk, samples per chunk or sample description index"));
        }
        Ok((first, samples))
    }
    fn load_next_run(&mut self, file: &mut impl Source) -> Result<()> {
        self.next_run = if self.chunk_run < self.chunks.count {
            let next = self.run(file, self.chunk_run)?;
            if next.0 <= self.current_run.unwrap().0 {
                return Err(self.chunks.atom.error("chunk runs must strictly increase"));
            }
            Some(next)
        } else {
            None
        };
        Ok(())
    }
    pub fn next(&mut self, file: &mut impl Source, file_bytes: u64) -> Result<Option<(u64, u32)>> {
        if let Some(composition) = &mut self.composition {
            composition.check(file, self.next, self.count)?;
        }
        if let Some(sync) = &mut self.sync {
            // stss numbers are one-based; at EOF all its entries are checked.
            sync.check(file, (self.next + 1).min(u64::from(self.count)), self.count)?;
        }
        if self.next == u64::from(self.count) {
            if self.chunk_left != 0
                || self.chunk_index != self.offsets.count
                || self.next_run.is_some()
                || self.time_left != 0
                || self.time_run != self.times.count
            {
                return Err(self
                    .sizes
                    .error("sample, chunk and duration counts disagree"));
            }
            return Ok(None);
        }
        if self.current_run.is_none() {
            self.start_chunks(file)?;
        }
        if self.chunk_left == 0 {
            if self.next_run.is_some_and(|r| r.0 == self.chunk_index + 1) {
                self.current_run = self.next_run;
                self.chunk_run += 1;
                self.load_next_run(file)?;
            }
            let offset = if self.offsets.width == 8 {
                u64be(&self.offsets.row::<8>(file, self.chunk_index)?)
            } else {
                u64::from(u32be(&self.offsets.row::<4>(file, self.chunk_index)?))
            };
            if offset < self.previous_end {
                return Err(invalid(
                    &self.offsets.atom.tag,
                    self.offsets.atom.data + 8 + u64::from(self.chunk_index) * self.offsets.width,
                    "chunks overlap or are not in sample order",
                ));
            }
            self.chunk_offset = offset;
            self.chunk_index += 1;
            self.chunk_left = self.current_run.unwrap().1;
        }
        if self.time_left == 0 {
            let raw = self.times.row::<8>(file, self.time_run)?;
            self.time_run += 1;
            self.time_left = u32be(&raw);
            if self.time_left == 0 || u32be(&raw[4..]) != 1024 {
                return Err(self
                    .times
                    .atom
                    .error("requires positive time runs and 1024 frames per sample"));
            }
        }
        let size = if self.fixed_size != 0 {
            self.fixed_size
        } else {
            u32be(&self.sizes.take::<4>(file, 12 + self.next * 4)?)
        };
        if size == 0 || size as u64 > MAX_PACKET_BUFFER as u64 {
            return Err(invalid(
                b"stsz",
                self.sizes.data
                    + if self.fixed_size == 0 {
                        12 + self.next * 4
                    } else {
                        4
                    },
                "sample size outside 1..16 MiB",
            ));
        }
        let offset = self.chunk_offset;
        let end = offset
            .checked_add(u64::from(size))
            .filter(|&v| v <= file_bytes)
            .ok_or_else(|| {
                invalid(
                    b"mdat",
                    offset,
                    "sample offset/size overflows or exceeds file",
                )
            })?;
        self.media.check(file, file_bytes, offset, end)?;
        self.chunk_offset = end;
        self.previous_end = end;
        self.chunk_left -= 1;
        self.time_left -= 1;
        self.next += 1;
        Ok(Some((offset, size)))
    }
}
