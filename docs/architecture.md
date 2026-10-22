# Architecture

nnnotes reads the game's files in layers: settings and sources at the bottom, file formats and Unity objects above
them, the game's content above those, then the pipeline, the products and the command line. Each layer is one
subpackage of `nnnotes` and imports only the layers below it.

## Layers

| Layer | Subpackage | What it holds |
|---|---|---|
| Command line | `nnnotes.cli` | one module per command group; parses options, reads the configuration, prints summaries and `--json` reports |
| Products | `nnnotes.site`, `nnnotes.musicdata`, `nnnotes.packing` | the ournotes-player site; music data and deck statistics; local song and audio packages |
| Pipeline | `nnnotes.pipeline` | stages, task descriptions and results, the content-addressed depot, plans, layouts, the catalog versions, the views of the master data |
| Content | `nnnotes.content` | the game's subjects as exports: ADV episodes and stories, lives and scores, spots, rooms, the UI library |
| Unity objects | `nnnotes.unity` | bundle reading, object ids, atomic texture/sprite/mesh/glTF/PNG conversions, class converters (`nnnotes.unity.convert`), clip bindings (`nnnotes.unity.bindings`), script type trees (`nnnotes.unity.scripttypes`), player data, text styles, font protocols and texel packing |
| File formats | `nnnotes.formats` | formats read from bytes: CRI ACB/AWB and HCA, USM movies, CRI Lips data, shaders, motions |
| Sources | `nnnotes.source` | the configuration, the APK, Addressables catalogs and bundles, the game API, master data, languages, `Session` |
| Core | `nnnotes.core` | the one JSON writer, canonical JSON and its hashes, media types, the process caches and the downloads cache |

The order is a contract: the `layers` contract of [import-linter](https://import-linter.readthedocs.io/) in
`pyproject.toml` fails the build when a module imports a layer above its own, and `nnnotes.site` and
`nnnotes.musicdata` do not import each other. No library module imports `nnnotes.cli`: the commands parse options
and print, and the work they run is in the library modules below them.

## Sources and the Session

Every setting comes from the configuration ([sources and precedence](configuration.md#sources-and-precedence)).
`nnnotes.source.session.Session` turns one configuration into the data sources a command needs: the catalog of a
region, its decoded master data and the master data version it serves, the APK, the bundle and master data keys and
the depot root. It reads a setting when it is first needed and raises a `ConfigError` naming the setting when it is missing or wrong;
the command line turns that error into exit status 2.

The catalog (`nnnotes.source.catalog.Catalog`) resolves an addressable key to its bundle and the bundle's
dependency closure, takes a bundle from the APK when the APK has it and from the CDN otherwise, and keeps fetched and
decrypted bundles in the depot's `downloads/` directory.

## Product and asset requests

`export assets` runs stages whose tasks are keyed by their input contents, options, stage versions and converter identities. Results and files are stored in the depot; layouts place their content into output directories. `depot plan` reports which tasks and placements would change.

`export live`, `export story`, `export live2d`, `export spot`, `export room` and `export ui` request the products of selected subjects through that same pipeline. Stories first produce an episode, then request its referenced products. Shared objects and unchanged products reuse committed tasks.

Class converters emit object records and binary files. Product assemblers read those stored records through an object table and identify shared dependencies by object id and product manifest. Text products use the same font lookup, material and in-memory atlas functions. A product request contains its complete object and media dependencies before assembly.

`site build` uses the same product requests, then runs player read-set, asset, document and index tasks. A named catalog retains unselected topics and references immutable products. Publication writes dependencies before entry manifests and indexes. The successful run stores the complete layout so a new output directory can be restored through the depot.

The site's `Builder` composes product snapshots, catalog indexes and player page files. Product assembly and conversion belong to the pipeline and content layers. `catalog_models` queries model subjects from a source catalog.

Converters use bounded process-local memoization for repeated reads and computations. Persistent reuse is keyed by depot tasks and their content inputs. Clearing a process memo releases its values; a later task can reuse its committed depot result.

The coordinator observes successful object batches after worker readers close. Their recoverable bundle downloads
can then be released toward the configured budget under the command's exclusive lease. Preliminary episode
requests retain inputs until the expanded product request is known. Stored conversion results remain available
for assembly and layouts.

`export episode`, `export shader` and `export audio` request depot tasks and project their results into files.
Episode assembly reads the common object table, shader files reuse converted Shader artifacts, and audio files
reuse `cri.audio` streams. `music-data` uses Session to obtain catalog and master inputs for its tabular export.
Commands record their accessed depot content and results as retention roots; dry runs leave no command record.

## Outputs

Every JSON file is written by one writer (`nnnotes.core.jsonio`), which fixes the encoding, the line endings and the
number format; [determinism](determinism.md) explains what makes the outputs reproducible. Command reports and the
documents of the asset export have JSON Schemas shipped in `nnnotes.schemas` ([JSON schemas](schemas.md)).
