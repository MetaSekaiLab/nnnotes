# Music data

`nnnotes music-data` writes one JSON file with every live song and chart of one master data version: what a song
listing shows (titles and credits in every language, bands, vocal characters, category, tags, release time, score
ranks, jacket, BGM length), the Gekisou catalog (member cards, snaps and their Gekisou skills), per difficulty the
chart facts (level, note counts, BPM, chart times, skill events, fevers), and per chart its **deck statistics**: what
the chart contributes to the live score whatever the deck, in a solo live (Gekisou off) and in a Gekisou live at every
rank, measured by the deck model [ournotes-deck](https://github.com/empty-sekai/ournotes-deck), the package
`ournotes-sim` that the extra `deck` installs (`pip install 'nnnotes[deck]'`). The format is `nnnotes.music-data/2`; its JSON Schema is
[music-data.schema.json](https://github.com/MetaSekaiLab/nnnotes/blob/main/src/nnnotes/schemas/music-data.schema.json).

```
nnnotes music-data (--master-files DIR | --apk-master | --decoded-master) [--full] [--no-deck] [--seeds N]
                   [--workers N] [--no-gekisou-aptitude]
                   [--stats-cache DIR] [--no-bgm] [--jackets DIR] -o FILE
```

- `--master-files DIR`: master data files as served, `DIR/MasterManifest.json` and the `.bin` files it lists
  (`nnnotes master download`). The file's `region` is `[catalog] region` (`--region`).
- `--apk-master`: the master data files the APK ships (`assets/Master/` of `[paths] apk`, the same layout). The
  file's `region` is `embedded`.
- `--decoded-master`: master data decoded elsewhere, the directory the other commands read (`[paths] master`,
  `--master`, `[servers.<region>] master`): one `<Table>.json` per table (`nnnotes master decode`) and the
  `MasterManifest.json` of the files they were decoded from, as a master data snapshot published with its manifest
  carries it. The file's `region` is `[catalog] region`; no master key is needed.
- `--full`: also write the deck model's input, every chart's runtime notes and the master data tables about cards,
  skills, bonuses, scores and events ([the deck input](#the-deck-input-full)), for tools that run a deck model of
  their own.
- `--no-deck`: do not run the deck model; every chart's `deck` is null. A full statistics build evaluates many
  complete lives per chart. `--workers N` sets the native worker count (default: every available processor; `0`: one thread).
- `--seeds N`: list this many replay seeds for a chart with a luck range (default 8). Expectations and aptitude
  use the model's independent nominal probabilities, not a sample of these seeds. Increasing this option changes
  the replay examples without changing the expected scores or their numerical enclosures.
- `--no-gekisou-aptitude`: keep the ordinary deck statistics and skip single-skill aptitude measurements.
- `--stats-cache DIR`: use the native model's persistent program and run cache. Reuse follows the complete
  compiled simulation inputs, so unchanged programs and runs can survive a change to the skill catalog. New
  shapes need their own measurements; changing a shape's numeric index does not by itself identify a new program.
  Every invocation still passes the complete current master data and charts to the native model, which constructs
  the current header and assembles the requested statistics.
  Cache records are isolated by cache schema and model source SHA-256. Each completed result is saved through a
  unique temporary file, a file sync and an atomic rename. Invalid or unreadable records are measured again;
  interrupted work retains its completed records. Concurrent processes may compute the same missing record, but
  publish complete records atomically. Old inputs remain available for later reuse: there is no automatic garbage
  collection, and unused model-source directories can be removed to reclaim space. A record larger than 64 MiB is
  not persisted. The command's `deckStats` summary reports native requests, hits, completed computations, writes,
  invalid entries and written bytes; these count cache operations, not charts. Cache counters are not part of the
  published music data. Removing the cache changes the amount of work, not the bytes of the music data.
- `--no-bgm`: do not read the cue sheets (every `bgm.length` is null).
- `--jackets DIR`: also write every song's jacket, the Texture2D `Image/Jacket/<jacket>`, as `DIR/<jacket>.webp`
  (WebP quality 88, scaled down with Lanczos to at most 320 pixels on the longer side, without alpha when opaque); a
  page next to the file finds a song's jacket at `<DIR>/<song.jacket>.webp`.

Each master data file is checked against the SHA-256 the manifest lists and decoded with `[master] key` and `iv`.
With `--decoded-master` the tables are read as decoded, and the master data version and each file's SHA-256 are the
manifest's: the decoded tables cannot be checked against the files as served, so the file records what the manifest
lists (the same values as `--master-files` on those files).
Charts are read from the catalog of `[catalog] language` (bundles fetched into the depot as for every command); the
BGM length from the cue sheet's ACB (its `CueTable` and `WaveformTable`, no audio is decoded). `FILE` ending in `.gz`
is written gzip-compressed. The command prints a summary (`songs`, `charts`, `deck`: the deck model's commit,
`unplayable`, `bytes`, `sha256` of the file, ...).

The command writes the file only when every table, chart and cue sheet was read and every chart measured: a missing
or mismatching master data file, a table without a column the file exports, a text id that `MasterText` does not
have, a score id that `MasterLiveMusicScore` does not have, a missing or unreadable chart asset, a note id that
occurs twice in a chart, a cue sheet without the song's cue, a member card or snap whose character, Gekisou (support)
skill or rank group the master data does not have, (with `--jackets`) a missing jacket texture, a chart the deck model
cannot measure or whose check deck fails, or deck statistics that disagree with the chart facts or the master data
stops it with exit status 1 and a line naming the input. The file is written through a temporary file and a rename.

An installation without the extension module (a source checkout that was not built) runs only with `--no-deck`;
the wheels on PyPI carry it. See [Building](#building).

## Layout

```json
{
  "format": "nnnotes.music-data/2",
  "provenance": {"region": ..., "client": {...}, "catalog": {...}, "master": {...}, "exporter": {...}, "deck": {...}},
  "languages": ["ja", "en", "zh-Hant", "zh-Hans", "ko"],
  "bands": [...], "characters": [...], "tags": [...], "categories": [...],
  "gekisouCatalog": {"skills": [...], "supportSkills": [...], "members": [...], "snaps": [...]},
  "deck": {"model": {...}, "kinds": [...], "gekisouAptitude": {...}},
  "songs": [{"id": 100001, ..., "charts": [{..., "deck": {...}}, ...]}, ...],
  "master": {...}, "charts": [...]
}
```

`master` and `charts` are present with `--full` only. The file is minified UTF-8 with one line feed at the end. Keys
are always in the order shown here and in the tables below, so the same inputs with the same nnnotes version (and so
the same deck model) give the same bytes. Master data values that are binary32 (single precision) in the game, a
number the master data writes with a fraction or an exponent, are written as the shortest decimal that reads back as
the same binary32 value (infinity as `1e999` / `-1e999`); the deck model's numbers are binary64 and written as it
writes them. A gzip file has no file name and a zero modification time in its header.

A **text** is an object with one string per language of `languages` (`{"ja": ..., "en": ..., "zh-Hant": ...,
"zh-Hans": ..., "ko": ...}`), the `MasterText` row of the id; a text field is null when the master data gives no id
(an empty string). A language's string may be empty when the game has no text in that language.

### provenance

| Field | Content |
|---|---|
| `region` | the region whose master data this is (a configured region name), or `embedded` for the APK's master data |
| `client.versionName`, `client.versionCode` | the APK's version name and code (null without `[paths] apk`) |
| `catalog.resourceVersion` | the resource version used to select the downloaded catalog, or recorded for an explicit file in the catalog depot (`nnnotes catalog fetch` / `import`); null when unknown |
| `catalog.sha256` | SHA-256 of the remote catalog file the charts were read with |
| `master.source` | `api` (`--master-files`, `--decoded-master`: the region's files) or `embedded` (`--apk-master`) |
| `master.version` | the `version` of the master data manifest |
| `master.tables.<Table>.sha256` | SHA-256 of each table's file as served, before decoding: the song tables (`MasterLiveMusic`, `MasterLiveMusicScore`, `MasterText`, `MasterBand`, `MasterCharacter`, `MasterTag`, `MasterLiveMusicCategory`, `MasterSound`, `MasterSoundCueSheet`, `MasterLiveScoreRank`), the tables of the Gekisou catalog (`MasterMemberCard`, `MasterSupportCard`, `MasterSupportCardRank`, `MasterGekisouSkill`, `MasterGekisouSkillEffect`, `MasterGekisouSupportSkill`, `MasterGekisouSupportSkillEffect`) and, when the deck model runs or with `--full`, the tables of [the deck input](#the-deck-input-full) |
| `exporter.name`, `exporter.version` | `nnnotes` and its version |
| `exporter.chartFormat` | the format of the chart converter the notes come from, `nnnotes.live-score/1` |
| `deck` | the deck model: `{name, version, source, commit, sourceSha256, format}`, `ournotes-deck`, the package version of its crate ournotes-sim, the repository, the git commit nnnotes is built with and the statistics format (`ournotes-deck.chart-stats/3`); null with `--no-deck` |

### bands, characters, tags, categories

| Field | Content |
|---|---|
| `bands[].id`, `.name`, `.mainColor`, `.subColor` | `MasterBand`: id, name text, color codes |
| `characters[].id`, `.bandId`, `.name`, `.shortName`, `.mainColor` | `MasterCharacter` |
| `tags[].id`, `.name` | `MasterTag` (the ids of `bestMusicTagIds`) |
| `categories[].id`, `.musicCategories`, `.name` | `MasterLiveMusicCategory` (the listing's category tabs; `musicCategories` are the song category values it shows) |

### gekisouCatalog

The Gekisou skills a deck brings to a Gekisou live and the cards that carry them, from the master data (written with
`--no-deck` too): the member cards' Gekisou skills, the snaps' Gekisou support skills, the member cards and the
snaps, every list sorted by `id`. Texts as above; `description` is the master data's format text with its
placeholders (`{effects[0].value}`, ...), which a page fills from the effect rows or leaves out.

| Field | Content |
|---|---|
| `skills[]` | `MasterGekisouSkill`, the member cards' Gekisou skills: `id`, `mission` (`_gekisouMissionType`: 1 combo, 2 luck, 3 Just count, 4 every mission), `maxLevel` (the highest `_level` of its `MasterGekisouSkillEffect` rows, 0 without one), `name` (`_nameTextID`), `description` (`_descriptionTextFormatID`) |
| `supportSkills[]` | `MasterGekisouSupportSkill`, the snaps' Gekisou support skills: the same fields (`MasterGekisouSupportSkillEffect` for `maxLevel`) |
| `members[]` | `MasterMemberCard`: `id`, `characterId`, `bandId` (the character's `MasterCharacter._bandID`), `rarity`, `gekisouSkillId` (`_gekisouSkillID`, null for 0), `name` (`_nameTextID`), `subtitle` (`_subtitleTextID`, the card's title) |
| `snaps[]` | `MasterSupportCard`: `id`, `characterIds`, `rarity`, `gekisouSupportSkillIds` (`_gekisouSupportSkillId01`, `_gekisouSupportSkillId02` that are not 0), `supportSkillLevel` (their level at the snap's highest rank: `_gekisouSupportSkill01Level` / `02Level` of the `MasterSupportCardRank` row of its rank group with the highest `_rank`), `name` (`_nameTextID`), `subtitle` (`_descriptionTextID`, the snap's title) |

A member card's character, Gekisou skill and a snap's Gekisou support skills must be in their tables, and a snap's
rank group must have a row; a snap whose two Gekisou support skills have different levels at its highest rank stops
the command (the catalog has one level per snap).

### songs

One entry per `MasterLiveMusic` row, sorted by `id`.

| Field | Content |
|---|---|
| `id`, `sortOrder`, `startAt`, `defaultUnlock` | the row's `_id`, `_sortOrder`, `_startAt` (as served, server time), `_defaultUnlock` |
| `title`, `ruby`, `phonetic` | texts of `_titleTextID`, `_rubyTitleTextID`, `_phoneticTextID` |
| `bandIds`, `bandName` | `_bandIDs`; the text of `_bandNameTextID`, a name the song shows instead of its first band's (null: none) |
| `vocalCharacterIds` | `_vocalCharacterIDs` |
| `lyricist`, `composer`, `arranger` | texts of `_lyricistTextID`, `_composerTextID`, `_arrangerTextID` |
| `musicType`, `musicCategories`, `bestMusicTagIds` | `_musicType` (the song type that card type bonuses match), `_musicCategories`, `_bestMusicTagIDs` |
| `jacket` | `_jacketAssetName` (the jacket sprite `Image/Jacket/<jacket>`) |
| `gekisouMissions` | `[_gekisouMission1, _gekisouMission2, _gekisouMission3]`: the missions of the song's Gekisou ranges (1 combo, 2 luck, 3 Just count) |
| `bgm.soundId`, `.cueSheet`, `.cue` | `_musicSoundID` and its `MasterSound` / `MasterSoundCueSheet` cue |
| `bgm.length` | `{lengthMs, samples, sampleRate, durationMs}`: the cue's `Length` in the ACB `CueTable`, the sample count and rate of its first waveform, and `samples * 1000 // sampleRate`; null with `--no-bgm` |
| `scoreRanks` | `[{rank, requiredScore, battleRequiredScore}]`: the `MasterLiveScoreRank` rows of `_liveScoreRankGroup` in required-score order (rank `E` .. `SS`; `_requiredScore`, `_battleLiveRequiredScore`). The group is the song's, so every difficulty shares these thresholds; a live's rank is the last row whose required score its score reaches |
| `charts` | one entry per difficulty the song has (`_easyID` .. `_expertID` not 0), in the order easy, normal, hard, expert |
| `master.MasterLiveMusic`, `master.MasterLiveScoreRank` | the whole `MasterLiveMusic` row and its score rank rows as decoded |

### charts

| Field | Content |
|---|---|
| `difficulty` | `easy`, `normal`, `hard` or `expert` |
| `scoreId`, `level`, `displayLevel`, `fullComboCount` | `MasterLiveMusicScore`: `_id`, `_musicScoreLevel`, `_musicScoreDisplayLevel`, `_fullComboCount` |
| `asset.key`, `asset.sha256` | the chart TextAsset `Live/MusicScore/<_musicScoreTextFileName>` and the SHA-256 of its bytes as shipped |
| `notes.judged` | notes that are judged (and count for a full combo) |
| `notes.total` | every runtime note, including hidden notes, guide notes and slide combo ticks |
| `notes.byOperateType` | `{"<NoteOperateType>": count}` over every runtime note, keys in ascending order |
| `bpm.changes` | every BPM change `{timeMs, bpm}` in time order |
| `bpm.main`, `.min`, `.max` | over the played span (first to last judged note): the BPM that holds longest (the earliest on a tie), the lowest and the highest |
| `firstNoteMs`, `lastJudgedNoteMs` | times of the first and the last judged note |
| `lastNoteMs` | the latest time of any runtime note (the time the score code calls the last timing note) |
| `musicLengthMs` | `lastNoteMs + 1000`: the live's music length on the game's score path (skill effects end at it at the latest) |
| `skillEventsMs` | skill event times in chart order; event `i` fires the skill of the member at performance position `i` |
| `fevers` | fever ranges `[startMs, endMs]` sorted by start |
| `deck` | the chart's deck statistics (below); null with `--no-deck` |

Chart times are milliseconds of chart time. The two lengths are different facts: `bgm.length` is how long the music
plays, `musicLengthMs` is the length the score code uses; a listing chooses the one it needs.

## Deck statistics

The native statistics format is `ournotes-deck.chart-stats/3`. It evaluates complete lives on the game's default
frame schedule in two scenarios:

- **Gekisou on** (`expectation`): each note is judged at its exact time, Just inside a Just-count range and Perfect
  elsewhere, with rank 1 in each range. Random lotteries and probabilistic skills follow independent nominal
  probabilities. The numbers enclose the expectation under that stated model.
- **Gekisou off** (`offSeeds`): each note is Perfect, with seed 0 and no Just, luck, Gekisou combo or rank bonus.
  This remains a deterministic measurement. A chart with more than three fevers can still play here.

### Expected values and their intervals

An **Estimate** is `[center, radius]`, denoting the interval `[center - radius, center + radius]`. Both values and
both endpoints must be finite, and the radius must be nonnegative. The radius bounds numerical uncertainty; it is
not a standard error, a confidence interval or variation between replay seeds. Point values are serialized at
millipoint precision, lottery counts at five decimal places and weights at twelve, with radii expanded outwards
so rounding keeps the enclosure. A zero radius is a point interval.

The no-skill expected score is measured at `deck.model.power` (300000). For each ordinary **score-up kind** and
performance position, `expectation.weights[kind][position]` encloses the expected gain from one effect at factor
1, divided by that power. Effects run through the simulator's own conditions, frames and appliers. A deck whose
ordinary live skills are represented by these kinds has the score approximation

```
P * (score / model.power + sum_k factor_k * weights[kind_k][k])
```

where `P` is its power. Arithmetic on estimates must carry their radii. The approximation also has a separate
integer-floor error, checked by a complete nominal evaluation at `model.checkPower`; a numerical radius does not
replace that error bound. The factor follows the game's value conversion: effect type 2000 uses
`floor(value / 10000f * 1e5) / 1e5`, type 2005 uses `floor(value / -10000f * 1e5) / 1e5`, and types 2002 and 2004
round the binary32 quotient `value / 10000f` half to even. Other effect types, including cumulative scoring, life
and judgement conversion, have no ordinary score-up weights.

`replaySeeds` is separate from these expectations. A playable chart without a luck range lists `[0]`; otherwise
it lists the first `--seeds` values of the published sequence. Candidate `k` is the low 32 bits, interpreted as a
signed integer, of output `k + 1` of SplitMix64 started at `0x6765_6B69_736F_7531` (`gekisou1`). A candidate is kept
when its effective stream-seed pair (`|b|`, `|b ^ 0x9E3779B9|`, with -2^31 treated as 2^31 - 1) differs from all
previous pairs. A shorter list is a prefix of a longer list. These values select concrete replay examples; the
exporter never averages them to obtain `expectation` or `gekisouAptitude`.

### Ranks and Just judgements

A range at rank `r` awards `trunc(rangeScore * p(r) / 100)`, where
`p(r) = ranges[i].rankBonusPercents[r - 1]`. `rankBonus` is the expected result of that integer operation at rank 1.
In general, **the expectation of a truncated score is not the truncation of the expected score**. Readers must
keep the separately measured `rankBonus` and `rankBonusPerfect`; neither is reconstructed by truncating an
expectation's center.

When rank changes do not alter range scoring, the other-rank approximation replaces the rank-1 bonus by
`E[rangeScore] * p(r) / 100` and adjusts an ordinary weight by
`sum_i (p_i(r_i) - p_i(1)) / 100 * rangeWeights[kind][position][i]`. The native rank check includes the resulting
integer-truncation slack in its bound. `rangeWeights` is null when overlapping ranges break that independence;
a kind's row is null when its conditions read a confirmed rank (condition 7012). These nulls mark a limit of the
rank formula, not a zero contribution. Such scenarios require full simulation.

`scorePerfect` and `rangeScorePerfect` use the same nominal model with every Just judgement replaced by Perfect.
`rankBonusPerfect` is measured on that play too. A chart with `justNotes == 0` has matching best and Perfect
expectations. Interpolating between these endpoints is a consumer's approximation, not an additional measured
Just-rate curve.

### deck

| Field | Content |
|---|---|
| `model` | the native model's description: engine, play, score formula, measurement and check powers, effect unit value, nominal expectation law, rank approximation, Perfect play, Free Live and single-shape aptitude |
| `kinds[]` | ordinary score-up kinds from `MasterLiveSkillEffect` types 2000, 2002, 2004 or 2005 without a cumulative condition. Each has a continuous zero-based `id`, effect type, activation time, duration, targets, condition/release/reset groups, effect limits, source `rows` and distinct sorted `values` |
| `gekisouAptitude` | the independently validated single-shape catalog below, or null when aptitude is disabled |

A kind's duration is `ceil(activationTimeSecond * 1000f)` milliseconds. Match a card's ordinary live-skill effect at
its actual level to the kind's structural fields. Kind ids and shape ids are indices in the current document;
they are not stable identifiers across master-data releases.

### charts[].deck

| Field | Content |
|---|---|
| `convertedNoteCount` | the converted note count used by the score formula |
| `skip` | points per unit of power for a skipped live: every note Great, combo 0, no skills |
| `events` | `[[position, timeMs], ...]` in chart order |
| `positions` | largest event position plus one; the size of each position dimension |
| `ranges[]` | `index`, `mission` (1 combo, 2 luck, 3 Just count), `startMs`, `endMs`, `rankBonusPercents` for ranks 1–5, and the rank-1 `rankBonusPercent` |
| `justNotes` | notes judged Just on the best Gekisou play |
| `expectation` | the expected Gekisou statistics below, or null when unplayable |
| `replaySeeds` | concrete replay seeds, empty when unplayable |
| `offSeeds` | exactly one deterministic Free Live result: seed 0, integer `score`, scalar `weights[kind][position]`, and `check` with `deck`, integer `exact`, scalar `predicted` and `bound`; a weight row is null when the kind reads unavailable Gekisou state |
| `unplayable` | null, or why Gekisou cannot play this chart; a fourth fever is unsupported by the game. Free Live statistics remain present |
| `gekisouAptitude` | per-range factors and single-shape increments below, or null |

The expectation object contains:

| Field | Content |
|---|---|
| `score`, `scorePerfect` | Estimate of no-skill points, including rank-1 bonuses, on the best and Perfect plays |
| `ranges[]` | `rangeScore`, `rankBonus`, `rankBonusPerfect`, `rangeScorePerfect` and `luckPoints` as Estimates; deterministic integer `maxCombo` and `justCount`; four `lotResults` Estimates in Miss, Hit, Super Hit, Critical order |
| `weights` | `[kind][position]` Estimates of points per unit of deck power and effect factor |
| `rangeWeights` | `[kind][position][range]` Estimates for rank adjustment, or null; individual kind rows may be null |
| `check` | `deck`: `[kind, value]` or null at each position; `ranks: null`; `expected` and `predicted` Estimates in points at `model.checkPower`; scalar nonnegative `bound` |
| `rankCheck` | the same check deck evaluated at ranks 1–5 per range; the same Estimate fields and scalar bound, or null when no rank check applies |

The exporter checks native chart identities and times against the chart facts, and checks each range's rank
percentages against `MasterLiveGekisouRankingScoreBonus`. It validates array dimensions, finite estimates,
nonnegative radii, deterministic counters, replay seeds, unavailable rank domains, best/Perfect agreement without
Just notes, and all check bounds. Checks compare the most distant endpoints; the serialized check allows only
the outward millipoint rounding expansion of its two intervals in addition to the native bound. Lottery counts
and luck points outside a luck range must enclose zero.

### Gekisou skill aptitude

`deck.gekisouAptitude` describes the shapes measured, and each chart's `deck.gekisouAptitude` describes the expected
increment with **one** such shape equipped. Ordinary `expectation` has no card Gekisou skills. Single-shape gains
cannot be added to estimate several interacting skills; complete formations still require simulation.

The header has `plainKind`, `host`, `law` and `shapes`. `plainKind` is the ordinary, unconditional, whole-team
five-second score-up kind used for cross terms, or null. `host` describes the paired member used for a support
skill. `law` is exactly `independent nominal lottery and skill probabilities`.

Each shape has a continuous zero-based `id`, `source` (`member` or `support`), `mission` (1 combo, 2 luck, 3 Just,
4 all), `bandCondition`, normalized `effects` and `skills`. Each skill reference is
`{id, level, memberTargetIds, bandIds}` and joins to the Gekisou catalog for its text. Members use their skill's
highest level; snaps use their support skills' levels at the highest rank. Effects preserve master order and
expose type, trigger, duration, value, limits, targets, four condition groups and cumulative condition, as specified
by the Schema. Equivalent parameters share a shape. Support condition 5000's target is normalized away;
`memberTargetIds` and `bandIds` retain the targets and bands for matching, or are null without that condition.

A chart's aptitude is `{factors, variants}`, or null when disabled, unplayable, without ranges, or without shapes.
`factors` has one object per range: deterministic `judgedNotes`, `justNotes`, `perfectNotes`, `tailNotes` and
`comboAtStart`, plus an Estimate of baseline `lotteries`. That interval must agree with the sum of the baseline
range's four expected lottery-result counts. The factors of overlapping ranges may refer to the same notes.

Variants appear in shape-id order for the chart's missions and mission 4. A band-conditioned shape has a
`bandMatch: true` variant followed by `false`; other shapes use null.

| Field | Content |
|---|---|
| `shape`, `bandMatch` | the current shape index and measured band condition |
| `score`, `scorePerfect` | Estimate of the total point increment on the best and Perfect plays |
| `tail`, `tailPerfect` | Estimate of the increment outside the range scores and their bonuses |
| `converted` | deterministic integer converted-note increment as `[value, 0]` |
| `ranges[]` | Estimates of `rangeScore`, `rankBonus`, `rankBonusPerfect`, `rangeScorePerfect` and `luckPoints`; deterministic integer `maxCombo` and `justCount` increments as `[value, 0]` |
| `weights` | `[position]` Estimates of cross terms with the plain kind, or null without it |
| `rangeWeights` | `[position][range]` Estimates of cross terms, or null without a plain kind or when the chart or shape cannot use linear rank adjustment |
| `check` | plain-kind `deck`, explicit `ranks`, expected/predicted point Estimates and scalar bound; a nonlinear-rank shape is checked at rank 1 in every range |

Point increments are measured at `deck.model.power`. Their intervals must be consistent with
`score = tail + sum(rangeScore + rankBonus)` and
`scorePerfect = tailPerfect + sum(rangeScorePerfect + rankBonusPerfect)`. These are interval identities, not
identities between rounded centers. A shape that reads confirmed rank in any condition group or cumulative
condition has no linear rank cross terms, even when the chart's ordinary range weights are available.

The exporter independently checks the shape catalog against the complete master data, including skill and level
coverage, normalized effects, member targets and bands. It then checks variant order and coverage, factor counts,
interval identities, dimensions, deterministic integer increments and expectation bounds. Aptitude has no
sample-count, standard-error target or convergence-cap fields in this format.

## The deck input (`--full`)

With `--full` the file ends with the input the deck model reads: `master`, the master data tables about cards,
skills, bonuses, scores and events, and `charts`, every chart as the client builds it at runtime. They hold facts as
the game has them (master data values as served, notes as the client's chart converter creates them), not values
derived from them.

### master

Each table is `{"columns": [...], "rows": [[...], ...]}`: one array per row, its values in the order of `columns`,
rows in the order the master data lists them. Values are as decoded: integers, strings, booleans, arrays; a binary32
column should be parsed as 32-bit floats.

Tables with a column list export those columns, and every row must have them. Tables marked "all" export every
column their rows have, in the order the rows first have them; when such a table has no rows, `columns` is empty
(the master data carries no field list for an empty table). A reader treats a table missing from `master` as empty.
`MasterCharacter._isNonPlayable` is the game's flag for a non-playable character; a row without it is written as
`false`.

| Table | Columns |
|---|---|
| `MasterMemberCard` | `_id` `_characterID` `_rarity` `_cardType` `_bestMusicTagIDs` `_performancePowerMax` `_technicPowerMax` `_visualPowerMax` `_memberCardLevelGroup` `_memberCardAwakeGroup` `_memberCardRankGroup` `_leaderSkillID` `_liveSkillID` `_gekisouSkillID` |
| `MasterMemberCardLevel` | `_id` `_group` `_level` `_exp` `_performanceRate` `_technicRate` `_visualRate` |
| `MasterMemberCardLevelLimit` | `_id` `_rarity` `_awakeCount` `_limitLevel` |
| `MasterMemberCardAwake` | `_id` `_group` `_awakeCount` `_performanceRate` `_technicRate` `_visualRate` |
| `MasterMemberCardRank` | `_id` `_group` `_rank` `_performanceRate` `_technicRate` `_visualRate` `_leaderSkillLevel` `_musicTypeBonusRate` `_musicTagBonusRate` |
| `MasterSupportCard` | `_id` `_characterIDs` `_rarity` `_cardType` `_performancePowerMax` `_technicPowerMax` `_visualPowerMax` `_supportCardLevelGroup` `_supportCardRankGroup` `_supportSkillId01` `_supportSkillId02` `_gekisouSupportSkillId01` `_gekisouSupportSkillId02` |
| `MasterSupportCardLevel` | `_id` `_group` `_level` `_exp` `_performanceRate` `_technicRate` `_visualRate` |
| `MasterSupportCardRank` | `_id` `_group` `_rank` `_limitLevel` `_cardTypeLinkBonusRate` `_supportSkill01Level` `_supportSkill02Level` `_gekisouSupportSkill01Level` `_gekisouSupportSkill02Level` |
| `MasterCharacter` | `_id` `_bandID` `_isNonPlayable` |
| `MasterBand` | `_id` |
| `MasterCharacterRank` | `_id` `_rank` `_exp` `_bonus` |
| `MasterCharacterTotalRank` | `_id` `_totalRank` `_bonus` |
| `MasterBandItemSkillEffect` | `_id` `_bandItemId` `_level` `_skillTargetIDs` `_skillEffectType` `_effectValue` |
| `MasterBandItem` | `_id` `_bandId` |
| `MasterBandItemLevel` | `_id` `_bandItemId` `_level` `_playerRank` |
| `MasterVip` | `_id` `_vipRank` |
| `MasterVipRankBonus` | `_id` `_vipRank` `_vipBonusType` `_value` |
| `MasterMemoryMusicGroup` | `_id` `_skillTargetIds` |
| `MasterMemoryMusic` | `_id` `_groupId` |
| `MasterMemoryMusicBonus` | `_id` `_groupId` `_scoreRank` `_performance` `_technic` `_visual` |
| `MasterMemoryMemberLevel` | `_id` `_point` `_performance` `_technic` `_visual` |
| `MasterMemorySupportLevel` | `_id` `_point` `_performance` `_technic` `_visual` |
| `MasterSkillTarget` | `_id` `_skillTargetType` `_characterID` `_bandID` `_cardType` `_tagID` `_judgement` `_liveMusicType` `_gekisouMissionType` `_liveSkillCategories` `_gekisouSkillCategories` |
| `MasterSkillCondition` | `_id` `_conditionType` `_conditionValues` `_isPositive` `_conditionTargetIDs` |
| `MasterSkillConditionSet` | `_id` `_group` `_conditionIds` |
| `MasterSkillCumulativeCondition` | `_id` `_skillCumulativeConditionType` `_conditionValues` `_conditionTargetIDs` `_maxCumulativeCount` |
| `MasterSkillEffectSetting` | `_id` `_skillEffectType` `_phase` |
| `MasterLeaderSkillEffect` | `_id` `_leaderSkillID` `_level` `_skillConditionGroup` `_skillTargetIDs` `_skillEffectType` `_effectValue` `_skillCumulativeConditionID` |
| `MasterLiveSkill` | `_id` `_skillCategories` |
| `MasterLiveSkillEffect` | `_id` `_liveSkillID` `_level` `_skillConditionGroup` `_skillReleaseConditionGroup` `_skillTargetIDs` `_skillEffectType` `_activationTimeSecond` `_effectValue` `_maxEffectValue` `_effectLimitCount` `_skillCumulativeConditionID` `_effectExecuteLimitCount` `_effectExecuteLimitResetConditionGroup` |
| `MasterSupportSkill` | `_id` |
| `MasterSupportSkillEffect` | `_id` `_supportSkillID` `_level` `_skillTriggerConditionGroup` `_skillTriggerType` `_skillConditionGroup` `_skillReleaseConditionGroup` `_skillTargetIDs` `_skillEffectType` `_activationTimeSecond` `_effectValue` `_maxEffectValue` `_effectLimitCount` `_skillCumulativeConditionID` `_effectExecuteLimitCount` `_effectExecuteLimitResetConditionGroup` |
| `MasterGekisouSkill` | `_id` `_gekisouMissionType` `_skillCategories` |
| `MasterGekisouSkillEffect` | `_id` `_gekisouSkillID` `_level` `_skillTriggerConditionGroup` `_skillTriggerType` `_skillConditionGroup` `_skillReleaseConditionGroup` `_skillTargetIDs` `_skillEffectType` `_activationTimeSecond` `_effectValue` `_maxEffectValue` `_effectLimitCount` `_skillCumulativeConditionID` `_effectExecuteLimitCount` `_effectExecuteLimitResetConditionGroup` |
| `MasterGekisouSupportSkill` | `_id` `_gekisouSupportSkillExecTiming` `_gekisouMissionType` |
| `MasterGekisouSupportSkillEffect` | `_id` `_gekisouSupportSkillID` `_level` `_skillTriggerConditionGroup` `_skillTriggerType` `_skillConditionGroup` `_skillReleaseConditionGroup` `_skillTargetIDs` `_skillEffectType` `_activationTimeSecond` `_effectValue` `_maxEffectValue` `_effectLimitCount` `_skillCumulativeConditionID` `_effectExecuteLimitCount` `_effectExecuteLimitResetConditionGroup` |
| `MasterLiveNoteParameter` | `_id` `_noteOperateType` `_scorePercent` |
| `MasterLiveJudgementParameter` | `_id` `_noteSimulateJudgement` `_scorePercent` `_damage` |
| `MasterLiveJudgementTiming` | `_id` `_assistLevel` `_judgementPriority` `_noteJudgementType` `_noteSimulateJudgement` `_beforeMs` `_afterMs` |
| `MasterLiveComboScoreBonus` | `_id` `_comboBonusType` `_requiredComboCount` `_bonusFactor` |
| `MasterLiveSettings` | all |
| `MasterParameter` | all |
| `MasterLiveGekisouLuckBasePoint` | `_id` `_noteCategory` `_noteSimulateJudgement` `_weight` `_basePoint` |
| `MasterLiveGekisouLuckBonusLot` | `_id` `_chanceLotType` `_lotResult` `_weight` |
| `MasterLiveGekisouRankingScoreBonus` | `_id` `_missionPattern` `_rank` `_count` `_scoreBonusPercent` |
| `MasterLiveMusic` | `_id` `_musicType` `_bestMusicTagIDs` `_liveScoreRankGroup` `_easyID` `_normalID` `_hardID` `_expertID` `_gekisouMission1` `_gekisouMission2` `_gekisouMission3` |
| `MasterLiveMusicScore` | `_id` `_musicScoreTextFileName` `_musicScoreLevel` `_fullComboCount` |
| `MasterArenaMusic` | all |
| `MasterChallengeMusic` | all |
| `MasterLiveScoreRank` | `_id` `_group` `_liveScoreRank` `_requiredScore` `_battleLiveRequiredScore` |
| `MasterEvent` | all |
| `MasterEventEffect` | all |
| `MasterEventAchievementReward` | `_id` `_eventId` `_eventPoint` `_rewardIds` |
| `MasterEventAchievementLoopReward` | `_id` `_eventId` `_loopStartEventPoint` `_loopEventPoint` `_rewardIds` |
| `MasterLiveEventReward` | `_id` `_group` `_eventGroup` `_scoreRank` `_resourceType` `_resourceId` `_resourceCount` `_probability` |
| `MasterChallengeLiveEventReward` | `_id` `_group` `_eventGroup` `_scoreRank` `_resourceType` `_resourceId` `_resourceCount` `_probability` |
| `MasterLiveEventPoint` | all |
| `MasterChallengeLiveEventPoint` | all |
| `MasterLiveChallengePoint` | all |
| `MasterLiveMusicBoostBonus` | `_id` `_consumedLiveBoostCount` `_liveMusicRewardRate` `_playerExpRate` `_memberCardExpRate` `_friendshipExpRate` `_eventPointRate` |
| `MasterChallengeMusicBoostBonus` | all |

### charts

One chart per `MasterLiveMusicScore` row, sorted by `scoreId`, including charts of no song. Song and score facts
are not copied into these records: they are in `songs` and in `master`, joined by `scoreId`.

| Field | Content |
|---|---|
| `scoreId` | `MasterLiveMusicScore._id` |
| `asset.key` | `Live/MusicScore/<_musicScoreTextFileName>`, the chart's TextAsset |
| `asset.sha256` | SHA-256 of the TextAsset's bytes as shipped |
| `notes.id` | note id, unique within the chart |
| `notes.op` | `NoteOperateType` |
| `notes.judgementType` | `NoteJudgementType` (from the operate type and the critical flag) |
| `notes.timeMs` | note time in chart milliseconds |
| `skillEvents.timeMs` | skill event times in chart order; the position in the array is the event index (the times need not ascend) |
| `fevers.startMs`, `fevers.endMs` | fever ranges sorted by start; the position in the arrays is the range index |

The notes are columns: `notes.id[i]`, `notes.op[i]`, `notes.judgementType[i]` and `notes.timeMs[i]` describe the
same note, and the four arrays have the same length; `fevers.startMs[i]` and `fevers.endMs[i]` are one range. The
notes are every note the client creates at runtime, including hidden notes, guide notes, and the combo ticks of
slides (operate types Combo and ComboSkip), in the order the client enumerates its note dictionary. That order is
not the time order: the combo ticks of a slide that fall on a time no other note has come after the slide's end.
Order by `timeMs` (then `id`) where time order is needed.

The deck model reads the same content under the format name `nnnotes.deck-data/1` (`format`, `provenance`,
`master`, `charts`); nnnotes hands it over in memory.

## Building

The deck model is the Python package [ournotes-sim](https://pypi.org/project/ournotes-sim/) that each ournotes-deck
release publishes: the Rust crate ournotes-sim of that release as an extension module, one wheel per platform. The
extra `deck` pins one release (`pip install 'nnnotes[deck]'`, or `pip install -e ".[deck]"` in a checkout), so the
same nnnotes version always carries the same deck model: the pin moves only through a pull request
(`.github/workflows/deck.yml` opens one when ournotes-deck publishes a newer release with its WASM packages and its
wheels), and `provenance.deck.version` and `provenance.deck.commit` name the release in every file. `provenance.deck.sourceSha256` is the SHA-256 of the deck model's sources (`ournotes_sim::SOURCE_SHA256`):
releases with the same value measure the same statistics. Replay bundles take the engines of the same release:
the replay and recommendation engines of `--replay-engine` and `--recommend-engine` are its WASM packages, whose
`build-info.json` names the release commit that `provenance.deck.commit` names; engines of another release are
rejected.

`--replay-dir OUT/replay --replay-engine PKG [--recommend-engine PKG]` writes normalized runtime inputs, the pinned
model's WASM release packages (the replay engine and, optionally, the recommendation engine) and same-snapshot Snap
label/reference-card metadata as described in [replay.md](replay.md). The music data stays compact and carries
the SHA-bound `replay.manifestUrl` pointer; its manifest optionally references `snap-labels.json`. The selected
decoded label tables retain names/templates, original per-level effects and artwork IDs. No encrypted master/chart
blobs or native binary are included in this artifact bundle. Label metadata does not change the score model.

## Versions

`format` names the major version. Within `nnnotes.music-data/2`, fields and tables are only added, never renamed,
removed or given another meaning, and readers ignore keys they do not know. Readers must reject an unknown major
version. This version carries `ournotes-deck.chart-stats/3` in `provenance.deck.format` and requires that exact
statistics format from the extension module.
