//! Items the research crate needs from core internals during the restructuring.
//! Each module mirrors its core counterpart; crate-private items the report
//! drivers still read directly are exposed here and nowhere else in research.

pub mod frame {
    pub use crate::frame::*;
}

pub mod synthesis {
    pub use crate::synthesis::*;
}

pub mod bwe2_math {
    pub use crate::bwe2_math::{format_sha256, math_sha256};
}

pub mod channel_layout {
    pub use crate::channel_layout::layout;
}

pub mod numeric {
    pub use crate::numeric::tables;
}
