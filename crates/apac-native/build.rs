fn main() {
    // The C bridge stays at the repository root: native validators hash it there.
    println!("cargo:rerun-if-changed=../../native/audio_toolbox.c");
    // APAC_NATIVE_RUST_CHECK=1 type-checks the Rust side from a non-macOS host
    // (`cargo check --target aarch64-apple-darwin`); it never links a binary.
    println!("cargo:rerun-if-env-changed=APAC_NATIVE_RUST_CHECK");
    let check_only = std::env::var_os("APAC_NATIVE_RUST_CHECK").is_some();
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") && !check_only {
        cc::Build::new()
            .file("../../native/audio_toolbox.c")
            .flag("-std=c11")
            .warnings(true)
            .compile("apac_audio_toolbox");
        println!("cargo:rustc-link-lib=framework=AudioToolbox");
        println!("cargo:rustc-link-lib=framework=CoreFoundation");
    }
}
