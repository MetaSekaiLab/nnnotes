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
nnnotes export story 10462 -o out/stories/10462            # everything the episode plays
```

`export story` writes the episode, its decoded cue sheets, the stage scene, the story UI, frames, effects, stills,
chats and videos below `-o`, and its Live2D models below the models directory (`--models`, default `live2d/` next to
the story directory). A model directory that exists is used as it is, so exporting the stories of a season into
sibling directories exports each model once.

- `--format flac|ogg|wav` and `--flac-level N` choose the audio format; `--no-audio` leaves the cue sheets undecoded.
- `--fonts game` also writes the game's fonts reduced to the characters shown (needs `pip install 'nnnotes[fonts]'`).
- `--force` exports the episode's models again.

`MasterAdv` lists the episode ids; `nnnotes voices list --source story --episode ID` lists the voiced lines of one.
Details: [export episode](commands.md#export-episode), [export story](commands.md#export-story).

## A live

```sh
nnnotes export live 100001 --difficulty expert -o out/lives/100001_expert
```

One chart and the products it plays with, in the layout ournotes-player reads: the chart as shipped and its
converted runtime notes, the 3D scene, the note highway with its skins and effect sets, the sound effects with their
routing, and the BGM in a web format.

- `--band BAND` or `--leader-card CARD_ID` picks the band of the background and start timeline (default: the band of
  the music's first vocal character).
- `--live-option NAME[=VALUES]` (repeatable) adds the products of other values of the live options, such as
  `NoteDesignId` or `NoteSePatternId`; `--live-option all` adds every option with every value.

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
