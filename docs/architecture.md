# Architecture

nnnotes reads the game's files in layers: settings and sources at the bottom, file formats and Unity objects above
them, the game's content above those, then the pipeline, the products and the command line. Each layer is one
subpackage of `nnnotes` and imports only the layers below it.

## Layers

| Layer | Subpackage | What it holds |
|---|---|---|
| Command line | `nnnotes.cli` | one module per command group; parses options, reads the configuration, prints summaries and `--json` reports |
| Products | `nnnotes.site`, `nnnotes.musicdata` | the ournotes-player site (charts, models, stories, indexes); the music data file and its deck statistics |
| Pipeline | `nnnotes.pipeline` | stages, task descriptions and results, the content-addressed depot, plans, layouts, the catalog versions, the views of the master data |
| Content | `nnnotes.content` | the game's subjects as exports: ADV episodes and stories, lives and scores, spots, rooms, the UI library |
| Unity objects | `nnnotes.unity` | bundle reading, object ids, the atoms (the pure functions objects are converted with: texture decoding, sprite images, mesh arrays, glTF, PNG, file sniffing), the converters (one per Unity class, `nnnotes.unity.convert`), clip bindings named in the hierarchy that plays a clip (`nnnotes.unity.bindings`), the type trees shipped for the player's script classes (`nnnotes.unity.scripttypes`), the typetree exporter, the player data, Live2D prefabs, text styles and TextMesh Pro fonts |
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

`site build` uses the same product requests, then runs player read-set, asset, document and index tasks. A named catalog retains unselected topics and references immutable products. Publication writes dependencies before entry manifests and indexes. The successful run stores the complete layout so a new output directory can be restored through the depot.

`export episode`, `export shader`, `export audio` and `music-data` expose subject-specific data and conversions through the Session. All commands use the readers and converters below the pipeline layer.

## Outputs

Every JSON file is written by one writer (`nnnotes.core.jsonio`), which fixes the encoding, the line endings and the
number format; [determinism](determinism.md) explains what makes the outputs reproducible. Command reports and the
documents of the asset export have JSON Schemas shipped in `nnnotes.schemas` ([JSON schemas](schemas.md)).
