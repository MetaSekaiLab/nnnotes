# JSON schemas

The documents nnnotes writes in a fixed format have JSON Schemas (draft 2020-12), shipped in the package
`nnnotes.schemas`. Each schema has the `$id` `urn:nnnotes:schema:<name>`, and the shared definitions are in
`common`. Every document names its format and version in a `schema` field, such as `nnnotes.plan/1`; within a major
version fields are only added ([versioning](contracts.md#versioning)).

```python
from nnnotes import schemas

schemas.names()  # ['addresses', 'artifact', ...]
schemas.load("plan")  # the schema as a dict
```

| Schema | Format | Document |
|---|---|---|
| `addresses` | `nnnotes.addresses/2` | the address table of a catalog version and the censuses of its bundles |
| `artifact` | `nnnotes.artifact/1` | the content, provenance and semantics of one output file |
| `catalog-diff` | `nnnotes.catalog-diff/1` | the difference between two catalog versions (`catalog diff`) |
| `catalog-index` | `nnnotes.catalog-index/1` | the index of a catalog pair: remote and APK catalogs, every location in catalog order |
| `catalogs` | `nnnotes.catalogs/1` | the catalog versions of a depot (`catalog versions`) |
| `census` | `nnnotes.census/2` | the census of one bundle: serialized files, type trees, objects and what they reference, read without decoding |
| `common` | | definitions shared by the other schemas |
| `cost` | `nnnotes.cost/1` | the measured cost of a task's last run |
| `coverage` | `nnnotes.coverage/1` | the coverage of a run per class and per reason code |
| `failures` | `nnnotes.failures/1` | the failed tasks and items of a run |
| `layout` | `nnnotes.layout/1` | the files a layout owns in its output directory |
| `music-data` | `nnnotes.music-data/2` | every song and chart of one master data version ([music data](music-data.md)) |
| `objects` | `nnnotes.objects/1` | the objects a product reaches: their class, item status and files ([products](products.md)) |
| `plan` | `nnnotes.plan/1` | what a run would do and why (`depot plan`) |
| `product` | `nnnotes.product/1` | a product's manifest: its files and the files of the objects it uses, as content-addressed assets ([products](products.md)) |
| `result` | `nnnotes.result/1` | the result of a task, a function of its key alone |
| `run` | `nnnotes.run/1` | a run manifest: context, selection, tasks with keys and statuses, layouts |
| `scripts` | `nnnotes.scripts/1` | every MonoScript of the censuses, by object id |
| `task` | `nnnotes.task/1` | one stage applied to one subject, with everything needed to run it (`depot run`) |
| `view` | `nnnotes.view/1` | a semantic view of a master table, each row with its names and the objects it names |

The documents of the asset export are described in [contracts](contracts.md), the views in [views](views.md). The
schema files are in
[src/nnnotes/schemas](https://github.com/MetaSekaiLab/nnnotes/tree/main/src/nnnotes/schemas).
