# nnnotes

[English](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.md) | [简体中文](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.zh-CN.md)

nnnotes turns your BanG Dream! Our Notes game files into structured data, media and static player sites.
It reads Addressables catalogs, bundles, master data and APK resources; exports stories, Live2D models, live
charts, rooms, UI, shaders and audio; and assembles sites for
[ournotes-player](https://github.com/empty-sekai/ournotes-player).

A persistent **depot** stores converted objects, task results and catalog versions. Exports share these results,
and named sites retain their selected products across builds. A depot can also share work through a directory
or S3-compatible object storage.

**[Documentation](https://metasekailab.github.io/nnnotes/)** ·
[Quick start](https://metasekailab.github.io/nnnotes/quickstart/) ·
[Commands](https://metasekailab.github.io/nnnotes/cli/)

## Install

Python 3.11 or newer:

```sh
pip install nnnotes==0.3.0
pip install 'nnnotes[fonts]==0.3.0'    # story and UI fonts
pip install 'nnnotes[remote]==0.3.0'   # S3-compatible depot storage
pip install 'nnnotes[deck]==0.3.0'     # music-data chart statistics
```

Choose the extras your commands need; they can be combined as `nnnotes[fonts,remote,deck]==0.3.0`.
Audio/video conversion uses [vgmstream](https://vgmstream.org/) and [FFmpeg](https://ffmpeg.org/).
Site builds additionally require Node.js 22+ and a built **ournotes-player 0.2.x** package.
The deck extra installs `ournotes-sim==0.0.12`.

## First export

Supply your own client files, keys and server addresses through the configuration wizard:

```sh
nnnotes config init --user
nnnotes config check
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes config set paths.master work/master
nnnotes export episode 10462 -o out/episode.json
nnnotes export story 10462 --fonts game -o out/story
```

Set `paths.depot` to a persistent directory and `paths.apk` to your game APK. The wizard reads keys without echo;
`config check` reports setting names, origins and validity. Story export requires the `fonts` extra and audio
tools. See the [quick start](https://metasekailab.github.io/nnnotes/quickstart/) for prerequisites and output layout.

## Build a player site

```sh
npm install ournotes-player@0.2.0
nnnotes site build --name stories -o out/site --story 10462 --fonts game --player node_modules/ournotes-player
node node_modules/ournotes-player/scripts/validate-data.mjs out/site
python -m http.server 8000 --directory out/site
```

Open `http://localhost:8000/story/`. Live2D, MotionSync and Spine features require the corresponding browser
runtimes supplied by the embedding page. See the [site guide](https://metasekailab.github.io/nnnotes/site/).
Use `--pair MUSIC_ID:DIFFICULTY` for a chart, `--live2d KEY` for a model, and `--story-languages ja,en` for selected
story languages. Reusing a site name updates selected topics and retains the others.

## Choose a workflow

| Task | Commands | Guide |
|---|---|---|
| Configure inputs | `config init`, `config check`, `servers` | [Configuration](https://metasekailab.github.io/nnnotes/configuration/) |
| Inspect or retain catalogs | `catalog keys`, `browse`, `versions`, `import`, `fetch`, `diff` | [Commands](https://metasekailab.github.io/nnnotes/cli/) |
| Copy source bundles | `bundle pull` | [Asset export](https://metasekailab.github.io/nnnotes/assets/) |
| Export a subject | `export episode`, `story`, `live`, `live2d`, `spot`, `room`, `shader`, `audio`, `ui` | [Exports](https://metasekailab.github.io/nnnotes/export/) |
| Convert selected assets | `export assets`, `depot plan`, `depot run` | [Asset export](https://metasekailab.github.io/nnnotes/assets/) |
| Check and retain stored work | `depot verify`, `depot gc` | [Depot](https://metasekailab.github.io/nnnotes/depot/) |
| Export song and chart statistics | `music-data` | [Music data](https://metasekailab.github.io/nnnotes/music-data/) |
| Find character voices | `voices list`, `search`, `summary`, `get` | [Commands](https://metasekailab.github.io/nnnotes/cli/) |

Commands print summaries; `--json` selects a structured report. The reference describes exit statuses and options.

## Data and compatibility

Product exports use versioned manifests and shared object records. Read the
[format reference](https://metasekailab.github.io/nnnotes/formats/),
[product reference](https://metasekailab.github.io/nnnotes/products/) and
[schemas](https://metasekailab.github.io/nnnotes/schemas/) to consume them.
Task identities include input contents, options and conversion implementations, including native encoders.
Byte-identical output requires matching inputs and library/tool implementations, including Pillow and its native
compression backend. JSON uses UTF-8 and LF; infinite values use `1e999` or `-1e999`.
[Determinism](https://metasekailab.github.io/nnnotes/determinism/) explains reproducibility across installations.

Versions cover the CLI, configuration, schemas and documented Python API. Before 1.0, a minor version can change
these interfaces. See [CHANGELOG.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/CHANGELOG.md) and the [architecture](https://metasekailab.github.io/nnnotes/architecture/).

## Contributing and license

[CONTRIBUTING.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/CONTRIBUTING.md) describes development and synthetic tests. nnnotes is licensed under
[MIT](https://github.com/MetaSekaiLab/nnnotes/blob/main/LICENSE). This is an unofficial fan project, unaffiliated with the game's developer or operator.
Game resources, keys and server configuration are supplied by each user and are subject to their owners' rights.
