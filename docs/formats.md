# Format reference

nnnotes publishes versioned JSON documents. The `schema` or `format` field identifies the document contract;
installed schemas are available through `nnnotes.schemas`. See the [schema index](schemas.md) for individual files.

| Document | Identity | Purpose |
|---|---|---|
| Task | `nnnotes.task/1` | Stage, version, parameters, inputs and task key |
| Result | `nnnotes.result/1` | Artifacts and item outcomes of a task |
| Run | `nnnotes.run/1` | Requested tasks, outcomes and retained layouts |
| Object table | `nnnotes.objects/1` | Converted objects, roles and reference closure |
| Product manifest | `nnnotes.product/1` | A product's files and the objects it uses |
| Layout | `nnnotes.layout/1` | Owned output paths and their stored content |

## Objects and references

Objects have stable addresses within their serialized file and are referred to by `<file>:<path id>`.
`{"$ref": "<object id>"}` names an object in the product's object table. Its roles identify JSON, images,
shader programs and binary files. `{"$hex": "..."}` carries inline hexadecimal bytes; blob descriptors identify
separate binary roles. Full representations and examples are in [products](products.md) and [contracts](contracts.md).

## Products

A product manifest names its `product`, `subject`, `files` and `objects`. Files point to content-addressed assets
and record decoded sizes. Encoded files additionally record stored sizes; split JSON files list their parts.
The manifest's optional `root` locates these paths relative to the manifest URL.

Story and live language parameters are part of their task identities. Their language-indexed part references
name the requested UI and font products. Site snapshots retain the selected available languages.

A site's chart alias may additionally carry `musicId`, `difficulty`, `chart`, `regions`, `quality`, `flows`,
`audio` and `audioFormat`. The chart facts agree with `charts.json`; its byte count covers unique assets in the
product closure, including variants. The supported start flow is `direct`. With audio disabled, `audio` is false
and `audioFormat` is null. Standalone live products provide chart facts through their `live.json` and music part.

For product composition see [products](products.md); for compressed files, regional aliases and publication
ordering see [site layout](site.md). The [depot reference](depot.md) describes storage and retention.
