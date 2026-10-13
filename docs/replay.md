

# Shared Rust/WASM replay inputs

`nnnotes music-data --replay-dir OUT/replay --replay-engine PKG [--recommend-engine PKG] -o OUT/music-data.json` writes the normalized input of the pinned deck model. Each engine is an ournotes-deck WASM release package of the pinned model, given as the downloaded archive or its extracted directory: `ournotes-replay-wasm-vVERSION.tar.gz` for `--replay-engine` and, optionally, `ournotes-recommend-wasm-vVERSION.tar.gz` for `--recommend-engine`. Its `build-info.json` must describe that module's WASM package (`kind:"wasm"`, `module`), name the pinned model's `commit`, and list in `files` the SHA-256 of the web binding's JS and WASM files, which must match the package files. The web binding (`web/ournotes_<module>_wasm.js` and `web/ournotes_<module>_wasm_bg.wasm`) and `build-info.json` are copied byte for byte. Actual ACB cue length is required. Original encrypted master files, chart blobs and native binaries are not copied.

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
    "model": {"name":"ournotes-deck","version":"<version>","source":"<repository>","commit":"<pinned commit>","sourceSha256":"<model source SHA-256>","format":"ournotes-deck.chart-stats/3"},
    "requestFormat":"ournotes.replay/1","class":"ReplaySession","methods":["describeChart","template","run"],
    "js":{"url":"engine/ournotes_replay_wasm.js","sha256":"<SHA>","bytes":0},
    "wasm":{"url":"engine/ournotes_replay_wasm_bg.wasm","sha256":"<SHA>","bytes":0},
    "build":{"url":"engine/build-info.json","sha256":"<SHA>","bytes":0}
  },
  "recommendEngine": {
    "model": {"name":"ournotes-deck","version":"<version>","source":"<repository>","commit":"<pinned commit>","sourceSha256":"<model source SHA-256>","format":"ournotes-deck.chart-stats/3"},
    "js":{"url":"recommend/ournotes_recommend_wasm.js","sha256":"<SHA>","bytes":0},
    "wasm":{"url":"recommend/ournotes_recommend_wasm_bg.wasm","sha256":"<SHA>","bytes":0},
    "build":{"url":"recommend/build-info.json","sha256":"<SHA>","bytes":0}
  },
  "unlistedScoreIds":[],
  "clock":"Explicit frames from ReplaySession.template; no Python/JS scoring or scheduling"
}
```

Numbers and SHA placeholders above illustrate the schema; actual manifests contain measured sizes, complete chart entries and computed hashes. A data-only export can leave `engine` null. A publishable interactive bundle supplies the pinned engine. `recommendEngine` is present only with `--recommend-engine`; its `model` is the same pinned model as `engine.model` and `deck-data.json`'s `provenance.deck`, so the recommendation module and the deck data it reads come from one model commit and one master snapshot. The module's exported API is the one of `wasm/recommend` at `model.commit`. Normalized runtime rows belonging to no listed live song are omitted and recorded in `unlistedScoreIds`.

Each chart resource is `{format:"nnnotes.replay-chart/1",scoreId,musicLengthMs,chart}`. `chart` retains the existing DeckData record: `asset:{key,sha256}`, equal-length `notes:{id,op,judgementType,timeMs}` arrays in native enumeration order, `skillEvents:{timeMs}` and `fevers:{startMs,endMs}`. `deck-data.json` uses the existing `nnnotes.deck-data/1` schema and adds `provenance.replay.musicLengthsMs:{"<scoreId>":<actual ACB lengthMs>}`. Score-table length is a separate native value derived by the shared Rust chart implementation; it must not be replaced by audio length.

The page loads and hashes the module/data once, calls `new ReplaySession(deckDataJson)`, then parses JSON returned by `describeChart(scoreId)`, `template(scoreId,power,fps)` and `run(requestJson)`. Template construction stays in Rust. It preserves simultaneous note order, supplies Perfect for judgement notes and native Pass for other operations, and does not guess Just. The request has `format:"ournotes.replay/1"`, explicit `scoreId`, `power`, audio and score-table lengths, `seed`, five performers, a `skillOrder` permutation, mode, and frames `{timeMs,deltaSeconds,judgements:[{noteId,judgement,judgementTimeMs}]}`. Complete input is required; missing notes are not silently changed to Miss. Advanced skill/rank scenarios use the same request JSON and engine.
## Snap labels and reference cards

Replay exports also write `snap-labels.json`, referenced by the optional `manifest.snapLabels` resource
(`format`, relative `url`, SHA-256 and byte length). Its format is `nnnotes.replay-labels/1`. Older manifests
without the resource remain valid; a consumer must not fill the gap with unbound tables from a newer global
master snapshot.

The resource contains `region`, `masterVersion` and `tables.<name> = {sha256, rows}` for 13 original decoded
tables: ordinary/Gekisou support definitions and per-level effects, `MasterText`, condition sets/conditions,
cumulative conditions, skill targets, characters, bands, member cards and support cards. Each table hash is the
served-file hash in the same `MasterManifest.json`/runtime master provenance. The producer rejects missing or
inconsistent hashes. This retains names, description templates, every effect level, condition target names and
card artwork IDs. No highest-level growth state or scoring coefficients are inferred. `MasterSkillIcon` is
omitted because the runtime provenance does not currently bind that table.

Consumers first verify the resource's SHA/bytes and its region/version/table hashes against the loaded replay
data. They can reuse their existing text/skill/card normalization with the chosen level; named skill targets
resolve through `MasterSkillTarget` to a character or band. Descriptions remain presentation metadata. Actual
Snap scoring uses the loaded Rust evaluator and explicit paired member predicates, skill levels, mode,
judgement plan, execution order and seed. The label file cannot enable an unsupported effect or substitute a
linear coefficient for whole-live evaluation. Reference-card maximum-power fields are not the evaluated deck's power.

The publisher validates this optional resource as part of the replay inventory and reads it back before
publishing the replay manifest/music-data pointer. Its content-addressed tree retains the prior resource files.
