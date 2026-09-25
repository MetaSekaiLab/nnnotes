# nnnotes？！

[简体中文](README.md) | [English](README.en.md)

nnnotes is an offline data toolkit for the game files of BanG Dream! Our Notes: it reads Addressables catalogs,
downloads and decrypts asset bundles, downloads and decodes master data, and exports stories, Live2D models, spots,
shaders, CRI audio and live charts as structured JSON and common file formats. The chart export is the data that
[ournotes-player](https://github.com/empty-sekai/ournotes-player) reads. The naming was inspired by [mos9527/sssekai](https://github.com/mos9527/sssekai).

This is an unofficial fan project, not affiliated with the game's developer or operator. The repository contains no
game assets, keys or server addresses: users supply the game files, the keys needed for decryption and the server
addresses in their own configuration, and the exports stay in local directories the user chooses.

## Features

| Command | Input | Output |
|---|---|---|
| `catalog` | the region's catalog | addressable keys (optionally by prefix) |
| `browse` | the configured regions' catalogs | a local web page to browse catalogs and bundles (`--host` / `--port`) |
| `pull` | addressable keys | every bundle of each key's dependency closure, decrypted into the local cache |
| `servers` | the bootstrap API root `[bootstrap] api` | the game API's server list: each region's name, area id and CDN / API roots (the roots only with `--show-hosts`) |
| `master version` | region | the master data version and resource version the region serves now (anonymous game API call) |
| `master download` | master data version, or `--latest` (the region's current one) | that version's `MasterManifest.json` and every `.bin` file (SHA-256 checked) |
| `master decode` | master data `.bin` files or directories | one JSON per table (Rijndael-256 CBC decryption + gzip) |
| `adv` | episode ID | `episode.json`: command list, lines in five languages, voice / sound / video index |
| `story` | episode ID | a full story directory: episode, every Live2D model, audio, stage scene and shaders, story UI |
| `live2d` | model key or model id | a Live2D (Cubism) runtime directory: moc3, textures, motion3, physics, expressions, prefab parameters |
| `spot` | spot ID | `spot.json` + Spine characters + the room as `room.glb` + shaders |
| `room` | background prefab key | the room model (binary glTF) |
| `shader` | key or APK bundles | every platform variant of the shaders (GLSL ES and others) with an index |
| `audio` | CRI cue sheet | one audio file per cue (FLAC / Ogg / WAV) + cue metadata |
| `crikey` | APK | the CRI HCA keycode from the game's boot data (shows whether it was found; can write a `.hcakey`) |
| `player` | APK | render-related global settings (color space, quality levels, renderers) as JSON |
| `live` | music ID + difficulty | a full chart directory: chart and runtime notes, 3D scene, note and effect assets, BGM and sounds, sound routing |
| `web` | `--pair music:difficulty` (repeatable) or `--all`; `--live2d model` (repeatable) or `--all-live2d` | an ournotes-player static site: shared player + per-chart / per-model manifests + content-addressed assets |

Export conventions:

- Commands that write files take the output path with `-o` (required); `web` takes the site directory as a
  positional argument.
- JSON is always UTF-8, LF and deterministic: the same input exported twice is byte-identical. Infinity is written
  as `1e999`.
- Values keep Unity's serialized values and field names (`m_LocalPosition`, `_bandIDs`, ...), so they can be
  compared with the game data.
- Textures are exported as PNG, shaders keep the game's own compiled programs, audio is decoded from the CRI formats
  to common formats.

## Status

Based on all data of the Taiwan server, version 1.0.1 (zh-Hant):

| Part | Status |
|---|---|
| catalog / bundle decryption / dependency closure | working |
| master data decoding | working |
| stories, `adv` | 946 / 946 episodes |
| stories, `story` (full directory) | 712 / 946 episodes; the other 234 use resource types not yet supported (Frame 209, Effect 17, PostEffect 3; 5 reference resources missing from the catalog) |
| Live2D models | 239 / 239 (every model of the catalog; episodes use 185 of them) |
| CRI audio | 681 / 681 cue sheets |
| charts, `live` | 336 / 336 (music, difficulty) pairs |
| web site, `web` | 336 / 336 charts, 239 / 239 Live2D models |
| spots, `spot` / `room` | one spot verified, the others not individually checked |
| other regions (en / kr) and languages | not verified |

## Requirements

- Python 3.11+; `pip install -e .` in the repository (not yet on PyPI)
- the game's `base.apk`: APK-local bundles, the CRI keycode, boot settings. `player`, `story`, `live` and `web` read
  MonoBehaviours of its boot data with type trees that ship with nnnotes; supported now: game version 1.0.1
  (Unity 6000.3.12f1). With an APK of another version whose classes do not match, these commands stop with an
  error naming the class, the game version and the Unity version
- a decoded master data directory (from `master download` + `master decode`): needed by `adv`, `story`, `spot`,
  `live`, `web`
- external tools: [vgmstream](https://vgmstream.org/) (CRI HCA decoding), [FFmpeg](https://ffmpeg.org/)
  (transcoding); `web` also needs Node.js 20+ and a built ournotes-player

## Configuration

The code contains no keys, server addresses or default paths. Settings are read in this order, later sources
overriding earlier ones:

1. config file: `--config <file>`, else `NNNOTES_CONFIG`, else `nnnotes.toml` in the working directory
2. environment variables: `NNNOTES_<SECTION>_<KEY>` (e.g. `NNNOTES_BUNDLE_KEY`, `NNNOTES_SERVERS_TW_CDN`)
3. command-line flags: `--region`, `--language`, `--catalog`, `--cache`, `--master`, `--apk`, `--ffmpeg`,
   `--vgmstream`, `--node` go before the command name; `--player` is an option of `web`

Copy [`nnnotes.example.toml`](nnnotes.example.toml) to `nnnotes.toml` and fill it in. The settings are: the bundle
key and nonce seed, the master data key and IV, the region in use (`[catalog] region`) and the catalog language
(`[catalog] language`), the CDN base and API root of each region (`cdn`, `api` of `[servers.<region>]`), the client
version (`[client] version`; unset: the APK's versionName), optionally the bootstrap API root (`[bootstrap] api`, for
`servers`), and the paths of the cache, the APK, the master data directory, ournotes-player and vgmstream / FFmpeg /
Node.js (the three tools are looked up on `PATH` when unset). All values come from the game client you own.

A missing or malformed setting stops the command with exit status 2 and one line naming the TOML key, the
environment variable and the flag; no setting value is printed. `nnnotes.toml` is in `.gitignore`; do not commit it.

See [docs/configuration.md](docs/configuration.md) for the full reference.

## Examples

```bash
nnnotes catalog --prefix Live/MusicScore/ --limit 20
nnnotes browse --port 8000
nnnotes pull Live/MusicScore/0001/0001_03
nnnotes master version
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes --master work/master adv 10462 -o out/adv_10462.json
nnnotes story 10462 -o out/story_10462
nnnotes live 100001 --difficulty expert -o out/live_100001
nnnotes web out/site --all --player <ournotes-player dir> --workers 5
nnnotes web out/site --pair 100001:expert --pair 100001:hard --player <ournotes-player dir>
nnnotes web out/site --live2d adv_live2d_rana_003_casual_spring_01 --player <ournotes-player dir>
```

`web` builds incrementally: charts and models whose manifest exists are skipped (`--force` rebuilds them) and assets
no longer referenced are removed. Common options:

- `--format aac|opus|vorbis|mp3|flac`: BGM format, default AAC; `--no-audio`: no audio files
- `--band` / `--leader-card`: the band of the LightWeight background and the start timeline; default: the band of
  the music's first vocal character
- `--workers`: parallel music processes (default up to 5) and model processes (default up to 4); `--tmp`:
  temporary build directory (default `<site>.tmp`)
- `--live2d MODEL` (model id or key, repeatable) / `--all-live2d`: add Live2D models (every model of the catalog);
  charts and models can be added in the same run
- `--player-only`: rewrite the player files, `charts.json` and `models.json` only; `--reingest-json`: store every
  chart's and model's JSON files again under the current rules

See [docs/commands.md](docs/commands.md) for every command's options and output layout.

## Architecture

```
settings (TOML / environment / flags)
  └─ access    addressables (catalog parsing, bundle decryption, local browser)
               catalog (dependency closures, remote and APK bundles, local cache)
               master (master data download and decoding)
               gameapi (anonymous game API calls: current master data version, server list)
       └─ reading  unity (UnityPy type trees and TextAssets)
                   export (prefabs / components / references resolved to JSON, textures and shaders alongside)
                   shader, tmpfont (TextMesh Pro fonts), player (boot settings), cri + crikey (CRI audio)
            └─ content  stories: adv, advscene, advui, story
                        Live2D: live2d, motion
                        spots: spot, room
                        charts: score (chart parsing and a reimplementation of the game's chart converter),
                                livescene, livenotes, liveui, liveaudio, live
                        site: web, webmodel (ournotes-player data)
```

Every JSON file is written by one writer (`jsonio`), so encoding, line endings and number formatting are the same
everywhere.

## License

MIT, see [LICENSE](LICENSE).
