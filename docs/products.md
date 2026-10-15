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

All are read from the planning facts alone, so a command can ask for what its products need before anything runs:
`assemble.request(stages, products, env)` gives the fact `roots` (`{"keys", "resources", "builtins", "objects"}`), and
the request of `unity.export` then holds the units of those objects and every unit reachable from them through the
census `refs` ([units](stages.md#units)), and nothing else of their bundles.

What a task reaches: the units holding its roots, then every unit that an item of a reached unit references (the
item's `refs`), through the `unity.export` results. A sprite whose atlas is in another bundle has its `image` from the
`sprite.crop` result of that atlas. A reached unit that the request did not export is an error of the request; an
object that a reached object references and no census holds is listed as `missing`.

| Part of the task | |
|---|---|
| inputs | `objects`: the object table of what it reaches (`nnnotes.objects/1`); the stage's own inputs (master tables, data its parameters name) |
| depends | `link.addresses` (when it has keys, resource paths or built-in selectors), the `unity.export` and `sprite.crop` tasks whose results it read |
| artifacts | `<task id>#<path>`: the product's own files; `<task id>#objects`: the object table of the objects its files use (the whole table unless the stage narrows it to some objects, or to some files of an object: a shader's programs for one graphics API) |
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
