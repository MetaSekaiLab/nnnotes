# Export stories and lives

The `export` commands each write one subject as a directory or file. They read the catalog, fetch and decrypt the
bundles they need into the depot, and need decoded master data where the subject is defined by it
([what each command needs](configuration.md#what-each-command-needs)). To export every object of many bundles at
once, use [`export assets`](assets.md) instead.

Every command below prints a short summary; add `--json` for its report as JSON. The layout of each output is in
[Command outputs](commands.md).

## A story

```sh
nnnotes export episode 10462 -o out/episode_10462.json     # the episode's commands, lines and resources
nnnotes export story 10462 --fonts game -o out/stories/10462  # the episode with the client's TMP fonts
```

`export story` writes the story product of an episode and the products it plays with, as a product layout below `-o`:
the story index in `stories/<id>.json`, the stage scene in `storyscenes/default.json`, each stage, frame, effect and
other asset in `storyassets/`, each Live2D model in `models/`, each cue sheet in `cuesheets/`, each video in
`videos/` and, when the episode uses them, the shared chat rows in `storychat/default.json` and the CRI Lips data in `crilips/default.json`. The files
themselves are under `assets/`.

`storyui/<language>.json` and `storyfonts/<id>_<language>.json` provide the UI, text bindings and glyph pages for
`[catalog] language`. `--story-languages ja,en` selects several languages. `--fonts open` (the default) uses font
files from `[paths.fonts]` or repeatable `--font LANG=PATH` flags; supply any languages needed by the fallback chain.
Both font sources require the `fonts` extra.

Products that several stories share (the scene, assets, models and cue sheets) are placed once, so exporting several
stories into one directory keeps one copy of each. Every product goes through the depot, so a second export reuses
its results.

- `--format NAME` chooses the web audio format of the cue sheets: `aac` (default), `opus`, `vorbis`, `mp3` or `flac`.
- `--flac-level N` sets the FLAC compression level (0 to 12, default 8) of the decoded sounds.
- `--dry-run` prints the plan and runs nothing.

`MasterAdv` lists the episode ids; `nnnotes voices list --source story --episode ID` lists the voiced lines of one.
Details: [export episode](commands.md#export-episode), [export story](commands.md#export-story).

## A live

```sh
nnnotes export live 100001 --difficulty expert --fonts game -o out/lives/100001_expert
```

One chart and the products it plays with, in the layout ournotes-player reads: the chart as shipped and its
converted runtime notes, the 3D scene, the note highway with its skins and effect sets, the sound effects with their
routing, the BGM in a web format, and localized start-canvas text with glyph pages.

- `--band BAND` or `--leader-card CARD_ID` picks the band of the background and start timeline (default: the band of
  the music's first vocal character).
- `--live-option NAME[=VALUES]` (repeatable) adds the products of other values of the live options, such as
  `NoteDesignId` or `NoteSePatternId`; `--live-option all` adds every option with every value.

- `--live-languages ja,en` selects UI languages (default: catalog language; `all` selects all client languages).
- `--fonts game` uses the client's TMP fonts. The default, `--fonts open`, reads font files from `[paths.fonts]`
  or repeatable `--font LANG=PATH` flags. Supply the languages used by the fallback chain. Both modes need the
  `fonts` extra.

Details: [export live](commands.md#export-live).

## A Live2D model

```sh
nnnotes catalog keys --prefix Character/Live2D/ --limit 0     # every model key
nnnotes export live2d adv_live2d_rana_003_casual_spring_01 -o out/live2d/rana
```

`MODEL` is the model's key or its id, the last part of the key. The output is the model's Cubism SDK files: moc3,
atlas pages, motions, expressions, physics and `model3.json`; `--player` writes the model the player reads instead.
Details: [export live2d](commands.md#export-live2d).

## A spot or a room

```sh
nnnotes export spot SPOT_ID -o out/spots/SPOT_ID
nnnotes export room KEY -o out/rooms/PLACE                 # a background key
```

`export spot` writes the spot's master row with its names and tap-talk episodes, its tap targets, its Spine
characters and their files, and its background as binary glTF. `export room` writes one background as binary glTF
with a summary next to it. Details: [export spot](commands.md#export-spot), [export room](commands.md#export-room).

## Shaders, audio and the UI library

```sh
nnnotes export shader --key KEY -o out/shaders                 # every shader of a key's bundle closure
nnnotes export audio CUE_SHEET -o out/audio/CUE_SHEET          # one cue sheet, one file per stream
nnnotes export ui -o out/ui                                    # the prefab library of the APK's embedded catalog
```

`export audio` decodes `Cri/Sound/<CUE_SHEET>` with the HCA key read from the APK; FLAC keeps the decoded PCM
bit-exact at every `--flac-level`. Details: [export shader](commands.md#export-shader),
[export audio](commands.md#export-audio), [UI library](ui.md).

## Voices

```sh
nnnotes voices list --character 1 --category character-rank-up
nnnotes voices search "hello" --search-language en
nnnotes voices get MasterTalk:561 -o out/voices/561
```

The `voices` commands list, search and fetch the character and story voices of the master data. Without an
`export assets` output (`--from`) they read the master data and the catalog directly and decode only the cue sheet a
`voices get` needs. Details: [voices](commands.md#voices).
