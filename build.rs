fn main() {
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
