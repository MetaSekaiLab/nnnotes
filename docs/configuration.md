# Configuration

nnnotes has no built-in keys, server addresses or data paths. Every setting comes from one of three sources, and a
command that needs a setting nobody gave stops before it does any work.

## Sources and precedence

Lowest to highest:

1. **TOML file.** `--config <file>`; else the file named by the environment variable `NNNOTES_CONFIG`; else
   `nnnotes.toml` in the working directory, when present. A file named by `--config` or `NNNOTES_CONFIG` must exist.
2. **Environment variables** `NNNOTES_<SECTION>_<KEY>`: the setting's dotted name upper-cased, with dots, dashes
   and other non-alphanumeric characters as underscores (`bundle.key` → `NNNOTES_BUNDLE_KEY`,
   `servers.tw.cdn` → `NNNOTES_SERVERS_TW_CDN`, `bundle.nonce_seed` → `NNNOTES_BUNDLE_NONCE_SEED`).
3. **Command-line flags**, for the settings that have one (table below).

An empty value (`""`, `[]`, an empty environment variable, an empty flag) counts as unset, so a lower source still
applies. Start from [`nnnotes.example.toml`](../nnnotes.example.toml), which lists every setting with an empty value.
`nnnotes.toml` and `*.local.toml` are in `.gitignore`; do not commit a filled-in copy.

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
| `[catalog] language` | `NNNOTES_CATALOG_LANGUAGE` | `--language` | catalog language: the `<language>` of `catalog_main_<language>.bin`: `ja`, `en`, `zh-Hant`, `zh-Hans` or `ko`; also the client language of `live`, `story` and `web` (the text field, fonts and line spacing of their UI) and of the model labels of `web --live2d` |
| `[servers.<region>] name` | `NNNOTES_SERVERS_<REGION>_NAME` | — | label of the region in `browse` (default: the region name) |
| `[servers.<region>] cdn` | `NNNOTES_SERVERS_<REGION>_CDN` | — | CDN base URL of the region (a trailing `/` is ignored) |
| `[servers.<region>] languages` | `NNNOTES_SERVERS_<REGION>_LANGUAGES` | — | catalog languages `browse` lists: a TOML array of strings; comma-separated in the environment |
| `[servers.<region>] api` | `NNNOTES_SERVERS_<REGION>_API` | — | API root of the region: `https://host[:port]` (TLS, port 443 by default), `host[:port]`, or `http://host[:port]` for a plain-text local server; no path |
| `[servers.<region>] master` | `NNNOTES_SERVERS_<REGION>_MASTER` | — | decoded master data directory of the region (default: `[paths] master`) |
| `[bootstrap] api` | `NNNOTES_BOOTSTRAP_API` | — | API root that serves the server list (`nnnotes servers`), same format |
| `[client] version` | `NNNOTES_CLIENT_VERSION` | — | client version sent to the game's API, e.g. `1.0.1`; unset: the `versionName` of `[paths] apk` |
| `[paths] catalog` | `NNNOTES_PATHS_CATALOG` | `--catalog` | a catalog `.bin` file to read instead of downloading `catalog_main_<language>.bin` |
| `[paths] cache` | `NNNOTES_PATHS_CACHE` | `--cache` | cache directory (created when missing) |
| `[paths] store` | `NNNOTES_PATHS_STORE` | `--store` (of `export`, `plan`, `run-stage`, `catalogs`, `store`) | store directory of the asset export ([assets.md](assets.md)); unset: `<[paths] cache>/store` |
| `[paths] master` | `NNNOTES_PATHS_MASTER` | `--master` | decoded master data directory, one `<Table>.json` per table; the flag overrides `[servers.<region>] master` |
| `[paths] apk` | `NNNOTES_PATHS_APK` | `--apk` | the game's `base.apk` |
| `[paths] ffmpeg` | `NNNOTES_PATHS_FFMPEG` | `--ffmpeg` | `ffmpeg` executable; unset: `ffmpeg` on `PATH` |
| `[paths] vgmstream` | `NNNOTES_PATHS_VGMSTREAM` | `--vgmstream` | `vgmstream-cli` executable; unset: `vgmstream-cli` on `PATH` |
| `[paths] node` | `NNNOTES_PATHS_NODE` | `--node` | Node.js executable; unset: `node` on `PATH` |
| `[paths] player` | `NNNOTES_PATHS_PLAYER` | `--player` (of `web`) | ournotes-player checkout (built) or installed package |

Hex values are case-insensitive; a `0x` prefix and surrounding whitespace are ignored. All values are TOML strings
except `languages` (array of strings).

Regions: `browse` serves every region that has a `[servers.<region>]` table or an `NNNOTES_SERVERS_<REGION>_CDN`
variable (the region name is then the lower-cased `<REGION>`), and `web --region` / `--all-regions` builds one site
for several of them. The other commands use the one region named by `[catalog] region`. Add one `[servers.<name>]`
table per region.

Master data per region: a command that reads master data for region `<r>` takes the directory of the `--master` flag,
else `[servers.<r>] master`, else `[paths] master`. The regions serve the same catalog for a language, so the
catalog settings and the cached `catalog_main_<language>.bin` serve every region; bundles are fetched from the CDN of
the region in use.

## What each command needs

"Catalog" below means: `[paths] cache`; either `[paths] catalog` or `[catalog] language` (the catalog is then
downloaded into the cache on first use); and, for what must be downloaded, `[catalog] region` and that region's
`cdn`, `[bundle] key` and `nonce_seed`. `[paths] apk` is optional for the catalog: when it is set, the APK's own catalog and bundles are merged
in, and bundles that ship inside the APK can only be read with it.

"Master data" means the decoded master data directory of the region: the `--master` flag, else
`[servers.<region>] master` of `[catalog] region`, else `[paths] master`.

`--fonts game` (`story`, `live`, `web`) also needs the optional `fonts` dependencies (`pip install 'nnnotes[fonts]'`);
it is not a setting.

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

An unreadable config file (not found, invalid TOML) is reported the same way. Command-line usage errors also exit
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
- **CRI HCA keycode**: not a setting. `audio`, `story`, `live` and `web` read it from the APK's boot data;
  `nnnotes crikey` shows whether one was found and can write it as a `.hcakey` file for vgmstream.
- **Tools**: [vgmstream](https://vgmstream.org/) (`vgmstream-cli`), [FFmpeg](https://ffmpeg.org/) and, for
  `web`, [Node.js](https://nodejs.org/) 20+ with a built
  [ournotes-player](https://github.com/empty-sekai/ournotes-player).
