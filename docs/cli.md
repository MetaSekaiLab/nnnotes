# Command reference

Every command of `nnnotes`, its arguments and their defaults. The global options go before or after the command
name. A command prints a short summary on standard output, or with `--json` its report as JSON; notes and errors go
to standard error. Exit status 2 is a usage or settings error.

## Global options

| Argument | Description |
| --- | --- |
| `--config` `FILE` | TOML config file (else NNNOTES_CONFIG, else ./nnnotes.toml) |
| `--region` `REGION` | region: a [servers.<region>] table ([catalog] region) |
| `--language` `LANG` | catalog and client language: ja, en, zh-Hant, zh-Hans or ko ([catalog] language) |
| `--catalog` `FILE` | catalog .bin file ([paths] catalog; else downloaded into the cache) |
| `--catalog-version` `VERSION` | the resource version of an international catalog ([catalog] version; else the version the game API serves when an API root is configured, otherwise main) |
| `--cache` `DIR` | cache directory ([paths] cache) |
| `--store` `DIR` | store directory ([paths] store; default <cache>/store) |
| `--master` `DIR` | decoded master data directory ([paths] master) |
| `--apk` `FILE` | base.apk ([paths] apk) |
| `--ffmpeg` `EXE` | ffmpeg executable ([paths] ffmpeg; else on PATH) |
| `--vgmstream` `EXE` | vgmstream-cli executable ([paths] vgmstream; else on PATH) |
| `--node` `EXE` | Node.js executable ([paths] node; else on PATH) |
| `--json` | print the command's report as JSON on standard output (default: a short summary) |

## nnnotes config init

write a config file from the template: the values are asked in a terminal, else taken from --set (the other settings empty)

| Argument | Description |
| --- | --- |
| `--file` `FILE` | the config file to write (default: the file --config or NNNOTES_CONFIG names, else ./nnnotes.toml) |
| `--user` | write the per-user config file, read from any directory (`config path` shows it) |
| `--set` `SECTION.KEY=VALUE` | a value to write (repeatable; no questions then): VALUE - reads it from standard input; a list comma-separated; a path is written absolute |
| `--no-input` | ask nothing, also in a terminal |
| `--force` | overwrite an existing file |

At most one of `--file`, `--user`.

## nnnotes config set

set one value of the config file the commands read (or of --file / --user; a missing one is made from the template); comments and other lines stay

| Argument | Description |
| --- | --- |
| `SECTION.KEY` | the setting: bundle.key, servers.tw.cdn, paths.fonts.ja, ... |
| `value` | the value (a list comma-separated); - reads it from standard input, without echo for the keys in a terminal |
| `--file` `FILE` | the config file to edit |
| `--user` | edit the per-user config file, read from any directory (`config path` shows it) |

At most one of `--file`, `--user`.

## nnnotes config unset

empty one value of the config file (an empty value counts as unset)

| Argument | Description |
| --- | --- |
| `SECTION.KEY` | the setting |
| `--file` `FILE` | the config file to edit |
| `--user` | edit the per-user config file, read from any directory (`config path` shows it) |

At most one of `--file`, `--user`.

## nnnotes config check

every setting: where it comes from and whether it is valid, never its value; exit status 1 when one is invalid, not found or unknown (--json: nnnotes.config-check/1)

## nnnotes config path

the config files looked up, in order, and the one the commands read (--json: nnnotes.config-path/1)

## nnnotes servers

the server list of the bootstrap API root: regions, their CDN and API roots

| Argument | Description |
| --- | --- |
| `--show-hosts` | print the CDN and API roots, not only their counts |

## nnnotes catalog keys

the addressable keys of the catalog (the total on standard error)

| Argument | Description |
| --- | --- |
| `--prefix` `PREFIX` | only the keys that start with this prefix (default ``) |
| `--limit` `LIMIT` | at most N keys (default 200; 0: all) |

## nnnotes catalog browse

browse the configured regions' catalogs and bundles in a local web page

| Argument | Description |
| --- | --- |
| `--port` `PORT` | port (default 8000) |
| `--host` `HOST` | address to listen on (default 127.0.0.1) |

## nnnotes catalog versions

the catalog versions imported into the store (--json: nnnotes.catalogs/1)

## nnnotes catalog import

import a catalog file (with the APK's catalog when [paths] apk is set)

| Argument | Description |
| --- | --- |
| `file` | catalog_<version>_<language>.bin |
| `--label` `LABEL` | label of the version (default: its resource version, else the sha256 prefix) |
| `--apk-catalog` `APK_CATALOG` | the APK's catalog.bin (default: read from [paths] apk) |
| `--resource-version` `RESOURCE_VERSION` | the game's resource version of this catalog (default: the JP source's version, else the version in the file name catalog_<version>_<language>.bin) |

## nnnotes catalog fetch

fetch the region's current catalog and import it into the store

| Argument | Description |
| --- | --- |
| `--label` `LABEL` | label of the version (default: the resource version when an API root is set) |

## nnnotes catalog diff

keys, bundles, raw files (and objects) that differ between two catalog versions of the store (--json: nnnotes.catalog-diff/1)

| Argument | Description |
| --- | --- |
| `old` | label, version id or sha256 prefix |
| `new` | label, version id or sha256 prefix |

## nnnotes bundle pull

fetch the bundle closure of keys into the cache (prints the cached bundle files)

| Argument | Description |
| --- | --- |
| `KEY` ... | addressable keys (`catalog keys` lists them) |

## nnnotes master version

the master data and resource version the region serves now (game API)

## nnnotes master decode

master data .bin files -> <Table>.json

| Argument | Description |
| --- | --- |
| `inputs` ... | directories (their *.bin files) or files |
| `-o`, `--out` `OUT` | output directory (required) |
| `--workers` `N` | parallel decodes (default the CPUs this process may use; 0: one at a time) |

## nnnotes master download

a master data version from the region's CDN (SHA-256 checked)

| Argument | Description |
| --- | --- |
| `--version` `VERSION` | master data version |
| `--latest` | the version the region serves now (`master version`) |
| `-o`, `--out` `OUT` | directory for MasterManifest.json and the .bin files (required) |
| `--fetch-workers` `N` | parallel downloads (default 16) |

Exactly one of `--version`, `--latest`.

## nnnotes export assets

every object of the selected bundles -> common formats (the store and its layouts)

| Argument | Description |
| --- | --- |
| `-o`, `--out` `OUT` | output directory (required) |
| `--select` `SEL` | group:<group>, key:<key prefix> or bundle:<glob> (repeatable; default: everything) |
| `--views` `all\|none\|a,b` | semantic views to build (default: all when master data is set, else none) |
| `--stored-catalog` `LABEL\|SHA` | a catalog version of the store (`catalog versions`; default: the current catalog) |
| `--png-level` `N` | PNG compression level 0-9 (changes the output) |
| `--flac-level` `N` | FLAC compression level 0-12 of the CRI audio (default 8; every level decodes to the same samples; changes the files) |
| `--only-class` `C,...` | export only objects of these classes |
| `--workers` `N` | worker processes (default: the CPUs this process may use; 0: this process) |
| `--memory` `GiB` | memory budget of the running tasks and workers (default: 80 % of the physical memory) |
| `--fetch-workers` `N` | parallel downloads (default 8) |
| `--layout` `LAYOUT` | original, cas or original,cas (default original) |
| `--explain` | print every setting of the run first |
| `--link` `LINK` | how the layout files are made from the store: auto (default; [export] link): a clone where the file system makes them, else a hard link (read-only), else a copy; or that method only (one of `auto`, `clone`, `hard`, `copy`) |
| `--strict` | exit 1 when a view has gaps |
| `--dry-run` | print the plan, run nothing |

## nnnotes export episode

an ADV episode -> JSON

| Argument | Description |
| --- | --- |
| `adv_id` | MasterAdv id of the episode |
| `-o`, `--out` `OUT` | output .json file (required) |

## nnnotes export story

an ADV episode -> story directory (episode, audio, scene, UI, media, videos) + its Live2D models

| Argument | Description |
| --- | --- |
| `adv_id` | MasterAdv id of the episode |
| `-o`, `--out` `OUT` | output directory (required) |
| `--models` `DIR` | directory of the Live2D models, one <model id>/ each (default: OUT/../live2d) |
| `--force` | export the models again whose directory exists |
| `--format` `FORMAT` | audio file format (default flac) (one of `flac`, `ogg`, `wav`) |
| `--flac-level` `N` | FLAC compression level 0-12 of --format flac (default 8; every level decodes to the same samples) |
| `--no-audio` | do not decode the cue sheets |
| `--fonts` `FONTS` | open (default): text layout and style only, no font data; game: also the game's TMP fonts (needs the 'fonts' extra) (one of `open`, `game`) |

## nnnotes export live

a live (music + difficulty) -> self-contained live directory

| Argument | Description |
| --- | --- |
| `music_id` | MasterLiveMusic id |
| `--difficulty` `DIFFICULTY` | chart difficulty (default expert) (one of `easy`, `normal`, `hard`, `expert`) |
| `--format` `FORMAT` | audio file format (default flac) (one of `flac`, `ogg`, `wav`) |
| `--flac-level` `N` | FLAC compression level 0-12 of --format flac (default 8; every level decodes to the same samples) |
| `--fonts` `FONTS` | start canvas text: open (default): layout and style only, no font data (your fonts are not used); game: also the game's TMP fonts (needs the 'fonts' extra) (one of `open`, `game`) |
| `--band` `BAND` | band of the LightWeight background and start timeline (default: band of the music's first vocal character) |
| `--leader-card` `LEADER_CARD` | deck centre MasterMemberCard id (its character's band) |
| `--live-option` `OPTION[=VALUES]` | also export the files of a Live option's variants (repeatable): MirrorChart, MeasureLineDisplay, NoteDesignId, NoteEffectId, LiveQuality, NoteSePatternId (every value of the master data, or =v,v...), defaults (the option defaults and ranges only) or all |
| `-o`, `--out` `OUT` | output directory (required) |

At most one of `--band`, `--leader-card`.

## nnnotes export live2d

a Live2D model -> runtime model directory

| Argument | Description |
| --- | --- |
| `key` | model key Character/Live2D/<group>/<name>/model/<name>, or its model id <name> |
| `-o`, `--out` `OUT` | output directory (required) |

## nnnotes export spot

a spot -> spot.json + Spine + room.glb + shaders

| Argument | Description |
| --- | --- |
| `spot_id` | MasterHomeSpot id |
| `-o`, `--out` `OUT` | output directory (required) |

## nnnotes export room

a background prefab -> glb

| Argument | Description |
| --- | --- |
| `key` | addressable key of the prefab |
| `-o`, `--out` `OUT` | output .glb file (required) |

## nnnotes export shader

the shaders of a key's bundle closure or of APK bundles

| Argument | Description |
| --- | --- |
| `--key` `KEY` | addressable key: the shaders of its bundle closure |
| `--apk-bundle` `APK_BUNDLE` ... | substring(s) of APK bundle file names |
| `-o`, `--out` `OUT` | output directory (required) |

Exactly one of `--key`, `--apk-bundle`.

## nnnotes export audio

a CRI cue sheet -> audio files

| Argument | Description |
| --- | --- |
| `cue_sheet` | cue sheet name: the key Cri/Sound/<cue_sheet> of the catalog |
| `-o`, `--out` `OUT` | output directory (required) |
| `--format` `FORMAT` | audio file format (default flac) (one of `flac`, `ogg`, `wav`) |
| `--flac-level` `N` | FLAC compression level 0-12 of --format flac (default 8; every level decodes to the same samples) |

## nnnotes export ui

the APK's UI prefabs -> an offline UI prefab library for ournotes-player/ui

| Argument | Description |
| --- | --- |
| `-o`, `--out` `OUT` | UI data directory outside the source repositories (required) |
| `--key` `KEY` | exact APK catalog key (repeatable) |
| `--prefix` `PREFIX` | APK UI key prefix (default EmbUI/) |
| `--limit` `LIMIT` | maximum number of keys |
| `--no-dependencies` | omit additional prefab roots and controllers |
| `--force` | re-export the selected keys |

## nnnotes apk settings

the player's graphics settings (color space, quality levels) -> JSON

| Argument | Description |
| --- | --- |
| `-o`, `--out` `OUT` | output .json file (required) |

## nnnotes apk hca-key

find the HCA keycode of the CRI audio (never printed)

| Argument | Description |
| --- | --- |
| `--write` `DIR` | write it as DIR/.hcakey (the file vgmstream reads) |

## nnnotes store plan

what `export assets` would run and why, without running it (--json: nnnotes.plan/1)

| Argument | Description |
| --- | --- |
| `-o`, `--out` `OUT` | output directory of the export (counts the layout files to write) |
| `--select` `SEL` | group:<group>, key:<key prefix> or bundle:<glob> (repeatable; default: everything) |
| `--views` `all\|none\|a,b` | semantic views to build (default: all when master data is set, else none) |
| `--stored-catalog` `LABEL\|SHA` | a catalog version of the store (`catalog versions`; default: the current catalog) |
| `--png-level` `N` | PNG compression level 0-9 (changes the output) |
| `--flac-level` `N` | FLAC compression level 0-12 of the CRI audio (default 8; every level decodes to the same samples; changes the files) |
| `--only-class` `C,...` | export only objects of these classes |
| `--workers` `N` | worker processes (default: the CPUs this process may use; 0: this process) |
| `--memory` `GiB` | memory budget of the running tasks and workers (default: 80 % of the physical memory) |
| `--fetch-workers` `N` | parallel downloads (default 8) |
| `--layout` `LAYOUT` | original, cas or original,cas (default original) |
| `--explain` | print every setting of the run first |
| `--since` `RUN` | explain against this run (default: the latest of the selection) |
| `--check` | exit 1 when anything would run or change |
| `--census` | first fetch and census the bundles without one |
| `--emit-tasks` `DIR` | write the runnable tasks as task descriptions |
| `--why` `TASK` | the reasons and key parts of one task |

## nnnotes store run

run task descriptions (nnnotes.task/1) into the store

| Argument | Description |
| --- | --- |
| `TASK` ... | task description files, or directories of them |
| `--fetch` | fetch inputs through their catalog locators |
| `--force` | run tasks whose result is stored (it must not change) |

## nnnotes store verify

check every stored content and result

| Argument | Description |
| --- | --- |
| `--quick` | check sizes only, do not hash the contents |
| `--workers` `N` | parallel hashing (default the CPUs this process may use; 0: one at a time) |

## nnnotes voices list

the voices matching the filters (the text column in the --language language, default [catalog] language)

| Argument | Description |
| --- | --- |
| `--from` `OUT` | an export directory with views/voices.json (export assets --views voices); else the index is built from the configured master data and catalog (no files) |
| `--character` `CHARACTER` | a MasterCharacter id or a part of a name in any language |
| `--category` `CATEGORY` | a category (CharacterRankUp or character-rank-up), Source.Category or a source |
| `--source` `SOURCE` | a source (Talk, CharacterVoice, ..., HomeSpot, Title, Sound, Story) |
| `--episode` `ADV` | the Story voices of one episode: a MasterAdv id or an episode asset (without --from: read from the catalog) |
| `--status` `STATUS` | only the rows with this status (one of `ok`, `missing-cue`, `missing-key`, `not-exported`, `unsupported`, `no-value`) |
| `--limit` `LIMIT` | at most N rows (0: all) |

## nnnotes voices search

the voices whose text (or cue name) contains TEXT

| Argument | Description |
| --- | --- |
| `text` | text to look for |
| `--from` `OUT` | an export directory with views/voices.json (export assets --views voices); else the index is built from the configured master data and catalog (no files) |
| `--character` `CHARACTER` | a MasterCharacter id or a part of a name in any language |
| `--category` `CATEGORY` | a category (CharacterRankUp or character-rank-up), Source.Category or a source |
| `--source` `SOURCE` | a source (Talk, CharacterVoice, ..., HomeSpot, Title, Sound, Story) |
| `--episode` `ADV` | the Story voices of one episode: a MasterAdv id or an episode asset (without --from: read from the catalog) |
| `--status` `STATUS` | only the rows with this status (one of `ok`, `missing-cue`, `missing-key`, `not-exported`, `unsupported`, `no-value`) |
| `--limit` `LIMIT` | at most N rows (0: all) |
| `--search-language` `SEARCH_LANGUAGE` | search this language only (default: all five) (one of `ja`, `en`, `zh-Hant`, `zh-Hans`, `ko`) |

## nnnotes voices summary

rows per status, category and character; gaps both ways

| Argument | Description |
| --- | --- |
| `--from` `OUT` | an export directory with views/voices.json (export assets --views voices); else the index is built from the configured master data and catalog (no files) |

## nnnotes voices get

the files of one voice: a row id (MasterTalk:561, MasterAdv:<id>:<command>) or a sound id

| Argument | Description |
| --- | --- |
| `id` | row id or sound id |
| `-o`, `--out` `OUT` | output directory (required) |
| `--from` `OUT` | an export directory with views/voices.json (export assets --views voices); else the index is built from the configured master data and catalog (no files) |
| `--format` `FORMAT` | flac (default: the exported files when --from has them), ogg or wav (decodes the sheet) (one of `flac`, `ogg`, `wav`) |
| `--flac-level` `N` | FLAC compression level 0-12 of --format flac: decode the sheet at this level, also with --from (default 8; every level decodes to the same samples) |

## nnnotes site build

live charts, Live2D models and stories -> static site for ournotes-player (shared player + per-chart / per-model / per-story data)

| Argument | Description |
| --- | --- |
| `-o`, `--out` `SITE` | site directory (required) |
| `--player` `PLAYER` | ournotes-player checkout (built) or installed package ([paths] player) |
| `--pair` `PAIR` | <musicId>:<difficulty> (repeatable) |
| `--all` | every MasterLiveMusic x difficulty |
| `--player-only` | rewrite the player files, charts.json and models.json only |
| `--reingest-json` | store every chart's and model's JSON files again |
| `--live2d` `MODEL` | Live2D model id or key Character/Live2D/<group>/<name>/model/<name> (repeatable) |
| `--all-live2d` | every Live2D model of the catalog |
| `--format` `FORMAT` | audio format of the charts and stories (default aac) (one of `aac`, `opus`, `vorbis`, `mp3`, `flac`) |
| `--no-audio` | export no audio files |
| `--compress` `COMPRESS` | encoding of the compressible assets (JSON, shaders, moc3, ...) where it makes them smaller: gzip (default), br (brotli) or none (one of `gzip`, `br`, `none`) |
| `--force` | rebuild charts, models and stories whose manifest exists (stories: also their models) |
| `--tmp` `TMP` | directory for the temporary live and model builds (default <site>.tmp) |
| `--workers` `N` | parallel music / story / model processes (default a quarter of the CPUs, up to 8; models up to 4; 0: this process) |
| `--read-workers` `N` | chart read sets run at a time (default half the CPUs, up to 16) |
| `--story` `ADV_ID` | story episode: its MasterAdv id (repeatable) |
| `--all-stories` | every story episode (MasterAdv) of the master data |
| `--story-languages` `LANGS` | languages of the stories' text, comma-separated (default: ja,en,zh-Hant,zh-Hans,ko) |
| `--font` `LANG=PATH` | font file the story text of a language is drawn with (repeatable; [paths] fonts.<lang>); emoji=PATH: the colour emoji font the emoji sprites are drawn from ([paths] fonts.emoji) |
| `--site-region` `REGION` | a region the site serves: a [servers.<region>] table (repeatable; the first is the base; default: [catalog] region) |
| `--all-regions` | every configured region |
| `--fonts` `FONTS` | text of the start canvas and the stories: open (default): the start canvas has layout and style only, the stories glyphs from your font files (--font); game: the game's TMP fonts (needs the 'fonts' extra) (one of `open`, `game`) |
| `--band` `BAND` | band of the LightWeight background and start timeline (default: band of the music's first vocal character) |
| `--leader-card` `LEADER_CARD` | deck centre MasterMemberCard id (its character's band) |
| `--live-option` `OPTION[=VALUES]` | also export the files of a Live option's variants (repeatable): MirrorChart, MeasureLineDisplay, NoteDesignId, NoteEffectId, LiveQuality, NoteSePatternId (every value of the master data, or =v,v...), defaults (the option defaults and ranges only) or all |

At most one of `--pair`, `--all`, `--player-only`, `--reingest-json`.

At most one of `--live2d`, `--all-live2d`.

At most one of `--story`, `--all-stories`.

At most one of `--site-region`, `--all-regions`.

At most one of `--band`, `--leader-card`.

## nnnotes music-data

every live song and chart: metadata in every language, chart facts and the deck model's chart statistics -> one JSON file

| Argument | Description |
| --- | --- |
| `--master-files` `DIR` | master data files as served: MasterManifest.json and the .bin files it lists (`master download`) |
| `--apk-master` | the master data files of base.apk ([paths] apk) |
| `--decoded-master` | decoded master data ([paths] master or --master) with the MasterManifest.json of the files it was decoded from; no master key |
| `--full` | also write the deck model's input: every chart's runtime notes and the master data tables about cards, skills, bonuses, scores and events |
| `--no-deck` | do not run the deck model (every chart's deck is null) |
| `--seeds` `N` | replay seeds for a chart with a luck range (default 8); expectations do not use sampling |
| `--workers` `N` | threads measuring charts (default: every processor; 0: one thread) |
| `--no-gekisou-aptitude` | leave out the charts' Gekisou aptitude (every gekisouAptitude is null) |
| `--stats-cache` `DIR` | reuse native simulation programs and runs in DIR across builds; completed cache entries survive interrupted builds |
| `--no-bgm` | do not read the BGM cue sheets (every song's bgm.length is null) |
| `--replay-dir` `DIR` | write canonical runtime DeckData, per-chart inputs and replay manifest under the output directory |
| `--replay-engine` `PKG` | the deck model's replay WASM release package (ournotes-replay-wasm-vVERSION.tar.gz or its directory); its web JS/WASM and build-info.json are copied into --replay-dir |
| `--recommend-engine` `PKG` | the deck model's recommendation WASM release package (ournotes-recommend-wasm-vVERSION.tar.gz or its directory); its web JS/WASM and build-info.json are copied into --replay-dir |
| `--jackets` `DIR` | also write every song's jacket as DIR/<jacket>.webp (at most 320 px on the longer side) |
| `-o`, `--out` `OUT` | output file (.json, or .json.gz for gzip) (required) |

Exactly one of `--master-files`, `--apk-master`, `--decoded-master`.
