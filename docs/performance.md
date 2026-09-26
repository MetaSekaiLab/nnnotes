# Performance

Where a full `web` build spends its time, which of that work already runs in compiled code, and what is left in
Python. The numbers below were measured with the version of nnnotes this file ships with. They describe that
version, on the machine and data listed; other versions, machines and data give other numbers. Times are rounded,
and shares are of the build's whole CPU time (all processes, including the external programs).

## Conditions

| | |
|---|---|
| Data | one region's full chart set: the Taiwan server, version 1.0.1 (zh-Hant), 336 charts of 84 musics, with the masterdata embedded in the APK |
| Command | `nnnotes web <site> --all --player <ournotes-player> --workers 24` into a new site directory and a new temporary directory (no build cache from an earlier run); read sets at the default of 16 at a time |
| Bundle cache | every bundle already downloaded and decrypted (a first build also downloads; that part depends on the network and is not included) |
| Software | Python 3.13.15 (and 3.11.2 for the comparison below); UnityPy 1.25.3, Pillow 12.3.0, numpy 2.4.6; Node.js 22.23.3 with ournotes-player; FFmpeg 5.1.9; vgmstream r2117 |
| Machine | Linux (Debian 12), 64 logical CPUs (AMD EPYC 9K65), 128 GiB memory |

## Wall time and CPU time

| | Python 3.13 |
|---|---|
| Wall time | 97 s |
| CPU time of all processes | 1 900 s: on average 20 of the 64 CPUs busy |
| Resident memory of all processes, summed, at the peak | 31 GB |

The first 14 s run in one process: reading the catalog, the masterdata and the player settings, and exporting the
note assets every chart shares (their PNG encoding runs in Pillow's C code). The rest of the build runs in parallel:
24 extraction processes, each working on one music at a time, and the read sets of the charts in Node.js. The
build is not bound by CPU on this machine; its wall time follows from that serial start and from how the musics pass
through extraction and read sets.

## Where the CPU time goes

The build was measured once more with every Python process sampled (py-spy with native stacks, 100 Hz) and with the
CPU time of every external program taken from its resource usage. The sampled build took 134 s and 1 790 CPU-s, and
wrote the same files, byte for byte, as the unsampled one; the shares below are of its CPU time.

| Part | Share of the CPU time |
|---|---|
| **External programs** | **52 %** |
| Node.js: the player's full simulation of 27 charts (the check of the read sets: the build's first chart and 1 in 10 others) | 21 % |
| Node.js: the player's read-set plans for all 336 charts, in 16 long-lived processes | 14 % |
| FFmpeg: audio transcoding | 16 % |
| vgmstream: CRI HCA decoding | 1 % |
| **Compiled code called from the Python processes** | **32 %** |
| JSON decoding and encoding (the C part of the standard library's `json`) | 14 % |
| UTF-8 decoding and encoding of text (CPython's C codecs) | 8 % |
| File-system calls: reading, writing, linking and deleting files | 4 % |
| SHA-256 (OpenSSL, through `hashlib`): content addresses and cache keys | 4 % |
| PNG encoding (Pillow's C encoder with zlib) | 1 % |
| UnityPy's C typetree reader, lz4 decompression of bundle blocks, ASTC texture decoding, numpy | under 1 % together |
| **Python bytecode** | **16 %** |
| nnnotes | 7 %, of which 3 % in the walk that writes infinite values as `1e999` |
| CPython's garbage collector | 6 % |
| `pathlib` and other standard-library modules | 2 % |
| UnityPy | under 1 % |
| attrs: building UnityPy's class definitions when a process starts | under 1 % |

Sampling does not tell every waiting thread from a running one: a thread waiting for the interpreter lock is seen
inside a call. Samples of threads waiting for the interpreter lock, other locks, pipes and child processes were
removed, and the remaining samples of each process were scaled to that process's CPU time. The main process, whose
threads wait most, has the largest correction, so the shares carry an uncertainty of a few percent: the Python
bytecode share lies between about 15 and 19 %.

## What already runs in compiled code

- **Bundles.** lz4 decompresses the blocks of the bundles (through UnityPy); when bundles are first downloaded,
  pycryptodome's AES decrypts them.
- **Objects.** UnityPy's C extension reads serialized objects along their typetrees into Python values.
- **Textures.** astc-encoder decodes the game's ASTC textures (through UnityPy); Pillow's C encoder with zlib writes
  the PNG files.
- **Audio.** vgmstream decodes CRI HCA; FFmpeg transcodes to the site's formats (and, for stories, muxes the videos
  into WebM with Opus audio).
- **JSON and text.** The standard library's C codec parses and writes JSON (on Python 3.13 also indented JSON);
  CPython's C codecs decode and encode UTF-8.
- **Hashing.** SHA-256 runs in OpenSSL through `hashlib`.
- **Read sets.** The player's own code runs in Node.js, compiled by V8.
- **Masterdata.** Decoding (`master decode`, not part of this build) runs the Rijndael-256 rounds on whole arrays of
  blocks in numpy and decompresses the gzip data with zlib.

## What stays in Python

The Python bytecode share, about 16 %, is the code that walks and builds objects: nnnotes' exporters and site
builder, UnityPy's object model, `pathlib`, and CPython's garbage collector. The largest single function, the walk
over documents with infinite values, takes about 3 % of the CPU time; every other function takes under 2 %.

A compiled implementation of all of that could save at most about 16 % of the CPU time, and only if it took no time
at all. The external programs and the compiled code already called from Python (84 % together) would stay. The wall
time would fall by less than the CPU time: the build keeps on average a third of the machine's CPUs busy, and its
wall time is set by the serial start and by the pipeline of musics and read sets.

## UnityPy

nnnotes reads the game files with UnityPy because of what UnityPy covers:

- It reads Unity's serialized files and bundles across Unity's versions (the game uses Unity 6000.3), with or
  without typetrees embedded in the files: for a file without them it takes the typetree of each engine class from
  the class database it ships with, which covers 415 engine classes over 1 420 Unity releases from 3.4 to 6000.
  nnnotes ships typetrees of its own only for the MonoBehaviours of the APK's boot data, where the game's build
  strips the script typetrees.
- It resolves references between objects (PPtrs) across the files and bundles loaded together, which is how nnnotes
  follows a prefab to its components, meshes, materials, textures and shaders in other bundles.
- It decodes the texture formats Unity uses through compiled decoders (astc-encoder, texture2ddecoder) and
  decompresses bundles with lz4, LZMA and Brotli.
- Its costly steps run in C: in this build UnityPy's Python code took under 1 % of the CPU time, and UnityPy with
  the compiled code it calls about 3 %.

## Python 3.13 and 3.11

The same build on both versions, with the same library versions; both wrote the same files, byte for byte.

| | Python 3.13.15 | Python 3.11.2 |
|---|---|---|
| Wall time | 97 s | 109 s |
| CPU time of all processes | 1 900 s | 2 290 s |
| CPU time of the Python processes | 870 s | 1 210 s |

The Python processes use 28 % less CPU time on 3.13. One reason is JSON: nnnotes writes indented JSON, and 3.13's
`json` encodes indented output in C, while 3.11 uses its C encoder only for output without indentation and encodes
indented output in Python.

## `OPENBLAS_NUM_THREADS`

nnnotes sets `OPENBLAS_NUM_THREADS=1` when it starts, unless the environment sets it: numpy's OpenBLAS otherwise
starts one busy thread per CPU in every process, and no command uses its parallelism. With 64 threads (one per CPU,
OpenBLAS's default on this machine) instead of one, the outputs are the same and:

| | 1 thread | 64 threads |
|---|---|---|
| `web --all`, CPU time | 1 900 s | 2 010 s (+6 %) |
| `web --all`, wall time | 97 s | 101 s |
| `live 100001 --difficulty expert`, CPU time | 27 s | 34 s (+27 %) |
| `live 100001 --difficulty expert`, wall time | 24 s | 24 s |

## Reproducing the measurement

1. Fill the bundle cache first (a first `web` build does it), so that the measured build does not download.
2. Time a build into new directories, for example with `/usr/bin/time -v`, which reports the wall time and the CPU
   time of all waited-for descendants:

   ```bash
   /usr/bin/time -v nnnotes web out/site --all --player <ournotes-player> --workers 24 --tmp out/site.tmp
   ```

3. For the CPU time of each external program, collect the resource usage of every child process as it is reaped
   (`os.wait4`), for example from a `sitecustomize.py` on `PYTHONPATH` that wraps `subprocess.Popen`'s wait, so that
   the worker processes load it too.
4. For the Python processes, attach one sampler to the main process and to every worker as it starts:
   `py-spy record --native --format raw --rate 100 --pid <pid> -o <pid>.txt`. With an interpreter whose runtime is
   linked into `bin/python3.x` (python-build-standalone), py-spy prints the runtime's frames as addresses; resolve
   them with `nm -n` of that binary. Drop the samples of waiting threads (interpreter lock, locks, pipes, child
   processes) and scale each process's samples to its CPU time from `/proc/<pid>/stat`.
5. Compare the files of the sampled and the unsampled build (SHA-256 of every file); they are the same.

## Asset export

`nnnotes export` of a whole catalog ([assets.md](assets.md)), measured the same way (wall time and CPU time of all
processes; resident memory sampled every second over the process tree).

| | |
|---|---|
| Data | the Taiwan server's zh-Hant catalog of version 1.0.1 with the APK's catalog: 14 694 bundles (14 692 present; two APK entries have no file), 1 805 921 objects |
| Command | `nnnotes export -o <out> --store <new store> --link hard` with the default pipeline (catalog index, census, script and address tables, bundle export, atlas sprites, artifact table, 27 views of the embedded master data) |
| Bundle cache | every bundle already downloaded and decrypted |
| Software | Python 3.13.15; UnityPy 1.25.3, Pillow 12.3.0, numpy 2.4.6 |
| Machine | as above; the runs were pinned to 32 of the 64 CPUs while other jobs used the rest |

| | 32 workers | 12 workers |
|---|---|---|
| Wall time, new store | 166 s | 286 s |
| CPU time of all processes | 2 700 s | 2 450 s |
| Resident memory of all processes, summed, at the peak | 12.9 GB | 5.7 GB |
| Largest process (the main process: catalog index, results, layout) | 4.4 GB | 4.4 GB |

Both runs, and a third made of `plan --emit-tasks` rounds and `run-stage` batches (12 processes of 50 tasks at a
time, 402 s wall, 3 280 s CPU: every `run-stage` process loads its libraries, every round plans again), wrote the
same store (84 473 contents, 11.2 GB; 29 445 results) and the same layout (79 655 files).

Where the CPU time of the 32-worker run goes:

| Stage | Tasks | CPU time | Largest task |
|---|---|---|---|
| `unity.export` | 14 692 | 2 460 s | 8.9 s, 0.74 GB resident |
| `unity.census` | 14 692 | 120 s | 1.3 s |
| `link.addresses`, `link.artifacts`, `link.scripts` | 3 | 14 s | `link.artifacts`: 2.2 GB resident |
| `sprite.crop` | 30 | 4 s | |
| the 27 views | 27 | under 1 s | |
| outside the tasks (planning, scheduling, layout, reports) | | 107 s (4 %) | |

The median worker process peaked at 0.38 GB resident. A second run over the same store is a no-op: `plan -o <out>
--check` takes 24 s (1.9 GB) and `export` 43 s (4.3 GB), each in one process (every task is a hit, so no worker
starts).

### PNG level

PNG encoding is more than half of the CPU time of `unity.export` at the default zlib level 6 (the texture images
alone about 40 %). `--png-level` trades file size for CPU time; every level writes the same pixels:

| `--png-level` | `unity.export` CPU time | PNG bytes of the catalog |
|---|---|---|
| 6 (default) | 2 690 s | 7.57 GB |
| 3 | −41 % | +12.1 % (8.49 GB) |
| 1 | −50 % | +19.6 % (9.05 GB) |

Measured on the same catalog with 16 workers (level 6: 171 s wall, level 1: 97 s). The default stays 6: the store
keeps each content once and for good, so the extra bytes of a lower level are kept by every store and copy, while
the CPU time is spent once per content; and level 6 writes the same bytes as the textures of the other commands.
The level is a task parameter, so changing it runs the bundles with textures and sprites again.
