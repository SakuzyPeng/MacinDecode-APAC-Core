//! An in-memory source the tests can change between reads, like a file
//! edited while it is open; every change advances its revision.
use crate::Source;
use std::{
    cell::RefCell,
    io::{self, Read, Seek, SeekFrom},
    rc::Rc,
    time::{Duration, SystemTime},
};

#[derive(Clone)]
pub(crate) struct Shared {
    state: Rc<RefCell<(Vec<u8>, u64)>>,
    position: u64,
}
impl Shared {
    pub(crate) fn new(bytes: &[u8]) -> Self {
        Self {
            state: Rc::new(RefCell::new((bytes.to_vec(), 0))),
            position: 0,
        }
    }
    /// Overwrite (or extend) the bytes at `offset`.
    pub(crate) fn change(&self, offset: u64, bytes: &[u8]) {
        let mut state = self.state.borrow_mut();
        let end = offset as usize + bytes.len();
        if state.0.len() < end {
            state.0.resize(end, 0);
        }
        state.0[offset as usize..end].copy_from_slice(bytes);
        state.1 += 1;
    }
    pub(crate) fn truncate(&self, length: u64) {
        let mut state = self.state.borrow_mut();
        state.0.truncate(length as usize);
        state.1 += 1;
    }
}
impl Read for Shared {
    fn read(&mut self, out: &mut [u8]) -> io::Result<usize> {
        let state = self.state.borrow();
        let start = (self.position as usize).min(state.0.len());
        let n = out.len().min(state.0.len() - start);
        out[..n].copy_from_slice(&state.0[start..start + n]);
        self.position += n as u64;
        Ok(n)
    }
}
impl Seek for Shared {
    fn seek(&mut self, to: SeekFrom) -> io::Result<u64> {
        let length = self.state.borrow().0.len() as i64;
        let target = match to {
            SeekFrom::Start(n) => n as i64,
            SeekFrom::End(n) => length + n,
            SeekFrom::Current(n) => self.position as i64 + n,
        };
        self.position = u64::try_from(target)
            .map_err(|_| io::Error::new(io::ErrorKind::InvalidInput, "negative seek"))?;
        Ok(self.position)
    }
}
impl Source for Shared {
    fn length(&mut self) -> io::Result<u64> {
        Ok(self.state.borrow().0.len() as u64)
    }
    fn revision(&mut self) -> io::Result<Option<SystemTime>> {
        Ok(Some(
            SystemTime::UNIX_EPOCH + Duration::from_secs(self.state.borrow().1),
        ))
    }
}
