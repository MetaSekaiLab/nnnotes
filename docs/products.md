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
- the objects of the Resources.Load paths it gives (`resources`): paths of the ResourceManager in the APK's built-in
  files, such as the materials a runtime loads by name, through the same table and compared ignoring case;
- the objects of the built-in selectors it gives (`builtins`): objects of the APK's built-in files by class
  (`GraphicsSettings`) or a MonoBehaviour by its script's class (`MonoBehaviour:<class>`), such as settings assets
  the game reads without a path, through the same table;
- the object ids the stage names besides (`root_objects`).

A product stage may also read cue sheets: the catalog keys `cue_sheets` gives (each also one of its keys). A cue
sheet is the ACB its key loads, held in a bundle (the `acb` file of an object of the key) or a raw file of the
catalog that `cri.audio` decoded. The task's inputs then hold each ACB (`acb:<key>`) and, as `sheets`, the files
`cri.audio` made of it (`{key: {acb, status, reason, files: {name: {sha256, size, ext}}}}`); it depends on those
`cri.audio` tasks, and a product file may be one of those files as stored, without copying its bytes. A stage that
reads files of the APK names them in `APK`: inputs `apk:<path in the APK>`, from the fact `apk`.

All are read from the planning facts alone, so a command can ask for what its products need before anything runs:
`assemble.request(stages, products, env)` gives the fact `roots` (`{"keys", "resources", "builtins", "objects"}`), and
the request of `unity.export` then holds the units of those objects and every unit reachable from them through the
census `refs` ([units](stages.md#units)), and nothing else of their bundles.

What a task reaches: the units holding its roots, then every unit that an item of a reached unit references (the
item's `refs`; a SpriteAtlas reached through a reference does not reach the sprites it packs), through the
`unity.export` results. A sprite whose atlas is in another bundle has its `image` from the
`sprite.crop` result of that atlas. A reached unit that the request did not export is an error of the request; an
object that a reached object references and no census holds is listed as `missing`.

| Part of the task | |
|---|---|
| inputs | `objects`: the object table of what it reaches (`nnnotes.objects/1`); the stage's own inputs (master tables, data its parameters name) |
| depends | `link.addresses` (when it has keys, resource paths or built-in selectors), the `unity.export` and `sprite.crop` tasks whose results it read, the `cri.audio` tasks of its cue sheets |
| artifacts | `<task id>#<path>`: the product's own files; `<task id>#objects`: the object table of the objects its files use (the whole table unless the stage narrows it to some objects, or to some files of an object: a shader's programs for one graphics API) |
| item | one for the subject: `exported`, or `unsupported` with a reason code |

The key holds the object table, so a product is assembled again exactly when a file of an object it reaches
changes, or what it reaches changes.

## Object table: nnnotes.objects/1

([objects.schema.json](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/schemas/objects.schema.json))

```json
{"schema": "nnnotes.objects/1",
 "roots": ["CAB-…:-4153…"],
 "keys": {"Character/Live2D/…/model/…": ["CAB-…:-4153…"]},
 "objects": {
   "CAB-…:-4153…": {"status": "exported", "class": "GameObject", "name": "model",
                    "files": {"json": {"sha256": "…", "size": 5120, "ext": "json"}}},
   "CAB-…:12": {"status": "contained", "class": "Transform", "in": "CAB-…:-4153…"},
   "CAB-…:40": {"status": "exported", "class": "Texture2D", "name": "texture_00",
                "files": {"image": {"sha256": "…", "size": 812345, "ext": "png"},
                          "json": {"sha256": "…", "size": 640, "ext": "json"}}}},
 "missing": []}
```

`keys` gives the root objects of each catalog key of the product, so an assembler finds an object by the key the
game loads it by. Each object has the status of its item (the most severe when two stages made items for it), its
class and name where known, its files by role (the artifacts of its items), the object whose file holds it (`in`, a contained
object), or the reason code of a generic, unsupported or failed object.

## Product layout

A product layout places products for a player (`export assets --layout product`, `export live2d --player`):

- every file of a product and every file of the objects of its object table at `assets/<sha256>.<ext>` (a file
  several products or objects share is written once);
- each product's manifest at `<directory>/<file>.json` (the stage's `manifest(subject)`, by default
  `<stage>/<subject>.json` with the subject escaped as one path segment).

A command asks for products in one of two ways: `export assets --layout product` makes, with each product stage, the
products of the catalog keys the selected bundles hold (the stage's `select`; a stage that reads master data has none
without it); `export live2d`, `export spot` and `export room` ask for the products of one subject. `export live2d`
without `--player`, `export spot` and `export room` place the files of their products at their own paths instead (the
`files` placement: the Cubism SDK files as a directory, a spot's files).

## Product manifest: nnnotes.product/1

([product.schema.json](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/schemas/product.schema.json))

```json
{"schema": "nnnotes.product/1", "product": "live2d.model", "subject": "…",
 "files": {"model.json": {"asset": "assets/<sha256>.json", "size": 2048}},
 "objects": {
   "CAB-…:-4153…": {"class": "GameObject", "name": "model",
                    "files": {"json": {"asset": "assets/<sha256>.json", "size": 5120}}}}}
```

`files` are the product's own files by path; `objects` are the objects of its object table that have files. An object
contained in another object's file (a Transform or a component in a prefab) is not listed: the prefab's JSON holds it
(its nodes and components carry their `pathId`). An asset path is relative to the directory above the manifest's directory, as
in the chart manifests of ournotes-player. A reference in an object's JSON (`{"$ref": {"file", "pathId"}}`) names
the object `<file>:<pathId>` of `objects`.

## Live2D models

Two product stages (`nnnotes.pipeline.live2d`) assemble a Live2D model from the objects its prefab reaches. Their
subjects are model keys, `Character/Live2D/<group>/<name>/model/<name>`; a model's id is its `<name>`.

`live2d.model` is the model the player reads. Its roots are the model's GameObject (the object of its key) and the
materials the Cubism mask pass draws with, which the APK's built-in files hold at the `Resources.Load` paths
`Live2D/Cubism/Materials/Mask` and `Live2D/Cubism/Materials/MaskCulling`. Its manifest is `models/<id>.json`, and its
one file `model.json` (format 3) names objects of its object table:

| Field | |
|---|---|
| `name`, `key` | the model's id and key |
| `prefab` | the object id of the model's GameObject; its `json` is the hierarchy (prefab.json) |
| `moc` | the object id of its CubismMoc (the root's `CubismModel._moc`); the moc3 is its file `blob:_bytes` |
| `canvas` | the moc3's canvas: `pixelsPerUnit`, `originX`, `originY`, `width`, `height`, `mocVersion` |
| `textures` | the object ids of the atlas pages the drawables draw with (`CubismRenderer._mainTexture`), by name |
| `shaders` | `{shader name: object id}` of the shaders of the object table |
| `resources` | `{"cubismMask", "cubismMaskCulling"}`: the object ids of the two mask materials |
| `motionSync` | the root has the MotionSync controller and its CRI audio input |
| `bindings` | `{clip object id: [{path, attribute}]}`: the bindings of every AnimationClip of the table named in the model's hierarchy, played from its root (`nnnotes.unity.bindings`; `null` where no name, or several, fit) |

Its object table holds every object the model reaches but the MonoScripts. Of a shader it keeps the parsed form
(`json`), the variant index (`index`) and the GLSL ES 3.00 programs (subshader 0, pass 0) the player draws the
drawables' materials with: for each material's keywords, alone and with `_ADDITIONAL_LIGHTS_VERTEX` (the keyword the
story renderer adds to a character draw), and the mask materials' when a drawable is masked (`CUBISM_MASK_ON`).

`live2d.cubism` is the model as the Cubism SDK files other tools read (`export live2d`); its manifest is
`cubism/<id>.json` and it has no objects:

| File | From |
|---|---|
| `<id>.moc3` | the CubismMoc's bytes |
| `textures/<page>.png` | the atlas pages, by name (a name two pages share is numbered: `<page>-2.png`) |
| `<id>.physics3.json` | the root's `CubismPhysicsController._rig` (when it has sub-rigs) |
| `<expression>.exp3.json` | each CubismExpressionData of the table |
| `motions/<motion>.motion3.json` | each CubismFadeMotionData, with the sample rate of the clip of its name |
| `<id>.model3.json` | the files, the model's fade times of each motion, and the EyeBlink and LipSync groups (the GameObjects holding a CubismEyeBlinkParameter or a CubismMouthParameter) |

## Spots

A spot of the home is a background prefab (a room) and a situation prefab placed on it. Two product stages
(`nnnotes.pipeline.spot`) assemble them; the roots of each are the objects of its subject's catalog key, and neither
keeps objects: its files are all it holds.

`room.gltf` is a background as binary glTF. Its subject is a background key, `Spot/<place>/Background/<place>`, and
its manifest `rooms/<place>.json`. Its files:

| File | |
|---|---|
| `room.glb` | every drawn mesh of the prefab (a MeshFilter with a mesh beside an enabled MeshRenderer), in hierarchy order |
| `room.json` | the summary: `backgroundKey`, `glb`, `meshCount`, `inactiveMeshes`, `materials`, `textures`, `samplers`, and what draws nothing (below) |

Each drawn mesh is one glTF node without a transform: its vertices are baked into the prefab's space through the
world matrix of its node (the root's own transform included) and written in glTF's right-handed space (z negated,
v = 1 - v). Each non-empty submesh is one primitive with the renderer's material of the same index. A node inactive in
the prefab is kept with `extras.unityActive = false`. The situation's `backgroundPosition`, `backgroundRotation` and
`backgroundScale` are not baked in: they go on top, as the game applies them to the prefab root.

Materials are translated from the shader's own render state, the first pass's: blend factors (colour and alpha), blend
operations, colour mask, culling, depth write, depth test and alpha to mask. A state the shader takes from a property
(`Blend [_SrcBlend] [_DstBlend]`, `Cull [_Cull]`, `ZWrite [_ZWrite]`, ...) is the material's value of that property,
else the shader's default for it. Each glTF material keeps the resolved state in `extras.unityRenderState` (and
`room.json` in `materials[].renderState`); a shader or a state glTF cannot express is an error of the task. Each
material and each texture is one object of the table, so materials and textures are told apart by object id. Each
texture a material's main slot names is one glTF texture with its own sampler (from the texture's filter, wrap and
mip count), and textures whose PNG bytes are equal share one glTF image. The `textures` of `room.json` has one entry
per glTF texture, in glTF order: the Unity texture's name.

What draws nothing is not written and `room.json` lists it: a submesh whose material slot is empty
(`nullMaterialSubmeshes`: `mesh`, object `path` in the prefab, `submesh` index), a MeshFilter without a mesh
(`nullMeshFilters`: the object path) and a mesh without vertices or triangles (`emptyMeshes`: `mesh` and `path`).

`spot.files` is a situation: its subject is a situation key, `Spot/<place>/<situation>/<situation>`, and its manifest
`spots/<situation>.json`. Its input `master` is the spot's record of the master tables `MasterHomeSpot`,
`MasterText` and `MasterStoryHomeSpotTapTalkEpisode` (the row whose `_situationAssetPath` is the key, its name, its
tap talks), made at planning from the master data. Its files are `spot.json` and the Spine files under `spine/`:

| Field of spot.json | |
|---|---|
| `spotId`, `master` | the spot's id and its `MasterHomeSpot` row |
| `name` | its name per language (`japanese`, `english`, `traditionalChinese`, `simplifiedChinese`, `korean`) |
| `tapTalk` | `{character id: [ADV ids]}` of its tap talks |
| `situationSettings` | the fields of the prefab's SpotSituationSettings (camera and interaction parameters) |
| `controller` | the fields of its SpotCharacterController but the character lists |
| `characters` | each SpotCharacter (a tap target) by character id: its object `path`, its fields, its `world` matrix and the world matrix of its `_focus` (`focusWorld`) |
| `spineCharacters` | each SpotSpineCharacter in hierarchy order: `path`, the name of the skeleton its SkeletonAnimation draws (`skeletonData`), that animation's settings (`animation`) and its `world` matrix |
| `skeletons` | each SkeletonDataAsset of the table, by name: `name`, `skeleton` and `atlases` (its files), `scale`, `defaultMix`, `mixes` |
| `matrixConvention` | `Unity world space (left-handed), 4x4 column-major` |

World matrices are in the space of the situation prefab, its root's own transform included. Fields that name objects
are written as the prefab document writes them. A reference the prefab leaves empty is written as null: a Spine
character with no `_animation` has null `skeletonData`, `animation` and `world`, and a tap target with no `_focus` a
null `focusWorld`.

| Spine file | From |
|---|---|
| `spine/<skeleton>.json` or `.skel` | the bytes of the SkeletonDataAsset's `skeletonJSON` (JSON when they start with `{`) |
| `spine/<atlas>.atlas` | the bytes of each SpineAtlasAsset's `atlasFile`, as stored (an atlas two skeletons share is written once) |
| `spine/<page>` | for each page of the atlas, the PNG of the `_MainTex` of the atlas asset's material of the same index |

## Live sounds

What a live plays besides its chart is two products of `nnnotes.pipeline.live`, each shared by every live that plays
it: the BGM of a music (`live.music`) and the sounds of an option set (`live.sounds`). Their roots are the objects of
the keys of their cue sheets, they read the sheets' `cri.audio` files, and neither keeps objects. A sound entry, of
either, is

| Field | |
|---|---|
| `row` | the `MasterSound` row |
| `sheet`, `cue` | its cue sheet and cue name |
| `category`, `categories` | the row's category and the CRI categories of the cue (ACB command 65) |
| `volume`, `busSends` | the cue's volume (with the sheet's) and bus sends (ACB commands 146 and 111) |
| `lengthMs`, `other` | the cue's length, its other commands as stored |
| `layers` | one per track of the cue: `file` (`audio/<sheet>/<file>`), the stream's `stream`, `awbId`, `sampleRate`, `channels`, `samples`, `loopStart`, `loopEnd`, the waveform's `loopFlag`, the track's and synth's `volume` and `busSends`, `other` |

`live.music`: its subject is a music id (`MasterLiveMusic._id`), its manifest `music/<id>.json` and its input
`master` the music's rows of `MasterLiveMusic`, `MasterSound` and `MasterSoundCueSheet`. Its files are `music.json`
(`format` 1, `musicId`, `music`: the BGM's `soundId`, `volume` and `startTimeSec`, and `sounds`: the BGM's entry)
and each layer's file in the web format of the parameter `format`: `aac` (AAC-LC 160 kbit/s in MP4, the default),
`opus`, `vorbis`, `mp3`, or `flac`, the decoded FLAC itself. An AAC layer records `encoderDelay`, the priming samples
of the MP4 edit list, which a decoder that does not apply the edit list outputs first. The encode is bit-exact; the
atom `audio.encode` names the ffmpeg build.

`live.sounds`: its subject names the note sound sets offered besides the default, `default` or
`NoteSePatternId=<id>,<id>,...` (the default set among them), and its manifest is `sounds/<subject>.json`. Its roots
include the game's sound settings, the `SoundVolumeSettings` and `AppConfigDefaultData` MonoBehaviours of the APK's
built-in files, and it reads the ACF of the APK (`assets/Cri/Sound/Sirius.acf`). Its input `master` holds the
option defaults of preset 1 (`MasterOptionDefault`), `MasterLiveNoteSe`, `MasterLiveSe` and the `MasterSound` and
`MasterSoundCueSheet` rows of its sounds. Its files are each sound's decoded FLAC and `sounds.json`:

| Field of sounds.json | |
|---|---|
| `format` | 1 |
| `sounds` | the entry of each sound by `MasterSound` id: the note sounds of the default options and of every set offered, and the live sounds of the start and finish cheers and of the finish direction of every result |
| `categories` | the CRI category volumes of a fresh profile: each category's default volume, and the live categories' config volumes |
| `acf`, `react` | the ACF's `name`, `buses` and `categories`; its REACT (ducking) entries |
| `noteSe` | the note sounds of option preset 1: `types` (note sound type -> sound id), `volumes`, `mutes`, `patternId`, `useIndividualNoteSe`, `gekisouTraceVolume`; with sets offered `groups` (set id -> type -> sound id) |
| `liveSe`, `liveSeNames` | live sound type -> sound id, and the types' names |
| `timeline` | the intro and end timings of the cheers and the start voice |

The `product` layout makes the `live.music` products of the musics whose BGM key the selection holds, and the
default `live.sounds` when the selection holds every cue sheet of its sounds; both need the master data.

## Note highway

What a live spawns and animates per note is three products of `nnnotes.pipeline.live`, each shared by every live that
loads it. Their roots are the objects of their keys, and each document names objects of its object table:

| Product | Subject | Manifest | Roots | Document |
|---|---|---|---|---|
| `live.notes` | `default` | `notes/default.json` | the note view prefabs (`EmbLive/Prefabs/LiveGame/<view>_note_view`), the slide and guide line views, the pair line, the groove loop, and the lane effect settings `Effect/Live/LaneEffect/effect001/LiveLaneEffectAssetSettings` | `notes.json` |
| `live.noteskin` | a note skin, `MasterLiveNoteSkin._assetName` | `noteskins/<name>.json` | the skin asset `Live/Note/<name>/LiveNoteSkinAsset` and the three flick views | `noteskin.json` |
| `live.noteeffect` | a note effect set, `MasterLiveNoteEffectSkin._assetName` or its `Light` variant (the set the game loads at quality Low where the catalog has it) | `noteeffects/<name>.json` | the effect settings `Effect/Live/NoteEffect/<name>/LiveNoteEffectAssetSettings` | `noteeffect.json` |

Each document has `format` 1 and

| Field | |
|---|---|
| `prefabs` (notes) | `{name: object id}` of the prefab GameObjects: `tap_note_view`, ..., `slide_line_view`, `guide_line_view`, `pair_note_line`, `note_groove_loop` |
| `laneEffect` (notes) | the object id of the lane effect settings |
| `settings` (notes) | the option defaults of preset 1 (`optionDefaults`) and the ranges (`optionRanges`) of the items the note runtime and the player read, the `MasterLiveSettings` values of the note speed and slides (`liveSettings`), the note display time of the default speed (`noteDisplayTimeMs`), the lane geometry, the default skin and effect set (`skin`, `effect`) and the lane effect set (`laneEffect`), and the names of every skin and effect set by option value (`skins`, `effects`) |
| `name`, `skin` (noteskin) | the skin's name and the object id of its `LiveNoteSkinAsset` |
| `name`, `settings` (noteeffect) | the set's name and the object id of its `LiveNoteEffectAssetSettings`, which references the effect prefabs |
| `bindings` | `{prefab GameObject id: {Animator node path: {clip id: [{path, attribute}]}}}`: the clips each Animator of the table's prefabs plays (its controller's clips and the `_loopAnim` of a `LoopAnimationTimeSync` on its `_animator`; for a skin, the arrow loop of each flick unit on its flick view's `LoopAnimationTimeSync`), named from the Animator's node (`nnnotes.unity.bindings`) |
| `shaders` | `{shader name: object id}`: the shader a lookup by name finds (a material names its shader by id). Copies of one shader in several bundles are one shader, named by the first id; a name of several different shaders names none of them |

The object table holds every object the product reaches but the MonoScripts; of a shader it keeps the parsed form
(`json`), the variant index (`index`) and every GLSL ES 3.00 program. The `product` layout makes `live.notes` when the
selection holds all its keys and the master data is there, and the `live.noteskin` and `live.noteeffect` products of
the skin and effect set keys the selection holds.
