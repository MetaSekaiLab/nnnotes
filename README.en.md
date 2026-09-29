# nnnotes？！

Japanese-release support includes anonymous version discovery, authenticated CDN downloads, gzip catalogs,
snapshot-isolated caches and split APKs. See the [JP guide](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/jp.md)
for configuration and validation limits.

[简体中文](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.md) | [English](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.en.md)

nnnotes is an offline data toolkit for the game files of BanG Dream! Our Notes: it reads Addressables catalogs,
downloads and decrypts asset bundles, downloads and decodes master data, and exports stories, Live2D models, spots,
shaders, CRI audio and live charts as structured JSON and common file formats. The charts, Live2D models and stories `web`
exports are the data that [ournotes-player](https://github.com/empty-sekai/ournotes-player) reads. The naming was inspired by [mos9527/sssekai](https://github.com/mos9527/sssekai).

This is an unofficial fan project, not affiliated with the game's developer or operator. The repository contains no
game assets, keys or server addresses: users supply the game files, the keys needed for decryption and the server
addresses in their own configuration, and the exports stay in local directories the user chooses.

## Install and set up

```sh
pip install nnnotes              # Python 3.11+, 3.13 recommended
nnnotes config init --user       # asks for each setting in the terminal (keys without echo), writes the per-user config file read from any directory
nnnotes config check             # each setting's origin and whether it is valid, without its value
```

- The values (keys, the CDN and API roots of the regions, the paths of your own data and tools) come from the game
  client you own; see Configuration below.
- Scripts and AI agents get no questions: `nnnotes config init --user --no-input --set SECTION.KEY=VALUE ...` writes
  the file at once, `nnnotes config set SECTION.KEY VALUE` changes one value (VALUE `-` reads it from standard input,
  so a key need not appear on the command line), and `nnnotes config check --json` reports the states for machines.
- `--fonts game` also needs `pip install 'nnnotes[fonts]'`; the APK, the master data and the external tools are listed
  under Requirements below.

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
| `story` | episode ID | a story directory: episode, audio, stage scene and shaders, story UI, frames / particle effects / post effects / stills / talk windows / chat assets, videos (WebM); the Live2D models it uses are exported into a models directory (`--models`, default `live2d/` next to the story directory), where other stories reuse them |
| `live2d` | model key or model id | a Live2D (Cubism) runtime directory: moc3, textures, motion3, physics, expressions, prefab parameters |
| `spot` | spot ID | `spot.json` + Spine characters + the room as `room.glb` + shaders |
| `room` | background prefab key | the room model (binary glTF) |
| `shader` | key or APK bundles | every platform variant of the shaders (GLSL ES and others) with an index |
| `audio` | CRI cue sheet | one audio file per cue (FLAC / Ogg / WAV) + cue metadata |
| `crikey` | APK | the CRI HCA keycode from the game's boot data (shows whether it was found; can write a `.hcakey`) |
| `player` | APK | render-related global settings (color space, quality levels, renderers) as JSON |
| `live` | music ID + difficulty | a full chart directory: chart and runtime notes, 3D scene, note and effect assets, BGM and sounds, sound routing |
| `web` | `--pair music:difficulty` (repeatable) or `--all`; `--live2d model` (repeatable) or `--all-live2d`; `--story episode` (repeatable) or `--all-stories`; `--region region` (repeatable) or `--all-regions` | an ournotes-player static site: shared player + per-chart / per-model / per-episode manifests + content-addressed assets; the Live2D models of the stories are built first as models, listed in `models.json`, and the story manifests reference them; one site can serve several regions, with listing texts in five languages; a story's interface texts are grouped by language, with TextMeshPro font assets generated from open fonts (the game's fonts with `--fonts game`); compressible assets (JSON, shaders, moc3, ...) are stored gzip encoded by default (`--compress br` / `none`) |
| `music-data` | master data files (`--master-files` directory or `--apk-master`), or decoded master data with its manifest (`--decoded-master`) | one JSON file with every song and chart: titles and credits in five languages, bands, vocal characters, category, tags, release time, score ranks, BGM length; the Gekisou catalog (member cards, snaps and their Gekisou skills and support skills); per difficulty the level, note counts, BPM, chart times, skill events and fever ranges; and the chart statistics the deck model ournotes-deck (built into nnnotes) measures on its whole-live simulation (the no-skill score, the weight of every score-up skill kind at every position), plus single-shape Gekisou aptitude (increments and standard errors, not an optimal deck; omit with `--no-gekisou-aptitude`). `--full` adds the deck model's input: every chart's runtime notes and the master data tables about cards, skills, bonuses, scores and events ([format](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/music-data.md)) |

Export conventions:

- Commands that write files take the output path with `-o` (required); `web` takes the site directory as a
  positional argument.
- JSON is always UTF-8 with LF line endings; infinity is written as `1e999`.
- Exports are deterministic for a given installation: the same input with the same versions of nnnotes, its Python
  dependencies (UnityPy, Pillow, numpy, ...) and the external tools (vgmstream, FFmpeg) gives byte-identical files.
  Other versions can encode the same content into other bytes: PNG files written by another Pillow version can
  differ in their bytes while their pixels are equal.
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
| stories, `story` (full directory) | 946 / 946 episodes have all their resources in the catalog and of supported kinds (the resource closure equals the game's own per-episode download list); 946 / 946 exported and checked one by one: the files, models and audio the output refers to exist, every JSON parses and every path it refers to resolves; the 235 episodes with frames, effects, post effects, stills, chats, talk windows or videos are byte-identical across two exports except for the models directory path |
| Live2D models | 239 / 239 (every model of the catalog; episodes use 185 of them) |
| CRI audio | 681 / 681 cue sheets |
| charts, `live` | 336 / 336 (music, difficulty) pairs |
| web site, `web` | 336 / 336 charts, 239 / 239 Live2D models, 946 / 946 story episodes (two full builds in English without audio are byte-identical and 946 / 946 pass the data validation); two full builds of the three regions in five languages with AAC audio (`--all-regions`, with the live master data: 340 charts of 85 musics) are likewise byte-identical and pass the data validation in full |
| spots, `spot` / `room` | 39 / 39 spots, every reference resolves; `room` exports 20 / 20 room backgrounds of the catalog. Every glb passes the Khronos glTF-Validator with 0 errors |
| other regions (en / kr) and languages | checked: the regions serve the same catalog for a language and the same bundles, and the keys are shared; the master tables the charts use are the same in the three regions, and the text tables have all five languages; the chart exports checked match the Taiwan server's. A full multi-region site build is verified (the row above) |

## Requirements

- Python 3.11+, 3.13 recommended (a `web` build reads and writes a lot of JSON, and the standard library encodes
  JSON faster on 3.13); for development `pip install -e .` in the repository
- the game's `base.apk`: APK-local bundles, the CRI keycode, boot settings. `player`, `story`, `live` and `web` read
  MonoBehaviours of its boot data with type trees that ship with nnnotes; supported now: game version 1.0.1
  (Unity 6000.3.12f1). With an APK of another version whose classes do not match, these commands stop with an
  error naming the class, the game version and the Unity version
- a decoded master data directory (from `master download` + `master decode`): needed by `adv`, `story`, `spot`,
  `live` and the charts of `web`; the Live2D models of `web` use it only for their character names (optional)
- external tools: [vgmstream](https://vgmstream.org/) (CRI HCA decoding), [FFmpeg](https://ffmpeg.org/)
  (transcoding, including the WebM muxing and Opus audio of story videos); `web` also needs Node.js 20+ and a built
  ournotes-player

## Configuration

The code contains no keys, server addresses or default paths. Settings are read in this order, later sources
overriding earlier ones:

1. config file: `--config <file>`, else `NNNOTES_CONFIG`, else `nnnotes.toml` in the working directory, else the
   per-user file (`%APPDATA%\nnnotes\nnnotes.toml` on Windows, `~/.config/nnnotes/nnnotes.toml` elsewhere;
   `nnnotes config path` lists the lookup order and the file read)
2. environment variables: `NNNOTES_<SECTION>_<KEY>` (e.g. `NNNOTES_BUNDLE_KEY`, `NNNOTES_SERVERS_TW_CDN`)
3. command-line flags: `--region`, `--language`, `--catalog`, `--cache`, `--master`, `--apk`, `--ffmpeg`,
   `--vgmstream`, `--node` go before the command name; `--player` is an option of `web`

`nnnotes config init` writes the config template (every setting empty; it ships with the package, in the repository
[`src/nnnotes/nnnotes.example.toml`](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/nnnotes.example.toml)),
asking for each value in a terminal; `config set` / `config unset` change one value and keep the file's comments.
The settings are: the bundle
key and nonce seed, the master data key and IV, the region in use (`[catalog] region`) and the catalog language
(`[catalog] language`), the CDN base and API root of each region (`cdn`, `api` of `[servers.<region>]`), the client
version (`[client] version`; unset: the APK's versionName), optionally the bootstrap API root (`[bootstrap] api`, for
`servers`), and the paths of the cache, the APK, the master data directory, ournotes-player and vgmstream / FFmpeg /
Node.js (the three tools are looked up on `PATH` when unset). All values come from the game client you own.

A missing or malformed setting stops the command with exit status 2 and one line naming the TOML key, the
environment variable and the flag; no setting value is printed. `nnnotes.toml` is in `.gitignore`; do not commit it.

See [docs/configuration.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/configuration.md) for the full reference.

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
nnnotes web out/site --all --region tw --region en --region kr --player <ournotes-player dir>
nnnotes web out/site --live2d adv_live2d_rana_003_casual_spring_01 --player <ournotes-player dir>
```

`web` builds incrementally: charts and models whose manifest exists are skipped (`--force` rebuilds them) and assets
no longer referenced are removed. Common options:

- `--format aac|opus|vorbis|mp3|flac`: BGM format, default AAC; `--no-audio`: no audio files
- `--band` / `--leader-card`: the band of the LightWeight background and the start timeline; default: the band of
  the music's first vocal character
- `--workers`: parallel music processes (default a quarter of the CPUs, up to 8) and model processes (default up
  to 4); `--read-workers`: chart read sets at a time (Node.js processes; default half the CPUs, up to 16); `--tmp`:
  temporary build directory (default `<site>.tmp`)
- `--live2d MODEL` (model id or key, repeatable) / `--all-live2d`: add Live2D models (every model of the catalog);
  charts and models can be added in the same run
- `--region REGION` (repeatable) / `--all-regions`: the regions the site serves (default: `[catalog] region`);
  each region's master data is `[servers.<region>] master`. Regions with the same chart data share one manifest;
  the chart list page switches region and language with `?region=&lang=`
- `--player-only`: rewrite the player files, `charts.json` and `models.json` only; `--reingest-json`: store every
  chart's and model's JSON files again under the current rules

See [docs/commands.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/commands.md) for every command's options and output layout.

## Architecture

```
settings (TOML / environment / flags)
  └─ access    addressables (catalog parsing, bundle decryption, local browser)
               catalog (dependency closures, remote and APK bundles, local cache)
               master (master data download and decoding)
               gameapi (anonymous game API calls: current master data version, server list)
       └─ reading  unity (UnityPy type trees and TextAssets)
                   export (prefabs / components / references resolved to JSON, textures and shaders alongside)
                   shader, textstyle (text layout and style), tmpfont (TextMesh Pro fonts), player (boot settings), cri + crikey (CRI audio)
            └─ content  stories: adv, advscene, advmedia (frames / effects / post effects / stills / chat),
                                 advvideo (USM videos), advui, story
                        Live2D: live2d, motion
                        spots: spot, room
                        charts: score (chart parsing and a reimplementation of the game's chart converter),
                                livescene, livenotes, liveui, liveaudio, live
                        site: web, webmodel (ournotes-player data)
```

Every JSON file is written by one writer (`jsonio`), so encoding, line endings and number formatting are the same
everywhere.

See [docs/performance.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/docs/performance.md) for where a
`web` build spends its time and which parts of it run in compiled code.

## License

MIT, see [LICENSE](https://github.com/MetaSekaiLab/nnnotes/blob/main/LICENSE).
