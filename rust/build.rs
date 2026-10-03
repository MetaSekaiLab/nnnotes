//! Records the ournotes-sim package the module is built with (version, repository and git commit, from Cargo.lock)
//! as the environment variables DECK_VERSION, DECK_SOURCE and DECK_COMMIT of the crate.

use std::path::Path;

fn main() {
    let dir = std::env::var("CARGO_MANIFEST_DIR").expect("CARGO_MANIFEST_DIR");
    let lock = Path::new(&dir).join("Cargo.lock");
    println!("cargo:rerun-if-changed={}", lock.display());
    let text = std::fs::read_to_string(&lock).expect("rust/Cargo.lock (the deck commit is read from it)");
    let (mut version, mut source) = (None, None);
    for block in text.split("[[package]]") {
        let field = |k: &str| {
            block.lines().find_map(|l| {
                let v = l.trim().strip_prefix(k)?.trim_start().strip_prefix('=')?.trim();
                Some(v.trim_matches('"').to_string())
            })
        };
        if field("name").as_deref() == Some("ournotes-sim") {
            version = field("version");
            source = field("source");
        }
    }
    let version = version.expect("Cargo.lock has no ournotes-sim package");
    let source = source.expect("ournotes-sim has no source in Cargo.lock");
    // git+https://github.com/empty-sekai/ournotes-deck?rev=<rev>#<commit>
    let (url, commit) = source.split_once('#').expect("ournotes-sim is not a git dependency");
    let url = url.strip_prefix("git+").unwrap_or(url);
    let url = url.split_once('?').map_or(url, |(u, _)| u);
    println!("cargo:rustc-env=DECK_VERSION={version}");
    println!("cargo:rustc-env=DECK_SOURCE={url}");
    println!("cargo:rustc-env=DECK_COMMIT={commit}");
}
