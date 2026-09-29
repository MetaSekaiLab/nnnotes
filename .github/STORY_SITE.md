# Story site workflow (StarMoe)

`.github/workflows/story-site.yml` keeps the StarMoe story site up to date: it builds the stories the published site
lacks with this repository's `nnnotes web --story` and uploads them to the bucket that serves the site
(`https://storage.bdon.moe/moenotes/`, the layout `nnnotes web` writes: `stories.json`, `stories/`, `models/`,
`assets/`, `story/`), one site per game region: `hk-tw-mo` at the bucket root, `jp` under `jp/`
(`https://storage.bdon.moe/moenotes/jp/`; JP Live2D model ids overlap the international ones). It only adds: a run
never deletes anything from the bucket. Its helper steps are in
`.github/scripts/`; nothing outside `.github/` differs from upstream, so the fork syncs with it as before.

## A run

`story-site.yml` picks the regions (`story_site.py regions`: those of `STORY_REGIONS` that the dispatch's
`client_payload.regions` names, the `region` input of a manual run, every one on the schedule) and calls
`story-site-region.yml` once per region, in parallel. Each region's run:

1. **plan** (a few seconds): the MasterAdv ids of the decoded master data of moenotes-masterdata-sync
   (`MasterAdv.json`, SHA-256 checked against its `index.json`) against the `stories/<id>.json` objects of the bucket.
   The ids without a manifest, at most `STORY_LIMIT` (40) in id order, are the run's stories; none: the run ends here.
2. **build** (only when there is something to build):
   - fonts (pinned by SHA-256: the files the published stories record in `ui/fonts.json`), vgmstream, ffmpeg, the
     built ournotes-player (`STORY_PLAYER_REF`), the APK (playfetch with the account in `PLAYFETCH_CREDENTIALS`), the
     decoded master data;
   - every object of the site except `assets/` (the manifests and indexes, about 150 MB), over plain HTTP like the
     player's browser: the bucket serves public read, and Cloudflare's S3-signed ranged downloads were rejected
     intermittently with `SignatureDoesNotMatch`;
   - `nnnotes web site --story <id> ...` (with the Live2D models these stories load that the site lacks), then
     `nnnotes web site --player-only`, which rewrites `stories.json`, `models.json`, `charts.json` and the player
     pages from every manifest present, also after a failed build;
   - upload: the assets the bucket lacks, then the new or changed manifests and player files, the indexes last.
     Stories that failed have no manifest, so the next run builds them again; their errors are in the job summary.

Triggers: `repository_dispatch` `masterdata-updated` (moenotes-masterdata-sync sends it when a region serves a new
snapshot: `dispatch_repositories`), a daily schedule (03:23 UTC) in case a dispatch was missed, and
`workflow_dispatch`:

| Input | Meaning |
|---|---|
| `stories` | MasterAdv ids to build (spaces or commas); empty: every story the site lacks |
| `force` | rebuild the given stories and their Live2D models although their manifests exist |
| `region` | `all` (every region of `STORY_REGIONS`), `hk-tw-mo` or `jp` |
| `dry_run` | build, then list what would be uploaded instead of uploading |

Runs of one region do not overlap (`concurrency: story-site-<region>`); the regions build side by side.

## Settings

Repository secrets (Settings → Secrets and variables → Actions → Secrets):

| Secret | Value |
|---|---|
| `NNNOTES_BUNDLE_KEY` | `[bundle] key` (32 hex digits) |
| `NNNOTES_BUNDLE_NONCE_SEED` | `[bundle] nonce_seed` |
| `NNNOTES_SERVERS_TW_CDN` | `[servers.tw] cdn`: the TW CDN base URL |
| `PLAYFETCH_CREDENTIALS` | the whole `credentials.json` of `playfetch login` (the account that can pull `com.bilibili.sirius`) |
| `STORY_S3_ACCESS_KEY`, `STORY_S3_SECRET_KEY` | an S3 key that can list, read and write the bucket |

Repository variables (optional; the defaults are the StarMoe site): `STORY_S3_ENDPOINT` (`https://storage.bdon.moe`),
`STORY_S3_BUCKET` (`moenotes`), `STORY_S3_PREFIX` (empty: the bucket root; the hk-tw-mo site), `STORY_S3_PREFIX_JP` (`jp`), `MASTERDATA_BASE_URL`
(`https://metadata.bdon.moe`), `STORY_REGIONS` (`hk-tw-mo jp`: the regions with a site), `STORY_LIMIT` (`40`), `STORY_PLAYER_REPOSITORY`
(`empty-sekai/ournotes-player`), `STORY_PLAYER_REF` (`3774d8ac3987`), `PLAYFETCH_VERSION` (`v0.92`),
`STORY_APK_PACKAGE` (`com.bilibili.sirius`).

## Notes

- **The player version.** The site's player pages come from `STORY_PLAYER_REF`, and its data must be what that player
  reads. After a player release that changes the data format, sync this fork with upstream nnnotes and set
  `STORY_PLAYER_REF` to the matching player; new stories are then written for it. Stories already published are
  not rebuilt (run with `stories` and `force` for that).
- **The APK.** Google Play serves the current game version. When nnnotes' type trees do not match it, the build stops
  naming the class and the Unity version: sync the fork with upstream once nnnotes supports that version.
- **Bytes.** A rebuild on the runner is not byte-identical to a build elsewhere: the AAC encoder (ffmpeg) and the PNG
  encoder (zlib) differ between machines. The pixels and every JSON value are the same, so new stories and the
  published ones fit together; only a forced rebuild re-uploads such files.
- **Master data.** The build reads moenotes-masterdata-sync's current snapshot; stories published from another master
  source keep their texts until they are rebuilt.
