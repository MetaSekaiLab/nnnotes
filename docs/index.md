# nnnotes

Export your BanG Dream! Our Notes game files as structured products, media or a static player site.
nnnotes reads catalogs, bundles, APK resources and master data, then stores reusable conversion results in a
persistent depot. You supply the client files, keys and server configuration.

## Start here

1. [Install and export a story](quickstart.md).
2. [Build and serve a player site](site.md) with ournotes-player 0.2.x.
3. [Share and retain depot results](depot.md), locally or through S3-compatible storage.

## Guides

| Goal | Guide |
|---|---|
| Set input paths, regions and tools | [Configuration](configuration.md) |
| Export a story, live, model or other subject | [Exports](export.md) |
| Convert selected bundles and objects | [Asset export](assets.md) |
| Use Japanese client files | [Japanese release](jp.md) |
| Produce song and chart statistics | [Music data](music-data.md) |
| Check your own outputs | [Validation](validation.md) |

## Reference

[CLI](cli.md) · [Command reports](commands.md) · [Python API](api.md) · [Schemas](schemas.md) ·
[Formats](formats.md) · [Products](products.md) · [Depot](depot.md) · [Contracts](contracts.md) ·
[Stages](stages.md) · [Views](views.md) · [Replay data](replay.md) · [UI library](ui.md)

[Architecture](architecture.md) explains the conversion and assembly layers.
[Determinism](determinism.md) explains task identities and output reproducibility.

## Requirements and versions

nnnotes 0.3.0 requires Python 3.11+. Optional extras provide fonts (`fonts`), S3 clients (`remote`) and
ournotes-sim 0.0.12 chart statistics (`deck`). Site builds require Node.js 22+ and a built ournotes-player 0.2.x
package. Audio and video conversion require vgmstream and FFmpeg.

Versions cover the CLI, configuration, schemas and documented Python API. Before 1.0, minor versions may change
these contracts; see the [changelog](https://github.com/MetaSekaiLab/nnnotes/blob/main/CHANGELOG.md).
This unofficial fan project is MIT-licensed. Game resources remain subject to their owners' rights.
