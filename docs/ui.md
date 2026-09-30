# Serialized UI libraries (experimental)

`nnnotes ui` exports an offline prefab library for the optional `ournotes-player/ui` preview module. It reads the
embedded Addressables catalog and bundle closure of the APK set you supply; it does not contact a game API. Keep
outputs outside the nnnotes and ournotes-player source repositories. The export contains game data that these
repositories and their packages do not redistribute.

```sh
nnnotes --apk /path/to/base.apk ui --prefix EmbUI/Prefab/ --limit 10 -o /path/to/ui-data
nnnotes --apk /path/to/base.apk ui --key YOUR_EXACT_APK_KEY -o /path/to/ui-data
```

`--key` is repeatable. Without it, `--prefix` defaults to `EmbUI/`. `--no-dependencies` omits additional root
prefabs and AnimatorControllers; `--force` re-exports the selected keys. A failed key is retained as an explicit
index entry and makes the command exit with status 1. Existing unrelated entries of the same input stay present.

## Inputs and regions

The command uses the same `ApkSet` reader as the existing extractors: one APK, base.apk with adjacent splits, a
directory of splits, or an APKS/XAPK archive. Explicit `--apk` wins over the selected region's APK configuration.
The configured cache and bundle decryption settings are used when needed. Readable unencrypted local bundles do
not require a key. Default Unity resources are read from the selected APK set through the existing player reader.

```sh
nnnotes --region jp ui --prefix EmbUI/Prefab/ --limit 10 -o /path/to/jp-ui-data
```

For JP, configure the actual JP APK set as described in [jp.md](jp.md). Do not point a JP region at an international
APK: this command exports the APK selected by the configuration, not a replacement fetched from that region.
Keep international and JP libraries in separate output directories. Their keys, classes and fonts can differ.

Current real UI smoke checks used international client 1.0.1 (versionCode 25). A synthetic split-APK test verifies
the JP command/configuration path; it is not a full JP UI export or pixel-fidelity verification. Existing JP
catalog, chart, story and model validation is described separately in [jp.md](jp.md).

## Files and resume

`index.json` has `schema: 1`, `format: "ournotes-ui-library"`, and three lists:

- `assets`: selected catalog keys, IDs, kind, export status, pack file and SHA-256; failures contain an error type
  and message rather than a pack file.
- `embedded`: additional root GameObjects found in a selected key's dependency closure.
- `controllers`: AnimatorControllers found in that closure, keyed by actual serialized object identity.

Pack files under `packs/`, `embedded/` and `controllers/` have `format: "ournotes-ui-pack"`, a `document`,
`resources` and `resourceBase: "../"`. Hierarchy nodes retain serialized rectangles, transforms, active flags,
component fields and preorder paths. `nodeId` and reference `nodeId` values distinguish same-named instances;
controller references carry a matching ID. Negative intermediate rectangle sizes are preserved.

Resources contain Sprite metadata and texture/font paths. PNGs and embedded TTFs are written under `textures/`
and `fonts/`. Each used VibeMO numeric font face has separate ASCII glyph metrics in `fontMetricsByAsset`; the
legacy `fontMetrics` field names the first one. A metrics file's texture is relative to that file when its
`textureBase` is `"metrics"`. Rotated packed sprites are decoded to standalone RGBA images.

The index records hashes of every pack and its texture/font/metrics files, as well as dependency IDs. A resumed
key is skipped only when these files and its dependency records still validate. Missing or corrupt files are
re-exported. Provenance records hashes of the APK catalog and effective APK-set member metadata, the manifest
version and configured region; it contains no credentials or server addresses. A different input is rejected
before updating an existing library. The index is replaced atomically after each key.

## Scope

This is inspectable serialized data, not an implementation of every game Presenter. The preview implements a
subset of layout, graphics, component binding and animation. Exported particle, shader/material, localization,
video, camera and custom component fields do not imply that the preview executes them. `runtime_verified` stays
false. The companion player documentation lists rendering limits and the optional viewer.
