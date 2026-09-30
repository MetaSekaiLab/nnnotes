# Music data

`nnnotes music-data` writes one JSON file with every live song and chart of one master data version: what a song
listing shows (titles and credits in every language, bands, vocal characters, category, tags, release time, score
ranks, jacket, BGM length), the Gekisou catalog (member cards, snaps and their Gekisou skills), per difficulty the
chart facts (level, note counts, BPM, chart times, skill events, fevers), and per chart its **deck statistics**: what
the chart contributes to the live score whatever the deck, in a solo live (Gekisou off) and in a Gekisou live at every
rank, measured by the deck model [ournotes-deck](https://github.com/empty-sekai/ournotes-deck), which nnnotes carries
as its extension module `nnnotes._deck`. The format is `nnnotes.music-data/1`; its JSON Schema is
[schema/music-data.schema.json](schema/music-data.schema.json).

```
nnnotes music-data (--master-files DIR | --apk-master | --decoded-master) [--full] [--no-deck] [--seeds N]
                   [--workers N] [--no-gekisou-aptitude] [--aptitude-max-seeds N] [--aptitude-cross-seeds N]
                   [--no-bgm] [--jackets DIR] -o FILE
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
- `--no-deck`: do not run the deck model; every chart's `deck` is null. The deck model measures every chart on a
  whole-live simulation, dozens of lives per chart: a full run takes processor time in proportion to the number of
  charts. `--workers N` sets the threads it uses (default: every processor), `--seeds N` the seeds measured on a chart
  with a luck range (default 8).
- `--no-gekisou-aptitude`: keep the existing deck statistics but skip single-skill aptitude measurements.
  `--aptitude-max-seeds N` caps their samples (default 65536), and `--aptitude-cross-seeds N` caps cross-term samples
  (default 64). Lower caps reduce work but may leave the standard-error target unmet.
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
  "format": "nnnotes.music-data/1",
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
| `catalog.resourceVersion` | the resource version recorded for the catalog in the catalog store (`nnnotes catalogs fetch` / `import`), null when none is recorded |
| `catalog.sha256` | SHA-256 of the remote catalog file the charts were read with |
| `master.source` | `api` (`--master-files`, `--decoded-master`: the region's files) or `embedded` (`--apk-master`) |
| `master.version` | the `version` of the master data manifest |
| `master.tables.<Table>.sha256` | SHA-256 of each table's file as served, before decoding: the song tables (`MasterLiveMusic`, `MasterLiveMusicScore`, `MasterText`, `MasterBand`, `MasterCharacter`, `MasterTag`, `MasterLiveMusicCategory`, `MasterSound`, `MasterSoundCueSheet`, `MasterLiveScoreRank`), the tables of the Gekisou catalog (`MasterMemberCard`, `MasterSupportCard`, `MasterSupportCardRank`, `MasterGekisouSkill`, `MasterGekisouSkillEffect`, `MasterGekisouSupportSkill`, `MasterGekisouSupportSkillEffect`) and, when the deck model runs or with `--full`, the tables of [the deck input](#the-deck-input---full) |
| `exporter.name`, `exporter.version` | `nnnotes` and its version |
| `exporter.chartFormat` | the format of the chart converter the notes come from, `nnnotes.live-score/1` |
| `deck` | the deck model: `{name, version, source, commit, format}`, `ournotes-deck`, its package version, repository, the git commit nnnotes is built with and the statistics format (`ournotes-deck.chart-stats/2`); null with `--no-deck` |

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

The deck model plays every chart of a song on its whole-live simulation in the theoretical best play, the frame
times of the game's default schedule, in two scenarios:

- **Gekisou on** (`seeds`), as a Gekisou live (Battle Live, up to five players) plays: every judged note at its exact
  time, Just inside the Just-count ranges and Perfect elsewhere, rank 1 in every Gekisou range. Other ranks follow
  from the same numbers (below).
- **Gekisou off** (`offSeeds`), as a solo live (Free Live, Challenge Live) plays: every judged note Perfect at its
  exact time, seed 0, no Just, luck, Gekisou combo or rank bonus. A chart with more than three fevers plays here too.

In each it measures, at deck power `model.power` (300000):

- `score`: the exact score without skills (with Gekisou on, the rank 1 bonuses of the Gekisou ranges included);
- for every **score-up kind** (`deck.kinds`) and every performance position `k`, `weights[kind][k]`: the exact score
  a deck gains when its position-`k` member has one effect of that kind at factor 1, divided by the deck power. The
  effect runs through the simulation's own updaters, conditions, frames and appliers, so the weight carries every rule
  of the game (execute and finish frames, the 40 ms score frames, combo and Gekisou combo factors, Just scores, luck
  rushes, the rank bonuses of the ranges it overlaps).

A deck whose live skills are all score-up kinds then scores, up to the floors,

```
P * (score / power + sum over positions k of factor_k * weights[kind_k][k])
```

with `P` the deck power and `factor_k` the effect's factor as the game's applier converts its value: effect type 2000
`floor(value / 10000f * 1e5) / 1e5`, 2005 `floor(value / -10000f * 1e5) / 1e5`, 2002 and 2004 the same quotient
rounded half to even (`value / 10000f` in binary32). Each seed also plays a **check deck**, random kinds at real
master values at another power (`model.checkPower`), and the command fails when its exact score leaves the bound of
the formula. Effects of other types (cumulative score 2001 / 2003, life, judgement conversion, Gekisou and snap
skills) are not linear in the chart alone and have no weights: a deck's score with them comes from the simulation.

A luck range draws lottery results from the play's random seed, so the Gekisou on measurements are given **per
seed**: one seed (0) when no range is a luck range, else the first `--seeds` seeds of the deck model's published seed
set. That set is a fixed sequence: candidate `k` is the low 32 bits (signed) of output `k + 1` of SplitMix64 started at
`0x6765_6B69_736F_7531` (`gekisou1`), kept when its pair of stream seeds (`|b|` and `|b ^ 0x9E3779B9|`, -2^31 as
2^31 - 1) differs from those of every seed kept before it, so a smaller set is a prefix of a larger one. The seed
set is not the game's seed law (which is unknown); a mean over it is not the game's expectation.

### Ranks

In a Gekisou live, range `i` takes a rank `r_i` from 1 to 5 among the room's players, and its rank bonus is
`trunc(rangeScore_i * p_i(r_i) / 100)` with `p_i(r) = ranges[i].rankBonusPercents[r - 1]`. The bonus is a fixed score
at the range's end: it changes no factor and no note score, and a later range's score holds it at both ends, so the
range scores do not depend on the ranks. At ranks `r` a seed's numbers are therefore

```
score_r            = score - sum_i rankBonus_i + sum_i trunc(rangeScore_i * p_i(r_i) / 100)      (exact)
weights_r[kind][k] = weights[kind][k] + sum_i (p_i(r_i) - p_i(1)) / 100 * rangeWeights[kind][k][i]
```

(`rankBonus_i`, `rangeScore_i`: `seeds[].ranges[i]`), and a deck scores `P * (score_r / power + sum_k factor_k *
weights_r[kind_k][k])` as above; `weights_r` is within `2 * ranges / power` per unit of factor of the exact weight at
those ranks. At rank 1 everywhere these are `score` and `weights`. `rangeWeights` is null when a range's bonus can fall
inside another range's score frames (overlapping ranges), and a kind's entry is null when its conditions read the
confirmed rank (condition 7012): the ranks do not follow linearly there. `rankCheck` plays the seed's check deck at
random ranks through the simulation's explicit rank confirmations and bounds it against these formulas.

### Just rate

`scorePerfect` and `ranges[i].rangeScorePerfect` are the no-skill score (rank 1 bonuses included) and the range
scores of the same play with every Just judged Perfect (the Just judgement is enabled only inside the Just-count
ranges, so nothing else changes). Between a Just rate of 1 (`score`, `rangeScore`) and 0 (`scorePerfect`,
`rangeScorePerfect`) a page can interpolate; the rank bonus of the Perfect play is `trunc(rangeScorePerfect_i *
p_i(r_i) / 100)`. A chart without Just notes (`justNotes` 0) has `scorePerfect == score`.

### deck

| Field | Content |
|---|---|
| `model` | the deck model's description of the measurement: `engine`, `play`, `score` (the formula), `power`, `checkPower`, `unitValue` (the effect value of factor 1, 10000), `seeds`, `ranks` (the rank formulas), `perfect` (the Perfect play), `off` (the Gekisou off scenario) |
| `kinds[]` | the score-up kinds of the master data: `MasterLiveSkillEffect` rows of type 2000, 2002, 2004 or 2005 without a cumulative condition, grouped by what shapes their score. `id` (the index in `weights`), `effectType`, `activationTimeSecond`, `durationMs` (`ceil(activationTimeSecond * 1000f)`), `skillTargetIds`, `skillConditionGroup`, `skillReleaseConditionGroup`, `effectLimitCount`, `effectExecuteLimitCount`, `effectExecuteLimitResetConditionGroup`; `rows` (master rows of the kind) and `values` (their distinct `_effectValue`s, ascending) |

The kind of a card's live skill is found by matching its `MasterLiveSkillEffect` row (at the skill level) on these
fields; `values` lists what the master data uses.

### charts[].deck

| Field | Content |
|---|---|
| `convertedNoteCount` | the note count the score formula divides by (converted notes) |
| `skip` | score per unit of deck power of a skipped live (every note Great, combo 0, no skills) |
| `events` | `[[position, timeMs], ...]`: the skill events in chart order with the performance position each fires |
| `positions` | the performance positions the events fire (the largest position + 1): the length of every `weights[kind]` |
| `ranges[]` | the Gekisou ranges: `index`, `mission` (1 combo, 2 luck, 3 Just count), `startMs`, `endMs`, `rankBonusPercents` (the rank bonus percentages of ranks 1..5 of the song's mission pattern, `MasterLiveGekisouRankingScoreBonus`), `rankBonusPercent` (the rank 1 percentage, `rankBonusPercents[0]`) |
| `justNotes` | notes judged Just on the Gekisou on play |
| `seeds[]` | Gekisou on, per seed: `seed`; `score` (points at `model.power`, rank 1 bonuses included); `ranges[]` (`rangeScore`: the points gained inside the range, `rankBonus`: its rank 1 bonus in points, `maxCombo`, `justCount`, `luckPoints`: the luck points gained (`TotalBonusPoint`; a combo, Just count or luck range ranks the room by `maxCombo`, `justCount` or `luckPoints`), `lotResults`: lottery results Miss, Hit, Super Hit, Critical, `rangeScorePerfect`: `rangeScore` on the Perfect play); `weights[kind][position]` (points per unit of deck power and of factor); `check` (`deck`: `[kind, value]` or null per position, `exact`, `predicted`, `bound`: points at `model.checkPower`); `scorePerfect` (`score` on the Perfect play); `rangeWeights[kind][position][range]` (range points per unit of deck power and of factor, or null; a kind null); `rankCheck` (`ranks`: 1..5 per range, `exact`, `predicted`, `bound`; null without ranges or range weights) |
| `offSeeds[]` | Gekisou off, one seed: `seed` (0), `score`, `weights[kind][position]` (a kind null when its conditions read the Gekisou state, which a solo live does not have) and `check`, as in `seeds[]` |
| `unplayable` | null, or why the game cannot play the chart with Gekisou (more than three fevers: the game fails when the fourth starts); `seeds` is then empty, `offSeeds` is not |

The deck model's chart facts are checked against the file's: the song, difficulty, level, judged note count, last
note time, music length, Gekisou missions, skill event times and fever ranges must agree, and are not repeated in
`deck`. Its numbers are checked against the master data and themselves: every range's `rankBonusPercents` are the
`MasterLiveGekisouRankingScoreBonus` rows of the song's mission pattern (0 without a row), every `rankBonus` is
`trunc(rangeScore * rankBonusPercent / 100)`, a chart without Just notes has the same scores on the Perfect play,
every array has its shape (`[kind][position]`, `[kind][position][range]`, one range result per range with its luck
points, one Gekisou off seed), the seeds are the chart's seed set (none on a chart unplayable with Gekisou, seed 0
without a luck range, else the first `--seeds` seeds of the published seed set, which nnnotes computes itself), and
every check and rank check is within its bound.

### Gekisou skill aptitude

`deck.gekisouAptitude` describes the shapes measured, and each chart's `deck.gekisouAptitude` describes how its
score changes with **one** such shape equipped. It does not select a deck, and increments measured separately must
not be added to estimate several skills together. The existing `deck.seeds` still measures Gekisou **without card
Gekisou skills**; a solo/free live uses `offSeeds`, without Gekisou. These are model results, not a guarantee that
they reproduce the game.

The file header has `plainKind` (the ordinary, unconditional, whole-team five-second score-up kind used for cross
terms, or null), `host` (how a support skill's paired member is measured), `seedRule` and `shapes`. A shape has a
continuous zero-based `id`, `source` (`member` or `support`), `mission` (1 combo, 2 luck, 3 Just, 4 all),
`bandCondition`, normalized `effects`, and `skills`. Each skill is `{id, level, memberTargetIds, bandIds}`; join its
id to `gekisouCatalog.skills` or `supportSkills` for its name. Members use their skill's highest level; snaps use
the support skill level at their highest rank. Effect rows preserve the master order and expose their effect,
trigger, duration, value, limits, targets, four condition groups and cumulative condition (the exact fields are in
the JSON Schema). Equivalent parameters share a shape. Support condition 5000's target is normalized away;
`memberTargetIds` and `bandIds` retain each skill's targets and bands for matching, or are null without that condition.

Each chart has `{factors, variants}`, or null when aptitude is disabled, the chart is unplayable with Gekisou, there
are no ranges, or the master has no shapes. `factors`, in range order, has `judgedNotes`, `justNotes`, `perfectNotes`,
`tailNotes`, `comboAtStart`, and `lotteries`: the mean and standard error of the number of lotteries without a skill,
on `deck.seeds` (zero outside luck ranges).

Variants are in shape-id order, only for the chart's missions or mission 4. A band-conditioned shape appears twice,
`bandMatch: true` then `false`; otherwise `bandMatch` is null. Each variant has:

| Field | Content |
|---|---|
| `shape`, `bandMatch` | shape and the measured band condition |
| `deterministic`, `seeds`, `seTargetMet`, `crossSeeds` | whether random dependencies were excluded and four initial plays agreed, number of measured seeds, whether the standard-error target was met, number used for cross terms |
| `score`, `scorePerfect`, `tail`, `tailPerfect`, `converted` | score increments, the increments after the ranges, and the conversion increment; each `[mean, standard error]` |
| `ranges` | one object per range: `rangeScore`, `rankBonus`, `rangeScorePerfect`, `maxCombo`, `justCount`, `luckPoints`, each `[mean, standard error]` |
| `weights` | cross terms `[position][mean, standard error]`, null without a plain kind |
| `rangeWeights` | cross terms `[position][range][mean, standard error]`, null without a plain kind or a linear rank model |
| `check` | first seed's check: `seed`, `ranks`, `deck` (plain kind/value or null per slot), `exact`, `predicted`, `bound` |

Point increments are measured at `deck.model.power`, using the same seed for the equipped and unequipped plays.
`score.mean = tail.mean + sum(rangeScore.mean + rankBonus.mean)`; **standard errors cannot be added this way**.
Point means and errors are rounded to 0.001. With R ranges this equality allows `(2+2R)*0.0005 + 1e-8` points of
independent rounding error. Weights, lottery statistics and the check's prediction and bound keep binary64 precision.
A deterministic result uses one seed and zero standard errors. Charts with luck ranges, effects 11000–11005,
or probability condition 4011 in any condition group always use random sampling, even if the first observations
agree; four matching observations alone do not establish determinism. Others expand through `seedRule.batches` (normally
32, 64, 128, 256, 512, 1024), stopping when the unrounded score error is at most the larger of `relative` (0.01) times
the absolute mean increment and `baseline` (0.001) times the no-skill mean on the same seeds. A result at the cap may
have `seTargetMet: false`. Cross terms use the first `min(seeds, seedRule.crossSeeds)` seeds (normally capped at 64).
The sampled increment is an estimate, not an exact expectation; consult its error and the check bound.

The exporter checks the shape table against independently read master data, its skill/level coverage and bands,
the variants' references, mission and band coverage, finite `[mean, se]` pairs with nonnegative errors, deterministic
zero errors, array lengths, the tail identity and each check's bound. New exports always include the aptitude keys;
the Schema still accepts older `/1` files without them.

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
| `MasterCharacterRank` | `_id` `_rank` `_bonus` |
| `MasterCharacterTotalRank` | `_id` `_totalRank` `_bonus` |
| `MasterBandItemSkillEffect` | `_id` `_bandItemId` `_level` `_skillTargetIDs` `_skillEffectType` `_effectValue` |
| `MasterBandItem` | `_id` `_bandId` |
| `MasterBandItemLevel` | `_id` `_bandItemId` `_level` `_playerRank` |
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

The deck model is the Rust crate ournotes-deck, pinned by commit in `rust/Cargo.toml` (and `rust/Cargo.lock`) and
built into the extension module `nnnotes._deck` with [maturin](https://www.maturin.rs/) (PyO3, the stable ABI of
Python 3.11 and later: one wheel per platform). The release workflow builds the wheels; `pip install .` or
`pip install -e .` in a checkout builds the module with the Rust toolchain. The same nnnotes version always carries
the same deck model: the commit moves only through a pull request (`.github/workflows/deck.yml` opens one when
ournotes-deck's `main` moves), and `provenance.deck.commit` names it in every file.

## Versions

`format` names the major version. Within `nnnotes.music-data/1`, fields and tables are only added, never renamed,
removed or given another meaning, and readers ignore keys they do not know. A reader rejects a file with a major
version it does not know. A change that breaks readers is a new major version, `nnnotes.music-data/2`. The deck
statistics follow the deck model's format (`provenance.deck.format`): a new major version of it is a new major
version of this file.

`nnnotes.music-data/1` replaces the `nnnotes.songs/1` file of `nnnotes songs` (its fields are the songs, charts and
their facts here) and the `nnnotes.deck-data/1` file of `nnnotes deck-data` (its content is the deck input of
`--full`).


Final aptitude exports require the standard-error target for both `score` and `scorePerfect`, each against its own paired no-skill baseline. Sampling extends the same published seed prefix through geometric batches, stopping as soon as both targets agree with the unchanged max(1% of increment, 0.1% of baseline) rule. The 65,536-seed cap is a failure guard, not a requirement to run every seed. An unmet cap aborts a normal export before writing artifacts. `--allow-unconverged-aptitude` is an explicit diagnostic option; it retains real SE values and unmet flags.

`--replay-dir OUT/replay --replay-engine WASM_PKG` writes normalized runtime inputs and pinned WASM assets as described in [replay.md](replay.md). The music data stays compact and carries the SHA-bound `replay.manifestUrl` pointer. No original chart/master blobs or native binary are included in this artifact bundle.
