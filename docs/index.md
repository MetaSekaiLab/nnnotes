# nnnotes

nnnotes is an offline data toolkit for the game files of BanG Dream! Our Notes. It reads Addressables catalogs,
downloads and decrypts asset bundles, downloads and decodes master data, and exports stories, Live2D models, spots,
shaders, CRI audio and live charts as structured JSON and common file formats. `nnnotes site build` writes the static
site that [ournotes-player](https://github.com/empty-sekai/ournotes-player) plays, and `nnnotes music-data` one JSON
file with every song and chart.

This is an unofficial fan project, not affiliated with the game's developer or operator. nnnotes contains no game
assets, keys or server addresses: you supply the game files, the keys and the server addresses in your own
configuration, and the exports stay in the local directories you choose.

## Where to start

<div class="grid cards" markdown>

- **[Quick start](quickstart.md)**

    From an empty config file to an exported story, step by step.

- **How-to guides**

    [Configure nnnotes](configuration.md), [export stories and lives](export.md),
    [build an ournotes-player site](site.md), [export every asset](assets.md),
    [the Japanese release](jp.md), [music data](music-data.md).

- **Reference**

    [Commands](cli.md), [command outputs](commands.md), [JSON schemas](schemas.md), [Python API](api.md),
    [contracts](contracts.md), [stages](stages.md), [views](views.md), [replay data](replay.md),
    [UI library](ui.md).

- **Explanation**

    [Architecture](architecture.md), [determinism](determinism.md), [validation](validation.md).

</div>

## Install

```sh
pip install nnnotes                  # Python 3.11+
pip install 'nnnotes[fonts]'         # the game's fonts (--fonts game) and the story text of site build
pip install 'nnnotes[deck]'          # the deck model's chart statistics of music-data
```

Besides the package, nnnotes reads the game's `base.apk` and, for the commands that need it, decoded master data;
audio and video need [vgmstream](https://vgmstream.org/) and [FFmpeg](https://ffmpeg.org/), and `site build` also
Node.js 20+ and a built ournotes-player. [Configuration](configuration.md#what-each-command-needs) lists what each
command needs.

## Versions

Versions follow [Semantic Versioning](https://semver.org/). The interface a version describes is the command line
(commands, options, exit statuses and the `--json` reports), the configuration keys, the JSON schemas shipped in
`nnnotes.schemas` and the documented Python API. Before 1.0 a minor version can change any of them; the
[changelog](https://github.com/MetaSekaiLab/nnnotes/blob/main/CHANGELOG.md) names every change.
