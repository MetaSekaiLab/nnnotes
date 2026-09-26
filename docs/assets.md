# Asset export

`nnnotes export` writes every object of the selected bundles in a common format: textures and sprites as PNG,
meshes as binary glTF, text assets and fonts as their stored bytes, prefabs, materials, animations and every other
class as JSON. It also writes the semantic views of the master data (cards, characters, stamps, …) with the files of
the objects they name. The work is split into stages ([stages.md](stages.md)); each stage runs one task per subject
(a bundle, a view, …), and a task's result is stored under a key that covers exactly what its output depends on
([contracts.md](contracts.md)). A second run over the same data finds every result in the store and only
rewrites what changed.

```
nnnotes export -o out/assets
nnnotes export -o out/cards --select group:membercard --views cards
nnnotes plan --select key:Image/Jacket/
nnnotes plan --check && echo "up to date"
```

## Commands

```
nnnotes export -o OUT [--layout original[,cas]] [--select group:G|key:PREFIX|bundle:GLOB ...] [--views all|none|a,b]
               [--store DIR] [--workers N] [--memory GiB] [--fetch-workers N] [--catalog-version L|SHA]
               [--png-level N] [--only-class C,...] [--link copy|hard] [--strict] [--dry-run] [--explain]
nnnotes plan  [the selection and parameters of export] [-o OUT] [--json] [--since RUN] [--check] [--census]
              [--emit-tasks DIR] [--why TASK]
nnnotes run-stage TASK.json|DIR [...] [--store DIR] [--fetch] [--force]
nnnotes catalogs list [--json] | import FILE [--label L] [--apk-catalog FILE] | fetch [--label L]
                 | diff OLD NEW [--json]
nnnotes store verify [--quick]
```

### export

| Flag | Meaning |
|---|---|
| `-o OUT` | output directory |
| `--select SEL` | what to export (repeatable, the union; default everything): `group:G` the bundles of an Addressables group (the stable bundle name up to `_assets_`, else up to its first `_`), `key:PREFIX` the bundles and raw files the catalog keys under `PREFIX` load, `bundle:GLOB` the bundles and raw files whose stable name matches |
| `--views` | the semantic views to build ([views.md](views.md)): `all`, `none` or names, comma-separated; default `all` when master data is set, else `none` |
| `--layout` | `original`, `cas` or both (below) |
| `--store DIR` | the store (`[paths] store`; default `<cache>/store`) |
| `--workers N` | worker processes (default: the CPUs the process may run on, its affinity mask as `taskset` or a container's cpuset sets it; `0`: every task in this process) |
| `--memory GiB` | memory budget (default: 80 % of the physical memory where it can be read, else none) |
| `--fetch-workers N` | parallel downloads of bundles not in the cache (default 8) |
| `--catalog-version` | an imported catalog version (`catalogs list`) instead of the current catalog |
| `--png-level N` | zlib level 0-9 of the PNG files (default 6); part of the task keys |
| `--only-class C,...` | export only objects of these Unity classes; part of the task keys |
| `--link` | `copy` the files from the store (default) or `hard`-link them (a file that cannot be linked is copied) |
| `--strict` | exit 1 when a view has gaps: a required role whose address names no catalog key or no fitting sub-object (empty values and roles that do not apply are not gaps) |
| `--dry-run` | print the plan and stop |
| `--explain` | print every setting of the run first: store, context, selection, pipeline, stages not installed, parameters, workers, memory budget, recycling, layouts |

A run resolves the context (region, language, catalog version) and indexes the catalog, identifies the selected
bundles (the sha256 of each decrypted bundle, remembered by path, size and modification time; bundles not in the
cache are fetched), takes the census of each bundle, links the scripts and addresses, exports the bundles, runs the
stages that need those results (atlas sprites, views), writes the layouts and the reports. The census also covers
the bundles the selected ones depend on, so script classes and atlas textures in other bundles resolve.

Tasks run in spawned worker processes that stay alive between tasks. The longest tasks (by estimated CPU time)
start first; a task starts only while the estimated peak memory of the running tasks and its own fits the budget,
and a task estimated above the budget runs alone. A worker is replaced after 500 tasks or when its resident memory
is above 3 GiB after a task. The estimates come from each stage's cost model, scaled per stage by the costs
measured in earlier runs of the store (`<store>/costs/calibration.json`, updated after every export). None of this
changes an output: a run with one worker and a run with many write the same store and the same files.

A failed object is reported with its task's result and stays failed until something in its key changes. A task that
fails as a whole (an exception, an input that cannot be read, a worker that dies) leaves no result and runs again
next time.

### plan

What `export` with the same flags would do, without running or fetching anything: every task with its status
(`hit`: its result is stored, `run`, `unknown`: it reads the result of a task that would run first, or of a bundle
not fetched yet), the reasons it runs (compared with the latest run of the same selection, or `--since RUN`), the
estimated CPU time and peak memory, and with `-o` the files each layout would write, remove and keep. The text
gives per stage its counts and its first 20 tasks that are not hits; `--json` has every task.

| Flag | Meaning |
|---|---|
| `--json` | the plan document (`nnnotes.plan/1`) |
| `--check` | exit 1 when anything would run or a layout file would change, else 0 |
| `--census` | first fetch the bundles without a census and take it, so the plan knows every bundle's classes |
| `--emit-tasks DIR` | write the tasks that can run now as task descriptions `DIR/<key>.json` |
| `--why TASK` | one task: its status, reasons, and its key parts beside those of the previous run |

Reasons: `new-input`, `input-changed`, `stage-version`, `atom-changed`, `params-changed`, `context-changed`,
`previous-failed`, `result-missing`, `forced`; hits are `unchanged` or `cached` (the result is in the store from
another run); unknown tasks are `pending` on a task, or on `fetch:<file>` for a bundle whose content is not known
yet. See [contracts.md](contracts.md#plan-nnnotesplan1).

### run-stage

Runs task descriptions (`nnnotes.task/1`, as `plan --emit-tasks` writes them) into a store: each is checked (its key
is recomputed, its stage must be installed at its version), its inputs are read by content id through their
locators (the store, the cache, a file, or with `--fetch` the catalog), and its result is stored exactly as `export`
would store it. Several tasks run one after another in one process; stages that do not read bundles never load
UnityPy. `--force` runs tasks whose result is stored again (the new result must be identical).

### catalogs

The catalog versions of a store: `import` a catalog file (with the APK's catalog from `[paths] apk` or
`--apk-catalog`), `fetch` the current catalog of the configured region and language, `list` them, and `diff` two of
them (labels, version ids or sha256 prefixes): keys added, removed and changed, bundles and raw files by stable name,
the keys that reach a changed file, and, when a run over each version has stored its address table, the objects by
stable address (an object keeps its stable address while its object id changes with its bundle's content). A version
is labelled by `--label`, else by the resource version (`fetch`, when the region has an API root), else by the
first 12 hex digits of its sha256. `export` imports the current catalog when it is not imported yet.

### store verify

Checks that every stored content has the sha256 its name says (`--quick`: sizes only) and that every result names
its own key, has the key of its parts and names stored contents of the right size. Exit 1 when a problem is found.

## Layouts

A layout places the artifacts of a run as files; it never changes their bytes. With one layout the files are
written into `OUT`, with several into `OUT/<layout>`. Each layout directory has a `manifest.json`
(`nnnotes.layout/1`) listing the files it owns; a run writes files atomically, removes the files it owned that are
no longer wanted, never touches other files, and continues an interrupted write on the next run.

`original` uses the game's names:

- an object with a container path is written at that path, in the catalog's spelling
  (`Assets/AddressableResources/Image/Jacket/jacket_001.png`), its extension appended unless the path has it
  (`…/prefab_x.prefab.json`);
- other objects under the same container path: `stem[<name>].<ext>`, the Addressables sub-object form
  (`…/member_full[square].png`);
- objects without a container path: `_bundles/<stable bundle name>/<Class>/<name>~<pathId>.<ext>`;
- secondary artifacts beside the primary one: `<path>.<role>.<ext>` (`….meta.json`, `….blob.m_Data.moc3`);
- a container path present in two bundles: `stem@<stable bundle name>.ext` for each;
- names Windows refuses, reserved device names and trailing dots are escaped as `%XX`; paths that coincide ignoring
  case get `~<8 hex>` and are listed in the layout report;
- the outputs of the CRI stages at the catalog names of their content: `Cri/Sound/<sheet>/<stream>.flac` with
  `cues.json` and `streams.json`, `Cri/Video/<name>/…`; a content several names refer to is placed under each
  name (linked or copied as `--link` says) and listed in the layout report;
- the views: `views/<view>.json`, each object entry with the `path` of its file in this layout.

`cas` is `assets/<sha256>.<ext>` per distinct content, plus the manifest. Each layout follows from the other: `cas`
from the `original` manifest alone, `original` from `cas` and the results. The views are the exception: `original`
has them with the paths of their objects, `cas` the view documents they are made from.

## Reports

`OUT/_reports/`:

| File | Content |
|---|---|
| `run.json` | the run manifest (`nnnotes.run/1`): context, selection, parameters, stage versions, every task with its key and status, the layout manifests, a summary; also stored as `<store>/runs/<id>.json` |
| `coverage.json` | per class the census total and the count of every item status (`exported`, `contained`, `generic`, `unsupported`, `failed`, and `missing`: objects no task covered; an object two tasks cover, such as a sprite whose image `sprite.crop` makes, counts once); per reason code its count and five examples |
| `failures.json` | failed tasks and failed objects, with their codes and messages |
| `sources.json` | files of the selection that could not be read: `source.absent` (a file the catalog names that is not there, or an APK file without `[paths] apk`), `source.error` (the download failed) |
| `layout-<name>.json` | the paths renamed for collisions, the container paths present in several bundles, the contents placed under several names |

The summary printed at the end names the run, the task and item counts, the layouts' write / remove / keep counts
and the reports directory.

## Exit codes

| Command | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `export` | complete (unsupported objects allowed) | a task, an object or a download failed (reports written); with `--strict` also view gaps | usage or settings | aborted: a setting or tool a task needs is missing, or interrupted; results written so far stay valid | |
| `plan` | | with `--check`: something would run or change | usage or settings | | |
| `run-stage` | every task ran or hit | a task failed or its result is partial | usage or settings | | a task is incompatible (edited, unknown stage or version); nothing of the batch runs |
| `store verify` | no problem | problems found | usage or settings | | |

## Running tasks elsewhere

Task descriptions carry everything a task needs; any machine with the same nnnotes version and access to the store
can run them. A task can only be described once the content of its inputs is known, so the bundles are identified
first (`plan --census` fetches and hashes them and takes their census). Then plan the tasks that can run now, run
them in batches on as many workers as you like, and plan again until nothing is left; `export` finally writes the
layouts and reports (every task is a hit by then).

```sh
store=/data/nnnotes-store
nnnotes plan --store "$store" --census > /dev/null
for round in 1 2 3 4 5 6 7 8; do
    rm -rf tasks
    nnnotes plan --store "$store" --emit-tasks tasks > /dev/null
    ls tasks/*.json > /dev/null 2>&1 || break           # nothing left to run
    ls tasks/*.json | xargs -n 50 -P 16 nnnotes run-stage --store "$store" --fetch
done
nnnotes export --store "$store" -o out/assets
```

Each round runs the tasks whose inputs are known: the catalog index and the censuses, then the script and address
tables, then the bundle exports, then the stages that read their results. A task whose result is already stored is
a hit, so a batch can be run twice; a task that keeps failing is planned again in every round (the loop above stops
after eight) and reported by `export`.
