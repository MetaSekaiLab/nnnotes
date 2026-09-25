# Contributing

Thank you for your interest in nnnotes. This document covers the development setup, tests, commit messages and
releases.

## Ground rules

nnnotes contains no game assets, keys or server addresses, and it stays that way. Do not put any of these into
issues, pull requests, commit messages, tests or fixtures:

- game files or excerpts of them (bundles, catalogs, master data, audio, textures, decoded JSON);
- keys, nonce seeds, IVs, keycodes or anything derived from them;
- server addresses, CDN URLs or host names;
- your filled-in `nnnotes.toml`.

When reporting a problem, describe the command, the options and the error message (nnnotes never prints setting
values), and refer to game data by its public identifiers (episode ID, music ID, addressable key).

## Setup

Python 3.11 or later:

```bash
git clone https://github.com/empty-sekai/nnnotes
cd nnnotes
python -m venv .venv
. .venv/bin/activate            # Windows: .venv\Scripts\activate
python -m pip install -e ".[test]"
```

The extractors also need vgmstream, FFmpeg and, for `web`, Node.js with a built ournotes-player; see
[docs/configuration.md](docs/configuration.md). The tests need none of them.

## Tests and lint

```bash
python -m pyflakes src tests
python -m pytest
```

The tests use synthetic inputs only: files built inside the test with obviously fake keys (for example
`bytes(range(16))`) or published known-answer vectors. They need no game data and no network, and the whole suite
runs in a few seconds. New tests follow the same rules. CI runs lint and tests on Python 3.11, 3.12 and 3.13.

Code conventions:

- every JSON file goes through `nnnotes.jsonio` (UTF-8, LF, deterministic, `1e999` for infinity);
- every setting goes through `nnnotes.config`: no defaults for keys, addresses or paths, and no setting value in a
  message, a log line or a `repr`;
- text files are UTF-8 with LF line endings (`.gitattributes` enforces LF).

## Commit messages

Commits and pull request titles follow [Conventional Commits](https://www.conventionalcommits.org/) in English:

```
<type>(<optional scope>): <summary>

feat(site): add --reingest-json
fix(master): reject files with bad padding
docs: describe the cache layout
```

Types: `feat`, `fix`, `perf`, `refactor`, `docs`, `test`, `build`, `ci`, `chore`, `style`, `revert`. A breaking
change is marked with `!` after the type or a `BREAKING CHANGE:` footer. The `Commit messages` workflow checks
commits and pull request titles with commitlint (`commitlint.config.mjs`).

## Releases

Versions follow [Semantic Versioning](https://semver.org/). The version is `__version__` in
`src/nnnotes/__init__.py`. To release:

1. set `__version__` to the new version and commit (`chore(release): vX.Y.Z`);
2. tag the commit `vX.Y.Z` and push the tag.

The `Release` workflow checks that the tag matches `__version__`, writes the release notes with
[git-cliff](https://git-cliff.org/) (`cliff.toml`), builds the sdist and wheel, and publishes a GitHub release with
both files.

## License

By contributing you agree that your contributions are licensed under the [MIT License](LICENSE).
