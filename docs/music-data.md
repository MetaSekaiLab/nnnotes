# Music data

`nnnotes music-data` writes one JSON file with every live song and chart of one master data version: what a song
listing shows (titles and credits in every language, bands, vocal characters, category, tags, release time, score
ranks, jacket, BGM length), the Gekisou catalog (member cards, snaps and their Gekisou skills), per difficulty the
chart facts (level, note counts, BPM, chart times, skill events, fevers), and per chart its **deck statistics**: what
the chart contributes to the live score whatever the deck, in a solo live (Gekisou off) and in a Gekisou live at every
rank, measured by the deck model [ournotes-deck](https://github.com/empty-sekai/ournotes-deck), which nnnotes carries
as its extension module `nnnotes._deck`. The format is `nnnotes.music-data/2`; its JSON Schema is
[schema/music-data.schema.json](schema/music-data.schema.json).

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
  skills, bonuses, scores and events ([the deck input](#the-deck-input---full)), for tools that run a deck model of
  their own.
- `--no-deck`: leave every chart's `deck` null. `--workers N` sets the measurement threads
  (default: every processor). `--seeds N` sets the replay seed count for a chart with a luck range (default 8);
  nominal expectation measurements are independent of this count.
- `--no-gekisou-aptitude`: omit single-skill aptitude measurements while retaining baseline statistics.
- `--stats-cache DIR`: keep every chart's deck statistics in `DIR`, one file per chart named by the SHA-256 of what
  they are a function of: the deck model's sources (`provenance.deck.sourceSha256`), `--seeds` and the aptitude
  options, the master data tables of the deck input and the chart's runtime notes. A chart found there is not measured
  again, so an export after a deck model release that keeps the model's sources, or after a master data update that
  keeps those tables, measures only the charts that changed. `DIR` then holds this file's charts only.
- `--no-bgm`: do not read the cue sheets (every `bgm.length` is null).
- `--jackets DIR`: also write every song's jacket, the Texture2D `Image/Jacket/<jacket>`, as `DIR/<jacket>.webp`
  (WebP quality 88, scaled down with Lanczos to at most 320 pixels on the longer side, without alpha when opaque); a
  page next to the file finds a song's jacket at `<DIR>/<song.jacket>.webp`.

Each master data file is checked against the SHA-256 the manifest lists and decoded with `[master] key` and `iv`.
With `--decoded-master` the tables are read as decoded, and the master data version and each file's SHA-256 are the
manifest's: the decoded tables cannot be checked against the files as served, so the file records what the manifest
lists (the same values as `--master-files` on those files).
Charts are read from the catalog of `[catalog] language` (bundles fetched into the cache as for every command); the
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
| `catalog.resourceVersion` | the resource version used to select the downloaded catalog, or recorded for an explicit file in the catalog store (`nnnotes catalogs fetch` / `import`); null when unknown |
| `catalog.sha256` | SHA-256 of the remote catalog file the charts were read with |
| `master.source` | `api` (`--master-files`, `--decoded-master`: the region's files) or `embedded` (`--apk-master`) |
| `master.version` | the `version` of the master data manifest |
| `master.tables.<Table>.sha256` | SHA-256 of each table's file as served, before decoding: the song tables (`MasterLiveMusic`, `MasterLiveMusicScore`, `MasterText`, `MasterBand`, `MasterCharacter`, `MasterTag`, `MasterLiveMusicCategory`, `MasterSound`, `MasterSoundCueSheet`, `MasterLiveScoreRank`), the tables of the Gekisou catalog (`MasterMemberCard`, `MasterSupportCard`, `MasterSupportCardRank`, `MasterGekisouSkill`, `MasterGekisouSkillEffect`, `MasterGekisouSupportSkill`, `MasterGekisouSupportSkillEffect`) and, when the deck model runs or with `--full`, the tables of [the deck input](#the-deck-input---full) |
| `exporter.name`, `exporter.version` | `nnnotes` and its version |
| `exporter.chartFormat` | the format of the chart converter the notes come from, `nnnotes.live-score/1` |
| `deck` | the deck model: `{name, version, source, commit, format}`, `ournotes-deck`, the package version of its crate ournotes-sim, the repository, the git commit nnnotes is built with and the statistics format (`ournotes-deck.chart-stats/3`); null with `--no-deck` |

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

The deck model reports `ournotes-deck.chart-stats/3`. Gekisou measurements use independent nominal
lottery and skill probabilities. An interval is `[center, half-width]`, enclosing the expectation under
that law with outward probability arithmetic and score rounding bounds. Deterministic judgement counters
are exact integers. The law describes the model; agreement with a particular game's seed distribution
is a separate question.

Serialized point centers use millipoints, consumed result counts use five fractional digits, and weights
use twelve. Half-widths round outward at the same precision and include the center's rounding.

The best play judges each note at its exact time, Just in Just-count ranges and Perfect elsewhere.
The Perfect play supplies the other endpoint, with every Just judged Perfect. Both include rank 1 bonuses.
Free Live has its own deterministic Perfect run, without Gekisou, and remains available on charts with
more than three fever ranges.

### deck

`model` describes the play, probability law, measurement `power`, `checkPower`, `unitValue` and score formulas.
`kinds` groups ordinary score-up rows of type 2000, 2002, 2004 or 2005 by duration, targets, conditions,
release and execution limits. A weight is the expected increment per unit of deck power and effect factor.
Type 2000 uses `value / 10000`; 2002 adds its intrinsic half factor; 2004 and 2005 use the effect's
half-to-even rounded activation factor. A card's kind is identified by its effect row's parameters.

### charts[].deck

| Field | Content |
|---|---|
| `convertedNoteCount`, `skip` | the score divisor and skipped-live score per unit of power |
| `events`, `positions` | `[position, timeMs]` skill events and the number of measured performance positions |
| `ranges` | index, mission, start/end times and `rankBonusPercents` for ranks 1..5 |
| `justNotes` | exact Just count of the best play |
| `expectation` | `score`, `scorePerfect`, `ranges`, `weights`, `rangeWeights`, `check`, `rankCheck`; null when Gekisou is unplayable |
| `replaySeeds` | seeds for per-play replay: `[0]` without a luck range, otherwise the first `--seeds` published seeds; empty when Gekisou is unplayable |
| `offSeeds` | one deterministic Free Live run: `seed: 0`, integer `score`, numeric `weights` and `{deck, exact, predicted, bound}` check |
| `unplayable` | null or the reason Gekisou cannot play the chart |

`expectation.score` and `scorePerfect` are point intervals at `model.power`. Each expected range has
`rangeScore`, `rangeScorePerfect`, `rankBonus`, `rankBonusPerfect` and `luckPoints` intervals, integer
`maxCombo` and `justCount`, and four `lotResults` intervals: expected consumed Miss, Hit, Super Hit and
Critical counts. `weights[kind][position]` and `rangeWeights[kind][position][range]` hold intervals in
the weight unit. A range-weight kind is null when its conditions read confirmed rank; the whole field
is null when overlapping ranges prevent a linear rank transformation.

A check has `{deck, ranks, expected, predicted, bound}`. Its deck contains `[kind, value]` or null per
position, its score intervals use `checkPower`, and the greatest separation of their endpoints must be
within `bound`. The baseline check uses `ranks: null`; `rankCheck` supplies explicit fixed rank arrivals
and is null when no linear rank check is available. The exporter checks chart facts, dimensions, finite
intervals, counters, replay seeds and every check before writing the file.

### Ranks and accuracy

For disjoint ranges whose effects do not read confirmed rank, an arbitrary-rank expectation uses

```
score_r = score - sum(rankBonus_i) + sum(E[trunc(rangeScore_i * p_i(r_i) / 100)])
weight_r = weight + sum((p_i(r_i) - p_i(1)) / 100 * rangeWeight_i)
```

The expected truncation can be enclosed by scaling the range-score expectation and adding a rounding
allowance. Truncating the center alone does not compute an expected rank bonus. At rank 1 the measured
bonus interval is available directly. `rankBonusPerfect` supplies the Perfect endpoint's bonus.
Just-rate interpolation and Great-share scaling are accuracy approximations; the summary does not
describe arbitrary misses or combo breaks. Per-note replay runs an explicit play with its own seed.

### Gekisou skill aptitude

The file's `deck.gekisouAptitude` has `plainKind`, `host`, `law` and `shapes`. Each shape groups equivalent
score-relevant parameters and retains its source, mission, band condition, normalized effect rows and
skill/level references. Member skills use their highest level; support skills use the highest Snap rank's
level. Support target differences share a shape while each skill retains `memberTargetIds` and `bandIds`.
A support skill is measured with an empty member skill of the same mission.

Each chart has `{factors, variants}` or null when there are no applicable ranges/shapes or aptitude is
disabled. Factors give judged, Just, Perfect and tail note counts, starting combo and the expected number
of consumed lotteries (`lotteries`, an interval). Each applicable shape has one variant, or matching and
nonmatching band variants. A variant gives:

| Field | Content |
|---|---|
| `score`, `scorePerfect`, `tail`, `tailPerfect`, `converted` | expected gains with the skill minus without it; `[center, half-width]` |
| `ranges` | `rangeScore`, `rankBonus`, `rangeScorePerfect`, `rankBonusPerfect`, `maxCombo`, `justCount`, `luckPoints` gain intervals |
| `weights` | the plain kind's weight changes, `[position][interval]`; null without that kind |
| `rangeWeights` | its range-weight changes, `[position][range][interval]`; null outside the linear domain |
| `check` | the full expected score and linear prediction for a random plain-skill deck at explicit ranks |

The score and tail centers satisfy `tail = score - sum(rangeScore + rankBonus)`, and the Perfect endpoint
uses the corresponding Perfect fields. Interval radii propagate by outward arithmetic. Conversion,
combo and Just counters have zero radius after proving their independence from lotteries.

Each variant measures one skill alone. Several gains cannot be added to estimate combined skills:
combo caps, lottery gauges, Rush and Just-triggered support effects interact. Plain-skill cross weights
are measured on the best play. Below 100% Just, a page can interpolate the no-ordinary-skill endpoints;
it cannot derive complete cross weights at that accuracy from this summary. A full play uses replay.
The exporter verifies shape coverage, normalized effect rows, targets, missions, variant ordering,
intervals, tail identities and expectation checks against the supplied master data.

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
| `MasterCharacter` | `_id` `_bandID` |
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

The deck model is the Rust crate ournotes-sim of the ournotes-deck repository, pinned to the commit of an ournotes-deck
release in `rust/Cargo.toml`
(and `rust/Cargo.lock`) and built into the extension module `nnnotes._deck` with [maturin](https://www.maturin.rs/)
(PyO3, the stable ABI of Python 3.11 and later: one wheel per platform). The release workflow builds the wheels;
`pip install .` or `pip install -e .` in a checkout builds the module with the Rust toolchain. The same nnnotes
version always carries the same deck model: the commit moves only through a pull request (`.github/workflows/deck.yml`
opens one when ournotes-deck publishes a newer release with its WASM packages), and `provenance.deck.commit` names it in
every file. `provenance.deck.sourceSha256` is the SHA-256 of the deck model's sources (`ournotes_sim::SOURCE_SHA256`):
releases with the same value measure the same statistics. The replay and recommendation engines of `--replay-engine` and `--recommend-engine` are that release's WASM
packages.

## Versions

`format` names the major version. `nnnotes.music-data/2` carries deck statistics
`ournotes-deck.chart-stats/3`. Additive fields and tables retain their existing meanings within a major
version; readers ignore unknown keys and reject unsupported major versions. Deck model provenance names
the exact statistics format and source digest. The optional runtime input keeps `nnnotes.deck-data/1`.

`--replay-dir OUT/replay --replay-engine PKG [--recommend-engine PKG]` writes normalized runtime inputs and the pinned model's WASM release packages (the replay engine and, optionally, the recommendation engine) as described in [replay.md](replay.md). The music data stays compact and carries the SHA-bound `replay.manifestUrl` pointer. No original chart/master blobs or native binary are included in this artifact bundle.
