# Stages, converters and atoms

The asset export is built from three layers:

- **Stages** apply one unit of work to one subject (a bundle, an object, a content id, a model key, a view). Each has a name and
  a version; a task is a stage applied to one subject, and its key covers exactly what its output depends on
  ([contracts.md](contracts.md)).
- **Converters** turn the objects of one class inside `unity.export` into artifacts. Each carries its own version; a
  task includes only the converters of the classes its objects have.
- **Atoms** are the pure functions stages and converters are made of: no file or network IO, no threads, the same
  output for the same input. Each atom has exactly one reference implementation.

## Stages

Every stage is at version 1, except `unity.census` (version 4: it lists what every object references, the
hierarchy holding it and the Resources.Load paths of a ResourceManager), `link.addresses` (version 3: the
Resources.Load paths and the built-in selectors), `live2d.model` (version 2: assembled from the exported objects), `unity.export` (version 2: one task per unit), `cri.movie` (version 2: every stream kind,
codec and channel of a USM) and `catalog.index` (version 2: gzip input and Japanese remote asset placeholders).

| Stage | Subject | Inputs | Outputs |
|---|---|---|---|
| `catalog.index` | a catalog (remote + APK) | the catalog files | every location (with the bundle hash, crc and size), bundles and raw remote files |
| `unity.census` | a bundle, or a built-in file of the APK | the bundle | serialized files, externals, objects (file, path id, class, size, type hash, name, script, the scripts an AnimationClip's bindings name, the objects it references, the hierarchy holding it), container, scene hashes, dependencies, script fields, the ResourceManager's paths (Resources.Load) |
| `link.scripts` | global | the censuses holding MonoScripts | serialized file and path id -> assembly, namespace, class |
| `unity.export` | a unit of a bundle or of a built-in file of the APK (below), by the stable address of its root object | the bundle; context `unit` (the root's object id), `bundle`, the script entries its objects use and, for files without type trees, the type trees nnnotes ships for their type hashes | its objects' artifacts and one item per object |
| `sprite.crop` | a SpriteAtlas | the atlas' JSON, the images of its textures, the metas of the sprites of other bundles it packs | the sprites' images |
| `cri.audio` | ACB content | `acb` (a raw file of the catalog, or the `acb` artifact of a cue sheet held in a bundle), `awb` (an AWB the catalog pairs with it), `boot` (the game's boot data: the key's source) | FLAC per stream, `cues.json`, `streams.json` |
| `cri.movie` | USM content | `usm` (a raw file of the catalog, or the `usm` artifact of a movie held in a bundle), `boot` | every stream as stored (video, alpha, audio, subtitles), `movie.mkv` (or `movie.webm`) |
| `live2d.model`, `live2d.cubism` | a Live2D model key (`Character/Live2D/<group>/<name>/model/<name>`) a command asks for | `objects`: the object table of what the model reaches ([products](products.md#live2d-models)); depends on `link.addresses` and the `unity.export` tasks it reads | `model.json` (the player's model) and the object table; the Cubism SDK files |
| `room.gltf` | a spot background key (`Spot/<place>/Background/<place>`) | `objects`: the object table of what the background reaches ([products](products.md#spots)) | `room.glb` and `room.json` |
| `spot.files` | a spot situation key (`Spot/<place>/<situation>/<situation>`) | `objects`; `master`: the spot's master record | `spot.json` and the Spine files |
| `live.music` | a music id (`MasterLiveMusic`) | `objects`; `acb:<key>` and `sheets`: the BGM's cue sheet and its `cri.audio` files ([products](products.md#live-sounds)); `master`: the music's master record; parameter `format` (`aac`) | `music.json` and the BGM in the web format |
| `live.sounds` | the note sound sets offered: `default` or `NoteSePatternId=<ids>` | `objects` (the sound settings of the APK's built-in files); `acb:<key>` and `sheets` of each cue sheet; `apk:assets/Cri/Sound/Sirius.acf`; `master` | `sounds.json` and the decoded sounds |
| `link.addresses` | global | catalog index, censuses (the built-in files' too) | key -> objects, Resources.Load path -> objects, built-in selector (class, MonoBehaviour script class) -> objects, serialized file -> bundle or built-in file, bundle -> keys |
| `view.<name>` | a catalog (`main`) | `master:<Table>` per master table it reads, `rules` (the view's rule and the resolvers it uses), `addresses` (the part of the address table it read: the keys looked up and the prefixes listed); no parameters, atoms or context | `view.<name>:<subject>#view` (facts: rows, entries, gaps, unreferenced) |
| `view.voices` | a catalog (`main`) | `master:<Table>` per master table its rules read, `rules`, `addresses`, `sheets` (each cue sheet the `cri.audio` results are known by that a voice names or the rules' prefixes list: its task and ACB content id), `acb:<sha256>` (the ACB of each decoded one), `episodes` (per story episode and part, the `unity.export` artifact holding the object its key names), `adv:<sha256>` (those contents); context `audio` (the keys of those `cri.audio` results); runs after `unity.export` and `cri.audio` | `view.voices:<subject>#view` (facts: rows, entries, gaps, unreferenced streams) |

`view.voices` is a stage of its own (`nnnotes.pipeline.voices:VoiceStage`), not one of the master-only views of
`nnnotes.pipeline.views:stages`: besides master tables and the address table it reads the story episodes of the
`unity.export` results, `cri.audio` results and ACB tables ([views.md](views.md#voices)).

## Converters (`unity.export`)

The converters are the library `nnnotes.unity.convert`, one module per group of classes; `unity.export`
(`nnnotes.pipeline.objexport`) runs them over one unit of a bundle read alone and gives every object of the unit
exactly one item status: `exported`,
`contained` (part of another artifact), `generic` (typetree JSON because the specialized form could not be made;
the reason says why), `unsupported` (a class the stage does not convert; its typetree JSON is written as well) or
`failed` (not even the typetree JSON could be made). A converter's documented limit gives the generic typetree JSON
with its reason code; any other error of a converter gives it with `generic.error` and the error's message.

| Class | Converter | Artifacts | Atoms | Notes |
|---|---|---|---|---|
| Texture2D | `tex.png/2` | `image`: PNG of the first mip level (HDR ASTC: `.astc`), `json`: the typetree without `image data` (settings, colour space, mip count, stream data) | `texture.decode`, `png.encode`, `astc.container` | further mip levels as facts; a texture without pixels: `empty.texture` |
| Sprite | `sprite.png/3` | `image`: PNG on the sprite's rect (packing undone, tight mask applied), `json`: the typetree, geometry, render data and image (see below) | `texture.decode`, `sprite.crop`, `png.encode`, `mesh.arrays` | the tasks of a bundle share the textures it decoded last; a sprite whose SpriteAtlas is in another bundle gets its `json` here (with its geometry and object facts) and its `image` from `sprite.crop` |
| Mesh | `mesh.glb/2` | `mesh`: binary glTF in the mesh's local space, `json`: the typetree without the vertex, index and compressed mesh data | `mesh.arrays`, `gltf.write` | see `gltf.write` below; limits of the skin and blend shapes as issues of the artifact |
| GameObject (a root) | `prefab.json/3` | `json`: the hierarchy's nodes (path, path ids, transform) with their components' fields | | the objects below it are `contained`; a root of a scene's file names its scene |
| Material | `material.json/2` | `json`: shader, keywords, textures, floats, colours | | |
| Shader | `shader.json/2` | `json`: the parsed form (the bytes of `nnnotes export shader`'s `<name>.json`), `program:<platform>/s<S>p<P>_<stage>_<N>`: each compiled sub-program as stored (`.glsl`, `.metal`, `.vkprog`), `index`: the variants (platform, subshader, pass, stage, GPU program type, keywords, program role), `typetree`: the typetree JSON | | a shader without a parsed form: its typetree with `generic.error` |
| AnimationClip | `clip.json/3` | `json`: the Mecanim clip data and its bindings as stored (see below) | | a legacy or compressed clip: `generic.clip.legacy` |
| AnimatorController | `controller.json/3` | `json`: name, clips (`$ref`), layers, state machines, parameters, default values | | an AnimatorOverrideController: `generic.controller.override` |
| MonoBehaviour (not in a hierarchy) | `mono.json/3`, `cri.usm/1` | `json`: `$script` (assembly, namespace, class) and the typetree; a cue sheet asset also `acb`, a movie asset holding its USM also `usm` (see below) | `sniff` | the class from the task's script table |
| TextAsset | `data/2` | `data`: the stored bytes, `json`: the typetree without `m_Script` | `sniff` | extension from the container path, else from the content |
| Font | `font/3` | `font`: the font file, `json`: the typetree without `m_FontData` | `sniff` | |
| any other class (a MonoScript, an AssetBundle, ...) | `generic.json/3` | `json`: the typetree | `sniff` | an object that can be read only in part (see below): that part, `unsupported.typetree` |

Every object a converter takes has a `json` artifact or is contained in another object's. Each item lists in
`refs` the objects its documents reference (by object id, other bundles' objects included, the objects of its own
prefab left out): the objects reachable from an object follow from the items alone. The census of a bundle lists the
same before anything is converted, as a superset: per object `refs`, every object its typetree references, and
`unit`, the root of the hierarchy whose prefab holds it.

A sprite's `json` holds `sprite` (its typetree), `mesh` (its geometry decoded: `positions` in the sprite's units,
`submeshes` of triangles, `uv0` when stored), `renderData` (`source`: `renderData` for the sprite's own render
data, `atlas` for its SpriteAtlas' entry; `atlas`; and that render data's `texture`, `alphaTexture`,
`secondaryTextures`, `textureRect`, `textureRectOffset`, `atlasRectOffset`, `uvTransform`, `downscaleMultiplier`,
`settingsRaw`) and `image` (`packed`, `packingMode`, `packingRotation`, `width`, `height`). The texture coordinate
of a position `p` is `((p.x * uvTransform.x + uvTransform.y) / width, (p.y * uvTransform.z + uvTransform.w) /
height)` of the render data's texture.

### The APK's built-in files

With `[paths] apk` set, the player's own Unity files under `assets/bin/Data/` of the APK are subjects of
`unity.census` and `unity.export` like the bundles, named `apk/<path under assets/bin/Data>`:

| Subject | Holds |
|---|---|
| `apk/data.unity3d` | `globalgamemanagers` (player, graphics and quality settings, the resource table), `globalgamemanagers.assets` (render pipeline assets, renderer data and features, post-process data, shaders), `level0`, `resources.assets`, `sharedassets0.assets`, `unity_builtin_extra` (built-in shaders) |
| `apk/datapack.unity3d` | `resources.assets` in split builds |
| `apk/Resources/unity default resources` | the serialized file of the engine's default meshes, textures, shaders and font |

Their objects have the ids of any serialized file's (`unity_builtin_extra:12`, `unity default resources:10`), so a
bundle's reference into them names an object these tasks export, and `link.addresses` maps their files to their
subjects. Census always covers them; everything and `--select bundle:apk/*` export them.

These files store no type trees. Objects of the engine's classes are read with the type trees UnityPy has for the
file's Unity version; MonoBehaviours with the type trees nnnotes ships for the game's classes it reads there
(`nnnotes.unity.scripttypes`, generated by `scripts/gen_typetrees.py`): the render pipeline assets, renderer data,
renderer features, post-process data and the game's settings objects. A MonoBehaviour takes the shipped type tree
made for the type hash the file stores for it (the hash of its class's serialized layout), and the read must consume
exactly the object's bytes. The export task's context holds the shipped type trees its MonoBehaviours use, and the
census of a built-in file has the shipped type trees' id among its atoms, so a new shipped tree runs those tasks
again. A MonoBehaviour without one is `unsupported.typetree` with its header (`m_GameObject`, `m_Enabled`, `m_Script`,
`m_Name`) as its typetree JSON; inside a hierarchy its component keeps that header and the prefab records the
issue. PlayerSettings, whose type tree reads the stored layout right up to `m_ActiveColorSpace` (by its stored
type hash), gives the fields up to it.

### Units

A unit is what one `unity.export` task converts: a hierarchy (a root GameObject with the GameObjects, Transforms and
components of its prefab: the objects whose census record names it as their `unit`) or an object outside any
hierarchy. Its subject is the stable address of its root object, `<stable bundle name>:<pathId>` (in a bundle of
several serialized files the file's tag follows the bundle name, as in `link.addresses`): path ids stay when a
bundle's content changes, so a unit keeps its task across catalog versions.

A run exports the units holding an object of the selected bundles, the units of the objects the fact "roots" names
(catalog keys, Resources.Load paths, built-in selectors and object ids: a product's roots, [products](products.md)) and every unit reachable
from those through what their objects reference (the census `refs`, across the censuses of the run); with `--only-class`
only the units whose root object is of those classes. The selection is not part of any key: a unit's task is the same
whichever request reaches it.

A task's key includes the atoms `reader` and `sniff`, the converter `generic.json`, the converters (with their
atoms) of the classes of the unit's objects, its bundle, its root and the script entries and shipped type trees its
objects use, so a converter's version bump runs again exactly the units holding its classes. The tasks of one bundle
are one batch: a worker loads the bundle once for them and keeps what depends on the bundle alone (the index of its
objects, its hierarchies, its atlases and the textures it decoded last) until the batch ends. A unit's result
depends on neither the other units of the batch nor their order.

Object JSON is canon/1 in the object's own field order:

- a reference is `{"$ref": {"file": ..., "pathId": ...}}`, with `class` and `name` when the target is in the
  bundle; a null reference is `null`. Inside a prefab a GameObject, Transform or component of the same prefab is
  named by its path: `{"gameObject": path}`, `{"transform": path}`, `{"component": class, "gameObject": path}`;
- a byte array (a vector of UInt8, SInt8 or char, or TypelessData) shorter than `blobMin` bytes (4096) is
  `{"$hex": "..."}`; a longer one is `{"$blob": {"sha256": ..., "size": ..., "format": ...}}` and its bytes are the
  artifact `<object id>#blob:<field path>` (field names and array indices joined by `/`, the format by `sniff`);
- a string whose stored bytes are not UTF-8 is `{"$hex": "<the stored bytes>"}`;
- NaN is `{"$float": "nan"}`, infinities `1e999` / `-1e999`.

An AnimationClip's `json` holds `name`, `sampleRate`, `wrapMode`, `startTime`, `stopTime`, `loopTime`,
`cycleOffset`, `events` (with `objectReferenceParameter` as a reference), `bindings`, `streamed` (`curveCount`, and
`discreteCurveCount` when the clip stores it; `frames`: `[time, [[curve, c0, c1, c2, c3], ...]]`, a curve's value at
`t` being `((c0*x + c1)*x + c2)*x + c3` with `x = t - time` from its last key at or before `t`), `dense`
(`curveCount`, `frameCount`, `sampleRate`, `beginTime`, `samples`), `constant` and `pptrCurveMapping` (references).
Curve indices run over the streamed curves, then the dense ones, then the constant ones; each float binding takes
`curves` of them (1 when the binding has no `curves`) in `bindings` order. A binding is the clip's GenericBinding as
stored: `pathCrc` (crc32 of the animated object's path relative to the GameObject whose Animator plays the clip),
`attributeCrc`, `typeID` with its `class` when it is a known class id, `customType`, `pptr`, `int`,
`serializeReference`; a binding that names a script has `script` (the MonoScript reference) and `scriptClass`
(its entry in the task's script table, `null` when the table lacks it), and a Transform binding (`typeID` 4) of
position, rotation, scale or euler angles has `attribute` (`m_LocalPosition`, `m_LocalRotation`, `m_LocalScale`,
`localEulerAnglesRaw`) and `curves` (3, 4, 3, 3). Paths and the attributes of other components are named by the
hierarchy that plays the clip, which the clip alone does not give.

A cue sheet held in a bundle is a MonoBehaviour in one of two layouts; either gives the artifact
`<object id>#acb` (kind `cri.acb`, facts: `cueSheet`, `layout`), which `cri.audio` decodes:

- a SplitAcbData (fields `_cueSheetName`, `_chunks`): its chunk TextAssets (of the same bundle, units it references) joined
  in order, every byte XORed with the one mask that gives the ACB signature `@UTF` (layout `split`; its refs name
  the chunks);
- an asset named like the sheet whose `implementation` managed reference is a CriSerializedBytesAssetImpl: its
  `data.data` bytes (layout `embedded`), whatever their size; that field of the JSON is the `{"$blob"}` of the `acb`
  artifact. One that names an AWB object gives `unsupported.cri.awb_external`.

A movie asset (CriWare.Assets `CriManaUsmAsset`) whose `implementation` is a CriSerializedBytesAssetImpl holds its USM
the same way: its `data.data` bytes, whatever their size, are the artifact `<object id>#usm` (kind `cri.usm`, facts:
`movie`, the asset's name), which `cri.movie` converts. Converter `cri.usm/1` does this; it is in the key of a unit
only when the scripts of its objects name that class, so no other unit's key changes with it.

`sprite.crop` makes one task per SpriteAtlas that sprites of other bundles use (subject: the `unity.export`
subject of the atlas' unit). Its inputs are the atlas' JSON, the `json` of each such sprite and the images of the
atlas textures they use; it writes `<sprite object id>#image` with the same bytes `unity.export` writes for a sprite
whose atlas is in its own bundle.

## CRI stages

`cri.audio` and `cri.movie` take their subjects by content: a task is `cri.audio:<sha256 of the ACB>` or
`cri.movie:<sha256 of the USM>`, so an ACB or a USM stored both as a raw file and in a bundle, or in several bundles,
is decoded once. Raw files are told apart by their first bytes (`@UTF` ACB, `AFS2` AWB, `CRID` USM), read once per
content and recorded in the depot, so a later plan reads no raw file it has seen. Both read the HCA keycode from the
`boot` input when the task runs; the key itself is never part of a task, a record or a log. Each task has one item
(object: the task id; class `ACB` or `USM`): `exported`, or `unsupported` with a reason code. Their artifacts belong
to no object; the `original` layout places them under each name the stage gives the content: the catalog keys that
load a raw file, `Cri/Sound/<cue sheet>` for a cue sheet held in a bundle (the sheet's name when the catalog has no
such key), the keys of its bundle that end in the movie asset's name for a movie held in a bundle.

| Stage | Parameters | Atoms | Artifacts (`<task id>#<role>`) |
|---|---|---|---|
| `cri.audio` | `flac.level` (8): ffmpeg's FLAC compression level 0-12 (every level decodes to the same samples) | `hca.decode` (vgmstream), `flac.encode` (ffmpeg) | one FLAC per vgmstream stream (a name repeated within the sheet gets `<name>_<stream>`; facts: stream, name, sample rate, channels, samples, loop points), `cues.json` (the first stream of each name), `streams.json` (every stream in order): the bytes `nnnotes export audio` writes for the sheet (FLAC) |
| `cri.movie` | `format`: `mkv` (default; with `flac.level`) or `webm` | `usm.demux` (nnnotes: `numpy-<v>/2`), `movie.mux` (ffmpeg), `hca.decode` (vgmstream) | per stream as stored and unmasked (a stream stored without its mask is recognised by its structure), named by its kind and, past channel 0, `_<channel>`: `video.<ext>` and `alpha.<ext>` (`ivf`: VP9 in IVF; `m1v`: MPEG-1 elementary stream, CRI's Sofdec.Prime; `h264`: H.264 Annex B; facts: codec, size, display size, frame rate, frames, an alpha stream's alpha type), `audio.<ext>` (`adx`; `hca`, still enciphered as stored; facts: channels, sample rate, samples or blocks, cipher), `subtitle.json` (the records as stored: language, time unit, start, duration, the text decoded as UTF-8 or `textHex`) with `subtitle.srt` and `subtitle.vtt`; `movie.mkv`: every video and alpha stream copied (the alpha as a second video track titled `alpha`), every audio stream as FLAC (HCA decoded by vgmstream with the boot data's key), every subtitle channel as a SubRip track (facts: the tracks); with `format: webm` `movie.webm`, the story's form: the first video stream, VP9 copied, or re-encoded as VP9 (libvpx, CRF 20) when it is not VP9 or has an alpha stream, which becomes its alpha channel cropped to the display size; the first audio stream as Opus; no subtitles |

`cri.movie` in players: a player shows the first video track of `movie.mkv` and no player composites the `alpha`
track (its luma is the opacity of the frame of the same number; when it is coded larger than the movie, its
top-left display-size part applies). MPEG-1 streams ask in their user data for an 11-bit intra DC precision, which
FFmpeg's decoder applies and other decoders may not. The game's catalog (version 1.0.1) holds VP9 and MPEG-1 video,
MPEG-1 alpha streams, ADX and HCA audio, at most one audio stream per movie and no subtitles: the H.264, subtitle
and multi-audio paths are tested on synthetic movies only.

The ids of the external tools are `<tool>-<version>+sha256.<sha256 of the executable>/<revision>` (the version as
`vgmstream-cli -V` / `ffmpeg -version` report it): another build of a tool runs the tasks that use it again, and a
task described for another build is refused as incompatible. The tools are `[paths] vgmstream` and `[paths] ffmpeg`,
else found on PATH. The `boot` input is the APK's `assets/bin/Data/data.unity3d` (`[paths] apk`), the input of
the `apk/data.unity3d` tasks.

## Atoms

`nnnotes.unity.atoms.ATOMS[name]` is the reference implementation of an atom: its id, its function and its cost estimate.
The id names the libraries whose behaviour the output depends on, with their installed versions, and the revision of
the nnnotes code (`pillow-12.1.1/1`); `nnnotes/<revision>` when no library's behaviour is involved. Task keys include
the ids of the atoms a task uses, so upgrading a library or changing an atom re-runs exactly the tasks that use it.

| Atom | Reference implementation | Id | Does |
|---|---|---|---|
| `reader` | `nnnotes.unity.atoms.reader.open_bundle` (UnityPy) | `unitypy-<v>/1` | one bundle loaded alone from its bytes: serialized files, objects, raw bytes, typetrees, container, externals, and the inputs of `texture.decode` and `mesh.arrays` (resource files are read from inside the bundle only) |
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

The current implementations:

| Atom | Implementation |
|---|---|
| `reader` | UnityPy |
| `texture.decode` | UnityPy (`Texture2DConverter`) |
| `png.encode` | Pillow, zlib level `png.level` (6) |
| `astc.container` | nnnotes |
| `sprite.crop` | nnnotes |
| `mesh.arrays` | nnnotes |
| `gltf.write` | nnnotes |
| `sniff` | nnnotes |

`png.level` (0-9) is a task parameter: a lower level writes larger files faster, with the same pixels. The default
stays 6, the level whose bytes equal the textures of the other commands.

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
| `unsupported.usm.codec` | a USM video or alpha stream in a codec other than VP9, H.264 or MPEG-1, an audio stream in a codec other than ADX or HCA, or an H.264 stream without a frame rate |
| `unsupported.usm.stream` | a USM stream of a kind other than video, alpha, audio or subtitles |
| `unsupported.usm.subtitle` | USM subtitles that are not records, or whose text is not UTF-8 (the movie's other artifacts are made) |
| `unsupported.class.AudioClip` | an AudioClip |
| `unsupported.class.VideoClip` | a VideoClip |
| `unsupported.class.MovieTexture` | a MovieTexture |
| `script.unresolved` | a MonoBehaviour whose script is not in the task's script table (no script bundle) |
| `script.missing` | a MonoBehaviour whose script reference is null |
| `no_data.sprite` | a sprite whose render data names no texture, or whose texture has no image |
| `unsupported.sprite.external_texture` | a sprite whose render data texture (not an atlas) is in another bundle |
| `generic.error` | a converter failed on the object (the message says how); its typetree JSON instead |
| `unsupported.typetree` | an object of a file without type trees read in part: a MonoBehaviour without a shipped type tree of its class (its header), a class whose type tree fits only up to a field (those fields) |
| `failed.read` | the object's typetree could not be read |
| `failed.export` | the object's typetree JSON could not be made |
| `source.absent` | a catalog entry whose file is not there (an APK entry without a file) |
| `source.error` | an input that could not be fetched |
