# Determinism

An export is a function of its inputs. The same game files, configuration and options, with the same versions of
nnnotes, its Python dependencies and the external tools, give byte-identical output files. This page says what
fixes the bytes, what can change them, and how the tests hold it.

## What fixes the bytes

- **One JSON writer.** Every JSON file goes through `nnnotes.core.jsonio`: UTF-8 without a byte order mark, LF line
  endings, non-ASCII characters as they are, and non-finite numbers as `1e999` / `-1e999`. Those literals are valid
  JSON and read back as infinity by `JSON.parse` and Python's `json.loads`; a NaN is an error that names where it
  occurs. A document's key order is the order it is built in, which is fixed by the code, not by a hash table.
- **canon/1.** The documents of the asset export follow a stricter form, [canon/1](contracts.md#canon1): sorted keys,
  fixed indentation, tagged values for NaN and binary data, and content ids that are the SHA-256 of the bytes. A
  task's key is the SHA-256 of the compact canonical form of everything its output depends on.
- **Fixed order.** Parallel results are collected in a fixed order and file listings are sorted before they are
  written, so `--workers` changes how long a command takes, never its output.
- **Unity values as stored.** Numbers keep the serialized value and field names (`m_LocalPosition`, `_bandIDs`), and
  textures, shaders and audio are converted from the stored data with fixed encoder settings.
- **No clocks.** No output holds the time of a run or a path of the machine it ran on. A result of the asset export
  holds no input name, locator, timestamp or traceback; the measured cost of a task (CPU and wall seconds, memory) is
  kept in the depot's cost report, apart from the results.

## What can change the bytes

Another version of an encoder can write the same content as other bytes:

- Pillow writes the PNG files. Another Pillow version can compress the same pixels differently: the bytes differ
  while the pixels are equal.
- numpy, UnityPy and the decoders of texture formats shape converted meshes and images.
- vgmstream and FFmpeg decode and encode audio and video; FLAC keeps the decoded samples at every compression level,
  while lossy formats depend on the encoder version.
- zlib and brotli encode the compressed assets of a site.

So the guarantee is per installation: pin the versions to reproduce a result exactly. The depot keeps this visible:
a task's key includes the implementation ids of the atoms it uses, with the versions of the libraries they call, so
another installation computes other keys instead of mixing results.

## How it is tested

PNG and glyph task identities include `Pillow.zlib`, the version of Pillow's linked zlib codec and its zlib-ng
backend when present. This distinguishes native encoders even when the Pillow package version is the same.
Golden records that use those codecs also record this native identity with their library versions.

- **Golden records.** `tests/golden/<stage>.json` records, for each stage version, the libraries that shape its
  bytes and, for every synthetic fixture, the task key and the SHA-256 of the canonical result. A change of a
  stage's output without a new stage version fails the test. The CI job that checks the records installs the exact
  library versions of `.github/goldens-constraints.txt` and sets `GOLDENS_STRICT=1`; elsewhere a record made with
  other library versions is skipped. Every registered output stage and site stage has a record; the coverage
  check includes the master-data view family. Product fixtures exercise shared objects, localized references,
  open and game fonts, host state, media indexes and regional site aliases.
- **Two runs, one result.** The tests run exports twice over synthetic inputs and compare the bytes, and compare
  one worker with several: a depot built with four workers equals one built with one, and parallel audio decoding
  equals sequential decoding.
- **Writer tests.** The JSON writer's tests cover the non-finite literals, the NaN error and the line endings.

[Validation](validation.md) describes the checks over a full game version.
