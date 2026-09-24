fn main() {
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
    println!("cargo:rerun-if-changed=native/audio_toolbox.c");
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        cc::Build::new()
            .file("native/audio_toolbox.c")
            .flag("-std=c11")
            .warnings(true)
            .compile("apac_audio_toolbox");
        println!("cargo:rustc-link-lib=framework=AudioToolbox");
        println!("cargo:rustc-link-lib=framework=CoreFoundation");
    }
}
