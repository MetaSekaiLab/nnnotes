# Commands

```
nnnotes [global options] <command> [command options]
```

Global options (before the command): `--config`, `--region`, `--language`, `--catalog`, `--cache`, `--master`,
`--apk`, `--ffmpeg`, `--vgmstream`, `--node`, plus `--version` and `--help`. They set the settings
described in [configuration.md](configuration.md), which also lists the settings each command needs.
`nnnotes <command> --help` prints the options of a command.

Commands that write files take the output path with `-o/--out` (required), except `web`, which takes the site
directory as its positional argument. JSON summaries on standard output are UTF-8. Every JSON file is written by one
writer: UTF-8, LF line endings, deterministic, non-finite numbers as `1e999` / `-1e999`.

A key, id or model the catalog or the master data does not have is a usage error: the command stops with exit
status 2 and a line naming it (`nnnotes <command>: error: ...`), before any bundle is fetched or file written.

## Cache

`pull` and every extractor read bundles through the cache (`[paths] cache`):

```
<cache>/catalog_main_<language>.bin     the catalog of the language, the same for every region (downloaded on first
                                        use unless [paths] catalog is set)
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

## servers

```
nnnotes servers [--show-hosts]
```

Reads the server list from the bootstrap API root (`[bootstrap] api`) with an anonymous call to the game's API and
prints `{servers: [{name, displayName, areaId, region, cdnRoots, apiRoots}]}`: per server its name, its area id, the
configured region (`[servers.<region>]` table) whose `cdn` or `api` is one of the server's roots, or `null`, and how
many alternative CDN and API roots the server list gives. `--show-hosts` adds the roots themselves (`cdn`, `api`:
lists), ready for the `cdn` and `api` settings of a region. The exit status is 1 when the call fails.

## master version

```
nnnotes master version
```

Asks the region's API root (`[servers.<region>] api`) for the master data version and the resource version the
region serves now (the game's API, anonymous) and prints `{region, masterVersion, resourceVersion}`. The client
version sent with the call is `[client] version`, else the `versionName` of `[paths] apk`. The exit status is 1 when
the call fails.

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
nnnotes master download (--version VERSION | --latest) -o OUT [--workers N]
```

Downloads master data version `VERSION`, or with `--latest` the version the region serves now (as
`master version`), from the region's CDN: `OUT/MasterManifest.json` and every `.bin` file it lists, each checked
against the manifest's SHA-256. Files already present with the right hash are kept.
`--workers` (default 16) downloads in parallel. Prints `{version, files, downloaded, kept, failed, out}`; the exit
status is 1 when a file failed or, with `--latest`, the version could not be fetched.

## adv

```
nnnotes adv ADV_ID -o OUT.json
```

One ADV episode as JSON: `advId`, `asset`, `commandCount`, `commands` (the episode's command list with the text,
sound and video rows resolved), `text` (every line in the five languages), `sounds`, `cuesheets`, `videos`,
`resources` (`kind`, `address`, `present` in the catalog), `master` (the `MasterAdv` row) and `title`.

`resources` lists the assets the game loads for the episode's command rows (rows marked `IgnoreData` load nothing;
cue sheets are in `cuesheets`): `live2d`, `stage`, `still`, `frame`, `effect`, `posteffect`, `timeline` and
`chatstamp` named by the row's asset name; `transition` for FadeIn / FadeOut rows (a row without a transition name
takes the player settings' default transition, which is read from `[paths] apk`); `talkwindow` for TalkWindow rows;
`chatwindow` and `chaticon` from the `MasterAdvChat` row of the chat rows' chat id; `video` from the episode's video
row of a Movie / Clip row's video id.

## story

```
nnnotes story ADV_ID -o OUT [--format flac|ogg|wav] [--no-audio] [--fonts open|game]
```

One ADV episode as a self-contained directory:

```
OUT/episode.json          as `adv`
OUT/live2d/<model>/       every Live2D model of the episode: moc3, atlas pages, full prefab
OUT/audio/<cueSheet>/     every cue sheet of the episode, one file per cue + cues.json, streams.json
OUT/scene.json            player graphics, cameras, ADV fields, volumes, settings and stages
OUT/frames.json           Frame prefabs by asset name: uGUI, Animator controllers and clips, UI particle systems
OUT/effects.json          particle effect prefabs by asset name, `instances` (effect name -> asset name)
OUT/posteffects.json      PostEffect volume profiles by asset name
OUT/stills.json           Still prefabs by asset name
OUT/talkwindows.json      talk window prefabs of the TalkWindow rows
OUT/chat.json             chat window prefabs, icons and stamps (sprites), the MasterAdvChat rows, shared chat texts
OUT/textures/, shaders/   textures and shaders of the scene and of the files above
OUT/videos/               Movie / Clip videos as WebM + videos.json (video id -> file, video row, size, frame rate)
OUT/ui/                   ADV front canvas UI: ui.json, packed textures, UI shaders, rule transitions
OUT/story.json            index of the above (a file the episode does not need is null and not written)
```

`--format` (default `flac`) is the audio format; `--no-audio` leaves the cue sheets undecoded (no `audio/`, `audio`
in story.json is empty). Videos keep the VP9 stream of the game's USM file and carry its ADX audio as Opus (FFmpeg,
`[paths] ffmpeg`); the USM streams are unmasked with the CRI key read from `[paths] apk`. TextMesh Pro text in
frames, talk windows and chat keeps its layout and style; with `--fonts open` its font and sprite assets and text
materials are only named (no atlas is written), with `--fonts game` they are exported. The command stops before
writing anything when the episode uses resources that are not in the catalog or resource kinds that are not
supported (`timeline`). Prints the index with a summary.

`--fonts` (default `open`) chooses how `ui/` handles text. Both modes write a text record `textStyle` for each text
node of `ui/ui.json`, and a document-level `textStyle` that holds the units and the line metrics of each font role.
With `open`, `ui/` contains no font data: no font atlases, glyph tables, font materials or text shader. `game` also
exports the game's TextMesh Pro fonts: per node the serialized text settings with the localized font and material
(`text`), and in the document the font assets reduced to the characters the episode shows (`fonts`), runtime glyphs,
text materials, `glyphCoverage`, `tmpSettings` and the text shader. `game` needs the optional `fonts` dependencies
(`pip install -e ".[fonts]"`).

The `ui/ui.json` text record (`textStyle` of a node; the node's `rect` and transform give its layout box):

| Field | Value |
|---|---|
| `class`, `enabled` | TMP component class, enabled flag |
| `fontRole` | font slot of the game's localized fonts: `primary` (main text face), `number` (Latin / numeral face) |
| `materialType` | material style name of the text (`Default`, `OutlineAdvCommon`, ...) |
| `localized`, `textKey` | LocalizeText enabled; its master text id, or null when the runtime sets the text (talk, speaker, location and title texts come from `episode.json`, one field per language) |
| `text`, `richText`, `parseControlCharacters` | serialized text, rich text tags on, `\n`-style escapes parsed |
| `fontSize`, `autoSize` | size in canvas units; auto-size `enabled`, `min`, `max`, `maxCharWidthAdjust`, `maxLineSpacingAdjust` |
| `fontStyle`, `fontWeight` | style flags (`bold`, `italic`, `underline`, `strikethrough`, `lowerCase`, `upperCase`, `smallCaps`, `superscript`, `subscript`, `highlight`), weight 100-900 |
| `alignment` | `horizontal` (`left`, `center`, `right`, `justified`, `flush`, `geometry`), `vertical` (`top`, `middle`, `bottom`, `baseline`, `geometry`, `capline`) |
| `wrapping`, `overflow` | `noWrap` / `normal` / `preserveWhitespace` / `preserveWhitespaceNoWrap`; `overflow` / `ellipsis` / `masking` / `truncate` / `scrollRect` / `page` / `linked` |
| `margin` | `left`, `top`, `right`, `bottom` insets of the rect |
| `lineSpacing` | `serialized`, `applied` (the value for the export language), `byLanguage` (the per-language line spacing LocalizeText applies; null when not localized) |
| `paragraphSpacing`, `characterSpacing`, `wordSpacing` | spacing in 1/100 em |
| `characterHorizontalScale`, `kerning`, `rightToLeft`, `orthographic` | as serialized |
| `color`, `colorMode`, `colorGradient`, `overrideHtmlColors` | vertex colour, gradient mode, four-corner gradient (null when off) |
| `face` | `color` (fill = `color` x `face.color`), `dilateEm`, `boldDilateEm` (glyph edge moved outwards), `softnessEm` (edge ramp width) |
| `outline` | null, or `color`, `widthEm` (band on each side of the glyph edge: stroke width 2 x `widthEm`, drawn over the fill), `softnessEm` |
| `underlay` | null, or `color`, `offsetEm` [x, y] (+x right, +y down), `dilateEm`, `softnessEm`, `inner` (shadow inside the glyph) |

`*Em` values are fractions of the drawn font size, converted from the text material's distance-field properties the
way the TMP shader applies them. The document-level `textStyle.roles` gives, per role, `lineHeightEm`, `ascentEm`,
`descentEm` (line pitch = `lineHeightEm` + `lineSpacing` / 100 em) and the font's `spacingOffset` / `boldSpacing`
(1/100 em) for the export language.

## live2d

```
nnnotes live2d MODEL -o OUT
```

A Live2D (Cubism) model prefab as a Cubism runtime directory. `MODEL` is the model's key
`Character/Live2D/<group>/<name>/model/<name>` or its id `<name>` (`nnnotes catalog --prefix Character/Live2D/` lists
the keys):

```
OUT/<name>.moc3                   the model's moc3
OUT/textures/*.png                atlas pages
OUT/<name>.prefab.json            the whole prefab: every GameObject and component, motions and expressions inlined
OUT/<exp>.exp3.json               expressions
OUT/<name>.physics3.json          physics (not written for a model without physics)
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

Needs `[paths] apk` and master data (`MasterHomeSpot`, `MasterText`, `MasterStoryHomeSpotTapTalkEpisode`). `room.glb` and
`room.json` are the `room` export of the spot's background.

A reference the situation prefab leaves empty is written as null: a Spine character with no `_animation` has null
`skeletonData`, `animation` and `world`, and a tap target with no `_focus` has a null `focusWorld`.

## room

```
nnnotes room KEY -o OUT.glb
```

A spot background prefab as binary glTF: every mesh baked into prefab space, converted to glTF's right-handed space,
textures embedded, materials translated from the shader's render state. Objects inactive in the prefab are kept with
`extras.unityActive = false`. A summary (`meshCount`, materials, textures, samplers) is written next to it as
`OUT.json`.

The render state is that of the shader's first pass for the material: blend factors (colour and alpha), blend
operations, colour mask, culling, depth write, depth test and alpha to mask. A state the shader takes from a
property (`Blend [_SrcBlend] [_DstBlend]`, `Cull [_Cull]`, `ZWrite [_ZWrite]`, ...) is the material's value of that
property, else the shader's default for it. Each glTF material keeps the resolved state in
`extras.unityRenderState` (and `OUT.json` in `materials[].renderState`); a shader or a state glTF cannot express
stops the command. A submesh whose material slot is empty is not written (there is no material to draw it with): the
summary lists each as `nullMaterialSubmeshes` (`mesh`, object `path` in the prefab, `submesh` index), and a mesh
left with no submesh is not written. A MeshFilter with no mesh, or a mesh with no triangles (no index data), draws
nothing and is not written; the summary lists the object path in `nullMeshFilters`, or the mesh and object path in
`meshesWithoutTriangles`.

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
`qualityPerPlatform`, `pipelines`, `renderers`, `postProcessData`. Needs `[paths] apk`. The MonoBehaviours are read
with the type trees that ship with nnnotes; an APK whose classes do not match them stops the command with exit
status 2 and a line naming the class, the game version and the Unity version (as `story`, `live` and `web`).

## live

```
nnnotes live MUSIC_ID -o OUT [--difficulty easy|normal|hard|expert] [--format flac|ogg|wav]
                             [--fonts open|game] [--band BAND | --leader-card CARD_ID]
```

One chart (music + difficulty, default `expert`) as a self-contained directory, the format ournotes-player reads:

```
OUT/score/                   the chart as shipped, the converted runtime notes, master rows (title and band
                             names in every language), summary.json
OUT/audio/<cueSheet>/        the BGM cue sheet decoded per cue + cues.json, streams.json
OUT/audio/live-audio.json    the live's sounds (BGM, note SE, live SE) with their CRI routing; their cue sheets
                             decoded next to the BGM
OUT/livescene/               scene graph, cameras, lane, background, start timeline, textures and shaders
OUT/livenotes/               note, line and effect prefabs, skins, clips, particle systems, textures and shaders
OUT/liveui/                  the start canvas in [catalog] language: strings, text records or fonts, sprites
OUT/live.json                index of the above
```

The start canvas follows the client language `[catalog] language` (`ja`, `en`, `zh-Hant`, `zh-Hans` or `ko`): its
text table column, fonts and line spacing. `--fonts open` (default) writes each text's layout and style (size,
alignment, wrapping, spacing, colours, outline / underlay from its material) and only the names of the game's fonts
and materials; `--fonts game` also writes the game's TextMesh Pro fonts reduced to the characters shown, with their
atlas textures (needs the optional `fonts` dependencies: `pip install 'nnnotes[fonts]'`).

The game takes the band of the background and start timeline from the player's deck centre. Without a deck the
band is `--band`, or the band of the character of `--leader-card` (a `MasterMemberCard` id), or by default the band
of the music's first vocal character; the choice is recorded in `livescene/scene.json`. Prints the index with a
summary.

## web

```
nnnotes web SITE [--pair MUSIC_ID:DIFFICULTY [--pair ...] | --all] [--live2d MODEL [--live2d ...] | --all-live2d]
nnnotes web SITE (--player-only | --reingest-json)
                  [--player DIR] [--format aac|opus|vorbis|mp3|flac] [--no-audio] [--force]
                  [--tmp DIR] [--workers N] [--read-workers N] [--band BAND | --leader-card CARD_ID]
                  [--region REGION [--region ...] | --all-regions] [--fonts open|game]
```

Builds or updates a static [ournotes-player](https://github.com/empty-sekai/ournotes-player) site of charts, Live2D
models or both:

```
SITE/index.html, chart-list.js ...        the player's chart list page; ?music=<id>&difficulty=<d> plays one chart
SITE/ournotes-player.element.min.js       the player's built bundle (and its source map)
SITE/charts.json                          chart index: listing facts (texts in every language), regions,
                                          manifest path, sizes
SITE/charts/<musicId>_<difficulty>.json   chart manifest: every path the player reads -> {asset, size}, or
                                          {parts: [[key, asset, size], ...], size} for a large JSON object
SITE/charts/<region>/<id>.json            the manifest of a region whose chart files differ (see Regions)
SITE/models.json                          Live2D model index: id, key, group, canvas, manifest path, sizes
SITE/models/<id>.json                     model manifest, the same entry forms as a chart manifest
SITE/live2d/                              the player's Live2D model page and its bundle (when the player has them)
SITE/assets/<sha256>.<ext>                content-addressed files shared by all charts and models
```

- `--pair` (repeatable; `<musicId>:<difficulty>` or `<musicId>_<difficulty>`) adds the given charts (a chart that
  no region of the site has a `MasterLiveMusicScore` row for is a usage error); `--all` adds every music and
  difficulty that has a `MasterLiveMusicScore` row (in the master data of any of the site's regions).
- `--live2d` (repeatable; a model id `<name>` or key `Character/Live2D/<group>/<name>/model/<name>`) adds the given
  Live2D models; `--all-live2d` adds every model key of the catalog. A model's id is its `<name>`. Its manifest lists
  the files the player's Live2D viewer reads: `model.json` (index: moc3, prefab, textures, shader index and the Cubism
  mask materials), the moc3 and prefab as `live2d` writes them, the atlas pages the drawables use, and the GLSL ES 3.00
  programs of the Live2D shaders that the drawables' materials select (the mask shader only for a model with masked
  drawables). Charts and models can be added in one run; models are built first.
- A chart or model whose manifest exists is skipped unless `--force`. Assets no chart and no model references are
  removed.
- `--player-only` rewrites the player files, `charts.json` and `models.json` only; `--reingest-json` stores every
  chart's and model's JSON files again from the site's own assets (then rewrites the player files and the indexes).
- `--player`: the ournotes-player checkout (after its build) or installed package; it must contain
  `scripts/read-set.mjs`, `dist/ournotes-player.element.min.js` and `examples/chart-list/index.html`. The read-set
  script runs under Node.js to list the files the player reads for each chart; only those are stored. When it also has
  `examples/live2d/index.html`, that page and `dist/ournotes-player.live2d.element.min.js` go to `SITE/live2d/`.
- `--format` (default `aac`) is the BGM format; note SE, cheers and voices stay FLAC. `--no-audio` stores no audio
  (the player then runs the chart silent on its own clock).
- `--workers`: parallel music processes (default a quarter of the CPUs, up to 8) and model processes (default up
  to 4); `1` builds in this process. `--read-workers`: chart read sets run at a time, each a Node.js process
  (default half the CPUs, up to 16).
- `--tmp`: directory for the temporary live and model builds (default `SITE.tmp`); the build directories in it are
  removed after use. `SITE.tmp/cache/` is kept: decoded cue sheets, encoded PNGs, shader dumps and read sets, each
  stored under a hash of everything it was made from (input bytes, settings, the external tools and libraries in use
  and nnnotes' own code), so a later build with the same `--tmp` reuses what is unchanged and writes the same files
  faster. Deleting it is
  safe at any time outside a build; the next build then makes everything again.
- `--band` / `--leader-card` / `--fonts`: as for `live`, for every chart (the site stores no start canvas files).
- Models need `[paths] apk` (component classes and the mask materials are read from the APK), not `[paths] master`.
- `--region` (repeatable) / `--all-regions`: the regions the site serves (see Regions); default: the one
  `[catalog] region`. This `--region` follows the command name (`nnnotes web SITE --region tw --region kr`); the
  global `--region` before it sets `[catalog] region`.

### Regions and languages

One site serves several regions and every language:

- The regions serve the same catalog for a language, so a chart's files depend on the region's master data only.
  Regions whose chart tables (the master tables the chart build reads) are identical share one manifest,
  `charts/<id>.json`, built once from the first region's data. A region with other chart tables is built on its own
  into `charts/<region>/<id>.json`; such a manifest whose files equal the shared one's is dropped and the region joins
  the shared manifest. Models read no master data: one build serves every region.
- Each region's master data: `[servers.<region>] master` (else `[paths] master`, for at most one of the regions; the
  global `--master` flag is refused with more than one region). A region offers the charts its master data has a
  `MasterLiveMusicScore` row for.
- Chart files come from the catalog of `[catalog] language`; the listing texts come from the text tables in every
  language: a manifest's `chart` has `title` and `bands` in `[catalog] language` (`language`), and `titles` /
  `bandNames` in `ja`, `en`, `zh-Hant`, `zh-Hans` and `ko`. The manifest's `regions` lists the regions it serves;
  regions accumulate over builds (a skipped chart gains the regions of the build), so a region is dropped by building
  the site again from scratch.
- `charts.json` has one entry per manifest (an id appears once per region) with `regions`, `titles`, `bandNames`,
  and at the top `language` (the default listing language: `[catalog] language` of the latest build), `languages`
  and `regions` (`id`, `name` from `[servers.<region>] name`, `languages` from `[servers.<region>] languages`). The
  chart list page switches with `?region=<id>&lang=<language>`.

Same inputs give byte-identical outputs. Charts that fail are listed in the printed summary and in
`SITE.failures.json`, models that fail in the summary and in `SITE.model-failures.json`; the exit status is then 1.
