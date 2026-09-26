# Stages, converters and atoms

The asset export is built from three layers:

- **Stages** apply one unit of work to one subject (a bundle, a content id, a model key, a view). Each has a name and
  a version; a task is a stage applied to one subject, and its key covers exactly what its output depends on
  ([contracts.md](contracts.md)).
- **Converters** turn the objects of one class inside `unity.export` into artifacts. Each carries its own version; a
  bundle's task includes only the converters of the classes the bundle holds.
- **Atoms** are the pure functions stages and converters are made of: no file or network IO, no threads, the same
  output for the same input. Each atom has exactly one reference implementation.

## Stages

Every stage is at version 1, except `unity.census` (version 2: it lists the scripts animation clip bindings name).

| Stage | Subject | Inputs | Outputs |
|---|---|---|---|
| `catalog.index` | a catalog (remote + APK) | the catalog files | every location (with the bundle hash, crc and size), bundles and raw remote files |
| `unity.census` | a bundle | the bundle | serialized files, externals, objects (file, path id, class, size, type hash, name, script, the scripts an AnimationClip's bindings name), container, scene hashes, dependencies, script fields |
| `link.scripts` | global | the censuses holding MonoScripts | serialized file and path id -> assembly, namespace, class |
| `unity.export` | a bundle | the bundle, the script entries its MonoBehaviours use | the objects' artifacts and one item per object |
| `sprite.crop` | a SpriteAtlas | the atlas' JSON, the images of its textures, the metas of the sprites of other bundles it packs | the sprites' images |
| `cri.audio` | ACB content | `acb` (a raw file of the catalog, or the `acb` artifact of a cue sheet held in a bundle), `awb` (an AWB the catalog pairs with it), `boot` (the game's boot data: the key's source) | FLAC per stream, `cues.json`, `streams.json` |
| `cri.movie` | USM content | `usm`, `boot` | `video.ivf` and `audio.adx` (the streams as stored), `movie.mkv` (or `movie.webm`) |
| `live2d.model` | a Live2D model key (`Character/Live2D/<group>/<name>/model/<name>`) whose bundle is selected | `bundle:<n>`: the bundles of the key's dependency closure in the order the extractor loads them (the order is part of the key); context `internalId` (the key's location); atom `live2d.extract_model` (the extractor of `nnnotes live2d`, run unchanged) | `live2d.model:<key>#<path>`: every file `nnnotes live2d` writes for the model, `<path>` relative to the model's directory; placed under `<key>/` |
| `spine.skeleton` | a SkeletonDataAsset of a selected bundle, by its stable address `<bundle>:<pathId>` (in a bundle of several serialized files the file's tag follows the bundle name, as in `link.addresses`) | `bundle:<stable name>`: the bundles holding the serialized files the asset's file reaches through its externals; context `object` (the asset's object id); atom `spot.write_skeleton` (the Spine writer of `nnnotes spot`) | `spine.skeleton:<subject>#<file>`: the skeleton (`.json` or `.skel`), its atlases (`.atlas`) and their page images, the bytes `nnnotes spot` writes into `spine/`; one item for the asset |
| `link.addresses` | global | catalog index, censuses | key -> objects, serialized file -> bundle, bundle -> keys |
| `link.artifacts` | global | the above, the results | object id -> artifact ids |
| `view.<name>` | a catalog (`main`) | `master:<Table>` per master table it reads, `rules` (the view's rule and the resolvers it uses), `addresses` (the part of the address table it read: the keys looked up and the prefixes listed); no parameters, atoms or context | `view.<name>:<subject>#view` (facts: rows, entries, gaps, unreferenced) |

## Converters (`unity.export`)

`unity.export` reads one bundle alone and gives every object of it exactly one item status: `exported`,
`contained` (part of another artifact), `generic` (typetree JSON because the specialized form could not be made;
the reason says why), `unsupported` (a class the stage does not convert; its typetree JSON is written as well) or
`failed` (not even the typetree JSON could be made). A converter's documented limit gives the generic typetree JSON
with its reason code; any other error of a converter gives it with `generic.error` and the error's message.

| Class | Converter | Artifacts | Atoms | Notes |
|---|---|---|---|---|
| Texture2D | `tex.png/1` | `image`: PNG of the first mip level (HDR ASTC: `.astc`) | `texture.decode`, `png.encode`, `astc.container` | further mip levels as facts; a texture without pixels: `empty.texture` |
| Sprite | `sprite.png/1` | `image`: PNG on the sprite's rect (packing undone, tight mask applied), `meta`: JSON | `texture.decode`, `sprite.crop`, `png.encode`, `mesh.arrays` | the texture is decoded once per task; a sprite whose SpriteAtlas is in another bundle gets its `meta` here (with its mesh and object facts) and its `image` from `sprite.crop` |
| Mesh | `mesh.glb/1` | `mesh`: binary glTF in the mesh's local space | `mesh.arrays`, `gltf.write` | see `gltf.write` below; limits of the skin and blend shapes as issues of the artifact |
| GameObject (a root) | `prefab.json/1` | `prefab`: the hierarchy's nodes (path, path ids, transform) with their components' fields | | the objects below it are `contained`; a root of a scene's file names its scene |
| Material | `material.json/1` | `json`: shader, keywords, textures, floats, colours | | |
| Shader | `shader.json/1` | `json`: the parsed form (the bytes of `nnnotes shader`'s `<name>.json`), `program:<platform>/s<S>p<P>_<stage>_<N>`: each compiled sub-program as stored (`.glsl`, `.metal`, `.vkprog`), `index`: the variants (platform, subshader, pass, stage, GPU program type, keywords, program role), `typetree`: the typetree JSON | | a shader without a parsed form: its typetree with `generic.error` |
| AnimationClip | `clip.json/1` | `json`: the Mecanim clip data with its bindings resolved in the bundle | | a legacy or compressed clip: `generic.clip.legacy` |
| AnimatorController | `controller.json/1` | `json`: layers, state machines, parameters | | an AnimatorOverrideController: `generic.controller.override` |
| MonoBehaviour (not in a hierarchy) | `mono.json/2` | `json`: `$script` (assembly, namespace, class) and the typetree; a cue sheet asset also `acb` (see below) | `sniff` | the class from the task's script table |
| TextAsset | `data/1` | `data`: the stored bytes | `sniff` | extension from the container path, else from the content |
| Font | `font/1` | `font`: the font file | `sniff` | |
| MonoScript | `scripts.json/1` | `<task id>#scripts.json`: every MonoScript of the bundle | | |
| AssetBundle | `bundle.json/1` | `<task id>#bundle.json`: serialized files, externals, the AssetBundle objects | | |
| any other class | `generic.json/1` | `json`: the typetree | `sniff` | |

A task's key includes the atoms `reader` and `sniff`, the converter `generic.json`, and the converters (with their
atoms) of the classes the bundle's census lists, so a converter's version bump runs again exactly the bundles holding
its classes. `classes` (a list of class names) limits a task to the objects of those classes and the objects their
hierarchies contain.

Object JSON is canon/1 in the object's own field order:

- a reference is `{"$ref": {"file": ..., "pathId": ...}}`, with `class` and `name` when the target is in the
  bundle; a null reference is `null`. Inside a prefab a GameObject, Transform or component of the same prefab is
  named by its path: `{"gameObject": path}`, `{"transform": path}`, `{"component": class, "gameObject": path}`;
- a byte array (a vector of UInt8, SInt8 or char, or TypelessData) shorter than `blobMin` bytes (4096) is
  `{"$hex": "..."}`; a longer one is `{"$blob": {"sha256": ..., "size": ..., "format": ...}}` and its bytes are the
  artifact `<object id>#blob:<field path>` (field names and array indices joined by `/`, the format by `sniff`);
- a string whose stored bytes are not UTF-8 is `{"$hex": "<the stored bytes>"}`;
- NaN is `{"$float": "nan"}`, infinities `1e999` / `-1e999`.

A cue sheet held in a bundle is a MonoBehaviour in one of two layouts; either gives the artifact
`<object id>#acb` (kind `cri.acb`, facts: `cueSheet`, `layout`), which `cri.audio` decodes:

- a SplitAcbData (fields `_cueSheetName`, `_chunks`): its chunk TextAssets (of the same bundle, exported as well) joined
  in order, every byte XORed with the one mask that gives the ACB signature `@UTF` (layout `split`; its refs name
  the chunks);
- an asset named like the sheet whose `implementation` managed reference is a CriSerializedBytesAssetImpl: its
  `data.data` bytes (layout `embedded`), whatever their size; that field of the JSON is the `{"$blob"}` of the `acb`
  artifact. One that names an AWB object gives `unsupported.cri.awb_external`.

`sprite.crop` makes one task per SpriteAtlas that sprites of other bundles use (subject `<the atlas bundle's
subject>:<atlas path id>`). Its inputs are the atlas' JSON, the `meta` of each such sprite and the images of the
atlas textures they use; it writes `<sprite object id>#image` with the same bytes `unity.export` writes for a sprite
whose atlas is in its own bundle.

## CRI stages

`cri.audio` and `cri.movie` take their subjects by content: a task is `cri.audio:<sha256 of the ACB>` or
`cri.movie:<sha256 of the USM>`, so an ACB stored both as a raw file and in a bundle, or in several bundles, is
decoded once. Raw files are told apart by their first bytes (`@UTF` ACB, `AFS2` AWB, `CRID` USM). Both read the
HCA keycode from the `boot` input when the task runs; the key itself is never part of a task, a record or a log.
Each task has one item (object: the task id; class `ACB` or `USM`): `exported`, or `unsupported` with a reason code.
Their artifacts belong to no object; the `original` layout places them under each name the stage gives the content:
the catalog keys that load a raw file, `Cri/Sound/<cue sheet>` for a cue sheet held in a bundle (the sheet's name
when the catalog has no such key).

| Stage | Parameters | Atoms | Artifacts (`<task id>#<role>`) |
|---|---|---|---|
| `cri.audio` | `flac.level` (8): ffmpeg's FLAC compression level 0-12 (every level decodes to the same samples) | `hca.decode` (vgmstream), `flac.encode` (ffmpeg) | one FLAC per vgmstream stream (a name repeated within the sheet gets `<name>_<stream>`; facts: stream, name, sample rate, channels, samples, loop points), `cues.json` (the first stream of each name), `streams.json` (every stream in order): the bytes `nnnotes audio` writes for the sheet (FLAC) |
| `cri.movie` | `format`: `mkv` (default; with `flac.level`) or `webm` | `usm.demux` (nnnotes: `numpy-<v>/1`), `movie.mux` (ffmpeg) | `video.ivf` (the VP9 stream in its IVF container, unmasked; facts: size, frame rate, frames), `audio.adx` (the ADX stream when there is one; facts: channels, sample rate, samples), `movie.mkv` (VP9 copied, the audio as FLAC) or with `format: webm` `movie.webm` (VP9 copied, the audio as Opus, as the story's videos) |

The ids of the external tools are `<tool>-<version>+sha256.<sha256 of the executable>/<revision>` (the version as
`vgmstream-cli -V` / `ffmpeg -version` report it): another build of a tool runs the tasks that use it again, and a
task described for another build is refused as incompatible. The tools are `[paths] vgmstream` and `[paths] ffmpeg`,
else found on PATH. The `boot` input is the APK's `assets/bin/Data/data.unity3d` (`[paths] apk`), stored once.

## Atoms

`nnnotes.atoms.ATOMS[name]` is the reference implementation of an atom: its id, its function and its cost estimate.
The id names the libraries whose behaviour the output depends on, with their installed versions, and the revision of
the nnnotes code (`pillow-12.1.1/1`); `nnnotes/<revision>` when no library's behaviour is involved. Task keys include
the ids of the atoms a task uses, so upgrading a library or changing an atom re-runs exactly the tasks that use it.

| Atom | Reference implementation | Id | Does |
|---|---|---|---|
| `reader` | `nnnotes.atoms.reader.open_bundle` (UnityPy) | `unitypy-<v>/1` | one bundle loaded alone from its bytes: serialized files, objects, raw bytes, typetrees, container, externals, and the inputs of `texture.decode` and `mesh.arrays` (resource files are read from inside the bundle only) |
| `texture.decode` | UnityPy `Texture2DConverter.parse_image_data` | `unitypy-<v>+texture2ddecoder-<v>+astc-encoder-py-<v>+pillow-<v>/1` | the first mip level, top row first, as UnityPy's `Texture2D.image` |
| `png.encode` | Pillow | `pillow-<v>/1` | PNG bytes; level 6 (the default) gives Pillow's default bytes, the ones the other commands write |
| `astc.container` | nnnotes | `nnnotes/1` | the standard `.astc` file (16-byte header, first mip level's blocks) |
| `sprite.crop` | nnnotes (a port of UnityPy's `SpriteHelper`) | `pillow-<v>+numpy-<v>/1` | a sprite's image from its texture's image: texture rect, packing rotation / flip, alpha texture, tight mask or mesh drawing; optionally on the sprite's full rect |
| `mesh.arrays` | nnnotes | `numpy-<v>/1` | every vertex channel with its stored type and component count, primitives per submesh (base vertex applied), skin |
| `gltf.write` | nnnotes | `numpy-<v>/1` | binary glTF: one node per mesh, one primitive per non-empty submesh, POINTS for a mesh without an index buffer, z negated, winding reversed, v flipped, custom `_...` attributes for components glTF has no place for, bind poses in extras |
| `sniff` | nnnotes | `nnnotes/1` | format, extension and media type by magic number (images, fonts, CRI, Unity, Live2D, Spine, archives, audio / video), then JSON, UTF-8 text |

### Reference implementations

Each atom has exactly one reference implementation, the one in the table above. Another implementation replaces it
only when all of the following hold, measured on a whole catalog with `scripts/atom_eval.py` (conformance with `m1`,
cost with `m3`, determinism with `m1 --checks det` and `compare`):

1. **Conformance.** Its output equals the reference's for every object of the atom's classes, or an independent
   reference shows the reference wrong for that object. For `png.encode` the output is equal when the file decodes to
   the same mode and pixels. No new refusals, and no object handed back to another implementation.
2. **Distribution.** A released package with wheels for every platform and Python version the CI tests; no source
   builds, git installs or vendored copies.
3. **Cost.** At least 20 % less CPU time or peak resident memory for the stage that uses the atom, on the
   whole-catalog workload, counting every additional load of a bundle.
4. **Determinism.** The same bytes on repeated runs and from any number of threads.

A switch changes one registry entry: the atom's id changes, exactly the tasks that use the atom run again, and their
golden records are regenerated with a version bump. A library that meets the criteria only in an unreleased version
is not used until it is released.

The current implementations and their measured position (one catalog, 14,692 bundles, 1,805,921 objects):

| Atom | Implementation | Cost share of its stage |
|---|---|---|
| `reader` | UnityPy | the whole of `unity.census`; the open and every typetree read of `unity.export` |
| `texture.decode` | UnityPy (`Texture2DConverter`) | about 5 % of `unity.export` CPU |
| `png.encode` | Pillow, zlib level `png.level` (6) | more than half of `unity.export` CPU (texture images alone about 40 %) |
| `astc.container` | nnnotes | |
| `sprite.crop` | nnnotes | |
| `mesh.arrays` | nnnotes | |
| `gltf.write` | nnnotes | |
| `sniff` | nnnotes | |

`png.level` (0-9) is a task parameter: a lower level writes larger files faster, with the same pixels
([performance.md](performance.md#png-level)).

`NNNOTES_ATOM_<NAME>` (`NNNOTES_ATOM_PNG_ENCODE` for `png.encode`) replaces an atom for evaluation: its value is
`module:attribute`, naming an `Impl` (or a callable returning one) whose id differs from the reference's. Keys and
provenance record the replacement.

## Reason codes

The codes of items that are `generic`, `unsupported` or `failed`, and of limits recorded with an exported artifact.

| Code | Meaning |
|---|---|
| `empty.texture` | a texture without pixels (0 wide or high) |
| `empty.mesh` | a mesh without vertices, or whose every submesh is empty |
| `unsupported.texture.format` | a texture format the decoder does not implement (HDR ASTC is exported as `.astc` instead) |
| `unsupported.image.kind` | a texture array, cube map or 3D texture (generic JSON with the raw data) |
| `unsupported.sprite.downscale` | an atlas-packed sprite with a downscale multiplier other than 1 |
| `unsupported.sprite.tight_mask` | a tightly packed sprite whose mesh cannot be drawn (not flat, no triangles, texture coordinates not 2D) |
| `unsupported.sprite.canvas` | a sprite image that does not fit its rect at its offset |
| `unsupported.mesh.blendshapes` | a mesh's blend shapes (the mesh is exported without them) |
| `partial.mesh.skin` | a skin not converted in full: more than four weights per vertex, or weight and index channels that do not match |
| `generic.clip.legacy` | a legacy or compressed AnimationClip |
| `generic.controller.override` | an AnimatorOverrideController |
| `no_data.font` | a Font without font data |
| `unsupported.cri.awb_external` | an ACB whose waveforms are in an AWB that is not available |
| `unsupported.usm.codec` | a USM video stream in a codec other than VP9 |
| `unsupported.usm.alpha` | a USM with an alpha stream |
| `unsupported.usm.subtitle` | a USM with a subtitle stream |
| `unsupported.usm.audio_streams` | a USM with more than one audio stream |
| `unsupported.class.AudioClip` | an AudioClip |
| `unsupported.class.VideoClip` | a VideoClip |
| `unsupported.class.MovieTexture` | a MovieTexture |
| `script.unresolved` | a MonoBehaviour whose script is not in the task's script table (no script bundle) |
| `script.missing` | a MonoBehaviour whose script reference is null |
| `no_data.sprite` | a sprite whose render data names no texture, or whose texture has no image |
| `unsupported.sprite.external_texture` | a sprite whose render data texture (not an atlas) is in another bundle |
| `generic.error` | a converter failed on the object (the message says how); its typetree JSON instead |
| `failed.read` | the object's typetree could not be read |
| `failed.export` | the object's typetree JSON could not be made |
| `source.absent` | a catalog entry whose file is not there (an APK entry without a file) |
| `source.error` | an input that could not be fetched |
