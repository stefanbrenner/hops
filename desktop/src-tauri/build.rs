fn main() {
    // the repository root (desktop/src-tauri/../..), used to find the Python backend at runtime
    let root = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    let root = root.canonicalize().unwrap_or(root);
    println!("cargo:rustc-env=HOPS_DEFAULT_ROOT={}", root.display());
    tauri_build::build()
}
