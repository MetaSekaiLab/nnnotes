# Configuration

For Japanese-release endpoints, catalog snapshots and split APKs, see [Japanese release](jp.md).

nnnotes has no built-in keys, server addresses or data paths. Every setting comes from one of three sources, and a
command that needs a setting nobody gave stops before it does any work.

## Sources and precedence

Lowest to highest:

1. **TOML file.** `--config <file>`; else the file named by the environment variable `NNNOTES_CONFIG`; else
   `nnnotes.toml` in the working directory, when present; else the per-user file, when present:
   `%APPDATA%\nnnotes\nnnotes.toml` on Windows, `$XDG_CONFIG_HOME/nnnotes/nnnotes.toml` elsewhere (`~/.config` when
   `XDG_CONFIG_HOME` is unset). A file named by `--config` or `NNNOTES_CONFIG` must exist. `nnnotes config path`
   lists the files in this order and the one that is read.
2. **Environment variables** `NNNOTES_<SECTION>_<KEY>`: the setting's dotted name upper-cased, with dots, dashes
   and other non-alphanumeric characters as underscores (`bundle.key` → `NNNOTES_BUNDLE_KEY`,
   `servers.tw.cdn` → `NNNOTES_SERVERS_TW_CDN`, `bundle.nonce_seed` → `NNNOTES_BUNDLE_NONCE_SEED`).
3. **Command-line flags**, for the settings that have one (table below).

An empty value (`""`, `[]`, an empty environment variable, an empty flag) counts as unset, so a lower source still
applies. The template [`nnnotes.example.toml`](../src/nnnotes/nnnotes.example.toml) ships with the package and lists
every setting with an empty value; `nnnotes config init` writes it (below). `nnnotes.toml` and `*.local.toml` are in
`.gitignore`; do not commit a filled-in copy.

## Writing the config file

```bash
nnnotes config init [--user | --file F] [--set SECTION.KEY=VALUE ...] [--no-input] [--force]
nnnotes config set SECTION.KEY VALUE [--user | --file F]
nnnotes config unset SECTION.KEY [--user | --file F]
nnnotes config check [--json]
nnnotes config path [--json]
```

- `config init` writes the template to `./nnnotes.toml` (the file `--config` or `NNNOTES_CONFIG` names, when one
  does; `--user`: the per-user file; `--file`: that file). An existing file is kept unless `--force`.
  - In a terminal, with no `--set` and no `--no-input`, it asks for each setting in turn. Enter leaves a setting
    empty, the four keys are read without echo, and a malformed value is asked again. It asks for the region names
    first, and writes one `[servers.<region>]` table per region.
  - Otherwise it asks nothing: the `--set` values are written (a list comma-separated, a path made absolute) and the
    other settings stay empty. A `--set` value `-` is read from standard input, once.
- `config set` sets one value in the file the commands read (`--user` / `--file`: that file, made from the template
  when it does not exist). The other lines and the comments stay as they are; a new region gets the template's region
  table. VALUE `-` reads the value from standard input, without echo for the keys in a terminal. `config unset`
  empties one value. Both check the value's format first and name only the setting when it is malformed.
- `config check` lists every setting: where it comes from (`file`, `env`, `flag` or `-`) and its status: `ok`, `unset`,
  `invalid` (with the reason), `not found` (a path that does not exist), or `unknown` (a key of the file or an
  `NNNOTES_` variable that names no setting). It exits with status 1 when a setting is invalid, not found or unknown.
  `--json` prints the report as `nnnotes.config-check/1`: `file`, `settings` (name, environment variable, flag,
  kind, whether it is a key, description, origin, status, reason) and `problems`.
- `config path` lists the config file candidates in lookup order and the one that is read; `--json` prints
  `nnnotes.config-path/1`.

No `config` command prints a setting's value. The config files these commands write are readable by their owner only
(mode 600 where the file system has modes).

The global flags go **before** the command name:

```bash
nnnotes --config ~/nnnotes.toml --cache /data/nnnotes-cache pull <key>
NNNOTES_PATHS_MASTER=/data/master nnnotes adv 10462 -o episode.json
```

`--player` is the exception: it is an option of `web` and follows the command. `web` also has a `--region` of its
own (with `--all-regions`), which follows the command and chooses the regions the site serves; the global `--region`
before the command sets `[catalog] region`:

```bash
nnnotes --region tw web out/site --all              # the one region [catalog] region = tw
nnnotes web out/site --all --region tw --region kr  # a site for two regions
```

### Paths

Relative paths in the TOML file are relative to the file's directory; relative paths from the environment or the
command line are relative to the working directory. `~` is expanded. `[paths] catalog`, `apk` and `master` must
exist when they are given.

## Settings

| Setting | Environment variable | Flag | Format |
|---|---|---|---|
| `[bundle] key` | `NNNOTES_BUNDLE_KEY` | — | AES-128 key of the asset bundle encryption: 16 bytes as 32 hex digits |
| `[bundle] nonce_seed` | `NNNOTES_BUNDLE_NONCE_SEED` | — | seed of the per-bundle nonce, hex (any length) |
| `[master] key` | `NNNOTES_MASTER_KEY` | — | Rijndael-256 key of the master data files: 32 bytes as 64 hex digits |
| `[master] iv` | `NNNOTES_MASTER_IV` | — | Rijndael-256 CBC initialization vector: 32 bytes as 64 hex digits |
| `[catalog] region` | `NNNOTES_CATALOG_REGION` | `--region` | name of one `[servers.<region>]` table |
| `[catalog] language` | `NNNOTES_CATALOG_LANGUAGE` | `--language` | catalog language: the `<language>` of `catalog_<version>_<language>.bin`: `ja`, `en`, `zh-Hant`, `zh-Hans` or `ko`; also the client language of `live`, `story` and `web` (the text field, fonts and line spacing of their UI) and of the model labels of `web --live2d` |
| `[catalog] version` | `NNNOTES_CATALOG_VERSION` | `--catalog-release` | optional international resource version; unset: query the configured region API, or use legacy `main` when no API is configured; JP uses its own discovery |
| `[servers.<region>] name` | `NNNOTES_SERVERS_<REGION>_NAME` | — | label of the region in `browse` (default: the region name) |
| `[servers.<region>] provider` | `NNNOTES_SERVERS_<REGION>_PROVIDER` | — | `international` or `jp`; empty selects `jp` for the region named jp, international otherwise |
| `[servers.<region>] client_version` | `NNNOTES_SERVERS_<REGION>_CLIENT_VERSION` | — | per-region API client version; overrides `[client] version`, then falls back to the region's APK versionName |
| `[servers.<region>] apk` | `NNNOTES_SERVERS_<REGION>_APK` | `--apk` | per-region APK, APKS/XAPK or directory with base.apk; adjacent splits of base.apk are read automatically; the flag overrides it |
| `[servers.<region>] catalog` | `NNNOTES_SERVERS_<REGION>_CATALOG` | `--catalog` | per-region catalog file; JP needs the matching `<file>.source.json`; the flag overrides it |
| `[servers.<region>] catalog_version` | `NNNOTES_SERVERS_<REGION>_CATALOG_VERSION` | `--catalog-release` | per-region international version pin; overrides `[catalog] version`, but not the command-line flag |
| `[servers.<region>] cdn` | `NNNOTES_SERVERS_<REGION>_CDN` | — | CDN base URL of the region (a trailing `/` is ignored) |
| `[servers.<region>] languages` | `NNNOTES_SERVERS_<REGION>_LANGUAGES` | — | catalog languages `browse` lists: a TOML array of strings; comma-separated in the environment |
| `[servers.<region>] api` | `NNNOTES_SERVERS_<REGION>_API` | — | API root of the region: `https://host[:port]` (TLS, port 443 by default), `host[:port]`, or `http://host[:port]` for a plain-text local server; no path |
| `[servers.<region>] master` | `NNNOTES_SERVERS_<REGION>_MASTER` | — | decoded master data directory of the region (default: `[paths] master`) |
| `[bootstrap] api` | `NNNOTES_BOOTSTRAP_API` | — | API root that serves the server list (`nnnotes servers`), same format |
| `[client] version` | `NNNOTES_CLIENT_VERSION` | — | client version sent to the game's API, e.g. `1.0.1`; unset: the `versionName` of `[paths] apk` |
| `[paths] catalog` | `NNNOTES_PATHS_CATALOG` | `--catalog` | a catalog `.bin` file to read instead of discovering/downloading a catalog; explicit files stay usable offline |
| `[paths] cache` | `NNNOTES_PATHS_CACHE` | `--cache` | cache directory (created when missing) |
| `[paths] store` | `NNNOTES_PATHS_STORE` | `--store` (of `export`, `plan`, `run-stage`, `catalogs`, `store`) | store directory of the asset export ([assets.md](assets.md)); unset: `<[paths] cache>/store` |
| `[paths] master` | `NNNOTES_PATHS_MASTER` | `--master` | decoded master data directory, one `<Table>.json` per table; the flag overrides `[servers.<region>] master` |
| `[paths] apk` | `NNNOTES_PATHS_APK` | `--apk` | the game's `base.apk` (the story export reads the CRI Lips library from it, or from `split_config.arm64_v8a.apk` next to it) |
| `[paths] ffmpeg` | `NNNOTES_PATHS_FFMPEG` | `--ffmpeg` | `ffmpeg` executable; unset: `ffmpeg` on `PATH` |
| `[paths] vgmstream` | `NNNOTES_PATHS_VGMSTREAM` | `--vgmstream` | `vgmstream-cli` executable; unset: `vgmstream-cli` on `PATH` |
| `[paths] node` | `NNNOTES_PATHS_NODE` | `--node` | Node.js executable; unset: `node` on `PATH` |
| `[paths] player` | `NNNOTES_PATHS_PLAYER` | `--player` (of `web`) | ournotes-player checkout (built) or installed package |
| `[paths] fonts.<language>` | `NNNOTES_PATHS_FONTS_<LANGUAGE>` (e.g. `NNNOTES_PATHS_FONTS_ZH_HANT`) | `--font <language>=PATH` (of `web`) | font file (OpenType or TrueType) the story text of a language is drawn with (`--fonts open`); also read for the other story languages whose game fonts fall back to that language's font |
| `[paths] fonts.emoji` | `NNNOTES_PATHS_FONTS_EMOJI` | `--font emoji=PATH` (of `web`) | colour emoji font (PNG bitmap glyphs: CBDT or sbix) the stories' emoji sprites are drawn from (`--fonts open`); Noto Color Emoji (SIL Open Font License 1.1) is the tested font. Optional: without it the sprites keep their layout with empty glyphs, listed in `ui/fonts.json` `coverage.sprites.missing` |
| `[export] link` | `NNNOTES_EXPORT_LINK` | `--link` (of `export`) | how `export` makes the layout files from the store: `auto` (unset), `clone`, `hard` or `copy` ([assets.md](assets.md)) |

Hex values are case-insensitive; a `0x` prefix and surrounding whitespace are ignored. All values are TOML strings
except `languages` (array of strings).

Regions: `browse` serves every region that has a `[servers.<region>]` table or an `NNNOTES_SERVERS_<REGION>_CDN`
variable (the region name is then the lower-cased `<REGION>`), and `web --region` / `--all-regions` builds one site
for several of them. The other commands use the one region named by `[catalog] region`. Add one `[servers.<name>]`
table per region.

Master data per region: a command that reads master data for region `<r>` takes the directory of the `--master` flag,
else `[servers.<r>] master`, else `[paths] master`. International catalog selection uses `resource_version`, not
the master version or a change to the CDN root. `catalog_main` is a separate catalog that may remain old.
When the region has an API root, catalog-based commands query Version before selecting a download; discovery
failure stops rather than treating main as current. Without an API or version pin, the old main/offline behavior
is retained. JP's version/hash directory and authentication remain unchanged.

For repeatable builds, pin the resource version belonging to your master snapshot:

```sh
nnnotes --region tw --catalog-release 1.0.0.201 web site --story 10948
nnnotes --region tw --catalog-release 1.0.0.201 live2d MODEL_ID -o out/model
```

Alternatively set `NNNOTES_SERVERS_TW_CATALOG_VERSION` in the build environment; workers inherit this pin.
`--catalog-release` selects a remote resource release. The asset commands' existing `--catalog-version LABEL|SHA`
still selects an already imported store record. To inspect the historical main catalog, explicitly pin `main`.
Versioned caches are isolated by CDN root and resource version; a cached pin needs the CDN setting to identify
its directory but performs no API/catalog download. A missing versioned file never falls back to main.

## What each command needs

"Catalog" below means: `[paths] cache`; either `[paths] catalog` or `[catalog] language` (the catalog is then
downloaded into the cache on first use); and, for what must be downloaded, `[catalog] region` and that region's
`cdn`, `[bundle] key` and `nonce_seed`. `[paths] apk` is optional for the catalog: when it is set, the APK's own catalog and bundles are merged
in, and bundles that ship inside the APK can only be read with it.

"Master data" means the decoded master data directory of the region: the `--master` flag, else
`[servers.<region>] master` of `[catalog] region`, else `[paths] master`.

`--fonts game` (`story`, `live`, `web`) also needs the optional `fonts` dependencies (`pip install 'nnnotes[fonts]'`),
and so does `web --story` / `--all-stories` with either font source (it generates the TextMesh Pro font assets of the
story text); it is not a setting.

| Command | Settings |
|---|---|
| `catalog` | `[paths] cache`; `[paths] catalog`, or `[catalog] language` (plus region and `cdn` while the catalog is not in the cache); no bundle key |
| `browse` | `[paths] cache`, `[bundle] key` + `nonce_seed`, and per region `cdn` + `languages` (`name` optional) |
| `pull` | catalog |
| `master decode` | `[master] key` + `iv` |
| `master download` | `[catalog] region` and that region's `cdn`; `--latest` also that region's `api` and the client version |
| `master version` | `[catalog] region` and that region's `api`; the client version: `[client] version` or `[paths] apk` |
| `servers` | `[bootstrap] api`; the client version: `[client] version` or `[paths] apk` |
| `adv` | catalog, master data |
| `story` | catalog, `[catalog] language`, master data, `[paths] apk`, `ffmpeg` (audio and videos), `vgmstream` (not with `--no-audio`) |
| `live2d` | catalog, `[paths] apk` |
| `spot` | catalog, master data, `[paths] apk` |
| `room` | catalog |
| `shader` | catalog; `--apk-bundle` also `[paths] apk` |
| `audio` | catalog, `[paths] apk`, `vgmstream`, `ffmpeg` (not for `--format wav`) |
| `crikey` | `[paths] apk` |
| `player` | `[paths] apk` |
| `live` | catalog, `[catalog] language`, master data, `[paths] apk`, `vgmstream`, `ffmpeg` |
| `web --pair` / `--all` | as `live`, plus `[paths] player`, `node`; with `--region` / `--all-regions` each region's `[servers.<region>]` table (its `cdn` for what must be downloaded) and master data (`[servers.<region>] master`; `[paths] master` for at most one region) |
| `web --live2d` / `--all-live2d` | catalog (bundles from the CDN of the site's first region), `[paths] apk`, `[paths] player`; not `node`; master data only for the model names (optional: without it `models.json` has no names) |
| `web --player-only` / `--reingest-json` | `[paths] player` |
| `music-data` | catalog, `[master] key` + `iv` (not with `--decoded-master`); `--master-files` also `[catalog] region`; `--apk-master` also `[paths] apk`; `--decoded-master` also `[catalog] region` and master data with its `MasterManifest.json`; `[paths] apk` (optional otherwise) for the client version |
| `export`, `plan` | the store (`[paths] store` or `[paths] cache`); catalog (bundles are fetched into the cache); `[paths] apk` for the bundles inside the APK (without it they are reported as `source.absent`); master data for `--views`; with `--catalog-version` an imported catalog version instead of the current catalog |
| `run-stage` | the store; `[paths] cache` for inputs located in the cache; `--fetch` also what fetching needs (region, `cdn`, bundle key, `[paths] apk`) |
| `catalogs list` / `import` / `diff`, `store verify` | the store; `import` reads the APK's catalog from `[paths] apk` when it is set |
| `catalogs fetch` | the store, `[catalog] region` and `language`, that region's `cdn`; its `api` (optional) for the resource version label |

The region, its `cdn`, `[bundle] key` and `nonce_seed` are read only when a file must be downloaded: when the
catalog and every bundle a command needs are in the cache, none of them is needed. A download that needs a missing
one stops the command with one line naming the file and the setting (see Errors).

## Errors

A missing or malformed setting stops the command with exit status 2 and one line on standard error. The line names
the setting, its environment variable and its flag where there is one; it never contains a value:

```
nnnotes: setting catalog.region is not set: give it as `region` in the [catalog] table of the config file, the environment variable NNNOTES_CATALOG_REGION or --region
nnnotes: <name>.bundle is not in the cache: setting bundle.key is not set: give it as `key` in the [bundle] table of the config file, the environment variable NNNOTES_BUNDLE_KEY
nnnotes: setting bundle.key: must be 16 bytes (32 hex digits)
nnnotes: setting paths.apk: file <path> not found
nnnotes: ffmpeg not found on PATH: give its path as `ffmpeg` in the [paths] table of the config file, the environment variable NNNOTES_PATHS_FFMPEG or --ffmpeg
```

When no config file is read, the line of an unset setting ends with `(no config file was found: \`nnnotes config
init\` writes one to fill in)`. An unreadable config file (not found, invalid TOML) is reported the same way. Command-line usage errors also exit
with status 2: a missing `-o`, or a key or id the data does not have (a key not in the catalog, an episode, spot or
music id without its master data row, a Live2D model the catalog does not have), which the error line names:

```
nnnotes adv: error: episode 99999: no MasterAdv row with this id
nnnotes pull: error: not a key of the catalog: Live/MusicScore/9999/9999_03
```

Setting values are never printed, logged or put into an error message, and the `repr` of the settings and key
objects shows no values.

A failed call to the game's API (`master version`, `master download --latest`, `servers`) exits with status 1 and
one line naming the method, the setting and the gRPC status, plus the game's error code when the server sent one;
it never contains the address:

```
nnnotes: game API call Version to [servers.tw] api failed: UNAVAILABLE (server unreachable)
```

## Where the values come from

- **Keys, nonce seed, CDN base, region and language**: properties of the game client you own. nnnotes does not
  include them and does not derive them.
- **API roots**: the region's API root and the bootstrap API root from your own client. With `[bootstrap] api` set,
  `nnnotes servers --show-hosts` prints every region's CDN and API roots from the server list; a root field there
  can hold several alternatives separated by `|`, and any one of them can be used as `cdn` / `api`.
- **Client version**: the version of your game client (`versionName` of its `base.apk`). The game's API rejects a
  version older than the one it accepts (`game error code CLIENT_UPDATE_REQUIRED`).
- **`[paths] apk`**: the `base.apk` of your own installation of the game. `player`, `story`, `live` and `web`
  read MonoBehaviours of its boot data with type trees that ship with nnnotes, one set per Unity version (currently
  game version 1.0.1, Unity 6000.3.12f1). With an APK whose classes do not match them, these commands stop with
  exit status 2 and a line naming the class, the game version and the Unity version.
- **`[paths] master`**: the output directory of `nnnotes master decode`, run on master data files from
  `nnnotes master download --latest` (or `--version <version>`) or on the game client's own files.
  `[servers.<region>] master` the same for one region: `nnnotes --region <region> master download --latest -o <dir>`,
  then `nnnotes master decode <dir> -o <region dir>` (the regions serve different master data versions).
  `music-data --decoded-master` also reads the `MasterManifest.json` of the decoded files there (copy it from the
  download directory; a published master data snapshot may carry it).
- **CRI HCA keycode**: not a setting. `audio`, `story`, `live` and `web` read it from the APK's boot data;
  `nnnotes crikey` shows whether one was found and can write it as a `.hcakey` file for vgmstream.
- **Tools**: [vgmstream](https://vgmstream.org/) (`vgmstream-cli`), [FFmpeg](https://ffmpeg.org/) and, for
  `web`, [Node.js](https://nodejs.org/) 20+ with a built
  [ournotes-player](https://github.com/empty-sekai/ournotes-player).
