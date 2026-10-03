//! The `alloc` items that the standard prelude would otherwise provide.
#[allow(unused_imports)]
pub(crate) use alloc::{
    borrow::{Cow, ToOwned},
    boxed::Box,
    collections::BTreeMap,
    format,
    string::{String, ToString},
    sync::Arc,
    vec,
    vec::Vec,
};
