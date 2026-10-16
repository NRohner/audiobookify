fn main() {
    // screencapturekit links the Swift runtime, but a dependency's rpath doesn't reach the
    // binaries built here (tests, examples); the OS copy lives in /usr/lib/swift.
    if std::env::var("CARGO_CFG_TARGET_OS").as_deref() == Ok("macos") {
        println!("cargo:rustc-link-arg=-Wl,-rpath,/usr/lib/swift");
    }
}
