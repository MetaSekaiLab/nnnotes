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

## Two ways to export

- **One subject.** `export episode`, `export story`, `export shader`, `export audio`, `export ui`, `site build`
  and `music-data` read what one subject needs through the Session and write their output directory directly
  ([Export stories and lives](export.md)). `export live`, `export live2d`, `export spot` and `export room` ask the
  pipeline below for the products of one subject ([products](products.md)).
- **Every asset.** `export assets` splits the work into stages ([stages](stages.md)). A task is one stage applied to
  one subject (a bundle, a content id, a view); its key covers exactly what its output depends on: the stage and
  converter versions, the input contents and the options ([contracts](contracts.md)). Results and their files are
  stored once by content hash in the depot, and the output directory is laid out from the depot. A second run finds
  every unchanged result in the depot and only lays out what changed; `depot plan` says what would run and why
  ([asset export](assets.md)).

Both ways use the same readers below the pipeline layer, so a texture, shader, mesh or audio stream is converted by
one implementation whichever command asks for it.

## Outputs

Every JSON file is written by one writer (`nnnotes.core.jsonio`), which fixes the encoding, the line endings and the
number format; [determinism](determinism.md) explains what makes the outputs reproducible. Command reports and the
documents of the asset export have JSON Schemas shipped in `nnnotes.schemas` ([JSON schemas](schemas.md)).
