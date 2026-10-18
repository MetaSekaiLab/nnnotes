# Contributing

Thank you for helping with nnnotes. This page describes how to set up a checkout, run the checks and send a change.

## Setup

```sh
git clone https://github.com/MetaSekaiLab/nnnotes
cd nnnotes
python -m venv .venv
. .venv/bin/activate              # Windows: .venv\Scripts\activate
pip install -e ".[test,deck,fonts,docs]"
```

Python 3.11 or later. The tests need no game data, no keys and no network.

## Checks

These are the checks CI runs on every pull request:

```sh
ruff check .                      # lint
ruff format --check .             # formatting (line length 120)
lint-imports                      # the layer contract of pyproject.toml
pyright                           # type check (the settings of pyproject.toml)
pytest                            # the test suite
mkdocs build --strict             # the documentation site
```

- **Layers.** Each subpackage imports only the layers below it ([architecture](docs/architecture.md)). A module
  that needs something from a higher layer takes it as an argument instead.
- **Golden records.** `tests/golden/` records the output of each stage version. When a change alters a stage's
  output, raise that stage's (or converter's) version and rewrite the records with
  `GOLDENS_UPDATE=1 pytest tests/pipeline/test_goldens.py`, using the library versions of
  `.github/goldens-constraints.txt` (`pip install -c .github/goldens-constraints.txt -e ".[test,fonts]"`).
  Node.js runs the synthetic read-set fixture. CI uses Node.js 24 and Python 3.13 for these records.
  PNG-dependent records also use the native `Pillow.zlib` identity listed in their library versions.
- **Command reference.** `docs/cli.md` is generated from the parser. After changing a command or an option, run
  `python -m nnnotes.cli.reference docs/cli.md`; a test fails while the file is out of date.

## Tests and fixtures

Tests build their inputs synthetically: small Unity-like objects, catalogs, master tables and audio made by the tests
themselves (`tests/helpers/`). Fixtures contain no game files, no data extracted from the game and no keys or server
addresses, so the suite runs anywhere. Test a change with the smallest synthetic input that shows the behaviour.

Exports must stay deterministic ([determinism](docs/determinism.md)): sort what the game does not order, keep
times and machine paths out of outputs, and write JSON through `nnnotes.core.jsonio`.

## Commits and pull requests

- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/) with a lowercase type
  (`feat`, `fix`, `docs`, `refactor`, `test`, `build`, `ci`, `chore`) and an optional scope (`feat(cli): ...`).
  The body says what changed and what users see. A breaking change uses `!` after the type or scope and a
  `BREAKING CHANGE:` footer. The commit messages and the pull request title are checked in CI.
- One topic per pull request. Pull requests are merged by rebase, so each commit should build and pass the checks.
- `CHANGELOG.md` is generated from the commits by [git-cliff](https://git-cliff.org/) when a version is released;
  do not edit it by hand.

## Releases

Versions follow [Semantic Versioning](https://semver.org/) over the command line, the configuration keys, the JSON
schemas and the documented Python API. A release commit sets `__version__` in `src/nnnotes/__init__.py` and
regenerates the changelog:

```sh
git cliff --tag vX.Y.Z -o CHANGELOG.md
```

Pushing the tag `vX.Y.Z` builds the sdist and the wheel, checks them and publishes them to PyPI and the GitHub
release.

## License

By contributing you agree that your contribution is licensed under the [MIT License](LICENSE).
