# Contracts

The asset stages exchange documents in fixed formats. They are the public interface of the extraction: a task
description can be run on another machine, a result can be read without nnnotes, and every output layout can be
derived from the records. JSON Schemas of all documents are in [schema/](schema/) (draft 2020-12, each with
`$id` `urn:nnnotes:schema:<name>`; shared definitions in [schema/common.schema.json](schema/common.schema.json)).

## Versioning

Every document names its format in a `schema` field (`nnnotes.result/1`). Within one major version fields are only
added, never renamed, removed or given another meaning; readers ignore fields they do not know. A breaking change
is a new major version (`nnnotes.result/2`).

Stages carry their own version. A change of what a stage writes for the same inputs is a new stage version, which
changes the key of every task of that stage (golden tests fail when a stage's output changes while its version
stays).

## canon/1

- UTF-8, LF line endings, one trailing newline, non-ASCII characters as they are.
- Contract documents (everything on this page): object keys sorted, indent 1.
- Object documents (the JSON of one Unity object): the object's own field order, indent 1.
- Non-finite numbers: `1e999` / `-1e999` (valid JSON, read back as infinity by `JSON.parse` and Python), NaN as
  `{"$float": "nan"}`. Objects with a single `$`-prefixed key (`$float`, `$ref`, `$blob`, `$hex`) are reserved for
  such tagged values.
- Content id: the sha256 of the bytes, lower-case hex.
- Key hash: the sha256 of the compact form of a JSON value (keys sorted, no whitespace, ASCII only with `\uXXXX`
  escapes, NaN and infinities as above).

## Identities

| Identity | Form | Example |
|---|---|---|
| object | `<serialized file>:<pathId>` | `CAB-2f1e…:-4153…`, `unity default resources:10` |
| artifact of an object | `<object id>#<role>` | `CAB-2f1e…:-4153…#image` |
| artifact of no object | `<stage>:<subject>#<role>` | `cri.audio:<acb sha256>#bgm_x.flac` |
| task | `<stage>:<subject>` | `unity.export:membercard_assets_membercard_100101` |
| stable bundle name | the bundle file name without `_<32 hex>` and `.bundle` | `membercard_assets_membercard_100101` |
| stable address | `<stable bundle>/<container>` (`[<name>]` for a sub-object), else `<stable bundle>:<pathId>` | `membercard_assets_membercard_100101/assets/…/member_full.png` |

Stage names contain no `:` or `#`; an artifact's owner contains no `#` (its role may). A bundle's serialized file
names change whenever its content changes, while its path ids stay: an object id names one content version of an
object, and the stable address finds the same object in another catalog version. Bundle stages use the stable
bundle name as their subject, so a task keeps its id across versions while its key follows the content. When two
bundles of a catalog have the same stable name, both keep their full file names.

## Task: nnnotes.task/1

One stage applied to one subject, with everything needed to run it
([schema/task.schema.json](schema/task.schema.json)):

```json
{"schema": "nnnotes.task/1", "id": "unity.export:membercard_assets_membercard_100101",
 "stage": {"name": "unity.export", "version": 1},
 "params": {"png": {"encoder": "pillow", "level": 6}, "blobMin": 4096},
 "atoms": {"png.encode": "pillow-12.3.0/1", "texture.decode": "unitypy-1.25.3/1"},
 "inputs": [{"role": "bundle", "sha256": "…", "size": 123456, "name": "membercard_…_3fa…c2.bundle",
             "locators": [{"kind": "store"}, {"kind": "cache", "path": "bundles/membercard_…_3fa…c2.bundle"}]}],
 "context": {"scripts": {"CAB-…:12345": "Assembly-CSharp|Fwk.Sound|SplitAcbData"}},
 "key": "…", "cost": {"cpuSeconds": 1.2, "peakBytes": 450000000}}
```

The key is the key hash of the key parts:

```json
{"stage": {"name": "…", "version": 1}, "subject": "…", "params": {…}, "atoms": {…},
 "inputs": [["<role>", "<sha256>"], …], "context": {…}}
```

- `params`: only parameters that change the output, with every default filled in.
- `atoms`: the implementation ids (with library versions) of only the atoms the task uses.
- `inputs`: sorted by role; each role appears once. Inputs are identified by content: a bundle by the sha256 of its
  decrypted bytes, an external tool by the sha256 of its executable and its version string. Keys never contain
  secrets.
- `context`: only the entries the task consumes (for example the script classes its MonoBehaviours reference).
- Input names, locators and the cost estimate are not part of the key.

A stage whose output depends on anything else puts it into its parameters or context. Running a task description
recomputes its key and refuses the task when it differs, when the stage is unknown, or when the installed stage has
another version.

Locator kinds, tried in order to find an input's bytes (each checked against the content id):

| Kind | Where |
|---|---|
| `store` | the store's `cas/` |
| `cache` | `path` relative to the cache directory |
| `file` | an absolute `path` |
| `catalog` | fetched through a catalog version of the store (`catalog`: its version id, `location`: the location id in its index) |

## Artifact record: nnnotes.artifact/1

One output file ([schema/artifact.schema.json](schema/artifact.schema.json)):

```json
{"id": "CAB-…:-4153…#image",
 "content": {"sha256": "…", "size": 1234, "mediaType": "image/png", "ext": "png"},
 "provenance": {"stage": {"name": "unity.export", "version": 1, "params": "<key hash of the params>"},
                "atoms": {"texture.decode": "…", "png.encode": "…"},
                "inputs": [{"role": "bundle", "sha256": "…"}],
                "object": {"file": "CAB-…", "pathId": -4153, "classId": 28, "class": "Texture2D",
                           "name": "member_full", "container": "assets/…/member_full.png"}},
 "semantics": {"kind": "texture.image", "format": "png", "facts": {"width": 1024, "height": 1024},
               "refs": [{"rel": "alphaTexture", "object": "CAB-…:77"}]}}
```

`ext` is lower-case letters and digits. The bytes are stored once, by content id, whatever their names.

Roles: an object's primary artifact is the one whose role comes first in `image`, `mesh`, `audio`, `video`, `font`,
`data`, `prefab`, `json` (then by role name); its other artifacts (`meta`, `blob:<field path>`, …) are secondary.

## Result: nnnotes.result/1

The result of a task, written after the bytes of all its artifacts are stored: its presence is the commit
([schema/result.schema.json](schema/result.schema.json)).

```json
{"schema": "nnnotes.result/1", "key": "…", "keyParts": {…}, "status": "ok",
 "artifacts": [<artifact records, sorted by id>],
 "items": [{"object": "CAB-…:1", "class": "Texture2D", "status": "exported", "artifacts": ["CAB-…:1#image"]},
           {"object": "CAB-…:2", "class": "Transform", "status": "contained", "in": "CAB-…:9#prefab"},
           {"object": "CAB-…:3", "class": "Texture2D", "status": "unsupported",
            "reason": {"code": "empty.texture", "message": "…"}}]}
```

A result is a function of its key: it holds no input name, locator, timestamp or traceback, so identical inputs of
two regions or catalog versions share it. Items, one per object the task covers, sorted by object:

| Status | Fields | Meaning |
|---|---|---|
| `exported` | `artifacts` | written in its specialized form |
| `contained` | `in` | part of another artifact (for example a transform inside a prefab) |
| `generic` | `artifacts`, `reason` | written as generic typetree JSON; the reason says why the specialized form was not made |
| `unsupported` | `reason` (`artifacts` possible) | not written in full, for a documented reason |
| `failed` | `reason` | the object could not be exported; deterministic, so cached with the result |

The result status is `ok`, or `partial` when an item failed. A task that fails as a whole (an exception out of its
stage, an input that cannot be read, a worker process that dies) leaves no result: it runs again next time.

## Run manifest: nnnotes.run/1

What one run covered ([schema/run.schema.json](schema/run.schema.json)): the context (region, language, the catalog
version, and the master data its views read: the version named by `MasterManifest.json` and the sha256 of each
table), the selection, the normalized parameters per stage, the stage versions, every task with its
key (`null` when it could not be described) and status (`hit`: the result was in the store, `ran`, `failed`), the
sha256 of each layout manifest written, and a summary (task and item counts). The run id is the key hash of the
context, selection, params, stages, the tasks' ids and keys, and the layouts, so a run with the same inputs has
the same id whether its tasks ran or hit. The run log (`runs/<id>.log.jsonl`) holds what is not deterministic:
worker numbers, timings, memory, tracebacks.

Task failure codes (failed items carry the reason codes of their stages):

| Code | Meaning |
|---|---|
| `task.error` | an exception out of the stage |
| `task.input` | an input could not be read with its content id |
| `task.io` | a file system error |
| `task.memory` | out of memory |
| `task.conflict` | the stage wrote a result that differs from the stored one for the same key |
| `task.incompatible` | the task description does not match this installation |
| `task.worker_died` | the worker process ended while running the task |
| `task.describe` | the task could not be described |
| `upstream.failed` | the task reads the result of a task that failed |
| `upstream.missing` | the task reads the result of a task that did not run |
| `config` | a setting or external tool the task needs is missing: the run stops (results already written stay valid) |

## Plan: nnnotes.plan/1

What a run would do, without running anything ([schema/plan.schema.json](schema/plan.schema.json)): every task as
a node with its key, status (`hit`, `run`, `unknown`), reasons and cost estimate, the edges between tasks, and a
summary (counts, estimated CPU seconds and peak memory for the given workers and budget, tasks above the budget,
and per layout the files it would write, remove and keep). Reasons compare the key parts with those of the same
task id in the previous run manifest:

| Reason | Status | Meaning |
|---|---|---|
| `new-input` | run | the previous run had no task with this id |
| `input-changed` | run | an input's content changed (`role`, `old`, `new` content ids, `name` of the new input) |
| `stage-version` | run | the stage version changed (`old`, `new`) |
| `atom-changed` | run | an atom implementation changed (`atom`, `old`, `new`) |
| `params-changed` | run | a parameter changed (`path`, `old`, `new`) |
| `context-changed` | run | a consumed context entry changed (`entry`) |
| `previous-failed` | run | the task failed in the previous run |
| `result-missing` | run | same key as before, but its result is not in the store |
| `key-changed` | run | the key changed for none of the reasons above |
| `forced` | run | asked to run again |
| `unchanged` | hit | same key as in the previous run |
| `cached` | hit | the result is in the store from another run |
| `pending` | unknown | the task reads the result of `task`, which would run first (`fetch:<file>`: an input file whose content is not known yet) |

## Layout manifest: nnnotes.layout/1

The files an output layout owns in its directory, as `manifest.json` there
([schema/layout.schema.json](schema/layout.schema.json)): name, parameters, and `entries` `{path, sha256, size, id}`
sorted by path, one per artifact (artifacts of equal content may share a path). A layout changes neither artifact
bytes nor keys; the file names of `original` and `cas` both follow from the records, and `cas` also from the
`original` manifest alone. Documents derived for `original` from records and paths (the views with the files of
their objects) are the exception: `original` has the derived documents, `cas` the artifacts they are made from.

## Reports

- Coverage, `nnnotes.coverage/1` ([schema/coverage.schema.json](schema/coverage.schema.json)): per class the census
  total, the objects without an item (`missing`) and the count of every item status; per reason code its count and
  the first five object ids. An object with items in several results (a sprite whose image `sprite.crop` makes
  beside its `unity.export` item) counts once, with the most severe of its statuses (`failed`, `unsupported`,
  `generic`, `exported`, `contained`, in that order) and that item's reason.
- Failures, `nnnotes.failures/1` ([schema/failures.schema.json](schema/failures.schema.json)): `{task, stage, input,
  object, code, message}` for failed tasks (`object` null) and failed items, sorted by task and object.
- Cost, `nnnotes.cost/1` ([schema/cost.schema.json](schema/cost.schema.json)): the measured CPU and wall seconds
  and peak resident memory of a task's last run.

Documents written by particular stages (artifacts of no object: their ids are `<stage>:<subject>#<role>`):

| Document | Stage | Schema |
|---|---|---|
| `nnnotes.catalog-index/1` | `catalog.index` | [schema/catalog-index.schema.json](schema/catalog-index.schema.json) |
| `nnnotes.census/1` | `unity.census` | [schema/census.schema.json](schema/census.schema.json) |
| `nnnotes.scripts/1` | `link.scripts` | [schema/scripts.schema.json](schema/scripts.schema.json) |
| `nnnotes.addresses/1` | `link.addresses` | [schema/addresses.schema.json](schema/addresses.schema.json) |
| `nnnotes.artifacts/1` | `link.artifacts` | [schema/artifacts.schema.json](schema/artifacts.schema.json) |
| `nnnotes.view/1` | `view.<name>` | [schema/view.schema.json](schema/view.schema.json) |

The catalog versions of a store, `nnnotes.catalogs/1` ([schema/catalogs.schema.json](schema/catalogs.schema.json),
`<store>/catalogs/index.json`), and the difference between two of them, `nnnotes.catalog-diff/1`
([schema/catalog-diff.schema.json](schema/catalog-diff.schema.json), `nnnotes catalogs diff --json`), are written
by the commands.

## Store

The store is a directory, and the cache of all stage work:

```
<store>/cas/sha256/ab/<sha256>       bytes by content id, written once (no extension: records carry it)
<store>/ac/ab/<key>.json             results by task key (written after the bytes they name)
<store>/inputs/<kind>.jsonl          input identities: name -> sha256, size, with the path, size and mtime of the
                                     file that was hashed (bundles.jsonl, raw.jsonl, files.jsonl)
<store>/catalogs/                    versioned catalogs
<store>/runs/<run>.json              run manifests
<store>/runs/<run>.log.jsonl         the log of the run's last execution
<store>/runs/selections/<digest>     the id of the latest run of a selection (key hash of the selection list)
<store>/costs/<key>.json             the measured cost of a task's last run
```

Every file is written to a temporary name and renamed into place, so concurrent writers never see or leave a
partial file and an interrupted task leaves no result. `cas/` and `ac/` depend only on the tasks run: two runs of the
same tasks, with any number of workers, leave identical trees. `inputs/`, `runs/*.log.jsonl` and `costs/` are
local and not deterministic.
