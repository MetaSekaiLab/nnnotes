# Commands

```
nnnotes [global options] <command> [command options]
```

Global options (before the command): `--config`, `--region`, `--language`, `--catalog`, `--cache`, `--master`,
`--apk`, `--dummy-dll`, `--ffmpeg`, `--vgmstream`, `--node`, plus `--version` and `--help`. They set the settings
described in [configuration.md](configuration.md), which also lists the settings each command needs.
`nnnotes <command> --help` prints the options of a command.

Commands that write files take the output path with `-o/--out` (required), except `web`, which takes the site
directory as its positional argument. JSON summaries on standard output are UTF-8. Every JSON file is written by one
writer: UTF-8, LF line endings, deterministic, non-finite numbers as `1e999` / `-1e999`.

## Cache

`pull` and every extractor read bundles through the cache (`[paths] cache`):

```
<cache>/catalog_main_<language>.bin     the region's catalog (downloaded on first use unless [paths] catalog is set)
<cache>/bundles/<bundle file name>      bundles of the dependency closures, decrypted (UnityFS)
<cache>/raw/<path>                      raw CDN files stored as they are (e.g. CRI cue sheet data)
<cache>/catalogs/<region>/              catalogs downloaded by `browse`
```

Files already in the cache are not downloaded again. When `[paths] apk` is set, the APK's own catalog is merged with
the region's and bundles that ship inside the APK are read from it.

## catalog

```
nnnotes catalog [--prefix PREFIX] [--limit LIMIT]
```

Prints the addressable keys that start with `PREFIX` (default: all), sorted, at most `LIMIT` (default 200), one per
line; the total count goes to standard error.

## browse

```
nnnotes browse [--port PORT] [--host HOST]
```

Serves a local web page on `HOST:PORT` (default `127.0.0.1:8000`) that lists every configured region, its catalog
languages, and per catalog the asset paths as directories. A link under an asset path downloads the bundle holding
it, decrypted; `bundles/<bundle key>` lists every bundle directly.

## pull

```
nnnotes pull KEY [KEY ...]
```

Fetches the bundle closure (the key's location and all its dependencies) of each key into the cache and prints the
cached path of every bundle.

## master decode

```
nnnotes master decode INPUT [INPUT ...] -o OUT [--workers N]
```

`INPUT`: master data files, or directories whose `*.bin` files are decoded. Each file (64-byte prefix, then
Rijndael-256 CBC with PKCS7 padding over gzip-compressed JSON) is written as `OUT/<file stem>.json` (UTF-8, one-space
indent). `--workers` (default 8) decodes in parallel. Prints `{decoded, failed: [{file, error}], out}`; the exit
status is 1 when a file failed.

## master download

```
nnnotes master download --version VERSION -o OUT [--workers N]
```

Downloads master data version `VERSION` from the region's CDN: `OUT/MasterManifest.json` and every `.bin` file it
lists, each checked against the manifest's SHA-256. Files already present with the right hash are kept.
`--workers` (default 16) downloads in parallel. Prints `{version, files, downloaded, kept, failed, out}`; the exit
status is 1 when a file failed.

## adv

```
nnnotes adv ADV_ID -o OUT.json
```

One ADV episode as JSON: `advId`, `asset`, `commandCount`, `commands` (the episode's command list with the text,
sound and video rows resolved), `text` (every line in the five languages), `sounds`, `cuesheets`, `videos`,
`resources` (`kind`, `address`, `present` in the catalog), `master` (the `MasterAdv` row) and `title`.

## story

```
nnnotes story ADV_ID -o OUT [--format flac|ogg|wav]
```

One ADV episode as a self-contained directory:

```
OUT/episode.json          as `adv`
OUT/live2d/<model>/       every Live2D model of the episode: moc3, atlas pages, full prefab
OUT/audio/<cueSheet>/     every cue sheet of the episode, one file per cue + cues.json, streams.json
OUT/scene.json            player graphics, cameras, ADV fields, volumes, settings and stages
OUT/textures/, shaders/   textures and shaders of the scene
OUT/ui/                   ADV front canvas UI: ui.json, packed textures, UI shaders
OUT/story.json            index of the above
```

`--format` (default `flac`) is the audio format. The command stops when the episode uses resources that are not in
the catalog or resource kinds that are not supported. Prints the index with a summary.

## live2d

```
nnnotes live2d KEY -o OUT
```

A Live2D (Cubism) model prefab as a Cubism runtime directory (`<name>` is the last part of `KEY`):

```
OUT/<name>.moc3                   the model's moc3
OUT/textures/*.png                atlas pages
OUT/<name>.prefab.json            the whole prefab: every GameObject and component, motions and expressions inlined
OUT/<exp>.exp3.json               expressions
OUT/<name>.physics3.json          physics
OUT/motions/<motion>.motion3.json motions (and motions/_fades.json)
OUT/<name>.model3.json            file references, EyeBlink / LipSync groups
```

Needs `[paths] apk` (component classes are resolved through the APK). Prints a JSON summary.

## spot

```
nnnotes spot SPOT_ID -o OUT
```

```
OUT/spot.json         master row, names, tap-talk episodes, situation settings, tap targets, Spine characters
OUT/spine/            Spine skeletons (.json or .skel), atlases, atlas page PNGs
OUT/room.glb          the spot's background as binary glTF (as `room`), with room.json
OUT/shaders/          shaders of the spot's bundles (as `shader`)
```

Needs `[paths] apk` and `[paths] master`.

## room

```
nnnotes room KEY -o OUT.glb
```

A spot background prefab as binary glTF: every mesh baked into prefab space, converted to glTF's right-handed space,
textures embedded, materials translated from the shader's render state. Objects inactive in the prefab are kept with
`extras.unityActive = false`. A summary (`meshCount`, materials, textures, samplers) is written next to it as
`OUT.json`.

## shader

```
nnnotes shader (--key KEY | --apk-bundle SUBSTRING [SUBSTRING ...]) -o OUT
```

Every Shader object in the bundle closure of `KEY`, or in the APK bundles whose file names contain the substrings
(each substring must match exactly one bundle; needs `[paths] apk`):

```
OUT/<name>.json                                    properties, subshaders, passes, render state, keywords
OUT/<name>/<platform>/s<S>p<P>_<stage>_<N>.<ext>   every compiled sub-program (GLSL ES as text, others as stored)
OUT/shaders.json                                   index with the keywords of every variant
```

## audio

```
nnnotes audio CUE_SHEET -o OUT [--format flac|ogg|wav]
```

Decodes the CRI cue sheet of the key `Cri/Sound/<CUE_SHEET>`: one file per stream (a name repeated within the
sheet gets `<name>_<stream>`), `cues.json` (first stream per name: file, sample rate, channels, samples, loop
points) and `streams.json` (every stream in order). `flac` (default) keeps the decoded PCM bit-exact; `ogg` is lossy.
The HCA keycode is read from `[paths] apk`.

## crikey

```
nnnotes crikey [--write DIR]
```

Looks up the CRI HCA keycode in the APK's boot data and prints whether one was found (and its number of digits,
not its value). `--write` writes it as `DIR/.hcakey` (8 bytes, big-endian), the file vgmstream reads next to its
input; `DIR` is created when missing.

## player

```
nnnotes player -o OUT.json
```

The game's render settings from the APK's boot data as JSON: `colorSpace`, `defaultPipeline`, `qualityLevels`,
`qualityPerPlatform`, `pipelines`, `renderers`, `postProcessData`. Needs `[paths] apk` and `[paths] dummy_dll`.

## live

```
nnnotes live MUSIC_ID -o OUT [--difficulty easy|normal|hard|expert] [--format flac|ogg|wav]
                             [--band BAND | --leader-card CARD_ID]
```

One chart (music + difficulty, default `expert`) as a self-contained directory, the format ournotes-player reads:

```
OUT/score/                   the chart as shipped, the converted runtime notes, master rows, summary.json
OUT/audio/<cueSheet>/        the BGM cue sheet decoded per cue + cues.json, streams.json
OUT/audio/live-audio.json    the live's sounds (BGM, note SE, live SE) with their CRI routing; their cue sheets
                             decoded next to the BGM
OUT/livescene/               scene graph, cameras, lane, background, start timeline, textures and shaders
OUT/livenotes/               note, line and effect prefabs, skins, clips, particle systems, textures and shaders
OUT/liveui/                  the start canvas: strings, fonts, materials, sprites
OUT/live.json                index of the above
```

The game takes the band of the background and start timeline from the player's deck centre. Without a deck the
band is `--band`, or the band of the character of `--leader-card` (a `MasterMemberCard` id), or by default the band
of the music's first vocal character; the choice is recorded in `livescene/scene.json`. Prints the index with a
summary.

## web

```
nnnotes web SITE (--pair MUSIC_ID:DIFFICULTY [--pair ...] | --all | --player-only | --reingest-json)
                  [--player DIR] [--format aac|opus|vorbis|mp3|flac] [--no-audio] [--force]
                  [--tmp DIR] [--workers N] [--band BAND | --leader-card CARD_ID]
```

Builds or updates a static [ournotes-player](https://github.com/empty-sekai/ournotes-player) site:

```
SITE/index.html, chart-list.js ...        the player's chart list page; ?music=<id>&difficulty=<d> plays one chart
SITE/ournotes-player.element.min.js       the player's built bundle (and its source map)
SITE/charts.json                          chart index: listing facts, manifest path, sizes
SITE/charts/<musicId>_<difficulty>.json   chart manifest: every path the player reads -> {asset, size}, or
                                          {parts: [[key, asset, size], ...], size} for a large JSON object
SITE/assets/<sha256>.<ext>                content-addressed files shared by all charts
```

- `--pair` (repeatable; `<musicId>:<difficulty>` or `<musicId>_<difficulty>`) adds the given charts; `--all` adds
  every music and difficulty that has a `MasterLiveMusicScore` row.
- A chart whose manifest exists is skipped unless `--force`. Assets no chart references are removed.
- `--player-only` rewrites the player files and `charts.json` only; `--reingest-json` stores every chart's JSON
  files again from the site's own assets (then rewrites the player files and `charts.json`).
- `--player`: the ournotes-player checkout (after its build) or installed package; it must contain
  `scripts/read-set.mjs`, `dist/ournotes-player.element.min.js` and `examples/chart-list/index.html`. The read-set
  script runs under Node.js to list the files the player reads for each chart; only those are stored.
- `--format` (default `aac`) is the BGM format; note SE, cheers and voices stay FLAC. `--no-audio` stores no audio
  (the player then runs the chart silent on its own clock).
- `--workers`: parallel music processes (default up to 5; `1` builds in this process). `--tmp`: directory for the
  temporary live builds (default `SITE.tmp`); the build directories in it are removed after use.
- `--band` / `--leader-card`: as for `live`, for every chart.

Same inputs give byte-identical outputs. Charts that fail are listed in the printed summary and in
`SITE.failures.json`, and the exit status is then 1.
