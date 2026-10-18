# UI libraries

`nnnotes export ui` assembles an offline UI library for `ournotes-player/ui`. The supplied APK set provides the
catalog keys, native prefab fields, animations, sprites and fonts. The depot stores their converted objects and
product results; the output directory is a product layout.

```sh
nnnotes --apk /path/to/base.apk export ui --prefix EmbUI/Prefab/ --limit 10 -o /path/to/ui-data
nnnotes --apk /path/to/base.apk export ui --key YOUR_EXACT_APK_KEY -o /path/to/ui-data
```

`--key` is repeatable. Otherwise `--prefix` selects APK asset keys and defaults to `EmbUI/`; `--limit` takes the
first keys in sorted order. `--stored-catalog` selects a saved catalog version. The common `--workers`, `--link`,
`--dry-run` and `--explain` options apply. The output describes the complete selection of that invocation;
repeated invocations reuse unchanged depot tasks. Reports under `_reports/` describe coverage and failures.

## Input configuration

Configure the APK set and depot as described in [configuration](configuration.md). APK input may be a single APK,
base.apk with adjacent splits, a split directory, or an APKS/XAPK archive. Region selection chooses its configured
APK. Keep each library's output directory associated with its intended input configuration.

## Product layout

`index.json` has `format: "nnnotes.ui-library/1"`:

- `assets`: selected catalog keys, display names, stable key IDs, root object IDs and product manifest paths.
- `embedded`: referenced prefab roots, identified by serialized object ID.
- `controllers`: referenced AnimatorController objects, with their object IDs and containing product manifests.

Each `uipacks/<id>.json` is a `nnnotes.product/1` manifest for `ui.pack`. Its `files` map contains `ui.json`, and its
`objects` map lists each retained object's files as content-addressed assets. Texture images, embedded Font data,
TMP font records, clips, controllers and prefabs use the shared object conversion layer.

`ui.json` has `format: "nnnotes.ui-pack/1"`, the catalog `key`, `roots`, `prefabs`, `controllers`, display `entries`,
`shaders`, and per-prefab animation `bindings`. Nodes preserve their serialized transforms, rectangles, active
flags, component type/script/fields and path IDs. References use object identity or a local hierarchy target.
Clips retain their raw curves; bindings name each curve's path and property in the Animator's hierarchy.

UI products include the fonts referenced by their object graph. Product stages opt into this traversal with
`INCLUDE_FONTS`; language-specific story products select fonts through their own products.

See [ui-pack schema](schemas.md) and [products](products.md) for the format contracts. The preview module provides
Canvas 2D inspection, animation sampling and editable copies of prefab state. It supports a defined subset of
runtime behavior; its API documents the rendering and animation limits.
