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
`cri.audio` tasks, and a product file may be one of those files as stored, without copying its bytes. Movies work the
same way: the catalog keys `movies` gives, each the USM its key loads (the `usm` file of an object of the key, or a raw
file of the catalog that `cri.movie` decoded), as `usm:<key>` and, in `movies`, the files `cri.movie` made of it with
their facts (`{key: {usm, status, reason, files: {name: {sha256, size, ext, facts}}}}`). A stage that
reads files of the APK names them in `APK`: inputs `apk:<path in the APK>`, from the fact `apk`.

All are read from the planning facts alone, so a command can ask for what its products need before anything runs:
`assemble.request(stages, products, env)` gives the fact `roots` (`{"keys", "resources", "builtins", "objects"}`), and
the request of `unity.export` then holds the units of those objects and every unit reachable from them through the
census `refs` ([units](stages.md#units)), and nothing else of their bundles.

What a task reaches: the units holding its roots, then every unit that an item of a reached unit references (the
item's `refs`; a SpriteAtlas reached through a reference does not reach the sprites it packs), through the
`unity.export` results. A sprite whose atlas is in another bundle has its `image` from the
`sprite.crop` result of that atlas. A reached unit that the request did not export is an error of the request; an
object that a reached object references and no census holds is listed as `missing`. A subject without roots (a
product made from the APK alone) reaches nothing and reads no census.

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
 "fenced": {},
 "missing": []}
```

`keys` gives the root objects of each catalog key of the product, so an assembler finds an object by the key the
game loads it by. Each object has the status of its item (the most severe when two stages made items for it), its
class and name where known, its files by role (the artifacts of its items), the object whose file holds it (`in`, a contained
object), or the reason code of a generic, unsupported or failed object.

`fenced` names the objects that a reached object references across the font fence: the font bundles (the keys under
`EmbFont/` and `Font/`) are not followed from objects outside them, so each such reference is listed with the class and
name of the object it names, while the reference itself carries only its file and path id. A reference to an object of
the table is not listed.

## Product layout

A product layout places products for a player (`export assets --layout product`, `export live`,
`export live2d --player`):

- every file of a product and every file of the objects of its object table at `assets/<sha256>.<ext>` (a file
  several products or objects share is written once);
- each product's manifest at `<directory>/<file>.json` (the stage's `manifest(subject)`, by default
  `<stage>/<subject>.json` with the subject escaped as one path segment).

A command asks for products in one of two ways: `export assets --layout product` makes, with each product stage, the
products of the catalog keys the selected bundles hold (the stage's `select`; a stage that reads master data has none
without it); `export live`, `export live2d`, `export spot` and `export room` ask for the products of one subject.
`export live2d` without `--player`, `export spot` and `export room` place the files of their products at their own
paths instead (the `files` placement: the Cubism SDK files as a directory, a spot's files).

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

`room.json` also records `nodes`, each node's prefab `path` and Mesh object id in glTF node order, and
`materialIds`, Material object ids in glTF material order. These identify the serialized objects when the room
is displayed with a host scene's activation and material state.

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
and each layer's file in the web format of the parameter `audio`: `aac` (AAC-LC 160 kbit/s in MP4, the default),
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
(`json`), the variant index (`index`) and every GLSL ES 3.00 program; of a Mesh its `json` and `arrays` (the vertex
channels and submeshes as JSON, see [stages](stages.md#converters-unityexport)). The `product` layout makes `live.notes` when the
selection holds all its keys and the master data is there, and the `live.noteskin` and `live.noteeffect` products of
the skin and effect set keys the selection holds.

## Live scene

What exists on the live screen before the first note, and the start timeline of each band, is one product of
`nnnotes.pipeline.live`, shared by every live:

| Product | Subject | Manifest | Roots | Document |
|---|---|---|---|---|
| `live.scene` | `default` | `scenes/default.json` | the scene `EmbScene/Live`, the lane line prefab, the judge effect view prefab, the combo and judgement sprite assets, the sprites of every lane skin (`MasterLiveLaneSkin`), the start timeline and LightWeight background of every band (`MasterBand`), and the built-in `GraphicsSettings`, `QualitySettings` and `PlayerSettings` | `scene.json` |

The objects of a scene key are the objects of the scene's file outside a hierarchy: its root GameObjects, each a
prefab document, and its settings. A root references a component of another root with a `$ref`; the object table
lists that component as contained in the root GameObject holding it (`in`).

`scene.json` has `format` 1 and

| Field | |
|---|---|
| `scene` | the object ids of the scene's root GameObjects |
| `renderSettings` | the object id of the scene's `RenderSettings` |
| `graphics` | the object ids of the built-in settings: `graphics` (`GraphicsSettings`: the render pipeline asset, its renderers and their post-process data), `quality` (`QualitySettings`), `player` (`PlayerSettings`: the colour space) |
| `laneSkins` | `{lane skin asset name: {lane_base, lane_tap_area, out_side_line: Sprite id}}` |
| `prefabs` | the GameObject ids of `laneLine` (the inner lane lines), `judgeEffectView` and `barLineView` (the prefab the scene's bar line container instantiates) |
| `spriteAssets` | the object ids of the `combo` and `judgement` sprite assets |
| `bands` | `{band id: {timeline, background, tracks}}` of the bands whose start timeline the catalog holds: the timeline prefab's GameObject, the background Sprite, and `{AnimationTrack id: {name, prefab, animator}}`, the GameObject and node path of the Animator each track plays on |
| `settings` | the option defaults of preset 1 (`optionDefaults`) and the ranges (`optionRanges`) of the items the render path reads, the `MasterLiveQualitySettings` rows (`qualitySettings`), the default lane skin (`laneSkin`), every lane skin by option value (`laneSkins`), and the judgement sprite names by judgement (`judgementSprites`) |
| `bindings` | as in the note highway, with the clips of each band's timeline tracks on the Animators they play on |
| `shaders` | as in the note highway; of different shaders of one name, the name names the one `GraphicsSettings` always includes. It holds the shaders the live screen finds by name: `Hidden/CompositeRectBlit`, `Hidden/SimpleGaussianBlur`, `Sirius/Live/LiveSofdecPrimeYuv`, `Sirius/Live/LiveSofdecPrimeYuv_Android` and `UI/Default` |

A live plays the timeline and background of one band. The object table and the shader files are kept as in the note
highway. The `product` layout makes `live.scene` when the selection holds the scene and the master data is there.

## Lives

A live is one chart of a music. It is a product of `nnnotes.pipeline.live` that holds what belongs to the chart and
names the products it plays with:

| Product | Subject | Manifest | Roots | Document |
|---|---|---|---|---|
| `live` | `<musicId>/<difficulty>[/<band>]`: a `MasterLiveMusic` id, `easy`, `normal`, `hard` or `expert`, and optionally a band | `lives/<musicId>_<difficulty>[_<band>].json` | the chart `Live/MusicScore/<_musicScoreTextFileName>` and the jacket `Image/Jacket/<_jacketAssetName>` | `live.json` |

The band is the one whose start timeline and LightWeight background the live plays. The game takes it from the
player's deck centre; a subject without a band takes the band of the music's first vocal character
(`MasterLiveMusic._vocalCharacterIDs[0]`, its `MasterCharacter._bandID`). The input `master` holds the music's rows
(`MasterLiveMusic`, its `MasterLiveMusicScore` row of each difficulty, the title and band names in every language from
`MasterText`, the BGM and jingle `MasterSound` rows with their cue sheets), the difficulty, the band and the manifest
paths of the parts.

| File | |
|---|---|
| `live.json` | the document below |
| `chart.json` | the chart as shipped: the `TextAsset` of the chart key, un-gzipped |
| `notes.json` | the runtime notes the game converts the chart to, with `source`: the chart key, the music, the difficulty, the `MasterLiveMusicScore` id and the full combo count |
| `notes.mirror.json` | the same with the `MirrorChart` option on: lanes and flick directions mirrored |

`live.json` has `format` 1 and

| Field | |
|---|---|
| `musicId`, `difficulty` | the chart |
| `band` | `{band, source, note}`: the band, what it was taken from, and that the game takes it from the deck centre |
| `chart`, `notes`, `notesMirror` | the files above |
| `judgementNoteCount`, `noteCount`, `lastNoteTimeMs` | of the converted chart |
| `fullComboCount` | `MasterLiveMusicScore._fullComboCount` |
| `master` | the music's rows of the input `master` |
| `jacket` | the object id of the jacket Sprite |
| `parts` | `{part: manifest path}` of the products the live plays with: `scene` (`scenes/default.json`), `notes` (`notes/default.json`), `noteskin` and `noteeffect` (the note skin and effect set of the option defaults, `NoteDesignId` and `NoteEffectId` of preset 1), `sounds` (`sounds/default.json`) and `music` (`music/<musicId>.json`). A path is relative to the layout's root, as a manifest path |

The object table holds the jacket Sprite and its texture; the chart's `TextAsset` is in `chart.json`. The `product`
layout makes the live of each chart whose key and jacket the selection holds (with the master data), without a band.
`export live` asks for one live and its parts ([export live](commands.md#export-live)).

### Live start UI

`live.ui` has subject `<musicId>/<difficulty>/<language>` and manifest
`liveui/<musicId>_<difficulty>_<language>.json`. It reads the live scene, the jacket, the client's font assets,
LocalizeManager and TMP Settings, plus the chart's master rows. `ui.json` uses format `nnnotes.live-ui/1`.

- `scene` identifies the manifest, prefab and canvas path in `live.scene`.
- `strings` contains the title, singer, credits, level and a fresh profile's high score (`0`). The title uses the
  Japanese column; singer and credit labels use the requested language. Empty localized strings stay empty.
- `texts` maps TMP component ids to their path, displayed `text`, `setByInitialize`, localized layout `style`,
  original `localized` font selection and generated `font` selection.
- `images` maps Image component ids to the selected jacket and difficulty Sprite ids. `difficultyIcons` lists
  the canvas's icons, and `jacket` identifies the jacket. Canvas components and difficulty sprites belong to
  `live.scene`; the jacket and its texture belong to this product.
- `fonts`, `materials`, `textures`, `materialKeywords`, `coverage`, `lineBreaking` and `shaders` describe the
  generated text resources, with the same fields as the story font product.

The `fonts` parameter is `open` (default) or `game`. Both modes generate glyph pages and use the client's font
metrics, layout settings and fallback chains. Open fonts use explicit `font:<language>` byte inputs; their
contents participate in the task key. The exported object table holds the jacket and text shaders. The canvas
hierarchy is shared through `live.scene`.

`live.json` includes `parts.ui`, a language-to-manifest map. `export live` requests the languages selected by
`--live-languages`, defaulting to `[catalog] language`.

## Stories

A story episode plays with products that several episodes share, made by `nnnotes.pipeline.story`:

| Product | Subject | Manifest | Roots | Files |
|---|---|---|---|---|
| `story.sound` | a cue sheet name (`_cueSheetName` of an episode's cue sheet table) | `cuesheets/<sheet>.json` | the cue sheet's key `Cri/Sound/<sheet>` | `cues.json` and the cues' sounds |
| `story.crilips` | `default` | `crilips/default.json` | none (it reads the APK) | `crilips.json` and `crilips.bin` |
| `story.scene` | `default` | `storyscenes/default.json` | the ADV scene `EmbScene/Adv`, the prefabs `EmbCommon/Prefab/CameraManager`, `EmbCommon/Adv/Prefab/AdvCharacterField`, `AdvBackgroundField` and `AdvGlobalVolume`, the settings `EmbCommon/Adv/Settings/AdvPlayerSettings` and `AdvMasterIdSettings`, and the built-in `GraphicsSettings`, `QualitySettings` and `PlayerSettings` | `scene.json` and the object table |
| `story.asset` | a catalog key a story loads: a stage (`Adv/Stage/<name>`), frame, effect, post effect, still, timeline, chat window, chat icon or stamp, transition | `storyassets/<name>.json`, `<name>` the key with `/` as `~` | the key | `asset.json` and the object table |
| `story.video` | a video key `Cri/Video/<asset>` (the video of a Movie / Clip command) | `videos/<name>.json`, `<name>` the asset with `/` as `~` | the key | `video.json` and `movie.webm` |
| `story.chat` | `default` | `storychat/default.json` | the shared chat shards `Adv/Chat/AdvChat-Text`, `Adv/Chat/AdvChat-Sound` and `Adv/Chat/AdvChat-SoundCueSheet` | `chat.json` |
| `story.ui` | a client language (`ja`, `en`, `zh-Hant`, `zh-Hans`, `ko`) | `storyui/<language>.json` | the story UI's catalog keys (the widget, talk windows, chat widget, dialogs, UIManager, letterbox, animations and settings), the built-in `GraphicsSettings` and `LocalizeManager`, and the resources `DOTweenSettings` and `TMP Settings` | `ui.json` and the object table |
| `story.fonts` | `<episode id>/<language>` | `storyfonts/<id>_<language>.json` | the UI and font keys, the episode's chat windows and frames, and the UI settings | `fonts.json`, glyph pages and text shaders |
| `story` | a `MasterAdv` id | `stories/<id>.json` | the episode key `Adv/Episode/<asset>/<asset>` (`<asset>`: the row's `_advEpisodeAsset`), its shard keys (the key with `-Text`, `-Sound`, `-SoundCueSheet`, `-Video`) and `EmbCommon/Adv/Settings/AdvPlayerSettings` | `episode.json` and `story.json` |

`story.sound`, `story.crilips`, `story.video`, `story.chat` and `story` keep no objects: their files are all they
hold.
`story.sound` reads its cue sheet's `cri.audio` files; `cues.json` maps each cue name to the
first stream of that name (in stream order): `file` (the sound, next to `cues.json`), `sampleRate`, `channels`,
`samples` (frames at `sampleRate`, without encoder padding) and, for a looping stream, `loopStart` and `loopEnd`
(sample frames). A cue whose ACB sequence names CRI categories also has `categories`, the ordered category names
(for example `["Voice", "VoiceConfig"]`), read from sequence command 65 and the ACB's ACF category references.
The sounds are in the web format of the parameter `audio`, as for `live.music`: `aac` (the default)
records `encoderDelay`, the priming samples of the MP4 edit list; `flac` keeps the decoded FLAC. The `product`
layout makes the `story.sound` product of every cue sheet key the selection holds.

`story.crilips` reads the CRI Lips library of the APK (`apk:lib/arm64-v8a/libcri_lips_unity.so`, in the arm64-v8a
split of an installed app) and writes the analysis data a player needs to run the CRI Lips analysis on a voice: the
descriptor `crilips.json` and the float32 weights `crilips.bin`. A library of a version nnnotes does not know makes
the product `unsupported` (`unsupported.crilips.version`). Only a request makes it.

`story.scene` holds what the ADV screen loads before an episode's first line. `scene.json` has `format` 1 and

| Field | |
|---|---|
| `scene` | the root GameObjects of the ADV scene that have their prefab document |
| `renderSettings` | the scene's RenderSettings |
| `prefabs` | the root GameObjects of `cameraManager`, `characterField`, `backgroundField` and `globalVolume` |
| `settings` | the `playerSettings` (AdvPlayerSettings) and `masterIdSettings` (AdvMasterIdSettings) MonoBehaviours |
| `graphics` | the built-in `graphics`, `quality` and `player` settings |
| `bindings` | `{prefab GameObject id: {Animator node path: {clip id: [{path, attribute}]}}}`: the clips each Animator plays through its controller, named in its prefab |
| `shaders` | `{shader name: object id}`: what a lookup by name finds; of different shaders of one name, the one GraphicsSettings always includes |

The renderers' shaders and post-processing textures are reached through the graphics settings' references (the
render pipeline asset, its renderer data and their features). `story.asset` holds the objects of one catalog key
and what they reach; an asset several stories load is one product. `asset.json` has `format` 1, `key`, `objects`
(the key's object ids), `bindings` and `shaders` as above. The CRI Lips data, the story assets and the videos are
made only when a request names them; the `product` layout makes `story.scene` when the selection holds every key of
it.

`story.video` is one movie of a story. Its key loads a CriWare.Assets MonoBehaviour describing the movie and the movie
itself, a USM; `movie.webm` is the `movie.webm` that `cri.movie` makes of the USM with its parameter `format` `webm`
([stages](stages.md#cri-stages)): the first video stream, VP9 as stored or re-encoded as VP9 when it is in another
codec or has an alpha stream (the alpha becoming the WebM's alpha channel), and the first audio stream as Opus.
`video.json` has `format` 1, `file` (`movie.webm`), `codec` (the stored video codec: `vp9`, `h264` or
`sofdec.prime`), `width` and `height` (the coded size), `displayWidth` and `displayHeight`, `frameRate`
(`[numerator, denominator]`), `frames` and `tracks`, the tracks of `movie.webm` (`kind`, `channel`, `codec` and, for a
converted stream, its `source` codec).

`story.chat` holds the rows of the chat shards every chat of a story reads. `chat.json` has `format` 1, `text` (the
chat message texts), `sounds` (their sound rows) and `cueSheets` (the cue sheet rows those name), each the `_allData`
rows of its shard as stored; a shard key without a TextAsset gives `[]`. The `product` layout makes it when the
selection holds the three shard keys.

`story.ui` is the ADV story UI in one client language: the ADV front canvas, the talk windows, the chat phone, the
dialogs and the settings, with the text records of their texts in that language. Its input `master` holds the
`MasterText` rows, each text in the language's column (`{"texts": {MasterText id: text}}`, the empty text when the row
has none). `ui.json` has `format` 1 and `language` (`code`, `mode` (the LanguageMode), `field` (the text column),
`lineSpacing`, `fontNames`, `additionalFontNames`, `materialTypes` and `fontSwap`, the Japanese font asset names mapped
to the language's), `widget`, `talkWindows` (each window's name to its object), `chatWidget`, `dialogs`, `uiManager`,
`letterbox`, `clips`, `controllers`, `settings` (`masterIdSettings`, `dotween`, `tmpSettings`, `localizeManager`),
`graphics`, `texts` (each TMP text by its component id: `localized`, the font asset, material and line spacing its
LocalizeText gives it in the language, or null, and `style`, its text record), `textStyle` (the language's field, the
units and the line metrics of the font roles the texts use), `masterIdTexts` (each `AdvMasterIdSettings` text with its
MasterText id and text), `chatStatusTexts` (incoming-call and lock-screen status keys to their texts), `bindings`
and `shaders`. Text styles use settings read from the client's font assets. The input `ui.objects` is the closure
of the UI roots; font assets and chat windows are additional assembly inputs for metrics and status text keys.
The retained object table is the UI closure, so the font assets' files remain in the depot. The input
`font-bundles` names the bundles holding the font assets, and `apk:AndroidManifest.xml` supplies the client version.
A client requests the product for each language it uses.

### Overlay hosts and simple UI

For episodes with `MasterAdv._playbackMode` 1, `story.json` includes a `host` descriptor. The host is selected
from one matching `MasterStoryHomeSpotTapTalkEpisode`, `MasterHomeSpot` or `MasterStoryLiveResultEpisode` row.
It identifies the host kind and subject, the home talk kind and character where applicable, and the area title
in each language. A missing or ambiguous host is an error.

`parts.host` maps its subject to `storyhosts/home_<spot id>.json` or `storyhosts/afterlive.json`. A home host also
uses `parts.spots` and `parts.rooms`, whose keys are the situation and background addresses. These name the shared
`spot.files` and `room.gltf` products.

`story.host` writes `host.json` with format `nnnotes.story-host/1`. `prefabs` identifies the host's UI, capture
camera, background and situation objects. `overlayRoot` and `openedSequences` are `{prefab, path}` locations;
`layoutRoot` also carries the serialized `SimpleAdvLayoutRoot` fields. `home` identifies the room and spot
products, the Spot scene roots, and `backgroundState`: activation after selecting the situation, and the floor
renderer's queue. `afterlive` identifies the result background image and sprite, reward panel and substitute
elements. Graphics, DOTween settings, animation bindings and shaders are object references.

`parts.simpleui` maps each language to `storysimpleui/<language>.json`. `story.simpleui` writes `ui.json` with
format `nnnotes.story-simple-ui/1`: the simple talk-window, system-message and UIManager prefab ids, settings,
text records, animation bindings and shaders. The episode's `story.fonts` product includes these texts and the
area title in either font mode. Font files are read as inputs; the retained UI closure uses fenced font references.

### Episode fonts

`story.fonts` contains the font records for one episode and language. Its `episode` input is the first run's
`episode.json`, supplied in the planning fact `product_inputs` under `episode:<id>`. This snapshot determines the
chat-window and frame roots before object export. Its other inputs are localized `MasterText` rows, the font
bundle record, APK version and object table. With parameter `fonts: open`, configured font files enter as
`font:<language>` and optionally `font:emoji`; `fonts: game` reads the client's TMP assets.

`fonts.json` has format `nnnotes.story-fonts/1`, `language`, `source`, `fonts`, `textures`, `materials`,
`materialKeywords`, `coverage`, `lineBreaking` and `shaders` (shader names to retained object ids).
`texts` maps TMP component ids to `style`, `localized` (the LocalizeText result, or null), and `font` (the effective
font asset, material and line spacing to render, including generated open font names). Ruby and Korean-adjustment
settings accompany the relevant text records. `sprites` maps driven TMP component ids to their sprite asset and
tint flag; `spriteAssets` and `emojiSpriteAsset` are present when text can draw emoji sprites.

Glyph selection includes episode lines and names, the title, localized UI labels, status text, chat runtime
strings and frame text. It follows each primary font's fallback chain and models synthesized control characters,
underline and missing glyphs. The product retains glyph pages and the required shader programs; font files and
source atlas objects remain in the depot. Page file names in `textures` resolve through the product's `files`.

`story` is one episode. Its input `master` is the episode's master record: its `MasterAdv` row, the `MasterText` row
of its title (none when `_titleTextId` is empty) and every `MasterAdvChat` row. `episode.json` is the episode as
`export episode` writes it, each resource given by its kind and address: the command list with the lines of every
language, the voices, sounds, cue sheets and videos the commands name, and the resources the episode loads.
`story.json` has

| Field | |
|---|---|
| `format` | 1 |
| `advId` | the `MasterAdv` id |
| `episode` | `"episode.json"`, the file next to it |
| `chats` | `{chat id: MasterAdvChat row}`: each chat id the commands name (`TargetChatID`) |
| `effects` | `{TargetName: "Adv/Effect/<asset>"}`: the effect instances, the first asset an Effect command loads under a name |
| `parts` | the manifests of the products the story plays with: `scene` (`storyscenes/default.json`), `assets` (`{address: manifest}` of each `story.asset`), `models` (`{address: manifest}` of each `live2d.model`), `sounds` (`{cue sheet: manifest}` of each `story.sound`), `videos` (`{address: manifest}` of each `story.video`), `chat` (`storychat/default.json` when a command names a chat, else null) and `crilips` (`crilips/default.json` when a voice command has a target and voice cues and sets neither `IgnoreData` nor `IgnoreLipSync`, else null) |

A model's MotionSync (model.json `motionSync`) decides whether a lip-synced voice runs the CRI Lips analysis, so
`crilips` names the data whenever a voice can reach it. The episode's talk windows are listed in `episode.json`
`resources`. `parts` depend on the episode's commands, which only the story's result holds: a command
asks for the `story` product first, reads its `story.json`, then asks for the story with every product `parts` names
(`nnnotes.pipeline.story.part_products`, with the requested languages); `export story` works this way.
`parts.ui` and `parts.fonts` map every client language to its product manifest. The command exports those entries
for the requested languages. The `product` layout makes the story of
every episode whose key the selection holds.

## UI library products

`ui.pack` assembles one catalog key and its reachable objects. Its `ui.json` (`nnnotes.ui-pack/1`) names root prefabs, controllers, animation bindings and shaders. The product layout writes `index.json` (`nnnotes.ui-library/1`) over the selected UI products; all referenced data lives in their object tables. See [UI libraries](ui.md).

A stage with `INCLUDE_FONTS = True` requests traversal through its referenced font objects. The request records these roots in `font_roots`; unit selection and assembly follow the same rule. Other products keep their own font selection behavior.
