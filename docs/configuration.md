# Configuration

nnnotes has no built-in keys, server addresses or data paths. Every setting comes from one of three sources, and a
command that needs a setting nobody gave stops before it does any work.

## Sources and precedence

Lowest to highest:

1. **TOML file.** `--config <file>`; else the file named by the environment variable `NNNOTES_CONFIG`; else
   `nnnotes.toml` in the working directory, when present. A file named by `--config` or `NNNOTES_CONFIG` must exist.
2. **Environment variables** `NNNOTES_<SECTION>_<KEY>`: the setting's dotted name upper-cased, with dots, dashes
   and other non-alphanumeric characters as underscores (`bundle.key` → `NNNOTES_BUNDLE_KEY`,
   `servers.tw.cdn` → `NNNOTES_SERVERS_TW_CDN`, `paths.dummy_dll` → `NNNOTES_PATHS_DUMMY_DLL`).
3. **Command-line flags**, for the settings that have one (table below).

An empty value (`""`, `[]`, an empty environment variable, an empty flag) counts as unset, so a lower source still
applies. Start from [`nnnotes.example.toml`](../nnnotes.example.toml), which lists every setting with an empty value.
`nnnotes.toml` and `*.local.toml` are in `.gitignore`; do not commit a filled-in copy.

The global flags go **before** the command name:

```bash
nnnotes --config ~/nnnotes.toml --cache /data/nnnotes-cache pull <key>
NNNOTES_PATHS_MASTER=/data/master nnnotes adv 10462 -o episode.json
```

`--player` is the exception: it is an option of `site` and follows the command.

### Paths

Relative paths in the TOML file are relative to the file's directory; relative paths from the environment or the
command line are relative to the working directory. `~` is expanded. `[paths] catalog`, `apk`, `master` and
`dummy_dll` must exist when they are given.

## Settings

| Setting | Environment variable | Flag | Format |
|---|---|---|---|
| `[bundle] key` | `NNNOTES_BUNDLE_KEY` | — | AES-128 key of the asset bundle encryption: 16 bytes as 32 hex digits |
| `[bundle] nonce_seed` | `NNNOTES_BUNDLE_NONCE_SEED` | — | seed of the per-bundle nonce, hex (any length) |
| `[master] key` | `NNNOTES_MASTER_KEY` | — | Rijndael-256 key of the master data files: 32 bytes as 64 hex digits |
| `[master] iv` | `NNNOTES_MASTER_IV` | — | Rijndael-256 CBC initialization vector: 32 bytes as 64 hex digits |
| `[catalog] region` | `NNNOTES_CATALOG_REGION` | `--region` | name of one `[servers.<region>]` table |
| `[catalog] language` | `NNNOTES_CATALOG_LANGUAGE` | `--language` | catalog language: the `<language>` of `catalog_main_<language>.bin`, e.g. `zh-Hant` |
| `[servers.<region>] name` | `NNNOTES_SERVERS_<REGION>_NAME` | — | label of the region in `browse` (default: the region name) |
| `[servers.<region>] cdn` | `NNNOTES_SERVERS_<REGION>_CDN` | — | CDN base URL of the region (a trailing `/` is ignored) |
| `[servers.<region>] languages` | `NNNOTES_SERVERS_<REGION>_LANGUAGES` | — | catalog languages `browse` lists: a TOML array of strings; comma-separated in the environment |
| `[paths] catalog` | `NNNOTES_PATHS_CATALOG` | `--catalog` | a catalog `.bin` file to read instead of downloading `catalog_main_<language>.bin` |
| `[paths] cache` | `NNNOTES_PATHS_CACHE` | `--cache` | cache directory (created when missing) |
| `[paths] master` | `NNNOTES_PATHS_MASTER` | `--master` | decoded master data directory, one `<Table>.json` per table |
| `[paths] apk` | `NNNOTES_PATHS_APK` | `--apk` | the game's `base.apk` |
| `[paths] dummy_dll` | `NNNOTES_PATHS_DUMMY_DLL` | `--dummy-dll` | Il2CppDumper `DummyDll` directory generated from the same APK |
| `[paths] ffmpeg` | `NNNOTES_PATHS_FFMPEG` | `--ffmpeg` | `ffmpeg` executable; unset: `ffmpeg` on `PATH` |
| `[paths] vgmstream` | `NNNOTES_PATHS_VGMSTREAM` | `--vgmstream` | `vgmstream-cli` executable; unset: `vgmstream-cli` on `PATH` |
| `[paths] node` | `NNNOTES_PATHS_NODE` | `--node` | Node.js executable; unset: `node` on `PATH` |
| `[paths] player` | `NNNOTES_PATHS_PLAYER` | `--player` (of `site`) | ournotes-player checkout (built) or installed package |

Hex values are case-insensitive; a `0x` prefix and surrounding whitespace are ignored. All values are TOML strings
except `languages` (array of strings).

Regions: `browse` serves every region that has a `[servers.<region>]` table or an `NNNOTES_SERVERS_<REGION>_CDN`
variable (the region name is then the lower-cased `<REGION>`). The other commands use the one region named by
`[catalog] region`. Add one `[servers.<name>]` table per region.

## What each command needs

"Catalog" below means: `[paths] cache`; `[catalog] region` and that region's `cdn`; `[bundle] key` and
`nonce_seed`; and either `[paths] catalog` or `[catalog] language` (the catalog is then downloaded into the cache on
first use). `[paths] apk` is optional for the catalog: when it is set, the APK's own catalog and bundles are merged
in, and bundles that ship inside the APK can only be read with it.

| Command | Settings |
|---|---|
| `catalog` | `[paths] cache`; `[paths] catalog`, or `[catalog] language` (plus region and `cdn` while the catalog is not in the cache); no bundle key |
| `browse` | `[paths] cache`, `[bundle] key` + `nonce_seed`, and per region `cdn` + `languages` (`name` optional) |
| `pull` | catalog |
| `master decode` | `[master] key` + `iv` |
| `master download` | `[catalog] region` and that region's `cdn` |
| `adv` | catalog, `[paths] master` |
| `story` | catalog, `[paths] master`, `apk`, `dummy_dll`, `vgmstream`, `ffmpeg` |
| `live2d` | catalog, `[paths] apk` |
| `spot` | catalog, `[paths] master`, `apk` |
| `room` | catalog |
| `shader` | catalog; `--apk-bundle` also `[paths] apk` |
| `audio` | catalog, `[paths] apk`, `vgmstream`, `ffmpeg` (not for `--format wav`) |
| `crikey` | `[paths] apk` |
| `player` | `[paths] apk`, `dummy_dll` |
| `live` | catalog, `[paths] master`, `apk`, `dummy_dll`, `vgmstream`, `ffmpeg` |
| `site --pair` / `--all` | as `live`, plus `[catalog] language`, `[paths] player`, `node` |
| `site --player-only` / `--reingest-json` | `[paths] player` |

Bundles already in the cache are read from there; the commands still ask for the settings above.

## Errors

A missing or malformed setting stops the command with exit status 2 and one line on standard error. The line names
the setting, its environment variable and its flag where there is one; it never contains a value:

```
nnnotes: setting catalog.region is not set: give it as `region` in the [catalog] table of the config file, the environment variable NNNOTES_CATALOG_REGION or --region
nnnotes: setting bundle.key: must be 16 bytes (32 hex digits)
nnnotes: setting paths.apk: file <path> not found
nnnotes: ffmpeg not found on PATH: give its path as `ffmpeg` in the [paths] table of the config file, the environment variable NNNOTES_PATHS_FFMPEG or --ffmpeg
```

An unreadable config file (not found, invalid TOML) is reported the same way. Command-line usage errors (for
example a missing `-o`) also exit with status 2. Setting values are never printed, logged or put into an error
message, and the `repr` of the settings and key objects shows no values.

## Where the values come from

- **Keys, nonce seed, CDN base, region and language**: properties of the game client you own. nnnotes does not
  include them and does not derive them.
- **`[paths] apk`**: the `base.apk` of your own installation of the game.
- **`[paths] dummy_dll`**: the `DummyDll` directory that [Il2CppDumper](https://github.com/Perfare/Il2CppDumper)
  writes for that same APK. `player`, `story`, `live` and `site` read the game's MonoBehaviours with type trees
  generated from it.
- **`[paths] master`**: the output directory of `nnnotes master decode`, run on master data files from
  `nnnotes master download --version <version>` or on the game client's own files.
- **CRI HCA keycode**: not a setting. `audio`, `story`, `live` and `site` read it from the APK's boot data;
  `nnnotes crikey` shows whether one was found and can write it as a `.hcakey` file for vgmstream.
- **Tools**: [vgmstream](https://vgmstream.org/) (`vgmstream-cli`), [FFmpeg](https://ffmpeg.org/) and, for
  `site`, [Node.js](https://nodejs.org/) 20+ with a built
  [ournotes-player](https://github.com/empty-sekai/ournotes-player).
