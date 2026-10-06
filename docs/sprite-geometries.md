# Original Sprite geometry

`nnnotes sprite-geometries` reads actual Sprite metadata through the same nnnotes
Exporter used for UI packs. It downloads only the explicitly selected bundle
closures and never decodes textures or writes game artwork into the repository.

```sh
nnnotes --config private.toml --catalog saved-catalog.bin sprite-geometries \
  --keys selected-keys.json -o ../private-ui/sprite-geometries.json
```

`selected-keys.json` is a JSON array of exact Addressables keys. `--key` is also
repeatable. The output follows
[sprite-geometries.schema.json](schema/sprite-geometries.schema.json).
Each `sprites["key[actual Sprite name]"]` entry contains the original `rect`,
`textureRect`, `textureRectOffset`, pivot, border, pixels per unit, packing flags
and downscale multiplier. Its source records the actual asset file, decimal
string path ID and SHA-256/size of every decrypted bundle read. Catalog hashes
identify the saved input; resource version/hash is included when known.

An APK adds its observed manifest fingerprint and client version. Missing client
fields remain absent. `--native-fingerprints` additionally hashes native
libraries from the supplied APK set. These observations do not certify current
runtime patches or pixel parity. No endpoint or session credentials are exported.

A public bitmap can be a tight integer crop while Unity Image still uses the
original logical Sprite rectangle. For `ournotes-player/ui`, bind that bitmap
with `_previewSource` and its original geometry with `_previewSpriteGeometry`.
The trimmed preview expects `floor(textureRect.min)..ceil(textureRect.max)` and
supports unrotated, unscaled crops; other bitmap contracts require explicit
conversion. Full textures already containing the logical Sprite rectangle must
not be labeled as a trimmed crop.

`--public-bitmaps observed-bitmaps.json` fingerprints a separate observation
file. A public URL, dimensions or file hash alone does not prove that its pixels
were generated from the native Sprite. Keep the native metadata and public
bitmap evidence distinct, and pin/verify both when publishing a UI library.
