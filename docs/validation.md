# Validation

The test suite runs on synthetic inputs, so it needs no game data. The experiments on this page check nnnotes
against a whole game version, which you supply yourself: each one names what it checks, how to run it with your own
configuration, and the result for the version named. Only commands and checks are listed. The outputs stay with
whoever runs them.

The results below are for the Traditional Chinese release, client 1.0.1, catalog language `zh-Hant`, with its master
data. The [Japanese release](jp.md#validation-and-current-limits) has its own section.

## Setup

```sh
nnnotes config check                          # every setting the commands below need is valid
nnnotes master download --latest -o work/master-bin --json > work/download.json
nnnotes master decode work/master-bin -o work/master --json > work/decode.json
nnnotes config set paths.master work/master
```

The lists of subjects come from the master data and the catalog:

```sh
python -c "from nnnotes.source.master import table; print(*sorted(r['_id'] for r in table('work/master', 'MasterAdv')), sep='\n')" > work/episodes.txt
nnnotes catalog keys --prefix Character/Live2D/ --limit 0 > work/live2d-keys.txt
```

## Experiments

| Experiment | How to run it | Result (client 1.0.1, zh-Hant) |
|---|---|---|
| Master data | `master download` checks every file against the SHA-256 of `MasterManifest.json`; `master decode` reports each table it could not decode under `failed` | every file verified, every table decoded |
| ADV episodes | `export episode ID` for every id of `episodes.txt` | 946 / 946 exported |
| Stories | `export story ID -o out/stories/ID` for every id, all with the same models directory; then read every JSON file and resolve every path `story.json` and the files it indexes name | 946 / 946 exported; every JSON file parses, every named file, model and cue exists |
| Story reproducibility | export the stories that use frames, effects, post effects, stills, chats, talk windows or videos twice into two directories and compare them byte by byte | 235 such episodes; the two exports are equal except the models directory path in `story.json` |
| Live2D models | `export live2d KEY` for every model key of `live2d-keys.txt` (the keys ending in `/model/<name>`) | 239 / 239 exported, 185 of them used by stories |
| CRI audio | `export audio CUE_SHEET` for every cue sheet under `Cri/Sound/` | 681 / 681 decoded |
| Lives | `export live MUSIC_ID --difficulty D` for every music and difficulty with a `MasterLiveMusicScore` row | 336 / 336 exported |
| Spots and rooms | `export spot ID` for every `MasterHomeSpot` row and `export room KEY` for every room background prefab; validate every `.glb` with the [Khronos glTF-Validator](https://github.com/KhronosGroup/glTF-Validator) | 39 / 39 spots, 20 / 20 rooms; every reference resolves; every glb has 0 validator errors |
| Site, one region | `site build --all --all-live2d --all-stories --story-languages en --no-audio` twice into two empty directories, then compare them | 336 charts, 239 models, 946 stories; no failures file; the two sites are byte-identical |
| Site, every region | `site build --all --all-regions --format aac` twice into two empty directories, then compare them | 340 charts of 85 songs from the regions' current master data, five languages; no failures file; the two sites are byte-identical |
| Regions | for each region and language, `catalog keys --limit 0` and the catalog's bundles; compare the master tables a chart build reads across regions | the regions serve the same catalog and bundles for a language with the same keys; the chart tables are equal across the three regions; the text tables have all five languages |

## Checks of every run

These hold for every command and are checked by the test suite on synthetic data:

- A key, id or model that the catalog or the master data does not have stops the command with exit status 2 before
  any bundle is fetched or file written.
- The documents of an asset export run (plan, tasks, results, artifacts, run manifest, layout, coverage, failures,
  cost) and the music data file validate against their [JSON schemas](schemas.md).
- The same inputs give byte-identical outputs ([determinism](determinism.md)).
