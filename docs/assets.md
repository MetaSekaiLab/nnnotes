# Asset export

`nnnotes export assets` writes every object of the selected bundles in a common format: textures and sprites as PNG,
meshes as binary glTF, text assets and fonts as their stored bytes, prefabs, materials, animations and every other
class as JSON. It also writes the semantic views of the master data (cards, characters, stamps, …) with the files of
the objects they name. The work is split into stages ([stages.md](stages.md)); each stage runs one task per subject
(a bundle, a view, …), and a task's result is stored under a key that covers exactly what its output depends on
([contracts.md](contracts.md)). A second run over the same data finds every result in the depot and only
rewrites what changed.

```
nnnotes export assets -o out/assets
nnnotes export assets -o out/cards --select group:membercard --views cards
nnnotes depot plan --select key:Image/Jacket/
nnnotes depot plan --check && echo "up to date"
```

## Commands

```
nnnotes export assets -o OUT [--layout original[,cas][,product]] [--select group:G|key:PREFIX|bundle:GLOB ...] [--views all|none|a,b]
               [--depot DIR] [--workers N] [--memory GiB] [--fetch-workers N] [--stored-catalog L|SHA]
               [--png-level N] [--flac-level N] [--only-class C,...] [--link auto|clone|hard|copy] [--strict]
               [--dry-run] [--explain]
nnnotes depot plan  [the selection and parameters of export] [-o OUT] [--json] [--since RUN] [--check] [--census]
              [--emit-tasks DIR] [--why TASK]
nnnotes depot run TASK.json|DIR [...] [--depot DIR] [--fetch] [--force]
nnnotes catalog versions [--json] | import FILE [--label L] [--apk-catalog FILE] [--resource-version V] | fetch [--label L]
                 | diff OLD NEW [--json]
nnnotes depot verify [--quick] [--workers N]
```

### export assets

| Flag | Meaning |
|---|---|
| `-o OUT` | output directory |
| `--select SEL` | what to export (repeatable, the union; default everything): `group:G` the bundles of an Addressables group (the stable bundle name up to `_assets_`, else up to its first `_`), `key:PREFIX` the bundles and raw files the catalog keys under `PREFIX` load, `bundle:GLOB` the bundles and raw files whose stable name matches |
| `--views` | the semantic views to build ([views.md](views.md)): `all`, `none` or names, comma-separated; default `all` when master data is set, else `none` |
| `--layout` | `original`, `cas`, `product`, comma-separated (below) |
| `--depot DIR` | the depot (`[paths] depot`) |
| `--workers N` | worker processes (default: the CPUs the process may run on, its affinity mask as `taskset` or a container's cpuset sets it; `0`: every task in this process) |
| `--memory GiB` | memory budget (default: 80 % of the physical memory where it can be read, else none) |
| `--fetch-workers N` | parallel downloads of bundles not downloaded yet (default 8) |
| `--stored-catalog` | a catalog version of the depot (`catalog versions`) instead of the current catalog |
| `--png-level N` | zlib level 0-9 of the PNG files (default 6); part of the task keys |
| `--flac-level N` | FLAC compression level 0-12 of the CRI audio and the movies' audio (default 8; every level decodes to the same samples; 12 writes files 0.2-1.3 % smaller in about five times the encoding time); part of the task keys |
| `--only-class C,...` | export only the units whose root object is of these Unity classes (a prefab by `GameObject`, [stages.md](stages.md#units)) |
| `--link` | how the layout files are made from the depot (below): `auto` (default; `[export] link`), `clone`, `hard` or `copy` |
| `--strict` | exit 1 when a view has gaps: a required role whose address names no catalog key or no fitting sub-object (empty values and roles that do not apply are not gaps); in `voices`, a voice whose cue sheet has no catalog key or whose decoded sheet lacks its cue, and a story episode without a catalog key |
| `--dry-run` | print the plan and stop |
| `--explain` | print every setting of the run first: depot, context, selection, pipeline, stages not installed, parameters, workers, memory budget, recycling, layouts |

A run resolves the context (region, language, catalog version) and indexes the catalog, identifies the selected
bundles (the sha256 of each decrypted bundle, remembered by path, size and modification time; bundles not
downloaded yet are fetched into the depot's `downloads/`), takes the census of each bundle, links the scripts and
addresses, exports the units (one task per hierarchy or object outside any, [stages.md](stages.md#units)), runs the
stages that need those results (atlas sprites, views), writes the layouts and the reports. The selected raw files
(CRI audio and movies) not downloaded yet are fetched while the bundle stages run. The census also covers
the bundles the selected ones depend on, so script classes and atlas textures in other bundles resolve, and the
export covers the units of those bundles that the selected ones reference.

Tasks run in spawned worker processes that stay alive between tasks. The tasks of a stage that read the same
loaded data (a stage's batch, such as one bundle) form one job: one worker runs them one after another and loads the
data once. Every other task is a job alone. No more workers start than a stage has jobs to run. The longest jobs (by
estimated CPU time, the sum over their tasks) start first; a job starts only while the estimated peak memory of the
running jobs and its own (the largest of its tasks') fits the budget, and a job estimated above the budget runs
alone. A worker is replaced after a job once it has run 500 tasks, or when its resident memory at the end of a job
is above 3 GiB, or above 40 % of its part of the memory budget (the budget divided by the workers) when that is
less, but not below 256 MiB: a worker keeps much of its largest task's memory, which the budget does not count
(`--explain` prints the threshold). The estimates come
from each stage's cost model, scaled per stage by the costs measured in earlier runs of the depot
(`<depot>/costs/calibration.json`, updated after every export; a task's measured peak counts from its worker's
memory at its start). None of this changes an output: a run with one worker and a run with many write the same depot
and the same files.

A failed object is reported with its task's result and stays failed until something in its key changes. A task that
fails as a whole (an exception, an input that cannot be read, a worker that dies) leaves no result and runs again
next time; when a worker dies, the tasks of its job after the failed one run in another worker.

### depot plan

What `export assets` with the same flags would do, without running or fetching anything: every task with its status
(`hit`: its result is stored, `run`, `unknown`: it reads the result of a task that would run first, or of a bundle
not fetched yet), the reasons it runs (compared with the latest run of the same selection, or `--since RUN`), the
estimated CPU time and peak memory, and with `-o` the files each layout would write, remove and keep. The text
gives per stage its counts and its first 20 tasks that are not hits; `--json` has every task.

| Flag | Meaning |
|---|---|
| `--json` | the plan document (`nnnotes.plan/1`) |
| `--check` | exit 1 when anything would run or a layout file would change, else 0 |
| `--census` | first fetch the bundles without a census and take it, so the plan knows every bundle's classes |
| `--emit-tasks DIR` | write the tasks that can run now as task descriptions `DIR/<key>.json`; retain their layout and stored input dependencies in the depot |
| `--why TASK` | one task: its status, reasons, and its key parts beside those of the previous run |

Reasons: `new-input`, `input-changed`, `stage-version`, `atom-changed`, `params-changed`, `context-changed`,
`previous-failed`, `result-missing`, `forced`; hits are `unchanged` or `cached` (the result is in the depot from
another run); unknown tasks are `pending` on a task, or on `fetch:<file>` for a bundle whose content is not known
yet. See [contracts.md](contracts.md#plan-nnnotesplan1).

Census planning writes a run record for its completed tasks. Emitted descriptions have a saved task layout whose
input references keep stored inputs reachable during collection. Interrupted pipeline runs also retain completed
task results in their run records.

### depot run

Runs task descriptions (`nnnotes.task/1`, as `plan --emit-tasks` writes them) into a depot: each is checked (its key
is recomputed, its stage must be installed at its version), its inputs are read by content id through their
locators (the depot, its downloads, a file, or with `--fetch` the catalog), and its result is stored exactly as
`export assets` would store it. Several tasks run one after another in one process; stages that do not read bundles never load
UnityPy. `--force` runs tasks whose result is stored again (the new result must be identical).

Each attempted task has a run record, selected by `depot.run` and its task id. The JSON report's `runs` array names
these records. Collection applies `--keep` separately to the recorded versions of each task.

### catalog versions, import, fetch, diff

The catalog versions of a depot: `import` a catalog file (with the APK's catalog from `[paths] apk` or
`--apk-catalog`), `fetch` the current catalog of the configured region and language, `list` them, and `diff` two of
them (labels, version ids or sha256 prefixes): keys added, removed and changed, bundles and raw files by stable name,
the keys that reach a changed file, and, when a run over each version has stored its address table, the objects by
stable address (an object keeps its stable address while its object id changes with its bundle's content). A version
is labelled by `--label`, else by its resource version, else by the first 12 hex digits of its sha256. `fetch`
records the resource version it downloads (none for `main`); `import` takes `--resource-version`, else the JP
catalog's source version, else the version in a file name `catalog_<version>_<language>.bin` as `fetch` and the
downloads name them (none for `main`). `export assets` imports the current catalog when it is not imported yet.

### depot verify

Checks that every stored content has the sha256 its name says (`--quick`: sizes only) and that every result names
its own key, has the key of its parts and names stored contents of the right size. Exit 1 when a problem is found. `--workers` hashes in
parallel (default: the CPUs the process may use; `0`: one at a time).

### depot gc

Removes what no kept run reaches. The kept runs are, for each selection, the `--keep N` latest (default: every run;
the latest run of a selection is always kept). A kept run reaches the results of its tasks, the contents those
results name (their artifacts, and the inputs their task keys name when the depot holds them) and its layout
manifests with the files they list. Results and contents younger than `--grace SECONDS` (default 86400) also stay;
a recent result keeps its older inputs and artifacts. Other runs, results (with their measured costs) and contents are removed.
`--dry-run` counts what would be removed. A layout made of hard links or clones keeps its files. `--remote`
collects the depot's remote the same way, by the runs on the remote and the same grace period. Remote collection
does not populate the local cache. gc runs only when
no other command uses the depot (it exits with an error otherwise), and commands that start meanwhile wait for it.
The downloads are bounded separately, by `[depot] downloads` ([commands.md](commands.md#downloads)).

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
- secondary artifacts beside the primary one: `<path>.<role>.<ext>` (`….blob.m_Data.moc3`), `<path>.<ext>` for a
  role that is its extension (`….png.json`, the `json` of a texture or sprite);
- a container path present in two bundles: `stem@<stable bundle name>.ext` for each;
- names Windows refuses, reserved device names and trailing dots are escaped as `%XX`; paths that coincide ignoring
  case get `~<8 hex>` and are listed in the layout report;
- the outputs of the CRI stages at the catalog names of their content: `Cri/Sound/<sheet>/<stream>.flac` with
  `cues.json` and `streams.json`, `Cri/Video/<name>/…`; a content several names refer to is placed under each
  name and listed in the layout report;
- the views: `views/<view>.json`, each object entry with the `path` of its file in this layout (`views/voices.json`:
  each stream of a voice).

How the files are made (`--link`, or `[export] link`). Every method writes the same files and the same layout
manifest; the run manifest and the printed summary name the method and why it was chosen.

| `--link` | the files | extra space | an edit of a file |
|---|---|---|---|
| `clone` | clones (reflinks) of the depot objects: Linux FICLONE (Btrfs, XFS, bcachefs, …), macOS APFS `clonefile` | none until a file is written (a quota that counts each file's blocks, such as an XFS project quota, counts them in full) | changes that file only |
| `hard` | hard links to the depot objects (same file system) | none | fails, except for the superuser: the depot objects are read-only (on Windows a delete fails too) |
| `copy` | copies | the whole layout again | changes that file only |
| `auto` (default) | `clone` where the file system makes clones, else `hard` on the same file system, else `copy` | | |

`auto` tries each method once, on a temporary file, before the run starts. An explicit `clone` or `hard` that does
not work there is a usage error (exit 2) before anything runs; a file that fails with the chosen method is reported
(summary, layout report, exit 1), left out of the manifest and placed by the next run: no file falls back to
another method. The depot writes its objects read-only; a layout clears the attribute of a file it owns before it
replaces or removes it where the system needs that (Windows), and the depot object stays read-only.

`cas` is `assets/<sha256>.<ext>` per distinct content, plus the manifest. Each layout follows from the other: `cas`
from the `original` manifest alone, `original` from `cas` and the results. The views are the exception: `original`
has them with the paths of their objects, `cas` the view documents they are made from.

`product` places the products of the selection ([products](products.md)): every product stage makes its products
of the catalog keys the selected bundles hold (the Live2D stages: each model key), and the layout writes each
product's manifest (`models/<name>.json`, `cubism/<name>.json`) with its files and the files of its objects at
`assets/<sha256>.<ext>`. The product stages run only with this layout. Without `original` or `cas`, the run exports
only the objects the products reach, not every object of the selection.

## Reports

`OUT/_reports/`:

| File | Content |
|---|---|
| `run.json` | the run manifest (`nnnotes.run/1`): context, selection, parameters, stage versions, every task with its key and status, the layout manifests, a summary; also stored as `<depot>/runs/<id>.json` |
| `coverage.json` | per class the census total and the count of every item status (`exported`, `contained`, `generic`, `unsupported`, `failed`, and `missing`: objects no task covered; an object two tasks cover, such as a sprite whose image `sprite.crop` makes, counts once); per reason code its count and five examples |
| `failures.json` | failed tasks and failed objects, with their codes and messages |
| `sources.json` | files of the selection that could not be read: `source.absent` (a file the catalog names that is not there, or an APK file without `[paths] apk`), `source.error` (the download failed) |
| `layout-<name>.json` | the paths renamed for collisions, the container paths present in several bundles, the contents placed under several names |

The summary printed at the end names the run, the task and item counts, the layouts' write / remove / keep counts
and the reports directory.

## Exit codes

| Command | 0 | 1 | 2 | 3 | 4 |
|---|---|---|---|---|---|
| `export assets` | complete (unsupported objects allowed) | a task, an object or a download failed (reports written); with `--strict` also view gaps | usage or settings | aborted: a setting or tool a task needs is missing, or interrupted; results written so far stay valid | |
| `depot plan` | | with `--check`: something would run or change | usage or settings | | |
| `depot run` | every task ran or hit | a task failed or its result is partial | usage or settings | | a task is incompatible (edited, unknown stage or version); nothing of the batch runs |
| `depot verify` | no problem | problems found | usage or settings | | |

## Running tasks elsewhere

Task descriptions carry everything a task needs; any machine with the same nnnotes version and access to the depot
can run them. A task can only be described once the content of its inputs is known, so the bundles are identified
first (`plan --census` fetches and hashes them and takes their census). Then plan the tasks that can run now, run
them in batches on as many workers as you like, and plan again until nothing is left; `export assets` finally writes the
layouts and reports (every task is a hit by then).

```sh
depot=/data/nnnotes-depot
nnnotes depot plan --depot "$depot" --census > /dev/null
for round in 1 2 3 4 5 6 7 8; do
    rm -rf tasks
    nnnotes depot plan --depot "$depot" --emit-tasks tasks > /dev/null
    ls tasks/*.json > /dev/null 2>&1 || break           # nothing left to run
    ls tasks/*.json | xargs -n 50 -P 16 nnnotes depot run --depot "$depot" --fetch
done
nnnotes export assets --depot "$depot" -o out/assets
```

Each round runs the tasks whose inputs are known: the catalog index and the censuses, then the script and address
tables, then the bundle exports, then the stages that read their results. A task whose result is already stored is
a hit, so a batch can be run twice; a task that keeps failing is planned again in every round (the loop above stops
after eight) and reported by `export assets`.
