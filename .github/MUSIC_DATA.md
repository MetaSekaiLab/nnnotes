# Music data workflow (StarMoe)

`.github/workflows/music-data.yml` keeps the music data file of the chart data page (ournotes-player
`examples/songs`) up to date: `nnnotes music-data` of the current master data ([docs/music-data.md](../docs/music-data.md):
every song and chart with the deck model's statistics, the play scenarios and the chart's Gekisou skill aptitude),
checked by quality gates and
published into the story site's bucket under `music-data/` (`https://storage.bdon.moe/moenotes/music-data/`):

| Object | Content | Cache-Control |
|---|---|---|
| `music-data.json` | the current file | `no-cache` |
| `jackets/<jacket>.webp` | every song's jacket (`--jackets`), where the page looks for them | `public, max-age=86400` |
| `archive/<master version>/<sha256>.json` | every published file, kept | `public, max-age=31536000, immutable` |
| `build.json` | the build marker: the file's SHA-256, size and counts, what it was made from, the gate results, the run | `no-cache` |

A run never deletes anything from the bucket. Its helper steps are `.github/scripts/music_data.py` (with the bucket,
HTTP and master data helpers of `story_site.py`), `music_data_smoke.mjs`, `songs_page.sh` and `apk.sh`; the gate
self-test is `test_music_data.py`. Nothing outside `.github/` differs from upstream, so the fork syncs with it as
before. The Cloudflare Pages preview of the page is not part of the workflow.

## A run

1. **plan** (seconds): the inputs of a build, from `index.json` of moenotes-masterdata-sync (the snapshot's master
   data version, resource version and client version of `MUSIC_DATA_MASTERDATA_REGION`) and the checkout (the
   ournotes-deck commit `rust/Cargo.lock` pins, the last nnnotes commit that changed `src/`, `rust/` or
   `pyproject.toml`, and `RECIPE` of `music_data.py`), against `inputs` of the published `build.json`. The same
   inputs (and a published `music-data.json`): the run ends here. `force` builds anyway.
2. **build**:
   - the chart data page's modules (`examples/songs` of `MUSIC_DATA_PLAYER_REF`, not built), nnnotes with its deck
     model (the install builds `nnnotes._deck`), the gate self-test, the APK (playfetch with `PLAYFETCH_CREDENTIALS`:
     `provenance.client`; nnnotes also reads the bundles the APK carries, as in the story site's builds), the decoded
     master data of moenotes-masterdata-sync (every file SHA-256 checked against `index.json`, `MasterManifest.json`
     included);
   - `nnnotes music-data --decoded-master --jackets jackets -o music-data.json`: the master data as decoded (no
     master key), `provenance.master` the manifest's version and SHA-256 of the files as served; the charts, cue
     sheets and jackets from the TW catalog, downloaded afresh on every run (never `actions/cache`: nnnotes keeps a
     downloaded catalog for good, and the cache holds decrypted game files);
   - the gates (below); a failed gate stops the run, the job summary lists why;
   - upload: the jackets the bucket lacks (or has at another size; every one with `force`), the archive copy, then
     `music-data.json`, `build.json` last. The archive copy, the file and the marker are each read back and checked
     against their SHA-256 before the next is written: a failure leaves the previous `build.json`, so the next run
     builds again.

Triggers: `repository_dispatch` `masterdata-updated` (moenotes-masterdata-sync's `dispatch_repositories` already
names this repository for the story site: both workflows run), a daily schedule (03:41 UTC) in case a dispatch was
missed, and `workflow_dispatch`:

| Input | Meaning |
|---|---|
| `force` | build and publish although the published file was made from the same inputs; upload every jacket again |
| `dry_run` | build and check, then list what would be uploaded instead of uploading |

Runs do not overlap (`concurrency: music-data`).

**Publishing switch.** Nothing is uploaded unless the repository variable `MUSIC_DATA_PUBLISH` is `true` (unset:
off). Off, every run, whatever its trigger (the schedule, `masterdata-updated`, `workflow_dispatch` with or without
`dry_run`), is a dry run: it builds, runs every gate and the smoke test, and lists what it would upload; the publish
step then gets no bucket key (anonymous, it can only read) and `music_data.py publish` itself refuses to upload.
Turn it on (`gh variable set MUSIC_DATA_PUBLISH --body true`) once the published format is final; while it is off,
nothing being published, `plan` finds no `build.json` and every run builds.

## Gates

Every one must pass, else nothing is published. Warnings go to the job summary and `build.json` and do not stop it.

| Gate | Checks |
|---|---|
| (build) | nnnotes' own checks: every table, chart and cue sheet read, every chart measured, the deck statistics cross-checked against the chart facts and the master data (the command writes no file otherwise) |
| `schema` | the file against `docs/schema/music-data.schema.json` of the checkout (JSON Schema 2020-12) |
| `provenance` | `format`; `region` `tw`; `master.source` `api`; `master.version` equal to the snapshot's and its `MasterManifest.json`'s; every table's SHA-256 the manifest's, every decoded table read the one `index.json` lists; the song tables and the deck model's present; `deck.commit` the one `rust/Cargo.lock` pins; `exporter.version` the installed nnnotes; an APK version; a catalog SHA-256 (warning: the APK is another client version than the snapshot's) |
| `counts` | no fewer songs and charts than the published file (warning: ids no longer in it) |
| `deck` | deck statistics on every chart: kinds, a positive power, events and positions matching the chart, seeds unless unplayable (a warning): the one seed 0 on a chart without a luck range, else two or more different seeds, the same on every luck chart (their number is the file's, not fixed), `weights[kind][position]` numbers, every seed range's `rankBonus` = trunc(`rangeScore` x `rankBonusPercent` / 100) and its `luckPoints` an int, every check deck within its bound |
| `scenarios` | the play scenario fields: `offSeeds` exactly one entry (seed 0, score, weights, check within its bound), every range's `rankBonusPercents` five ints (the first `rankBonusPercent`), every seed's `scorePerfect`, `rangeWeights` (`[kind][position][range]`) and `rankCheck` (within its bound), every seed range's `rangeScorePerfect` (warnings, none in TW: a null `rangeWeights`, a null kind in it or in `offSeeds`' weights) |
| `aptitude` | the Gekisou skill aptitude (every shape alone on a chart). `deck.model.gekisouAptitude` a text; `deck.gekisouAptitude`: every key, `plainKind` the page's plain kind, a `host` text, the `seedRule` (a deterministic test, increasing batches, the targets, the cross seeds), `shapes` numbered 0, 1, 2, ... (source `member` or `support`, mission 1 to 4, `bandCondition` a support skill's alone and exactly when an effect has condition 5000, effect rows with every key and their condition groups, condition 5000 without targets, skills with a level and, with a band condition alone, member targets and bands). Every chart's `deck.gekisouAptitude`: null exactly when the chart is unplayable with Gekisou on, has no Gekisou range or there is no shape; else `factors` one per range (counts; no Just or Perfect notes outside a Just range; `lotteries` `[0, 0]` outside a luck range, else the mean of `deck.seeds`' `lotResults`) and `variants` one per shape of the chart's missions (or mission 4) in shape order, a band condition shape's `bandMatch` true then false: every `[mean, se]` two finite numbers with se >= 0 (every se 0 when deterministic), ranges one per range, `tail` = `score` less the ranges' `rangeScore` and `rankBonus` (allowing 0.0005 per rounded term plus 1e-6), deterministic point deltas integers and `tailPerfect` checked against the baseline Perfect range bonuses, 1 seed when deterministic else a batch of the seed rule (the last one when `seTargetMet` is false), `crossSeeds` min(seeds, the rule's), `weights` one per position and `rangeWeights` per position and range where the plain kind and `deck.seeds[0].rangeWeights` are, else null, the `check` on `deck.seeds[0]`'s seed, a rank per range (1 where the ranks are not linear), a plain kind value or null per position, within its bound (warning: variants that missed the standard error target) |
| `finite` | no NaN or infinity (warning: one inside master data rows, `songs[].master`, which the format writes as `1e999`) |
| `references` | texts in every language of `languages` (names and titles not empty); unique ids; songs sorted; the songs' bands, vocal characters and tags in the file; a band or a band name; a jacket, and its file in `jackets/`; a BGM cue; score ranks; charts in difficulty order, score ids unique (warnings: a title without a `zh-Hant` text, a music category on no tab, a character of no band) |
| `bgm` | every song's BGM length: `durationMs = samples * 1000 // sampleRate`, 30 s to 10 min, within 1 s of the cue's `lengthMs`, not ending before a chart's last note (warning: more than a minute after it) |
| `size` | 0.8 to 2 times the published file |
| `gzip` | the file gzipped at most 2 MB (0.37 MB before the aptitude), its Gekisou skill aptitude gzipped at most 1.2 MB (about 0.4 MB expected) |
| `page` | `music_data_smoke.mjs`: the page's `catalog.js` and `ranking.js` in Node.js over the file: a row per chart, a plain score-up kind, data for the free, rank and Just scenarios, finite positive figures for every chart the data covers in seven scenarios (Gekisou Live at several ranks, Just rates and a Great share, Free Live), the ranking, frontier and event figures |
| (publish) | read back after upload, SHA-256 checked |

`counts` and `size` compare with the published `music-data.json` and are skipped while nothing is published. A
legitimate drop (a song the game removed) stops the run: a person checks it, then moves the published
`music-data.json` away (the archive keeps it) or changes the gate in a pull request.

The self-test runs in each build and locally in seconds, without the network:

```
python -m pytest -q -p no:cacheprovider .github/scripts/test_music_data.py
```

with, optionally, `MUSIC_DATA_SCHEMA` (a schema file when the checkout has none), `MUSIC_DATA_PAGE` (an
`examples/songs` directory: the smoke test), `MUSIC_DATA_SAMPLE` (a real file with the play scenario fields: its
content gates pass; one made before the ranges' `luckPoints` and the aptitude: the deck and aptitude gates stop it
on those alone) and
`MUSIC_DATA_OLD_SAMPLE` (one without the play scenario fields: the scenario gate stops it).

### Aptitude page smoke

With the aptitude API (ournotes-player PR #11, `1522c24`), the same Node smoke also checks shape/skill/band
lookups, chart variants, all five battle scenarios, Free Live exclusion, finite gains, raw standard errors,
missing cross terms and the absence of standard errors for transformed or combined figures. Removing aptitude
must not change the default chart figures: default ranking still has no card Gekisou skills.

Only deterministic variants are reconstructed against their individual `check` seed, using positional cards and
`masterSkillFactor` for the game's float32 conversion. Stochastic means are never used to reconstruct a check.
Older pinned page modules explicitly report `API unavailable (skipped)`; moving `MUSIC_DATA_PLAYER_REF` remains a
separate rollout decision. No browser or page build is needed.

## Settings

Repository secrets: the story site's (`.github/STORY_SITE.md`), no new one: `NNNOTES_BUNDLE_KEY`,
`NNNOTES_BUNDLE_NONCE_SEED`, `NNNOTES_SERVERS_TW_CDN`, `PLAYFETCH_CREDENTIALS`, `STORY_S3_ACCESS_KEY`,
`STORY_S3_SECRET_KEY`. The master key is not needed: the master data comes decoded.

Repository variables:

| Variable | Default | |
|---|---|---|
| `MUSIC_DATA_PLAYER_REF` | none: **required** | the ournotes-player commit whose chart data page reads this file (the page with the play scenarios); a run stops before building without it |
| `MUSIC_DATA_PUBLISH` | none: off | `true`: upload; anything else: every run is a dry run |
| `MUSIC_DATA_PLAYER_REPOSITORY` | `empty-sekai/ournotes-player` | |
| `MUSIC_DATA_S3_PREFIX` | `music-data` | the key prefix in the bucket |
| `MUSIC_DATA_MASTERDATA_REGION` | `hk-tw-mo` | the region of `index.json`; the build reads the TW catalog (`[catalog] region` `tw`) |
| `STORY_S3_ENDPOINT`, `STORY_S3_BUCKET`, `MASTERDATA_BASE_URL`, `PLAYFETCH_VERSION`, `STORY_APK_PACKAGE` | the story site's | shared with it |

## Before the first run

- **nnnotes.** The workflow runs this fork's nnnotes. It needs upstream's `music-data` command with the play
  scenarios (MetaSekaiLab/nnnotes `a03591e`) and `--decoded-master` (MetaSekaiLab/nnnotes#6, `12df2a6`): sync the
  fork with upstream first. Until then `plan` stops naming what is missing. The deck and aptitude gates also need
  the ranges' `luckPoints` and the Gekisou skill aptitude, which come with nnnotes' and ournotes-deck's Gekisou skill
  changes: until the fork has them every build stops there.
- **The page.** Set `MUSIC_DATA_PLAYER_REF` to the ournotes-player commit of the chart data page that reads the play
  scenario fields, once that page is merged.
- **Publishing.** Set `MUSIC_DATA_PUBLISH` to `true` last, when dry runs pass and the published format is final.

## Notes

- **Versions.** A new ournotes-deck pin (`rust/Cargo.toml`, `rust/Cargo.lock`) or a new nnnotes commit in `src/`,
  `rust/` or `pyproject.toml` reaches this fork with a sync, and the next run builds a new file. The deck
  statistics may then differ: `provenance.deck.commit` and `build.json` name the commit.
- **The page and the data.** The page's modules are pinned by `MUSIC_DATA_PLAYER_REF`: after a page release that
  reads new fields, move it (a new ref alone does not start a build; run with `force` to check the published data
  against the new page).
- **Byte identity.** A build from the same inputs gives the same bytes (the file is canonical); the jackets'
  WebP bytes depend on the Pillow version.
- **Logs.** The steps print counts, ids, SHA-256 and field names, not game content; nothing decrypted is cached or
  uploaded as an artifact.
