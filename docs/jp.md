# Japanese release

JP uses the same catalog, bundle and master parsers, with a separate version discovery and CDN download path.
The anonymous `MasterdataService/Version` response provides the master version, Android asset version/hash and
CDN authentication. No player account or login is needed.

## Configuration

Use your own client addresses and encryption settings. As elsewhere in nnnotes, neither addresses nor keys are
built into the package. Add a region table with these settings to your private configuration:

```toml
[catalog]
region = "jp"
language = "ja"

[servers.jp]
provider = "jp"
name = "JP"
api = "https://YOUR_API_HOST"
cdn = "https://YOUR_CDN_HOST"
client_version = "YOUR_CURRENT_CLIENT_VERSION"
languages = ["ja"]
apk = "/path/to/jp/base.apk"
master = "/path/to/jp/master"

[paths]
cache = "/path/to/cache"
```

Keep `[bundle] key/nonce_seed` and `[master] key/iv` in your configuration. Master keys are unnecessary when
reading already decoded tables with `music-data --decoded-master`.

`provider` defaults to `jp` for the region named `jp`, and to `international` otherwise. An alias such as
`servers.japan` needs `provider = "jp"`. `client_version` overrides `[client] version`; when neither is set,
nnnotes reads the APK versionName. Use the current accepted JP client version: an old extracted APK can still
contain readable local data even when its version is no longer accepted by the API.

The `cdn` setting is also the allowed CDN origin for authentication. A different origin in Version fails before
any authenticated download. The JP transport uses HTTPS, follows no redirects, keeps authentication in memory,
and refreshes it once on 401/403. A 429 does not cause an authentication-refresh loop. Credentials are not written
to catalog metadata or task files.

## APKs and split data

`apk` accepts:

- a normal APK;
- `base.apk` with adjacent `split_*.apk` / `config.*.apk` files;
- a directory containing `base.apk` and its splits;
- an `.apks` or `.xapk` archive containing `base.apk` and its splits.

JP stores boot data in base.apk and the embedded catalog/master/bundles in the Unity data split. Both are read
through the same APK-set interface. `datapack.unity3d` is loaded alongside `data.unity3d` for external resources
such as materials and shaders. Native libraries are also read from the splits. No repacked APK is required.

`[servers.<region>] apk` and `catalog` override their `[paths]` equivalents; explicit `--apk` / `--catalog` flags
still win. For mixed-release chart builds, configure separate master directories and APK sets. JP stories and
models must use a separate site directory from international releases, since their model IDs may overlap.

## Commands

```sh
nnnotes --region jp master version
nnnotes --region jp master download --latest -o work/jp-master-bin
nnnotes master decode work/jp-master-bin -o work/jp-master
nnnotes --region jp catalog --prefix Live/MusicScore/ --limit 10
nnnotes --region jp pull Live/MusicScore/0001/0001_00
nnnotes --region jp catalogs fetch
nnnotes --region jp export --select key:Image/Jacket/jacket_temporary --views none -o out/jp-sample
nnnotes --region jp music-data --decoded-master --no-bgm -o out/jp-music-data.json
```

For `--decoded-master`, keep `MasterManifest.json` with the decoded tables (copy it from the download directory,
or use a snapshot from your masterdata service). `--no-bgm` omits song audio length inspection; omit that flag to
include it. `master version` reports `resourceHash` for JP as well as the two version fields. `music-data` records
the JP hash in `provenance.catalog.resourceHash`.

JP catalogs are `catalog_main.bin` in a version/hash directory, without a language suffix. The HTTP payload can
be gzip; nnnotes keeps its original bytes and SHA-256, and bounds decompression before parsing. Remote bundle and
CRI locations use `{Fwk.Resource.RemoteAssetDir}`. These are indexed, resolved and downloaded through the selected
snapshot, including in `browse`, `catalogs`, `export`, `plan` and `run-stage`.

The cache lives under `<cache>/jp/<CDN-origin-digest>/<asset-version>/<asset-hash>/`. It cannot reuse the
international catalog cache. Embedded files are further isolated under `apk/<APK-catalog-SHA256>/`, so an APK
update cannot reuse old local bundles when the CDN snapshot stays unchanged. Each cached `catalog_main.bin` has a `catalog_main.bin.source.json` with its SHA-256
and public source metadata. For an offline `--catalog` or `catalogs import`, copy the pair together. Credentials
are reacquired only when a missing file must be downloaded.

Catalog database records include this source metadata, and JP catalog identity includes version/hash even when
the catalog bytes remain the same. Old international records retain their previous IDs. Cached historical files
can be read offline; downloading a missing historical file fails if Version now selects a different snapshot.
Refresh the catalog to use the new snapshot. Historical local bundles require the APK set matching the imported
APK catalog. A JP master download likewise rejects a different current master snapshot.

## Workflow integration

For the StarMoe workflows, set `MUSIC_DATA_MASTERDATA_REGION=jp`; the story site builds JP beside hk-tw-mo (`STORY_REGIONS`,
default `hk-tw-mo jp`, one run per region). This selects the
JP package and Japanese catalog language; the build reads API/CDN/client-version from the JP entry in the public
masterdata index. The default output prefixes become `jp/music-data` and `jp`, with separate concurrency groups.
Custom output prefixes must also be separate from the international outputs. Publishing remains controlled by
the existing workflow switches. No GitHub variable, secret, deployment or bucket is changed by installing nnnotes.

The music build marker includes resource hash, so hash-only updates rebuild. Its provenance gate checks that the
catalog version/hash matches the master snapshot. A mixed master/catalog region is rejected before building.

## Validation and current limits

The synthetic tests use local gRPC and HTTP servers, including gzip catalogs, split packages, authentication
rotation, redirects, 429, truncated responses, source isolation, APK-only and hash-only updates, offline replay
and invalid paths.

Live validation on 2026-09-30 used JP client 1.0.4 and local JP Android 1.0.3 data:

- 238 master tables downloaded with manifest hashes and decoded successfully;
- a 73-note chart and a 512×512 jacket read successfully;
- one Live2D runtime model and one complete story exported; the story's 22 FLAC files passed full FFmpeg decoding;
- selected `export` produced six files without failed tasks;
- `music-data --decoded-master --no-bgm` built 85 songs / 340 charts, including the pinned deck model's
  statistics, with no unplayable chart; output passed JSON Schema validation.

This does not establish full JP story/Live2D/movie export coverage, browser playback, every BGM, or native scoring
parity. JP's text tables retain five language columns, but most translated values are empty; story web builds
default to Japanese. Existing Bili chat skin view rules still refer to international `_iconAssetPath` fields;
JP rows with a different schema report no-value and are not represented as verified skin mappings.
