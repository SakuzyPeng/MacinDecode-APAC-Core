//! Build script: compiler identity for reports, plus every runtime table.
//!
//! Each generator reads its `data/*.json` sources, performs the validation the
//! former runtime loader performed and emits `static` items into
//! `$OUT_DIR/tables.rs`, which `src/tables/mod.rs` includes.
extern crate alloc;

mod emit;
mod formats;
mod sq;
#[path = "../src/tables/trie_build.rs"]
mod trie_build;

use serde::de::DeserializeOwned;
use std::path::PathBuf;

/// Reads a data file and asks Cargo to rerun when it changes.
fn data_bytes(name: &str) -> Vec<u8> {
    let path = PathBuf::from(std::env::var_os("CARGO_MANIFEST_DIR").expect("manifest dir"))
        .join("../../data")
        .join(name);
    println!("cargo:rerun-if-changed={}", path.display());
    std::fs::read(&path).unwrap_or_else(|e| panic!("read {}: {e}", path.display()))
}

fn data_json<T: DeserializeOwned>(name: &str) -> T {
    serde_json::from_slice(&data_bytes(name)).unwrap_or_else(|e| panic!("parse {name}: {e}"))
}

fn compiler_identity() {
    let compiler = std::process::Command::new(std::env::var_os("RUSTC").expect("Cargo compiler"))
        .args(["--version", "--verbose"])
        .output()
        .expect("compiler identity");
    assert!(compiler.status.success());
    let identity = String::from_utf8(compiler.stdout).expect("compiler version UTF-8");
    println!(
        "cargo:rustc-env=APAC_BUILD_RUSTC={}",
        identity.trim().replace('\n', " | ")
    );
}

fn main() {
    println!("cargo:rerun-if-changed=build");
    println!("cargo:rerun-if-changed=src/tables/trie_build.rs");
    compiler_identity();
    let mut out = emit::Output::new();
    sq::numeric(&mut out);
    sq::codebooks(&mut out);
    sq::cac(&mut out);
    sq::tns(&mut out);
    sq::bwe2(&mut out);
    formats::shared_config(&mut out);
    formats::shared_drc(&mut out);
    formats::hoa_profiles(&mut out);
    formats::hoa_ambient(&mut out);
    formats::hoa_controls(&mut out);
    let dynamic = formats::hoa_dynamic(&mut out);
    formats::hoa_salient_subbands(&mut out, &dynamic);
    formats::hoa_source(&mut out);
    formats::file_identities(&mut out);
    let path = PathBuf::from(std::env::var_os("OUT_DIR").expect("out dir")).join("tables.rs");
    out.finish(&path);
}
