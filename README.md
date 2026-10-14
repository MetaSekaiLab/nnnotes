# nnnotes？！

[English](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.md) | [简体中文](https://github.com/MetaSekaiLab/nnnotes/blob/main/README.zh-CN.md)

nnnotes is an offline data toolkit for the game files of BanG Dream! Our Notes. It reads Addressables catalogs,
downloads and decrypts asset bundles, downloads and decodes master data, and exports stories, Live2D models, spots,
shaders, CRI audio and live charts as structured JSON and common file formats. `nnnotes site build` writes the static
site that [ournotes-player](https://github.com/empty-sekai/ournotes-player) plays, and `nnnotes music-data` one JSON
file with every song and chart. The name was inspired by sssekai.

This is an unofficial fan project, not affiliated with the game's developer or operator. The repository contains no
game assets, keys or server addresses: you supply the game files, the keys and the server addresses in your own
configuration, and the exports stay in the local directories you choose.

**Documentation: <https://metasekailab.github.io/nnnotes/>**

## Install

```sh
pip install nnnotes                  # Python 3.11+
pip install 'nnnotes[fonts]'         # the game's fonts (--fonts game) and the story text of site build
pip install 'nnnotes[deck]'          # the deck model's chart statistics of music-data
```

nnnotes also needs the game's `base.apk`, decoded master data for the commands that read it, and the external tools
[vgmstream](https://vgmstream.org/) and [FFmpeg](https://ffmpeg.org/) for audio and video (Node.js 20+ and a built
ournotes-player for `site build`).

## Quick start

```sh
nnnotes config init --user          # asks for each setting (keys without echo) and writes the per-user config file
nnnotes config check                # where each setting comes from and whether it is valid, never its value
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes --master work/master export story 10462 -o out/story_10462
nnnotes site build -o out/site --all --player path/to/ournotes-player
```

Every command prints a short summary, or with `--json` its report as JSON. Scripts set the config file without
questions: `nnnotes config init --user --no-input --set SECTION.KEY=VALUE ...`.

## Commands

| Command | What it does |
| --- | --- |
| `config init\|set\|unset\|check\|path` | write, edit and check the config file |
| `servers` | the game API's server list: regions with their CDN and API roots |
| `catalog keys\|browse` | the addressable keys of a region's catalog; a local web page to browse catalogs and bundles |
| `catalog versions\|import\|fetch\|diff` | catalog versions kept in the depot, and what changed between two |
| `bundle pull` | fetch the bundle closure of keys and copy the decrypted bundles to a directory |
| `master version\|download\|decode` | the master data version a region serves, its download and its decoding to JSON |
| `export assets` | every object of the selected bundles in common formats, through the content-addressed depot |
| `export episode\|story\|live\|live2d\|spot\|room\|shader\|audio\|ui` | one subject: an ADV episode, a story, a live, a Live2D model, a spot, a room, shaders, a cue sheet, the UI library |
| `apk settings\|hca-key` | the player's graphics settings and the CRI HCA keycode of `base.apk` |
| `depot plan\|run\|verify\|gc` | what `export assets` would run and why, single tasks, the depot's check and collection |
| `voices list\|search\|summary\|get` | the character and story voices of the master data |
| `site build` | the static site of ournotes-player: charts, Live2D models and stories, in several regions and languages |
| `music-data` | every song and chart in one JSON file, with the deck model's chart statistics |

The [command reference](https://metasekailab.github.io/nnnotes/cli/) lists every option.

## Documentation

- [Quick start](https://metasekailab.github.io/nnnotes/quickstart/): from an empty config to an exported story.
- How-to guides: [configure nnnotes](https://metasekailab.github.io/nnnotes/configuration/),
  [export stories and lives](https://metasekailab.github.io/nnnotes/export/),
  [build an ournotes-player site](https://metasekailab.github.io/nnnotes/site/),
  [export every asset](https://metasekailab.github.io/nnnotes/assets/),
  [the Japanese release](https://metasekailab.github.io/nnnotes/jp/),
  [music data](https://metasekailab.github.io/nnnotes/music-data/).
- Reference: [commands](https://metasekailab.github.io/nnnotes/cli/),
  [command outputs](https://metasekailab.github.io/nnnotes/commands/),
  [JSON schemas](https://metasekailab.github.io/nnnotes/schemas/),
  [Python API](https://metasekailab.github.io/nnnotes/api/),
  [contracts](https://metasekailab.github.io/nnnotes/contracts/), [stages](https://metasekailab.github.io/nnnotes/stages/),
  [views](https://metasekailab.github.io/nnnotes/views/).
- Explanation: [architecture](https://metasekailab.github.io/nnnotes/architecture/),
  [determinism](https://metasekailab.github.io/nnnotes/determinism/),
  [validation](https://metasekailab.github.io/nnnotes/validation/).

Exports are deterministic for a given installation: the same input with the same versions of nnnotes, its Python
dependencies (UnityPy, Pillow, numpy, ...) and the external tools gives byte-identical files; JSON is UTF-8 with LF
line endings and non-finite numbers written as `1e999`. Other versions can encode the same content into other bytes
(PNG files written by another Pillow version can differ in their bytes while their pixels are equal).

## Changelog and versions

[CHANGELOG.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/CHANGELOG.md) lists the changes of every version.
Versions follow [Semantic Versioning](https://semver.org/) over the command line, the configuration keys, the JSON
schemas and the documented Python API.

## Contributing

Setup, checks and commit conventions are in
[CONTRIBUTING.md](https://github.com/MetaSekaiLab/nnnotes/blob/main/CONTRIBUTING.md).

## License

MIT, see [LICENSE](https://github.com/MetaSekaiLab/nnnotes/blob/main/LICENSE).
