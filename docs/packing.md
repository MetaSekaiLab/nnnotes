# Build and edit asset bundles

For complete song projects and common audio to ACB conversion, see [Package a song](song-packing.md).

`nnnotes pack build` constructs UnityFS bundles from assets you supply. A template provides the Unity version,
serialization schemas and container flags. The default `new` mode starts with an empty object and resource table;
`edit` mode keeps existing objects and applies selected changes. Both save with `packer="original"` and check the
saved header, object table and container references.

## Build a bundle and catalog

Save this as `build.json`, with paths relative to that file:

```json
{
  "template": "templates/text.bundle",
  "mode": "new",
  "bundle_name": "example_assets_v1",
  "internal_id": "https://example.com/asset/Android/example_assets_v1.bundle",
  "assets": [
    {
      "type": "TextAsset",
      "name": "example",
      "file": "assets/example.bytes",
      "container": "Assets/Examples/example.bytes",
      "primary_key": "Examples/Text"
    }
  ]
}
```

```sh
nnnotes pack build build.json -o build/example_assets_v1.bundle \
  --encrypt served/example_assets_v1.bundle \
  --catalog catalog.bin --catalog-out build/catalog.bin \
  --catalog-add build/catalog-add.json
```

The command builds and validates the outputs before writing them. It writes the plain UnityFS file, an encrypted
file when `--encrypt` is supplied, and an updated catalog when `--catalog-out` is supplied. The plain file is kept
for inspection and further catalog generation. The optional sidecar can be consumed independently:

```sh
nnnotes catalog rebuild catalog.bin --add build/catalog-add.json -o build/catalog.bin
```

Give each independently loaded new bundle a distinct `bundle_name`. New serialized file identities are derived
deterministically from that name. `cab_name` can specify the serialized file identity explicitly. An asset's
`name` is its object name, `container` is the path used by Unity to load it, and `primary_key` is the Addressables
lookup key. The catalog's asset internal ID must match the container path. A bundle's internal ID ends with the
served file name; that same name seeds encryption. `--name` specifies it explicitly when output names differ.

In edit mode, changing `bundle_name` also gives a single serialized file an independent identity while preserving
objects and managed-reference schemas. Internal archive resource paths are remapped. An explicit `cab_name`
also applies in edit mode.

## Asset inputs

| Type | Input | Result |
| --- | --- | --- |
| `TextAsset` | Any file | Binary payload, preserved without text decoding |
| `Texture2D` | PNG | RGBA32 pixels, one image and one mip level |
| `AudioClip` | Mono or stereo 16-bit integer PCM WAV | Single-sample PCM FSB5 bank and AudioClip resource reference |
| `serialized` | Complete `fields` object or JSON file | Object serialized with a selected or explicit type tree |
| `raw` | Serialized object bytes | Bytes supplied by the caller, with `managed_references: true` |

Built-in types need a compatible schema in the bundle template. An asset can use another template with
`"template": {"file": "templates/texture.bundle", "type": "Texture2D", "name": "texture"}`. Its Unity version,
serialized format, endianness and target platform must match the bundle. Select a unique object by `type`, `name`
or `path_id`. A bundle containing multiple serialized files requires `serialized_file` in the build manifest.

For custom serialized objects, supply a template selector or `schema` with `class_id`, `type_hash`, `nodes` and,
when needed, `script_id`, `script_type_index` and type dependencies. `typetree` overrides the selected object's
type tree. Complete fields must include the object's required pointers and metadata. Local pointer targets are
checked and included in preload ranges. Explicit external references use a top-level `externals` array of
`{path, guid?, type?}` objects, with a 32-digit zero GUID and type `0` as defaults. Their order gives one-based
PPtr file IDs. Top-level `dependencies` lists internal bundle names for Unity; `dependency_keys` lists catalog
keys for Addressables. Supply the corresponding external bundles separately. The client must support the
serialized class and script schema. New managed-reference registry schemas are currently unsupported.

An operation that fails invalidates the builder; start a new builder before retrying. Output files are staged
before replacement. If a filesystem error also prevents restoration, recoverable backups are retained and their
paths are included in the error.

Raw inputs require explicit reference ownership because arbitrary serialized bytes do not describe their
dependency graph. `AudioClip` output is a Unity audio resource; a client that requests a separate cue-sheet or
sound-bank class needs an asset of that class and its dependencies.

When a source catalog is supplied, generated bundle locations use its unique bundle provider. This preserves a
custom provider's download and decoding behavior. A catalog with multiple bundle providers requires either
`"bundle_provider": "ExampleEncryptedBundleProvider"` or `"catalog_template": "source.bundle"` in the manifest.
The template selects an existing bundle by primary key or full internal ID and also copies its timeout, redirect
limit, retry count and flags. Explicit `bundle_provider` and `request_options` fields take precedence. The new
bundle's CRC, size, cache hash and internal name are computed separately. Without a source catalog, the default is
Unity's standard AssetBundleProvider; use an explicit provider when the runtime loads encrypted bundles through
a custom provider. The runtime must already register that provider.

These fields also apply to `catalog rebuild --add` bundle descriptions. A sidecar written by `pack build` records
the chosen provider and request options, so importing it retains that selection. Asset-level `provider` controls
the bundled asset loader independently.

## Edit existing objects

Use `"mode": "edit"` and describe operations in order:

```json
{
  "template": "input.bundle",
  "mode": "edit",
  "assets": [
    {"op": "replace", "type": "TextAsset", "name": "example", "file": "changed.bytes"},
    {"op": "rename", "name": "example", "new_name": "updated", "new_container": "Assets/Examples/updated.bytes"}
  ]
}
```

`op` defaults to `add`. New objects receive unique positive path IDs, or an explicit `path_id`. Replace and rename
operations select an existing object with `target`, or their `name`/`path_id` fields. Object renaming preserves the
container path unless `new_container` is supplied. Use `primary_key`, `aliases`, `provider`, `resource_type`,
`resource_type_assembly` and `dependency_keys` to describe catalog locations when needed.

With a source catalog, `edit` updates the matching bundle location and only the asset locations affected by the
manifest. Asset locations match the source container path and the bundle dependency; existing keys, aliases,
types, providers and dependencies are retained. A container rename updates those locations' internal IDs.
Explicit catalog fields override the retained values, including empty `aliases` or `dependency_keys` arrays.
Other locations sharing a lookup key remain available. Added assets receive new locations. The edited bundle's
sidecar records these changes and applies the same incremental update through `catalog rebuild --add`.

The compact TextAsset replacement command is also available:

```sh
nnnotes pack asset input.bundle -o build/input.bundle --name example --data changed.bytes
nnnotes catalog rebuild catalog.bin -o updated-catalog.bin --bundle build/input.bundle
```

The replacement bundle's file name must identify its existing catalog location. The second command updates its
content CRC, size and cache version. `--hash HEX` supplies a particular 32-digit cache hash.

## Catalog and encryption fields

Catalog request options use the CRC32 of concatenated decompressed UnityFS blocks, excluding the header and
block/directory information. `bundleSize` is the served byte length, which encryption preserves. The default
cache hash is MD5 of the plain UnityFS bytes: a deterministic Hash128 cache version. It can be overridden; it is
independent of Unity's build pipeline hash. `bundleName` identifies the bundle in the cache.

`catalog rebuild --add` also accepts bundle descriptions with `file`, `internal_id`, `bundle_name` and `assets`.
It computes CRC, size and cache hash from the plain file. Low-level location descriptions remain available for
custom providers, with dependencies named by key. Existing catalog headers, typed keys and dependencies survive
rebuilding. Missing dependencies, invalid fields and unsupported extra-data types stop the command.

Encryption uses `[bundle] key` and `[bundle] nonce_seed` from the local configuration. AES-128-CTR transforms the
first 16 KiB; the remaining bytes are preserved. The nonce is the first eight bytes of SHA-256(seed followed by
the UTF-8 served file name), followed by a 64-bit big-endian counter starting at zero. To encrypt existing plain
bundles independently, use `nnnotes pack bundle INPUT -o DIR [--name NAME]`.
