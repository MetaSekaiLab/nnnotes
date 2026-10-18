# JSON schemas

The documents nnnotes writes in a fixed format have JSON Schemas (draft 2020-12), shipped in the package
`nnnotes.schemas`. Each schema has the `$id` `urn:nnnotes:schema:<name>`, and the shared definitions are in
`common`. Documents name their format and version in `schema` (such as `nnnotes.plan/1`) or `format`; within a major
version fields are only added ([versioning](contracts.md#versioning)).

```python
from nnnotes import schemas

schemas.names()  # ['addresses', 'artifact', ...]
schemas.load("plan")  # the schema as a dict
```

| Schema | Format | Document |
|---|---|---|
| `addresses` | `nnnotes.addresses/3` | the address table of a catalog version and the censuses of its bundles |
| `artifact` | `nnnotes.artifact/1` | the content, provenance and semantics of one output file |
| `catalog-diff` | `nnnotes.catalog-diff/1` | the difference between two catalog versions (`catalog diff`) |
| `catalog-index` | `nnnotes.catalog-index/1` | the index of a catalog pair: remote and APK catalogs, every location in catalog order |
| `catalogs` | `nnnotes.catalogs/1` | the catalog versions of a depot (`catalog versions`) |
| `census` | `nnnotes.census/2` | the census of one bundle: serialized files, type trees, objects and what they reference, read without decoding |
| `common` | | definitions shared by the other schemas |
| `cost` | `nnnotes.cost/1` | the measured cost of a task's last run |
| `coverage` | `nnnotes.coverage/1` | the coverage of a run per class and per reason code |
| `crilips` | product document | CRI Lips tensor blocks and frontend constants |
| `episode` | product document | story commands and resource tables |
| `failures` | `nnnotes.failures/1` | the failed tasks and items of a run |
| `layout` | `nnnotes.layout/1` | the files a layout owns in its output directory |
| `live` | `1` | live chart entry and shared parts |
| `live-music` | `1` | music layers and cue metadata |
| `live-noteeffect` | `1` | note effect resources and animation bindings |
| `live-notes` | `1` | note prefabs, settings and animations |
| `live-noteskin` | `1` | note skin resources and animation bindings |
| `live-scene` | `1` | scene objects, settings and band timelines |
| `live-sounds` | `1` | sound groups and category routing |
| `live-ui` | `nnnotes.live-ui/1` (`format`) | live start strings, text styles, glyph pages and image bindings |
| `model-json` | `3` | Live2D model objects and animation bindings |
| `music-data` | `nnnotes.music-data/2` | every song and chart of one master data version ([music data](music-data.md)) |
| `objects` | `nnnotes.objects/1` | the objects a product reaches: their class, item status and files ([products](products.md)) |
| `plan` | `nnnotes.plan/1` | what a run would do and why (`depot plan`) |
| `prefab` | product document | prefab nodes, transforms and components |
| `product` | `nnnotes.product/1` | a product's manifest: its files and the files of the objects it uses, as content-addressed assets ([products](products.md)) |
| `result` | `nnnotes.result/1` | the result of a task, a function of its key alone |
| `room` | product document | room geometry, materials and textures |
| `run` | `nnnotes.run/1` | a run manifest: context, selection, tasks with keys and statuses, layouts |
| `scripts` | `nnnotes.scripts/1` | every MonoScript of the censuses, by object id |
| `site` | `nnnotes.site/1` | a named site's complete region-topic catalog and immutable product references |
| `spot` | product document | spot settings, characters and Spine data |
| `story` | `1` | story entry and shared parts |
| `story-asset` | `1` | story media prefab objects |
| `story-fonts` | `nnnotes.story-fonts/1` (`format`) | an episode's text bindings, font records, glyph pages and text shaders |
| `story-host` | `nnnotes.story-host/1` (`format`) | an Overlay story's host screen, layout and initial state |
| `story-scene` | `1` | story scene objects and graphics settings |
| `story-simple-ui` | `nnnotes.story-simple-ui/1` (`format`) | localized Overlay talk-window and system-message prefabs |
| `story-sound` | product document | cue metadata and waveform files |
| `story-ui` | `1` | localized story UI and text bindings |
| `story-video` | `1` | video dimensions, timing and media tracks |
| `task` | `nnnotes.task/1` | one stage applied to one subject, with everything needed to run it (`depot run`) |
| `ui-library` | `nnnotes.ui-library/1` | selected UI keys and their product manifests |
| `ui-pack` | `nnnotes.ui-pack/1` | native UI roots, prefab and controller identities, animation bindings |
| `view` | `nnnotes.view/1` | a semantic view of a master table, each row with its names and the objects it names |


The documents of the asset export are described in [contracts](contracts.md), the views in [views](views.md). The
schema files are in
[src/nnnotes/schemas](https://github.com/MetaSekaiLab/nnnotes/tree/main/src/nnnotes/schemas).

## Product documents

The product root and prefab schemas are listed in [Product document schemas](contracts.md#product-document-schemas). Their fields match the shared object interfaces consumed by ournotes-player.

Compare the shared constraints with an ournotes-player checkout or extracted package:

```sh
python scripts/check_product_schemas.py /path/to/ournotes-player
```

The check covers product, live, model, story, prefab, room, spot, audio, video and UI documents. Schema identifiers,
titles and descriptions may differ by package; validation constraints must agree.
