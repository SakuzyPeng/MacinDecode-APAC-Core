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
}
