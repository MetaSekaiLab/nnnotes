# Deck data

`nnnotes deck-data` writes one JSON file with the game data that deck-building tools read: every live chart as the
client builds it at runtime, and the master data tables about cards, skills, bonuses, scores and events. One file
describes one master data version. The file holds facts as the game has them (master data values as served, notes
as the client's chart converter creates them), not formulas or values derived from them. The format is
`nnnotes.deck-data/1`; its JSON Schema is [schema/deck-data.schema.json](schema/deck-data.schema.json).

```
nnnotes deck-data (--master-files DIR | --apk-master) -o FILE
```

- `--master-files DIR`: master data files as served, `DIR/MasterManifest.json` and the `.bin` files it lists
  (`nnnotes master download`). The file's `region` is `[catalog] region` (`--region`).
- `--apk-master`: the master data files the APK ships (`assets/Master/` of `[paths] apk`, the same layout). The
  file's `region` is `embedded`.

Each master data file is checked against the SHA-256 the manifest lists and decoded with `[master] key` and `iv`.
Charts are read from the catalog of `[catalog] language` (bundles fetched into the cache as for every command).
`FILE` ending in `.gz` is written gzip-compressed. The command prints a summary (`charts`, `notes`, `tables`,
`rows`, `bytes`, `sha256` of the file, ...).

The command writes the file only when every table and every chart was read: a missing or mismatching master data
file, a table without a column the file exports, a `MasterLiveMusicScore` row whose chart asset is missing or cannot
be converted, a note id that occurs twice in a chart, or a note whose operate type has no judgement type stops it with
exit status 1 and a line naming the input. The file is written through a temporary file and a rename.

## Layout

```json
{
  "format": "nnnotes.deck-data/1",
  "provenance": {"region": ..., "client": {...}, "catalog": {...}, "master": {...}, "exporter": {...}},
  "master": {"<Table>": {"columns": ["_id", ...], "rows": [[...], ...]}, ...},
  "charts": [{"scoreId": ..., "asset": {...}, "notes": {...}, "skillEvents": {...}, "fevers": {...}}, ...]
}
```

The file is minified UTF-8 with one line feed at the end. Keys are always in the order shown here and in the tables
below, so the same inputs with the same nnnotes version give the same bytes. A gzip file has no file name and a zero
modification time in its header.

### provenance

| Field | Content |
|---|---|
| `region` | the region whose master data this is (a configured region name), or `embedded` for the APK's master data |
| `client.versionName`, `client.versionCode` | the APK's version name and code (null without `[paths] apk`) |
| `catalog.resourceVersion` | the resource version recorded for the catalog in the catalog store (`nnnotes catalogs fetch` / `import`), null when none is recorded |
| `catalog.sha256` | SHA-256 of the remote catalog file the charts were read with |
| `master.source` | `api` (`--master-files`) or `embedded` (`--apk-master`) |
| `master.version` | the `version` of the master data manifest |
| `master.tables.<Table>.sha256` | SHA-256 of the table's file as served, before decoding |
| `exporter.name`, `exporter.version` | `nnnotes` and its version |
| `exporter.chartFormat` | the format of the chart converter the notes come from, `nnnotes.live-score/1` |

### master

Each table is `{"columns": [...], "rows": [[...], ...]}`: one array per row, its values in the order of `columns`,
rows in the order the master data lists them. Values are as decoded: integers, strings, booleans, arrays. A number
the master data writes with a fraction or an exponent is a binary32 (single precision) value in the game; it is
written as the shortest decimal that reads back as the same binary32 value, so a reader should parse such columns
as 32-bit floats. Infinity is written `1e999` / `-1e999`.

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
| `MasterLiveEventPoint` | all |
| `MasterChallengeLiveEventPoint` | all |
| `MasterLiveChallengePoint` | all |
| `MasterLiveMusicBoostBonus` | `_id` `_consumedLiveBoostCount` `_liveMusicRewardRate` `_playerExpRate` `_memberCardExpRate` `_friendshipExpRate` `_eventPointRate` |
| `MasterChallengeMusicBoostBonus` | all |

### charts

One chart per `MasterLiveMusicScore` row, sorted by `scoreId`. Song and score facts (level, full combo count, song
type, the song's difficulties) are not copied into the charts: they are in `MasterLiveMusicScore` and
`MasterLiveMusic`, joined by `scoreId` (`MasterLiveMusicScore._id`, and `MasterLiveMusic._easyID` .. `_expertID`).

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

Readers can derive further counts from these fields (for example the number of judged notes from `notes.op`); the
file does not contain derived values.

## Versions

`format` names the major version. Within `nnnotes.deck-data/1`, fields and tables are only added, never renamed,
removed or given another meaning, and readers ignore keys they do not know. A reader rejects a file with a major
version it does not know. A change that breaks readers is a new major version, `nnnotes.deck-data/2`.
