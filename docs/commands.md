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

During product and asset requests, a completed object-conversion batch makes its bundle downloads eligible for
release toward the same budget. Readers finish before release; active batches, failed batches and preliminary
episode requests stay protected. A batch already present in the depot can release its downloaded input as well.
Only files recoverable through an explicit catalog or depot locator are released, and their contents are checked
against the input identity. `all` retains downloads throughout the run; `0` releases each eligible batch without
scanning the download directory. With a finite size, finished files are evicted in LRU order and active files can
temporarily exceed the budget. Later requests can download a released bundle again.

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

## catalog rebuild

```
nnnotes catalog rebuild CATALOG -o OUT [--add FILE ...] [--drop KEY ...]
                         [--bundle FILE ...] [--hash HEX]
```

Rebuilds an Addressables binary catalog, preserving headers, typed keys and location dependencies. `--add` accepts
location objects or bundle manifests (`file`, `internal_id`, `bundle_name`, `assets`), computing content CRC, size
and a deterministic cache hash for each plain UnityFS file. Asset locations depend on bundles by key. `--drop`
removes locations and supports replacements under the same key. `--bundle` refreshes an existing bundle entry
with its decompressed-content CRC, size and MD5 cache version; `--hash` overrides one bundle cache hash.
See [packing.md](packing.md) for the input contract. Reports `{in, out, added, dropped, updated, addedBundles, bytes}`.

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

## pack master

```
nnnotes pack master INPUT [INPUT ...] -o OUT [--prefix-from BIN] [--manifest VERSION]
```

The inverse of `master decode`: each `.json` file, or the `*.json` files of a directory, is written as
`OUT/<file stem>.bin` — gzip-compressed, then Rijndael-256 CBC with PKCS7 padding, behind a 64-byte prefix. The key
and IV are `[master] key` and `[master] iv`, so a client configured with the same pair reads the result like any
other master data file. `--prefix-from` copies the prefix of an existing master data file: the loader ignores it, so a
file that replaces another should carry that file's prefix; without it the prefix is 64 zero bytes. `--manifest`
also writes `OUT/MasterManifest.json` for that master data version — the file `master download` reads, listing each
packed file's name, SHA-256 and size — which is what a CDN tree of our own needs. The report is
`{out, packed, files, manifest}`; the exit status is 1 when a file could not be packed.

## pack bundle

```
nnnotes pack bundle INPUT [INPUT ...] -o OUT [--suffix .bundle] [--name NAME]
```

The inverse of the decryption `bundle pull` applies: each UnityFS bundle is written as the CDN serves it — AES-128 in
CTR mode over the first 16 KiB, the nonce being the first 8 bytes of SHA-256(`[bundle] nonce_seed` + the file name).
`--name` is the file name the bundle is served under, which seeds the nonce; it applies to a single input (default:
the input's own name). `--suffix` (default `.bundle`) is the suffix bundles have among the inputs of a directory. The
report is `{out, packed, files}`; the exit status is 1 when a file is not a UnityFS bundle or could not be packed.

## pack asset

```
nnnotes pack asset BUNDLE -o OUT --name NAME --data FILE [--new-name NAME]
```

Replaces a binary TextAsset while preserving its path ID and container path, then validates and saves the
UnityFS bundle with its original flags. `--new-name` changes the object name. Reports
`{in, out, asset, payloadBytes, bundleBytes, crc}`.

## pack build

```
nnnotes pack build MANIFEST -o PLAIN.bundle [--encrypt ENCRYPTED.bundle] [--name NAME]
                  [--catalog SOURCE --catalog-out CATALOG] [--catalog-add JSON]
```

Builds or edits a UnityFS bundle from a JSON manifest. New bundles use template schemas and flags with supplied
TextAsset, PNG texture, PCM WAV audio, serialized or raw object inputs. Operations add, replace and rename objects;
container paths, unique path IDs and preload references are managed by the builder. Optional outputs include an
encrypted bundle, a rebuilt catalog and a reusable catalog-add manifest. All outputs are prepared before writing.
See [packing.md](packing.md) for examples, schema selection, input limits and encryption. Reports
`{out, bundle_name, bundle_provider, assets, bundleBytes, crc, hash, encrypted, catalog, catalog_add}`.

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
OUT/storysimpleui/<lang>.json  the Overlay talk-window and system-message UI (`story.simpleui`)
OUT/storyhosts/<host>.json     the Overlay host screen (`story.host`)
OUT/spots/<situation>.json     a home host's spot and Spine files (`spot.files`)
OUT/rooms/<place>.json         a home host's background (`room.gltf`)
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

Overlay episodes also request their home or live-result host, the simple UI in each requested language, and,
for home hosts, the shared spot and room products. The host's master-data relationship is recorded in
`story.json` `host`; the episode font product covers simple UI text and any area title in both font modes.

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

```sh
nnnotes site build --name NAME -o SITE [--pair MUSIC_ID:DIFFICULTY | --all]
                  [--live2d MODEL | --all-live2d] [--story ADV_ID | --all-stories]
                  [--player DIR] [--format aac|opus|vorbis|mp3|flac] [--no-audio]
                  [--compress gzip|br|none] [--workers N] [--memory GiB] [--fetch-workers N]
                  [--stored-catalog LABEL|SHA] [--site-region REGION | --all-regions]
                  [--fonts open|game] [--story-languages LANGS] [--font LANG=PATH]
                  [--band N | --leader-card ID] [--live-option OPTION[=VALUES]]
nnnotes site build --name NAME -o SITE --player-only
```

The [site guide](site.md) covers selection, fonts, read sets and hosting. Chart, model and story requests execute the same depot product stages as their export commands. `--name` (default `default`) is a persistent namespace in the depot. Partial selections retain other topics; complete selections replace that product kind in the requested regions.

```text
SITE/
  index.html, chart-list.js, ...     chart list and player page
  live2d/, story/, songs/            player pages and bundles
  charts.json, models.json,
  stories.json                      listings and localized facts
  site.json                         complete nnnotes.site/1 catalog
  charts/<id>.json                   live product entry manifests
  models/<id>.json                   model product entry manifests
  stories/<id>.json                  story product entry manifests
  charts/<region>/<id>.json,
  stories/<region>/<id>.json         regional variants
  products/<sha256>.json             immutable nnnotes.product/1 manifests
  assets/<sha256>.<ext>[.gz|.br]      shared asset bytes
  manifest.json               output ownership
```

Product manifests retain logical file paths, object IDs and file roles. Their declared dependencies point to immutable products. Public aliases have roots relative to their depth. Asset entries support whole and split JSON, with decoded sizes and optional encoded sizes.

The complete catalog and layout are committed to the depot after publication succeeds. `--player-only` restores that catalog to the chosen output with current player pages. A configured remote supports restoration from a fresh local depot. Layout reachability keeps the retained products and assets available to depot collection.

Read-set tasks freeze player sources, product inputs, Node identity and settings. Plans are checked against full chart simulations on the first chart and a deterministic sample. Plan failures fall back to full simulation; mismatches switch subsequent reads to full. Variants union every offered setting combination's read set. Structured documents and their image/audio/WebGL2 dependencies remain available; additional binary file roles are selected from the reads.

Publication orders assets before immutable products, aliases/pages, and indexes. Retired owned files are collected after replacements succeed. Failures preserve the journal and previous successful-run pointer; retry the request after correcting the error. Product failures stop publication and appear in the run report. `--json` prints structured task and placement counts.

### Stories

Story entry products reference their episode, scene, models, assets, cue sheets, videos, localized UI/font products and optional host products. The selected languages default to the catalog language. Story models also appear in the model listing. `--no-audio` retains cue metadata while omitting the sound streams; playback uses silent timing.

### Regions and languages

Configured regions use their own master data. Products with identical immutable manifests and listing metadata share an alias and list all regions they serve. Other versions use regional paths. JP stories/models and international releases use separate sites; conflicting contents for the same model ID require separate sites. Chart and story listings include localized titles, supported languages and region metadata for the player pages.

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

## Song and audio packages

`pack plan`, `pack build`, `pack verify` and `pack audio` work with local song projects and audio.
See [Package a song](song-packing.md) for input manifests, tool requirements, validation and output layouts.
