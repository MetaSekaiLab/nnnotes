# Quick start

This walk-through takes you from an empty configuration to an exported ADV episode and a story export. It needs
your own installation of the game: its `base.apk`, and the keys and server addresses of your client
([where the values come from](configuration.md#where-the-values-come-from)).

## 1. Install

```sh
pip install 'nnnotes[fonts]'
```

Python 3.11 or later. Install [vgmstream](https://vgmstream.org/) (`vgmstream-cli`) and
[FFmpeg](https://ffmpeg.org/) as well, on `PATH` or named in the config file; stories decode their audio and
videos with them.

## 2. Write the config file

```sh
nnnotes config init --user
```

In a terminal this asks for each setting in turn, among them the bundle and master data keys, the names of your
regions with each region's CDN base and API root, the catalog language, and the paths of the depot, the APK and the
tools. Enter leaves a setting empty; the keys are read without echo. The file is written to the per-user location,
which every command reads from any directory (`nnnotes config path` shows it).

Check it:

```sh
nnnotes config check
```

Every setting is listed with where it comes from and whether it is valid, never with its value. A command that
needs an unset setting stops with exit status 2 and names the TOML key, the environment variable and the flag that
set it.

## 3. Look at the catalog

```sh
nnnotes catalog keys --prefix Adv/ --limit 5
```

The first command downloads the region's catalog into the depot; the keys it lists are the addressable keys of the
game's assets. `nnnotes catalog browse` serves a local web page to browse the catalogs and bundles of every
configured region.

## 4. Get the master data

```sh
nnnotes master version
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes config set paths.master work/master
```

`master version` asks the region's game API which master data version it serves now; `master download` fetches
that version and checks every file's SHA-256; `master decode` writes one JSON file per table. The last line makes
the decoded directory the default of every command that reads master data.

## 5. Export an episode and a story

```sh
nnnotes export episode 10462 -o out/episode_10462.json
nnnotes export story 10462 --fonts game -o out/story_10462
```

`export episode` writes the episode's command list with its lines in five languages and the index of its voices,
sounds and videos. `export story` writes the story product of the episode and the products it plays with below
`out/story_10462`: the story index, the stage scene, the assets, the Live2D models, the cue sheets decoded to audio
(AAC by default, `--format` chooses another web format), the videos as WebM, and the shared chat rows and CRI Lips
data when the episode uses them. It also exports the UI and glyph pages in `[catalog] language`; select several
with `--story-languages ja,en`. The example uses the client's TMP fonts. For open fonts, use
`--fonts open --font ja=/path/to/font.otf` (or configure `[paths.fonts]`). Both modes require the `fonts` extra.
The files are under `assets/`. Add `--json` to any command to get its report as JSON.

## Next

- [Export stories and lives](export.md): the other single-subject exports and their options.
- [Build an ournotes-player site](site.md): charts, models and stories as one static site.
- [Export every asset](assets.md): every object of the selected bundles through the content-addressed depot.
- [Command reference](cli.md): every command and option.
