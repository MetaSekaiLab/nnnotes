# Package a song

`nnnotes pack` builds a song from local chart JSON, a PNG jacket and common audio files. It creates independent
Unity bundles, an updated Addressables catalog and encrypted master tables. The output is an incremental package
for a specific local resource baseline.

## Audio tools

Install [FFmpeg](https://ffmpeg.org/download.html), [VGAudioCli](https://github.com/Thealexbarney/VGAudio) and
[vgmstream](https://vgmstream.org/). FFmpeg decodes audio, VGAudioCli encodes HCA and vgmstream independently
decodes the completed ACB. Configure their locations:

```sh
nnnotes config set paths.ffmpeg /tools/ffmpeg
nnnotes config set paths.ffprobe /tools/ffprobe
nnnotes config set paths.vgaudio /tools/VGAudioCli
nnnotes config set paths.vgmstream /tools/vgmstream-cli
```

Programs on `PATH` are used when these settings are empty. `paths.vgaudio` can also name a VGAudioCli `.dll`,
which requires `dotnet` on `PATH`. Tool binaries are external to the Python distribution. Builds use their content
fingerprints and finish without downloading tools or assets.

## Local target

Create `target.json`; paths are relative to this file:

```json
{
  "format": "nnnotes.pack-target/1",
  "catalog": "baseline/catalog.bin",
  "master": "baseline/master",
  "bundles": "baseline/bundles",
  "bundle_base_url": "https://example.com/asset/Android",
  "audio": {
    "cipher_type": 56,
    "templates": {"full": "templates/full.acb", "preview": "templates/preview.acb"}
  }
}
```

The master directory contains encoded `Master*.bin` files and complete `MasterManifest.json`. The bundle
directory contains the local files named by the catalog. The template song supplies chart, jacket and sound
schemas. Full sound supports split ACB data; preview supports embedded ACB managed references or split data.
Jacket templates contain a Sprite and its local Texture2D; both normal and small jacket keys are required.
Single serialized-file bundles and finite, single-cue, single-waveform ACB templates are supported.

`cipher_type` is `0` for unencrypted HCA or `56` for encryption with the configured APK's HCA key. Cipher 56 uses
`paths.apk`; key material stays outside project files and reports. ACB templates specify the compatible cue graph
and routing. Select templates whose playback settings match the target application.

Bundle transport follows its catalog provider. Unity's standard AssetBundleProvider uses plain UnityFS.
Other providers need an explicit mapping in `target.json`, for example:

```json
{"providers": {"ExampleEncryptedBundleProvider": "encrypted"}}
```

Values are `plain` or `encrypted`. The runtime must implement the provider. HCA encryption and Bundle transport
are independent settings.

## Convert audio to ACB

An audio-only target needs `format` and `audio`; it can omit catalog, master and bundle paths.

```sh
nnnotes pack audio media/song.mp3 --target target.json --template full \
  --cue-name example_full -o audio/full.acb
nnnotes pack audio media/song.flac --target target.json --template preview \
  --cue-name example_short --start-ms 45000 --duration-ms 20000 \
  --fade-in-ms 20 --fade-out-ms 150 -o audio/preview.acb
```

MP3, WAV, FLAC and AAC, including `.aac` and `.m4a`, use the same decoded PCM processing path. Mono/stereo at
44,100 or 48,000 Hz is supported. Other inputs require explicit `--sample-rate` and/or `--channels` conversion.
`--stream` selects an audio stream by its absolute index when several exist.

Gain defaults to 0 dB; `--gain-db` applies a fixed gain once. Start/end silence is preserved. Cropping and fades
operate on decoded samples before HCA encoding. Milliseconds map to samples by rounding half upward. Invalid
crop/fade ranges are rejected. Sample conversion rounds to PCM16 without dither; values outside the PCM range
stop conversion. Encoding quality defaults to `high`. The output is finite and contains its AWB in the ACB.

The command writes `<output>.json` with input properties, processing settings, tool fingerprints and validation.
Checks cover HCA CRCs, encryption round trips, ACB references, sample counts and independently decoded PCM.
Active-channel normalized mean-square error must be at most 0.05 and power change at most 1 dB; silent-channel
error is checked separately. HCA is lossy; decoded PCM is compared to the processed source.

## Song project

Create `song.json`:

```json
{
  "format": "nnnotes.song-package/1",
  "target": "target.json",
  "template_song": 1001,
  "song": {
    "id": 900001,
    "title": "Example Song",
    "performer": "Example Artist",
    "credits": {"composer": "Example Composer", "lyricist": "Example Writer"},
    "tags": ["Example"],
    "bands": []
  },
  "assets": {
    "jacket": "media/jacket.png",
    "audio": {
      "full": {"file": "media/song.flac"},
      "processing": {"gain_db": 0, "sample_rate": 48000},
      "preview": {"from": "full", "start_ms": 45000, "duration_ms": 20000,
                  "fade_in_ms": 20, "fade_out_ms": 150}
    }
  },
  "charts": {
    "easy": {"file": "charts/easy.json", "score_id": 90000100, "level": 5},
    "normal": {"file": "charts/normal.json", "score_id": 90000101, "level": 10},
    "hard": {"file": "charts/hard.json", "score_id": 90000102, "level": 15},
    "expert": {"file": "charts/expert.json", "score_id": 90000103, "level": 20}
  }
}
```

Paths resolve against `song.json`. Charts use client JSON or gzip JSON. Notes and events are preserved, binary
TextAssets are generated, and combo counts come from the existing score converter. Four difficulties are
required; they may explicitly share a file. `display_level` sets a displayed numeric level. `input_format`
can require `json` or `gzip-json`.

The jacket must be a square PNG. Both sizes use the template's dimensions; each Sprite references its own RGBA
texture. `preview.file` accepts a separate common audio file instead of `preview.from`. Audio fields use
`AudioOptions` names: `sample_rate`, `channels`, `stream`, `gain_db`, `start_ms`, `duration_ms`, `fade_in_ms`,
`fade_out_ms`, `quality`. Shared `processing` values apply to both inputs; input values override shared ones.
Both outputs are encoded directly from decoded source PCM.

Text can be a string for all languages or a mapping with `ja`, `en`, `zh-Hant`, `zh-Hans`, `ko`. Missing
translations are empty unless `song.fallback_language` names a provided translation. Optional `phonetic`,
`ruby_title`, `performer` and credits use the same text form. Unspecified credits and performer are empty.
Tags receive independent rows. Band IDs reference baseline bands; omitted bands and tags are empty.

Song/score IDs must be unused. Derived tag, rank, sound and cue-sheet IDs are checked for collisions. Songs are
unlocked by default. Presentation, reward and stage behavior use template defaults. `sort_order` and `start_at`
override their defaults. `song.fields` can override existing non-identity behavior fields. Resource IDs and
display-text references are assigned by the packager. Vocal, video and alternate-vocal references start empty.
The `song-package` and `pack-target` JSON Schemas ship in `nnnotes.schemas`.

## Plan, build and verify

```sh
nnnotes pack plan song.json --json
nnnotes pack build song.json -o build/example
nnnotes pack verify build/example --target target.json --json
```

Planning reports templates/providers, new resource keys, master row IDs and diagnostics. It reads local inputs
and probes tools without writing resource outputs. Missing inputs, conflicting IDs/keys and unsupported schemas
stop building. A Python `PackagePlan` also rejects changed inputs or tool binaries.

Build outputs use a new directory. Required validations finish in staging before publication. Existing output
directories are rejected. The result contains:

| Path | Contents |
| --- | --- |
| `package.json` | Package format, file sizes/SHA-256, Bundle metadata and baseline catalog identity |
| `bundles/` | Eight independent chart, jacket and sound bundles |
| `audio/` | Full/preview ACBs and conversion reports |
| `catalog/catalog.bin`, `catalog/catalog.hash` | Updated catalog and its matching marker |
| `master/` | Changed encoded tables and complete `MasterManifest.json` |
| `validation.json` | Automatic structure and audio verification |

Unchanged master files and existing Bundle dependencies come from the baseline. The output is applied as an
overlay. New Bundle URLs, catalog and master version must be available through the application's resource update
mechanism. Copying a single ACB or Bundle does not register a song.

Build does not upload resources or operate an application. Client playback compatibility is checked for supported
target profiles separately from automatic per-package validation. Repeated builds with the same inputs, target
and tool binaries produce identical files; HCA encoding across tool versions can produce different bytes.

## Python API

```python
from nnnotes.packing import SongProject, build_package, plan_package, verify_package

project = SongProject.load("song.json")
plan = plan_package(project, config=config)
plan.raise_for_errors()
result = build_package(plan, out="build/example")
verification = verify_package(result.root, target="target.json", config=config)
```

`build_audio` is the standalone audio service. `AudioOptions` controls processing and `AudioTools` accepts explicit
tool paths. Keys are separate arguments with no representation in reports.
