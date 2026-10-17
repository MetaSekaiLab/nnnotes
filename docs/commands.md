# Command outputs

```
nnnotes <group> <command> [options]
```

Global options (before or after the command name): `--config`, `--region`, `--language`, `--catalog`,
`--catalog-version`, `--depot`, `--master`, `--apk`, `--ffmpeg`, `--vgmstream`, `--node` and `--json`,
plus `--version` and `--help`. They set the settings described in [configuration.md](configuration.md), which also
lists the settings each command needs. `nnnotes <group> <command> --help` prints the options of a command;
[cli.md](cli.md) lists every command with its options. The asset export commands (`export assets`, `depot plan`,
`depot run`, `depot verify` and the catalog versions of the depot) are described in [assets.md](assets.md).

Commands that write files take the output path with `-o/--out` (required). A command prints a short summary on
standard output; with `--json` it prints its report as JSON (UTF-8). Every JSON file is written by one
writer: UTF-8, LF line endings, non-finite numbers as `1e999` / `-1e999` (a NaN is an error). The output is
deterministic for a given installation: the same inputs with the same versions of nnnotes, its Python dependencies
and the external tools give byte-identical files. Other versions can encode the same content into other bytes (a
PNG written by another Pillow version can differ in its bytes while its pixels are equal).

nnnotes sets `OPENBLAS_NUM_THREADS=1` unless the environment sets it: numpy's OpenBLAS otherwise starts one busy
thread per CPU in every process, and no command uses its parallelism.

A key, id or model the catalog or the master data does not have is a usage error: the command stops with exit
status 2 and a line naming it (`nnnotes <command>: error: ...`), before any bundle is fetched or file written.

## config

```
nnnotes config init [--user | --file F] [--set SECTION.KEY=VALUE ...] [--no-input] [--force]
nnnotes config set SECTION.KEY VALUE [--user | --file F]
nnnotes config unset SECTION.KEY [--user | --file F]
nnnotes config check [--json]
nnnotes config path [--json]
```

Write the config file from the template (asking in a terminal, else from `--set`), set or empty one value in place,
check every setting's origin and format, and list where the config file is looked up. No `config` command prints a
setting's value. Details in [configuration.md](configuration.md#writing-the-config-file).

## Downloads

The commands read the game files from the depot's `downloads/` directory (`[paths] depot`), which keeps the files
under their own names:

```
<depot>/downloads/catalog_main_<language>.bin   the main catalog (explicit main or no API/version pin)
<depot>/downloads/bundles/<bundle file name>    bundles of the dependency closures, decrypted (UnityFS)
<depot>/downloads/raw/<path>                    raw CDN files stored as they are (e.g. CRI cue sheet data)
<depot>/downloads/catalogs/<region>/            catalogs downloaded by `catalog browse`
<depot>/downloads/international/<CDN SHA256>/<resource version>/
                                                versioned catalog, bundles and raw files of one CDN and version
```

A file is downloaded when a command first needs it and read from `downloads/` while it is there. When `[paths] apk`
is set, the APK's own catalog is merged with the region's and bundles that ship inside the APK are read from it.

The bundles and raw files are a cache bounded by `[depot] downloads`. Every command that reads the depot holds a
lease in `<depot>/leases/` while it runs; when the last running command ends, the bundles and raw files used least
recently are removed until the rest fit the budget:

| `[depot] downloads` | kept once no command runs |
|---|---|
| unset or `0` | no bundles or raw files |
| a size, e.g. `20GiB` | the most recently used ones, up to that size |
| `all` | every one |

Catalog files are kept. A removed file is downloaded again when a command needs its bytes. The depot records the
identity (sha256 and size) of every bundle and raw file it reads, so `export assets` plans with files whose name
carries the catalog's hash without downloading them again.

Commands may run at the same time on one depot: no command removes downloads while another one holds a lease, and a
command that starts while one trims waits for it. The lease of a process that ended without releasing it is dropped
by the next command; a lease written on another host counts for a day.

For international regions with a configured API, catalog-based commands discover `resource_version` first and
download `catalog_<resource_version>_<language>.bin`. `catalog fetch` labels the bytes with the version used to
select that file, rather than querying a newer label after downloading main. Use `--catalog-version VERSION` or
the [version settings](configuration.md) to pin builds and use downloaded data offline. An explicit `--catalog FILE`
bypasses discovery. JP keeps its existing Version-driven path.

## catalog keys

```
nnnotes catalog keys [--prefix PREFIX] [--limit LIMIT]
```

Prints the addressable keys that start with `PREFIX` (default: all), sorted, at most `LIMIT` (default 200), one per
line; the total count goes to standard error.

## catalog browse

```
nnnotes catalog browse [--port PORT] [--host HOST]
```

Serves a local web page on `HOST:PORT` (default `127.0.0.1:8000`) that lists every configured region, its catalog
languages, and per catalog the asset paths as directories. A link under an asset path downloads the bundle holding
it, decrypted; `bundles/<bundle key>` lists every bundle directly.

## bundle pull

```
nnnotes bundle pull KEY [KEY ...] -o DIR
```

Fetches the bundle closure (the key's location and all its dependencies) of each key, copies the decrypted bundles
to `DIR` under their file names, and prints the path of every copied bundle.

## servers

```
nnnotes servers [--show-hosts]
```

Reads the server list from the bootstrap API root (`[bootstrap] api`) with an anonymous call to the game's API and
reports `{servers: [{name, displayName, areaId, region, cdnRoots, apiRoots}]}`: per server its name, its area id, the
configured region (`[servers.<region>]` table) whose `cdn` or `api` is one of the server's roots, or `null`, and how
many alternative CDN and API roots the server list gives. `--show-hosts` adds the roots themselves (`cdn`, `api`:
lists), ready for the `cdn` and `api` settings of a region. The exit status is 1 when the call fails.

## master version

```
nnnotes master version
```

Asks the region's API root (`[servers.<region>] api`) for the master data version and the resource version the
region serves now (the game's API, anonymous) and reports `{region, masterVersion, resourceVersion}`. The client
version sent with the call is `[client] version`, else the `versionName` of `[paths] apk`. The exit status is 1 when
the call fails.

## master decode

```
nnnotes master decode INPUT [INPUT ...] -o OUT [--workers N]
```

`INPUT`: master data files, or directories whose `*.bin` files are decoded. Each file (64-byte prefix, then
Rijndael-256 CBC with PKCS7 padding over gzip-compressed JSON) is written as `OUT/<file stem>.json` (UTF-8, one-space
indent, non-finite numbers as `1e999` / `-1e999`; a table with a NaN fails). `--workers` (default: the CPUs the process may use; `0`: one at a time) decodes in parallel. The report is
`{decoded, failed: [{file, error}], out}`; the exit
status is 1 when a file failed.

## master download

```
nnnotes master download (--version VERSION | --latest) -o OUT [--fetch-workers N]
```

Downloads master data version `VERSION`, or with `--latest` the version the region serves now (as
`master version`), from the region's CDN: `OUT/MasterManifest.json` and every `.bin` file it lists, each checked
against the manifest's SHA-256. Files already present with the right hash are kept.
`--fetch-workers` (default 16) downloads in parallel. The report is `{version, files, downloaded, kept, failed, out}`;
the exit
status is 1 when a file failed or, with `--latest`, the version could not be fetched.

## export episode

```
nnnotes export episode ADV_ID -o OUT.json
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

## export story

```
nnnotes export story ADV_ID -o OUT [--format FORMAT] [--flac-level N] [--stored-catalog LABEL|SHA] [--workers N]
              [--story-languages LANG,...] [--fonts open|game] [--font LANG=PATH]
              [--link auto|clone|hard|copy] [--explain] [--dry-run]
```

One ADV episode and the products it plays with, in the `product` layout ([products](products.md#stories)):

```
OUT/stories/<id>.json          the story (`story`): episode.json and story.json
OUT/storyscenes/default.json   the ADV scene (`story.scene`)
OUT/storyassets/<name>.json    each stage, frame, effect, post effect, still, timeline, transition, chat window,
                               chat icon and stamp the episode loads (`story.asset`; <name>: its key, "/" as "~")
OUT/models/<id>.json           each Live2D model of the episode (`live2d.model`)
OUT/cuesheets/<sheet>.json     each cue sheet of the episode, decoded (`story.sound`)
OUT/videos/<name>.json         each video of the episode, as WebM (`story.video`; <name>: its asset, "/" as "~")
OUT/storychat/default.json     the shared chat rows, when the episode shows chat (`story.chat`)
OUT/storyui/<lang>.json        the story UI in each requested language (`story.ui`)
OUT/storyfonts/<id>_<lang>.json the episode's font records and glyph pages (`story.fonts`)
OUT/crilips/default.json       the CRI Lips analysis data, when a voice can reach it (`story.crilips`)
OUT/assets/<sha256>.<ext>      the files the manifests name
```

`episode.json` is the episode as `export episode` writes it, each resource given by its kind and address.
`story.json` names the episode's chat rows, its effect instances and, in `parts`, the manifest of each product above
it plays with. The command runs twice: it asks for the story, reads its `story.json`, then asks for the story and
every product `parts` names and places them. It stops with a usage error before the second run when a key of such a
product is not in the catalog. The episode's talk windows are listed in `episode.json` `resources`. The videos are
the `movie.webm` of `cri.movie` with `format` `webm`: VP9 as stored, re-encoded when the video is in another codec or
has an alpha stream.

A product several stories play with (the scene, a stage, a model, a cue sheet) is one product: stories exported into
one directory share its manifest and files, and the depot keeps every result for the next export.

`--story-languages` selects text languages, separated by commas; the default is `[catalog] language`.
`--fonts open` (the default) generates glyph pages from the font files in `[paths.fonts]` or the repeatable
`--font LANG=PATH` flags. Supply each requested language's font and any fonts needed by its fallback chain.
`--font emoji=PATH` supplies a bitmap emoji font. Without it, open emoji sprites retain their layout with empty
images. `--fonts game` uses the client's TMP glyphs and generates the runtime glyphs its font assets require.
Both sources use font settings from the client's assets and require the `fonts` extra. Font files are explicit
content-addressed inputs, so changing their bytes rebuilds the corresponding font product.

`--format` (default `aac`) is the web audio format of the cue sheets: `aac` (AAC-LC in MP4, `cues.json` records each
file's encoder delay), `opus`, `vorbis`, `mp3`, or `flac` (the decoded FLAC as it is); `--flac-level` is the FLAC
compression level of the decoded sounds (as for `export audio`). `crilips/` holds the weights of the game's CRI Lips
mouth analysis, read from the CRI Lips library in `[paths] apk` (or in the arm64-v8a split next to it,
`split_config.arm64_v8a.apk`); `story.json` names it when a voice of the episode is lip-synced, and the models'
MotionSync (model.json `motionSync`) decides whether a player runs the analysis.

The shared chat sound rows (`chat.json` `sounds` and `cueSheets` of `storychat/default.json`) name the cue sheet
`AdvSe_Common`. In the version 1.0.1 data nothing holds it: no catalog language (`ja`, `en`, `zh-Hant`, `zh-Hans`,
`ko`) has a key `Cri/Sound/AdvSe_Common` or `EmbCri/Sound/AdvSe_Common` (remote or APK catalog), the APK has no ACB
file of that name, and it is not a Resources asset. The story therefore has no audio for these rows; the rows are
kept as they are. Every cue sheet of the episodes' own `-SoundCueSheet` shards has its `Cri/Sound/` key.

## export live2d

```
nnnotes export live2d MODEL -o OUT [--player]
```

A Live2D (Cubism) model as the Cubism SDK files other tools read. `MODEL` is the model's key
`Character/Live2D/<group>/<name>/model/<name>` or its id `<name>` (`nnnotes catalog keys --prefix Character/Live2D/` lists
the keys):

```
OUT/<name>.moc3                   the model's moc3
OUT/textures/<page>.png           the atlas pages the drawables draw with, by name
OUT/<name>.physics3.json          physics (not written for a model without physics)
OUT/<exp>.exp3.json               expressions
OUT/motions/<motion>.motion3.json motions
OUT/<name>.model3.json            file references, the model's fade times per motion, EyeBlink / LipSync groups
```

With `--player`, the model the player reads instead: the product layout of `live2d.model`, `OUT/models/<name>.json`
and the files it names under `OUT/assets/` ([products](products.md#live2d-models)).

The command runs the depot's pipeline for the model alone (the census of the bundles of its key, the objects its
prefab reaches, the product) like `export assets`, and writes the same reports in `OUT/_reports/`; it takes
`--stored-catalog`, `--workers`, `--link`, `--explain` and `--dry-run` as `export assets` does. Needs `[paths] apk`
(the component classes and the mask materials are in the APK).

## export spot

```
nnnotes export spot SPOT_ID -o OUT
```

```
OUT/spot.json         master row, names, tap-talk episodes, situation settings, tap targets, Spine characters
OUT/spine/            Spine skeletons (.json or .skel), atlases, atlas page PNGs
OUT/room.glb          the spot's background as binary glTF (as `export room`)
OUT/room.json         its summary
```

The spot whose `MasterHomeSpot` row has the id `SPOT_ID`: the products `spot.files` of its situation
(`_situationAssetPath`) and `room.gltf` of its background (`_backgroundAssetPath`), their files at their own paths
([products](products.md#spots)). The command runs the depot's pipeline for the two products alone, as `export live2d`
does, writes the same reports in `OUT/_reports/` and takes the same options. Needs `[paths] apk` (the Spot and Spine
component classes are in the APK) and master data (`MasterHomeSpot`, `MasterText`,
`MasterStoryHomeSpotTapTalkEpisode`).

## export room

```
nnnotes export room KEY -o OUT
```

A spot background as binary glTF, `OUT/room.glb`, with its summary `OUT/room.json`: the product `room.gltf` of the
background key `KEY` (`Spot/<place>/Background/<place>`; [products](products.md#spots)). Runs and reports as
`export spot`.

## export shader

```
nnnotes export shader (--key KEY | --apk-bundle SUBSTRING [SUBSTRING ...]) -o OUT
```

Every Shader object in the bundle closure of `KEY`, or in the APK bundles whose file names contain the substrings
(each substring must match exactly one bundle; needs `[paths] apk`):

```
OUT/<name>.json                                    properties, subshaders, passes, render state, keywords
OUT/<name>/<platform>/s<S>p<P>_<stage>_<N>.<ext>   every compiled sub-program (GLSL ES as text, others as stored)
OUT/shaders.json                                   index with the keywords of every variant
```

## export audio

```
nnnotes export audio CUE_SHEET -o OUT [--format flac|ogg|wav] [--flac-level N]
```

Decodes the CRI cue sheet of the key `Cri/Sound/<CUE_SHEET>`: one file per stream (a name repeated within the
sheet gets `<name>_<stream>`), `cues.json` (first stream per name: file, sample rate, channels, samples, loop
points) and `streams.json` (every stream in order). `flac` (default) keeps the decoded PCM bit-exact; `ogg` is lossy.
`--flac-level` (0 to 12, default 8) is ffmpeg's FLAC compression level; every level decodes to the same samples.
The HCA keycode is read from `[paths] apk`.

## voices

```
nnnotes voices list [--from OUT] [--character C] [--category C] [--source S] [--episode ADV] [--status S]
                    [--limit N]
nnnotes voices search TEXT [--from OUT] [--search-language L] [--character C] [--category C] [--source S]
                           [--episode ADV] [--status S] [--limit N]
nnnotes voices summary [--from OUT]
nnnotes voices get ID -o DIR [--from OUT] [--format flac|ogg|wav] [--flac-level N]
```

Queries the voices index ([views.md](views.md#voices)): the character voices the master data names and the voices
of the story episodes, with their source row, characters (for story voices, inferred from the speaker), category,
five-language text, cue sheet and cue. `--from OUT` reads `OUT/views/voices.json`, written by
`nnnotes export assets --views voices` (or `all`) with the exported audio and episodes (`--select key:Adv/Episode/`): then
every voice also has its status and the files of its streams. Without `--from` the index is built from the
configured master data and catalog: content and catalog keys only, every present voice `not-exported`; story
voices only for the episode of `--episode` (or of a `get` row id), read from the catalog.

- `--character`: a MasterCharacter id, or a part of a character's name in any language (full, short or English
  display name, case ignored). `--category`: a category (`CharacterRankUp`; case, `-` and `_` ignored, so
  `character-rank-up` works), `Source.Category` (`Story.Main`) or a source (`Talk`). `--source`: `Talk`,
  `CharacterVoice`, `MemberCard`, `LiveCharacter`, `LiveGekisouVoice`, `LiveDialogueCommon`,
  `LiveDialogueFixedPair`, `LiveStartCharacterVoice`, `HomeSpot`, `Title`, `Sound` or `Story`. `--episode`: the
  story voices of one episode, by MasterAdv id or episode asset. `--status`: a status of the index.
- `list` and `search` print one line per voice: id, characters (story voices: the speaker's name in `--language`),
  `Source.Category`, `sheet/cue`, status and the text in the global `--language` (default `[catalog] language`);
  `--json` prints the rows. `search TEXT` matches a part of the text in `--search-language` (every language when not
  given) or of the cue name, case ignored.
- `summary`: rows per status, per source and category and per character, the gaps (voices whose sheet has no key
  or whose decoded sheet lacks the cue, episodes without a key) and the unreferenced keys and streams; for the
  story episodes, their number per status and kind, the rows and inferred characters per speaker name, and the
  story voices whose cue a row of another source also names.
- `get ID`: `ID` is a row id (`MasterTalk:561`, `MasterAdv:<adv id>:<command index>`) or a sound id. Writes the
  files of the voice's streams into `DIR`, named as the export names them, and `voice.json` (its rows and files).
  With `--from` and the files exported, they are copied; otherwise (or with `--flac-level`) the voice's cue sheet
  alone is decoded as `nnnotes export audio` does (`--format`, `--flac-level`, default 8) and the cue's streams are
  picked by the ACB's cue table. Exit
  status 1 when the voice has no file (its status says why).

## apk hca-key

```
nnnotes apk hca-key [--write DIR]
```

Looks up the CRI HCA keycode in the APK's boot data and prints whether one was found (and its number of digits,
not its value). `--write` writes it as `DIR/.hcakey` (8 bytes, big-endian), the file vgmstream reads next to its
input; `DIR` is created when missing.

## apk settings

```
nnnotes apk settings -o OUT.json
```

The game's render settings from the APK's boot data as JSON: `colorSpace`, `defaultPipeline`, `qualityLevels`,
`qualityPerPlatform`, `pipelines`, `renderers`, `postProcessData`. Needs `[paths] apk`. The MonoBehaviours are read
with the type trees that ship with nnnotes; an APK whose classes do not match them stops the command with exit
status 2 and a line naming the class, the game version and the Unity version (as `export story` and `site build`).

## export live

```
nnnotes export live MUSIC_ID -o OUT [--difficulty easy|normal|hard|expert] [--format aac|flac|mp3|opus|vorbis]
                             [--flac-level N] [--band BAND | --leader-card CARD_ID]
                             [--live-option OPTION[=VALUES] ...]
                             [--live-languages LANG,...] [--fonts open|game] [--font LANG=PATH]
```

One chart (music + difficulty, default `expert`) and the products it plays with, in the product layout
([products](products.md#lives)):

```
OUT/lives/<musicId>_<difficulty>[_<band>].json  the live: the chart as shipped, its converted notes (also mirrored),
                                                the music's master rows, the jacket, the manifest paths of the parts
OUT/scenes/default.json                         the live scene: scene graph, cameras, lane, UI assets, the start
                                                timeline and LightWeight background of every band, the built-in
                                                graphics settings
OUT/notes/default.json                          the note highway: note, line and lane effect prefabs, settings
OUT/noteskins/<skin>.json                       a note skin
OUT/noteeffects/<set>.json                      a note effect set
OUT/sounds/<sets>.json                          the note and live sounds with their CRI routing and volumes
OUT/music/<musicId>.json                        the BGM in a web format
OUT/liveui/<musicId>_<difficulty>_<lang>.json    localized start-canvas text, glyph pages and image bindings
OUT/assets/<sha256>.<ext>                       every file the manifests name, once
```

`--format` (default `aac`) is the web format of the BGM: `aac` (AAC-LC in MP4), `opus`, `vorbis`, `mp3`, or `flac`,
the decoded FLAC itself. `--flac-level` is the FLAC compression level the cue sheets are decoded at (every level
decodes to the same samples).

The game takes the band of the background and start timeline from the player's deck centre. Without a deck the
band is `--band`, or the band of the character of `--leader-card` (a `MasterMemberCard` id), or by default the band
of the music's first vocal character; `live.json` `band` records the choice, and the live's manifest path holds the
band only when one of the two flags is given. A band whose start timeline or background the catalog does not hold is
a usage error.

`--live-languages` selects UI languages separated by commas (`all` selects all client languages); the default is
`[catalog] language`. `--fonts open` (default) generates glyph pages from `[paths.fonts]` or repeatable
`--font LANG=PATH` flags. Supply fonts for the requested language and its fallback languages. `--fonts game` uses
the client's TMP assets. Both modes require the `fonts` extra and use the client's font metrics and layout
settings. Font bytes participate in the task key. The UI's canvas and difficulty icons are in `live.scene`, and
its jacket and text shaders are in the UI product's object table. `live.json` names UI products by language in
`parts.ui`.

The products hold what a live reads with the game's default options (option preset 1 of a fresh profile), and always
the mirrored notes, the bar line view and the option tables. `--live-option` (repeatable) adds the products of other
values of the options that select files, so that a player can offer them; the options are named as in the game (the
names ournotes-player's settings use):

| `--live-option` | Adds |
|---|---|
| `NoteDesignId` | the other note skins (`MasterLiveNoteSkin`): a `live.noteskin` product each |
| `NoteEffectId` | the other note effect sets (`MasterLiveNoteEffectSkin`; the lane effects are the same for every set): a `live.noteeffect` product each |
| `LiveQuality` | the qualities of `MasterLiveQualitySettings`; at the Low quality (2) the game loads the `<name>Light` variant of a note effect set where the catalog has one (else the set itself), whose `live.noteeffect` products are added |
| `NoteSePatternId` | the other note sound sets (`MasterLiveNoteSe` groups): the `live.sounds` product `NoteSePatternId=<ids>`, which holds them and the default set |
| `MirrorChart`, `MeasureLineDisplay`, `defaults` | nothing: the products always hold their files |
| `all` | every option above with every value |

An option without values offers every value the master data has; `NAME=v,v,...` offers those values (the default
value is always included; a value the master data does not have is a usage error).

The command runs the depot's pipeline for these products alone, as `export live2d` does, writes the same reports in
`OUT/_reports/` and takes `--stored-catalog`, `--workers`, `--link`, `--explain` and `--dry-run` as `export assets`
does. Needs `[paths] apk` (the built-in graphics and sound settings and the CRI ACF are in the APK), master data,
`vgmstream` and `ffmpeg`.

## site build

```
nnnotes site build -o SITE [--pair MUSIC_ID:DIFFICULTY [--pair ...] | --all] [--live2d MODEL [--live2d ...] | --all-live2d]
                 [--story ADV_ID [--story ...] | --all-stories] [--story-languages LANG,...] [--font LANG=PATH ...]
nnnotes site build -o SITE (--player-only | --reingest-json)
                  [--player DIR] [--format aac|opus|vorbis|mp3|flac] [--no-audio] [--compress gzip|br|none]
                  [--force]
                  [--tmp DIR] [--workers N] [--read-workers N] [--band BAND | --leader-card CARD_ID]
                  [--site-region REGION [--site-region ...] | --all-regions] [--fonts open|game]
                  [--live-option OPTION[=VALUES] ...]
```

Builds or updates a static [ournotes-player](https://github.com/empty-sekai/ournotes-player) site of charts, Live2D
models, stories or any of them:

```
SITE/index.html, chart-list.js ...        the player's chart list page; ?music=<id>&difficulty=<d> plays one chart
SITE/ournotes-player.element.min.js       the player's built bundle (and its source map)
SITE/charts.json                          chart index: listing facts (texts in every language), regions,
                                          manifest path, sizes
SITE/charts/<musicId>_<difficulty>.json   chart manifest: every path the player reads -> {asset, size[, stored]},
                                          or {parts: [[key, asset, size[, stored]], ...], size} for a large JSON
                                          object
SITE/charts/<region>/<id>.json            the manifest of a region whose chart files differ (see Regions)
SITE/models.json                          Live2D model index: id, key, group, canvas, manifest path, sizes; with
                                          master data the character and its names
SITE/models/<id>.json                     model manifest, the same entry forms as a chart manifest
SITE/live2d/                              the player's Live2D model page and its bundle (when the player has them)
SITE/stories.json                         story index: titles and story groups in every language, commands,
                                          languages, manifest path, sizes, regions
SITE/stories/<advId>.json                 story manifest: the common files and one file group per language, the
                                          same entry forms as a chart manifest; the model manifests of its models
                                          and the path to the site root
SITE/stories/<region>/<advId>.json        the manifest of a region whose story files differ (see Regions)
SITE/story/                               the player's story page and its bundle (when the player has them)
SITE/assets/<sha256>.<ext>[.gz|.br]       content-addressed files shared by all charts, models and stories
                                          (see --compress)
```

- `--pair` (repeatable; `<musicId>:<difficulty>` or `<musicId>_<difficulty>`) adds the given charts (a chart that
  no region of the site has a `MasterLiveMusicScore` row for is a usage error); `--all` adds every music and
  difficulty that has a `MasterLiveMusicScore` row (in the master data of any of the site's regions).
- `--live2d` (repeatable; a model id `<name>` or key `Character/Live2D/<group>/<name>/model/<name>`) adds the given
  Live2D models; `--all-live2d` adds every model key of the catalog. A model's id is its `<name>`. Its manifest lists
  the files the player's Live2D viewer and story page read: `model.json` (index: moc3, prefab, textures, shader index,
  the Cubism mask materials and `motionSync`, whether the prefab's root has the MotionSync controller with its CRI
  audio input), the moc3 and prefab as `export live2d` writes them, the atlas pages the drawables use, and the GLSL ES 3.00
  programs of the Live2D shaders that the drawables' materials select, each keyword set also with
  `_ADDITIONAL_LIGHTS_VERTEX` (the story player adds it at quality 4), and the mask shader's for a model with masked
  drawables. Charts and models can be added in one run; models are built first.
- `--story` (repeatable; a `MasterAdv` id) adds the given story episodes (an id no region of the site has is a usage
  error); `--all-stories` adds every `MasterAdv` episode. See [Stories](#stories) below. Stories are built after
  models and charts; the Live2D models the stories to build use are built before them, as with `--live2d` (listed in
  `models.json`, names from the master data as for `--live2d`).
- A chart, model or story whose manifest exists is skipped unless `--force` (with stories, their models too). A
  model manifest whose `model.json` is of an older format (without `motionSync`) is outdated and built again; the
  summary lists such models in `modelsRebuilt` (`storyModels.modelsRebuilt` for the models of stories). Assets no
  chart, no model and no story (common or language file, or model manifest it names) references are removed.
- `--player-only` rewrites the player files, `charts.json`, `models.json` and `stories.json` only; `--reingest-json`
  stores every chart's and model's JSON files again from the site's own assets (then rewrites the player files and
  the indexes; story manifests are left as they are).
- `--player`: the ournotes-player checkout (after its build) or installed package; it must contain
  `scripts/read-set.mjs`, `dist/ournotes-player.element.min.js` and `examples/chart-list/index.html`. The read-set
  script runs under Node.js to list the files the player reads for each chart; only those are stored. When it also has
  `examples/live2d/index.html`, that page and `dist/ournotes-player.live2d.element.min.js` go to `SITE/live2d/`;
  when it has `examples/story-list/index.html` and `dist/ournotes-player.story.element.min.js`, they go to
  `SITE/story/` (a build that adds stories stops when the page is there without its bundle).
- `--compress` (default `gzip`): how the compressible assets are stored: JSON files (split parts included), GLSL,
  moc3, atlas, skel, bin, wav and glb files. With `gzip` or `br` (brotli, quality 11) such a file is stored encoded
  when that makes it smaller: `assets/<sha256>.<ext>.gz` (or `.br`), named by the SHA-256 of the decoded bytes; its
  entry's `size` is the decoded size and `stored` the size of the stored file (a part: `[key, asset, size, stored]`).
  Other files, and every file with `none`, are stored as they are, without `stored`. `--reingest-json` stores the
  JSON files again with the given `--compress`.
- Hosting: the player decodes a `.gz` or `.br` asset when the bytes it receives have the entry's `stored` length and
  uses them as they are when they have its `size`. A gzip site can be served as plain files; `Content-Encoding:
  gzip` on its `.gz` files is optional (with it the browser decodes them, without it the player does). A brotli site
  needs `Content-Encoding: br` on its `.br` files for Chromium-based browsers, which cannot decode brotli in the
  page.
- `--format` (default `aac`) is the BGM format of the charts and the format of every story sound (music, sound
  effects, voices); the charts' note SE, cheers and voices stay FLAC. `--no-audio` stores no audio (the player then
  runs the chart silent on its own clock, and plays stories without their sounds; story videos keep their sound
  track).
- `--workers`: parallel music processes (default a quarter of the CPUs, up to 8), model processes (default up
  to 4) and story processes (default a quarter of the CPUs, up to 8); `0` or `1` builds in this process. `--read-workers`: chart read sets run at a time, each a Node.js process
  (default half the CPUs, up to 16). With a player whose read-set script lists a chart's files from its plan
  (without stepping the chart) and serves many charts from one process, the plans run in one long-lived Node.js
  process per read-set slot, ended with the build; the full simulations that check a sample of the plans still run
  in a process each.
- `--tmp`: directory for the temporary live and model builds (default `SITE.tmp`); the build directories in it are
  removed after use. `SITE.tmp/cache/` is kept: decoded cue sheets, encoded PNGs, shader dumps and read sets, each
  stored under a hash of everything it was made from (input bytes, settings, the external tools and libraries in use
  and nnnotes' own code), so a later build with the same `--tmp` reuses what is unchanged and writes the same files
  faster. Deleting it is
  safe at any time outside a build; the next build then makes everything again.
- `--band` / `--leader-card`: as for `export live`, for every chart. `--fonts`: for stories, where the glyphs of the
  story text come from (see Stories); the site stores no start canvas of a chart.
- `--live-option` (repeatable): as for `export live`, for the charts of this run (with `--pair` or `--all`). The live
  directories carry the variants' files, and a chart's read set is the union of its read set with the default
  options and its read sets with the settings of each offered variant (every combination of the offered mirror,
  note skin, effect set and quality values, the bar lines on, and each other note sound set; the player's read-set
  script needs its `--settings`); only the files a variant reads are stored, the shared ones once per site. A chart
  manifest lists the offered qualities as `options` (`{"LiveQuality": [0, 1, 2]}`); the other options are offered by
  the chart's files. Each variant adds a read-set plan per chart (`all` makes several dozen), so such a build takes
  longer. Charts whose manifest exists keep the options they were built with unless `--force`.
- Models need `[paths] apk` (component classes and the mask materials are read from the APK). Master data is
  optional: when it is configured (of the site's first region), each model that a `MasterCharacterCostume` row maps
  to a character (its `_live2dPath` is the key after `Character/Live2D/`) gets, in its manifest's `model` and its
  `models.json` entry, `character` (the `MasterCharacter` id), `names` (`{language: the character's name}`, from the
  `MasterText` row of `_nameTextID`, the languages that have a text) and `label` (the name in `[catalog] language`).
  A key the rows map to two characters gets none. The manifests of skipped models get this build's fields; without
  master data the fields are not written and skipped manifests keep theirs.
- `--site-region` (repeatable) / `--all-regions`: the regions the site serves (see Regions); default: the one
  `[catalog] region`. The global `--region` sets `[catalog] region`.

### Stories

A story's manifest lists what the player's story page reads (below the site's `assets/`):
`story.json`, `episode.json`, `scene.json`, the scene shaders (GLSL ES 3.00 programs only), textures, per cue sheet
`cues.json` and the file of each cue in the `--format` (an AAC file's encoder delay in
`cues.json` as `encoderDelay`; the CRI category names a cue plays in as `categories`, e.g. `["Voice", "VoiceConfig"]`), the media files the episode uses and its videos; per language (`--story-languages`,
default `ja,en,zh-Hant,zh-Hans,ko`) the story UI in that language (`ui/ui.json`), its font data (`ui/fonts.json` with
glyph pages under `ui/fonts/`) and `ui/languages.json`. UI files that are equal in every language are common files.
`scene.json` and `ui/ui.json` are stored split per top-level key, so the parts stories and languages share are stored
once. The Live2D models are the site's: the manifest names the model manifest of each model the story uses
(`models`, `{id: "models/<id>.json"}`, relative to the site root; `root` is the path from the manifest to the site
root), and `stories.json` counts their files in `size.models`. The format is described in the player's
`docs/story-data-format.md`.

- Story text is laid out with TextMeshPro's rules from TextMeshPro font assets holding exactly the characters the
  episode shows in the language (its text table, title and UI labels). `--fonts open` (default) generates them from
  a font file per language: `--font LANG=PATH` (repeatable) or `fonts.<lang>` in the `[paths]` table of the config
  file (`NNNOTES_PATHS_FONTS_<LANG>`, e.g. `NNNOTES_PATHS_FONTS_ZH_HANT`); every language of `--story-languages`
  needs one. Any OpenType or TrueType font (the first face of a collection) with the characters of the language
  works. The game's fallback chains are mirrored: where the game takes a character from a fallback font asset of
  another language's font (Korean text falls back to the Japanese font, for example), the generated asset falls
  back to an asset of that language's font file, which then needs to be given too (the build stops with the setting
  it needs when a character reaches it); characters no font file of the chain has are listed in `ui/fonts.json`
  `coverage.missing`. Characters the game's own font assets lack are drawn as the game draws them, as its missing
  glyph (U+25A1 where the game's font has it), not from the font file (`coverage.missingGlyph`). The simple talk
  window of an Overlay story is handled the same way. Each generated asset takes the
  point size, padding, style settings and render mode of the game font asset the texts use in that language, so the
  game's text materials apply unchanged; those values, the game's fallback chains, the characters the game's assets
  have and the text materials come from the table of them shipped with nnnotes (`src/nnnotes/unity/fontsettings.json`,
  `fontsettings.FORMAT`), so an open-font export reads no game font asset, material or font bundle;
  its face info and glyph metrics come from the font file (FreeType, no
  hinting), its distance field from the generator `--fonts game` uses for runtime glyphs (supersampled render modes
  at up to 8x). The asset records the font's family, version, license and SHA-256. `--fonts game` uses the game's
  font assets instead (the `fonts` extra); the format is the same. No font file is stored in the site other than as
  the generated glyph pages.
- Emoji are laid out as the game lays them out: its UI texts (the talk and chat texts) draw a character their font
  assets lack from the game's emoji sprite asset, and emoji sequences (several code points with U+200D or U+FE0F)
  become sprite tags through the game's emoji search. The layout keeps the game's sprite metrics; the images come
  from a colour emoji font of your own: `--font emoji=PATH` or `fonts.emoji` in the `[paths]` table
  (`NNNOTES_PATHS_FONTS_EMOJI`), a font with PNG bitmap glyphs (CBDT or sbix tables). Noto Color Emoji (SIL Open Font
  License 1.1) is the tested font. Only the sprites an episode's texts can draw are generated, each at the game's
  sprite size, into one page under `ui/fonts/`; `ui/fonts.json` records the emoji font as it records a text font.
  Without an emoji font the sprites keep their layout with empty glyphs, and `ui/fonts.json`
  `coverage.sprites.missing` lists them. `--fonts game` uses the game's sprite asset.
- Stories need `[paths] apk` and the optional `fonts` dependencies (`pip install 'nnnotes[fonts]'`) in both modes.
- The story index's groups come from the story tables of the master data (`MasterStoryEpisode` and its chapter,
  `MasterStoryFriendshipEpisode`, `MasterStoryHomeSpotTapTalkEpisode`, `MasterStoryLiveResultEpisode`,
  `MasterHomeSpot`), with chapter, character and spot names in every language.
- An Overlay episode (`MasterAdv._playbackMode` 1: the game plays it in its simple ADV player over the screen that
  opened it) is listed with `playbackMode` 1. With `--fonts open` its story also has the host screen (`host/`: the
  home spot or the live result screen of its story group) and, per language, the simple talk window
  (`ui/simple/ui.json`, `ui/simple/fonts.json` and its glyph pages); the manifest names them in `host`. The host has
  no game-font variant: with `--fonts game` an Overlay story has neither.
- With `--fonts open` the chat window texts of an episode with chat rows get their bindings in `ui/fonts.json`
  `chatTexts`, and the font assets also hold the chat windows' status texts and the texts they format at run time.
  The text nodes of the episode's frames get theirs in `frameTexts`, and the font assets also hold the texts a frame
  that receives texts is given at run time (the texts of its Frame rows' `TargetTextIDs`, as the frame formats them).
- Stories that fail are listed in the printed summary and in `SITE.story-failures.json`; the exit status is then 1.

`ui/ui.json` holds the parts of the game's ADV widget the story draws: the front canvas (talk window, speaker plate,
location caption, title, rule transition, curtains, flash, subtitles caption, next indicator, menu entry button, menu
panel and video buttons, backlog, choices, tap areas), the canvases of stills, frames and videos with the screen
image and the camera they render with (`videoAndStillCamera`), the UI camera that renders the frame canvas
(`uiCamera`), and the letterbox bands; per node its transform, rect
and uGUI components, the views' serialized references as node paths (the menu view with its fast-forward sprites),
the other game components' serialized fields (`behaviours`), the widget's canvas sort orders (`widget`), the ADV
screen's dialogs (`dialogs`: the skip confirm and common dialog prefabs) and their texts of the language
(`masterIdTexts`), the chat phone (`chatWidget`), the text
records of the episode's chat windows (`chatTexts`) with their status texts (`chatStatusTexts`). The talk windows
sit under `TalkView`: the default window,
then each other window the episode's TalkWindow rows attach; when one of them dims and blurs the screen behind its
talk (`UICenterTalkWindow`), the front canvas' `CenterTalkBackdrop` node, the renderer's blur settings (`blur`) and
the blur shader come with it.

Each language's `ui/` holds the localized fonts and materials, the line spacing LocalizeText applies, and, with
`--fonts game`, the characters of the lines (Talk, Location and Subtitles rows, ruby readings included), speaker
names, title and UI texts in that language (`ui/ui.json` `language`: `mode`, `field`, `lineSpacing`).

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

### Regions and languages

One site serves several regions and every language:

- The regions serve the same catalog for a language, so a chart's files depend on the region's master data only.
  Regions whose chart tables (the master tables the chart build reads) are identical share one manifest,
  `charts/<id>.json`, built once from the first region's data. A region with other chart tables is built on its own
  into `charts/<region>/<id>.json`; such a manifest whose files equal the shared one's is dropped and the region joins
  the shared manifest. Models read no master data: one build serves every region. Stories follow the charts' rule
  with the master tables the story build reads (`stories/<id>.json`, `stories/<region>/<id>.json`); a region offers
  the stories of its `MasterAdv`.
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

The same inputs with the same versions of nnnotes, its libraries and tools (and the same font files) give
byte-identical outputs; the encoded assets also need the same zlib and brotli versions. Charts that fail are listed
in the printed summary and in `SITE.failures.json`, models that fail in the summary and in
`SITE.model-failures.json`, stories in `SITE.story-failures.json`; the exit status is then 1.

## music-data

```
nnnotes music-data (--master-files DIR | --apk-master | --decoded-master) [--full] [--no-deck] [--seeds N]
                   [--workers N] [--no-gekisou-aptitude]
                   [--stats-cache DIR] [--no-bgm] [--jackets DIR] -o FILE
```

Writes one JSON file with every `MasterLiveMusic` song and its charts for one master data version: titles, readings
and credits in the five text languages, bands (and a song's own band name), vocal characters, category, tags,
release time, jacket, Gekisou missions, score ranks, the whole `MasterLiveMusic` row, the live BGM's cue and length
(read from the cue sheet's ACB, without decoding audio); the Gekisou catalog (member cards and snaps with their
characters, bands and Gekisou skills and support skills: missions, highest levels, names and descriptions); per
difficulty the chart facts (level and display level, full combo count, note counts, BPM, note times, the live's music
length, skill event times, fever ranges) and the chart's deck statistics: the no-skill score and the weight of every
score-up skill kind at every performance position, with Gekisou on (a Gekisou live at rank 1, with what every other
rank and the Perfect play need) and off (a solo live), measured by the deck model ournotes-deck (the package ournotes-sim,
installed with `pip install 'nnnotes[deck]'`) on its whole-live simulation and checked against the chart facts and the master data. `--full` also writes the deck model's input: every
`MasterLiveMusicScore` row's chart as the client builds it at runtime (notes, skill events, fever ranges) and the
master data tables about cards, skills, bonuses, scores and events. Single-skill Gekisou aptitude is included by
default as independent nominal expectations; `--no-gekisou-aptitude` omits it.
`--no-deck` leaves every chart's `deck` null. `--seeds N` (default 8) gives replay seeds for luck charts;
`--workers N` sets measurement threads (default: every processor). `--stats-cache DIR` keeps every chart's statistics in `DIR` under the SHA-256 of what
they are a function of (the deck model's sources, these options, the master data tables and the chart) and measures
only the charts it lacks; `DIR` then holds this file's charts only. The master data is decoded from the files as served: `--master-files DIR` reads
`DIR/MasterManifest.json` and the `.bin` files it lists (`master download`; the file's region is `[catalog] region`),
`--apk-master` the same files inside `[paths] apk` (region `embedded`); each file is checked against the manifest's
SHA-256. `--decoded-master` reads master data decoded elsewhere instead, without the master key: the `<Table>.json`
files of the master data directory (`[paths] master`, `--master`) and the `MasterManifest.json` of the files they were
decoded from, whose version and SHA-256 the file records (region `[catalog] region`). `--no-bgm` skips the cue sheets
(every `bgm.length` is null). `--jackets DIR` also writes every song's jacket as `DIR/<jacket>.webp` (at most 320 px
on the longer side). `FILE` ending in `.gz` is written gzip-compressed; the file is canonical: the same inputs and
nnnotes version give the same bytes. Reports `{out, format, region, masterSource, masterVersion, songs, charts, deck,
unplayable, full, bgm, jackets, bytes, fileBytes, sha256}`. A missing or unreadable input (a master data file, a
column, a text id, a chart asset, a cue sheet or cue, a jacket), a chart the deck model cannot measure, or deck
statistics that disagree with the chart facts stop the command with exit status 1 before the file is written. The
format is described in [music-data.md](music-data.md).
