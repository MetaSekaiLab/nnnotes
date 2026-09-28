# Songs

`nnnotes songs` writes one JSON file with every live song's metadata for one master data version: what a song
listing shows (titles and credits in every language, bands, vocal characters, category, tags, release time, jacket,
BGM length) and, per difficulty, the chart facts (level, note counts, BPM, chart times, skill events, fevers). The
format is `nnnotes.songs/1`.

```
nnnotes songs (--master-files DIR | --apk-master) [--no-bgm] [--jackets DIR] -o FILE
```

The master data is read as for [deck data](deck-data.md): the files as served, each checked against the manifest's
SHA-256. A songs file and a deck data file made from the same master data version join by `scoreId`
(`MasterLiveMusicScore._id`). Charts are read from the catalog of `[catalog] language`; the BGM length from the cue
sheet's ACB (its `CueTable` and `WaveformTable`, no audio is decoded). `--no-bgm` does not read the cue sheets.
`--jackets DIR` also writes every song's jacket, the Texture2D `Image/Jacket/<jacket>`, as `DIR/<jacket>.webp`
(WebP quality 88, scaled down with Lanczos to at most 320 pixels on the longer side, without alpha when opaque); a
page next to the songs file finds a song's jacket at `<DIR>/<song.jacket>.webp`.

The command writes the file only when every table, chart and cue sheet was read: a missing master data file, a text
id that `MasterText` does not have, a score id that `MasterLiveMusicScore` does not have, a missing or unreadable
chart asset, a cue sheet without the song's cue, or (with `--jackets`) a missing jacket texture stops it with exit status 1 and a line naming the input.

## Layout

```json
{
  "format": "nnnotes.songs/1",
  "provenance": {"region": ..., "client": {...}, "catalog": {...}, "master": {...}, "exporter": {...}},
  "languages": ["ja", "en", "zh-Hant", "zh-Hans", "ko"],
  "bands": [...], "characters": [...], "tags": [...], "categories": [...],
  "songs": [{"id": 100001, ..., "charts": [...]}, ...]
}
```

The file is minified UTF-8 with one line feed at the end, keys in the order shown, and canonical in the same way as
deck data (same inputs, same nnnotes version: same bytes; floats are the shortest decimal of their binary32 value).
`provenance` is the deck data provenance, with `master.tables` listing the tables this file reads: `MasterLiveMusic`,
`MasterLiveMusicScore`, `MasterText`, `MasterBand`, `MasterCharacter`, `MasterTag`, `MasterLiveMusicCategory`,
`MasterSound`, `MasterSoundCueSheet`.

A **text** is an object with one string per language of `languages` (`{"ja": ..., "en": ..., "zh-Hant": ...,
"zh-Hans": ..., "ko": ...}`), the `MasterText` row of the id; a text field is null when the master data gives no id
(an empty string). A language's string may be empty when the game has no text in that language.

### bands, characters, tags, categories

| Field | Content |
|---|---|
| `bands[].id`, `.name`, `.mainColor`, `.subColor` | `MasterBand`: id, name text, color codes |
| `characters[].id`, `.bandId`, `.name`, `.shortName`, `.mainColor` | `MasterCharacter` |
| `tags[].id`, `.name` | `MasterTag` (the ids of `bestMusicTagIds`) |
| `categories[].id`, `.musicCategories`, `.name` | `MasterLiveMusicCategory` (the listing's category tabs; `musicCategories` are the song category values it shows) |

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
| `gekisouMissions` | `[_gekisouMission1, _gekisouMission2, _gekisouMission3]` |
| `bgm.soundId`, `.cueSheet`, `.cue` | `_musicSoundID` and its `MasterSound` / `MasterSoundCueSheet` cue |
| `bgm.length` | `{lengthMs, samples, sampleRate, durationMs}`: the cue's `Length` in the ACB `CueTable`, the sample count and rate of its first waveform, and `samples * 1000 // sampleRate`; null with `--no-bgm` |
| `charts` | one entry per difficulty the song has (`_easyID` .. `_expertID` not 0), in the order easy, normal, hard, expert |
| `master.MasterLiveMusic` | the whole `MasterLiveMusic` row as decoded |

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

Chart times are milliseconds of chart time. The two lengths are different facts: `bgm.length` is how long the music
plays, `musicLengthMs` is the length the score code uses; a listing chooses the one it needs.

## Versions

As for deck data: within `nnnotes.songs/1` fields are only added, never renamed, removed or given another meaning;
readers ignore keys they do not know and reject a major version they do not know.
