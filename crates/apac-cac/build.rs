//! Emits the CAC rotation table from `data/cac-math-v1.json` into `$OUT_DIR/rotations.rs`.
use serde::Deserialize;
use std::fmt::Write;
use std::path::PathBuf;

#[derive(Deserialize)]
struct Rotation {
    a_f64: u64,
    b_f64: u64,
    swap: bool,
}
#[derive(Deserialize)]
struct Math {
    numeric_profile: String,
    tables_sha256: String,
    rotations: Vec<Rotation>,
}

fn main() {
    let manifest = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"));
    let path = manifest.join("../../data/cac-math-v1.json");
    println!("cargo:rerun-if-changed={}", path.display());
    let bytes = std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()));
    let math: Math = serde_json::from_slice(&bytes).expect("parse cac-math-v1.json");
    assert_eq!(math.numeric_profile, "apac-cac-math-v1");
    assert_eq!(math.rotations.len(), 35);
    let mut out = String::new();
    writeln!(
        out,
        "/// SHA-256 of the rotation table, as recorded in `data/cac-math-v1.json`."
    )
    .unwrap();
    writeln!(
        out,
        "pub const MATH_SHA256: &str = {:?};",
        math.tables_sha256
    )
    .unwrap();
    writeln!(
        out,
        "static ROTATIONS: [Rotation; {}] = [",
        math.rotations.len()
    )
    .unwrap();
    for r in &math.rotations {
        writeln!(
            out,
            "    Rotation {{ a_f64: 0x{:016x}, b_f64: 0x{:016x}, swap: {} }},",
            r.a_f64, r.b_f64, r.swap
        )
        .unwrap();
    }
    writeln!(out, "];").unwrap();
    let target = PathBuf::from(std::env::var_os("OUT_DIR").expect("out dir")).join("rotations.rs");
    std::fs::write(&target, out).unwrap_or_else(|e| panic!("write {}: {e}", target.display()));
}
