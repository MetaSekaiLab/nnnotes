# Products

A product is something a player or a site reads as a whole: a Live2D model, a story episode, a live. Products are
assembled from the results of `unity.export` ([stages](stages.md)), never from a bundle: by the time a product is
made, every object it uses has been converted once, and the product combines those conversions with what it adds
itself.

## Product stages

A product stage (`nnnotes.pipeline.assemble.ProductStage`) has one task per subject. Its task starts from root
objects:

- the objects of the catalog keys the stage gives for the subject (`keys`), through the address table of
  `link.addresses`;
- the object ids the stage names besides (`root_objects`).

Both are read from the planning facts alone, so a command can ask for what its products need before anything runs:
`assemble.request(stages, products, env)` gives the fact `roots` (`{"keys", "objects"}`), and the request of
`unity.export` then holds the units of those objects and every unit reachable from them through the census `refs`
([units](stages.md#units)), and nothing else of their bundles.

What a task reaches: the units holding its roots, then every unit that an item of a reached unit references (the
item's `refs`), through the `unity.export` results. A sprite whose atlas is in another bundle has its `image` from the
`sprite.crop` result of that atlas. A reached unit that the request did not export is an error of the request; an
object that a reached object references and no census holds is listed as `missing`.

| Part of the task | |
|---|---|
| inputs | `objects`: the object table of what it reaches (`nnnotes.objects/1`); the stage's own inputs (master tables, data its parameters name) |
| depends | `link.addresses` (when it has keys), the `unity.export` and `sprite.crop` tasks whose results it read |
| artifacts | `<task id>#<path>`: the product's own files; `<task id>#objects`: the object table of the objects its files use (the whole table unless the stage narrows it) |
| item | one for the subject: `exported`, or `unsupported` with a reason code |

The key holds the object table, so a product is assembled again exactly when a file of an object it reaches
changes, or what it reaches changes.

## Object table: nnnotes.objects/1

([objects.schema.json](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/schemas/objects.schema.json))

```json
{"schema": "nnnotes.objects/1",
 "roots": ["CAB-…:-4153…"],
 "objects": {
   "CAB-…:-4153…": {"status": "exported", "class": "GameObject", "name": "model",
                    "files": {"json": {"sha256": "…", "size": 5120, "ext": "json"}}},
   "CAB-…:12": {"status": "contained", "class": "Transform", "in": "CAB-…:-4153…"},
   "CAB-…:40": {"status": "exported", "class": "Texture2D", "name": "texture_00",
                "files": {"image": {"sha256": "…", "size": 812345, "ext": "png"},
                          "json": {"sha256": "…", "size": 640, "ext": "json"}}}},
 "missing": []}
```

Each object has the status of its item (the most severe when two stages made items for it), its class and name
where known, its files by role (the artifacts of its items), the object whose file holds it (`in`, a contained
object), or the reason code of a generic, unsupported or failed object.

## Product layout

A product layout places products for a player:

- every file of a product and every file of the objects of its object table at `assets/<sha256>.<ext>` (a file
  several products or objects share is written once);
- each product's manifest at `<directory>/<file>.json` (the stage's `manifest(subject)`, by default
  `<stage>/<subject>.json` with the subject escaped as one path segment).

## Product manifest: nnnotes.product/1

([product.schema.json](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/schemas/product.schema.json))

```json
{"schema": "nnnotes.product/1", "product": "live2d.model", "subject": "…",
 "files": {"model.json": {"asset": "assets/<sha256>.json", "size": 2048}},
 "objects": {
   "CAB-…:-4153…": {"class": "GameObject", "name": "model",
                    "files": {"json": {"asset": "assets/<sha256>.json", "size": 5120}}},
   "CAB-…:12": {"class": "Transform", "in": "CAB-…:-4153…"}}}
```

`files` are the product's own files by path; `objects` are the objects of its object table that have files, or are
contained in an object that has one. An asset path is relative to the directory above the manifest's directory, as
in the chart manifests of ournotes-player. A reference in an object's JSON (`{"$ref": {"file", "pathId"}}`) names
the object `<file>:<pathId>` of `objects`.
