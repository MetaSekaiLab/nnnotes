# Shared Rust/WASM replay inputs

`nnnotes music-data --replay-dir OUT/replay --replay-engine WASM_PKG -o OUT/music-data.json` writes the normalized input of the pinned deck model. The engine package must contain `ournotes_replay.js`, `ournotes_replay_bg.wasm` and `build.json`; its commit and both file hashes must match the measured model. Actual ACB cue length is required. Original encrypted master files, chart blobs and native binaries are not copied.

The music-data pointer is:

```json
{"replay":{"format":"nnnotes.replay-manifest/1","manifestUrl":"replay/<manifest SHA-256>/manifest.json","sha256":"<manifest SHA-256>","charts":340}}
```

`manifestUrl` is relative to the music-data URL. The manifest hash names its directory, so uploading a new release preserves the preceding document's resource tree. Every manifest resource URL below is relative to the manifest URL:

```json
{
  "format": "nnnotes.replay-manifest/1",
  "deckData": {"format":"nnnotes.deck-data/1","url":"deck-data.json","sha256":"<SHA>","bytes":0},
  "charts": [{"scoreId":10000303,"url":"charts/10000303.json","sha256":"<SHA>","bytes":0,"musicLengthMs":120557,"noteCount":0,"assetSha256":"<original chart asset SHA>"}],
  "engine": {
    "model": {"name":"ournotes-deck","version":"<version>","source":"<repository>","commit":"<pinned commit>","format":"ournotes-deck.chart-stats/2"},
    "requestFormat":"ournotes.replay/1","class":"ReplaySession","methods":["describeChart","template","run"],
    "js":{"url":"engine/ournotes_replay.js","sha256":"<SHA>","bytes":0},
    "wasm":{"url":"engine/ournotes_replay_bg.wasm","sha256":"<SHA>","bytes":0},
    "build":{"url":"engine/build.json","sha256":"<SHA>","bytes":0}
  },
  "unlistedScoreIds":[],
  "clock":"Explicit frames from ReplaySession.template; no Python/JS scoring or scheduling"
}
```

Numbers and SHA placeholders above illustrate the schema; actual manifests contain measured sizes, complete chart entries and computed hashes. A data-only export can leave `engine` null. A publishable interactive bundle supplies the pinned engine. Normalized runtime rows belonging to no listed live song are omitted and recorded in `unlistedScoreIds`.

Each chart resource is `{format:"nnnotes.replay-chart/1",scoreId,musicLengthMs,chart}`. `chart` retains the existing DeckData record: `asset:{key,sha256}`, equal-length `notes:{id,op,judgementType,timeMs}` arrays in native enumeration order, `skillEvents:{timeMs}` and `fevers:{startMs,endMs}`. `deck-data.json` uses the existing `nnnotes.deck-data/1` schema and adds `provenance.replay.musicLengthsMs:{"<scoreId>":<actual ACB lengthMs>}`. Score-table length is a separate native value derived by the shared Rust chart implementation; it must not be replaced by audio length.

The page loads and hashes the module/data once, calls `new ReplaySession(deckDataJson)`, then parses JSON returned by `describeChart(scoreId)`, `template(scoreId,power,fps)` and `run(requestJson)`. Template construction stays in Rust. It preserves simultaneous note order, supplies Perfect for judgement notes and native Pass for other operations, and does not guess Just. The request has `format:"ournotes.replay/1"`, explicit `scoreId`, `power`, audio and score-table lengths, `seed`, five performers, a `skillOrder` permutation, mode, and frames `{timeMs,deltaSeconds,judgements:[{noteId,judgement,judgementTimeMs}]}`. Complete input is required; missing notes are not silently changed to Miss. Advanced skill/rank scenarios use the same request JSON and engine.
