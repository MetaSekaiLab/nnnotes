# Quick start

This guide exports one episode, a playable story product and a static story site. Use your own matching client
files, keys and server addresses; [configuration](configuration.md#where-the-values-come-from) explains the inputs.

## 1. Install the tools

```sh
pip install 'nnnotes[fonts]==0.3.0'
```

Use Python 3.11+. Install [vgmstream](https://vgmstream.org/) (`vgmstream-cli`) and
[FFmpeg](https://ffmpeg.org/) on PATH, or configure their executable paths. They decode story audio and video.
The final site step also needs Node.js 22+.

## 2. Configure your inputs

```sh
nnnotes config init --user
nnnotes config check
```

The wizard asks for region names, keys, server addresses and paths. Set `paths.depot` to a persistent directory,
`paths.apk` to your game APK and `catalog.language` to the desired language. Keys are entered without echo.
`config check` lists setting names, their source and validity. A command names any required setting it lacks.
The per-user file is available from any directory; `nnnotes config path` shows the configuration search order.

## 3. Inspect the catalog and prepare master data

```sh
nnnotes catalog keys --prefix Adv/ --limit 5
nnnotes master version
nnnotes master download --latest -o work/master-bin
nnnotes master decode work/master-bin -o work/master
nnnotes config set paths.master work/master
```

The catalog is retained in the depot. Master downloads are checked against their manifest; decoding writes a
JSON file for each table. Use a story ID available in your selected region in the examples below.

## 4. Export the episode and story

```sh
nnnotes export episode 10462 -o out/episode.json
nnnotes export story 10462 --fonts game -o out/story
```

The episode file contains commands and localized lines. The story directory contains a product manifest at
`stories/10462.json`, its referenced products and content-addressed files under `assets/`. It includes the
scene, models, text resources and any media the story uses. Audio defaults to AAC; `--format` selects another
supported encoding. UI and font products use the catalog language by default.

For two languages, add `--story-languages ja,en`. Open fonts are also supported:
`--fonts open --font ja=fonts/NotoSansJP.otf --font en=fonts/NotoSans.ttf`.
Supply a font for every selected language, either as a flag or in `[paths.fonts]`.

## 5. Build, validate and serve a site

```sh
npm install ournotes-player@0.2.0
nnnotes site build --name stories -o out/site --story 10462 --fonts game --player node_modules/ournotes-player
node node_modules/ournotes-player/scripts/validate-data.mjs out/site
python -m http.server 8000 --directory out/site
```

Open `http://localhost:8000/story/`. Configure the page's Cubism Core and, for the features in your story,
MotionSync Core or Spine runtime; see the player's
[getting started guide](https://github.com/empty-sekai/ournotes-player/blob/main/docs/getting-started.md).
These runtimes have their own licenses and are provided by the page.

The site builder accepts a built ournotes-player 0.2.x package, validates its package identity and required files,
and reuses the story's depot tasks. The validator checks manifests, references and data contracts before playback.

## 6. Update and retain your work

Reuse `--name stories` to update selected topics while retaining the rest. Use a different name for a separate
site. To materialize its saved products with the current player, configure `paths.player` and run:

```sh
nnnotes site build --name stories -o out/restored-site --player-only --player node_modules/ournotes-player
nnnotes depot verify
nnnotes depot gc --dry-run
```

The depot and the published output are separate directories. Read [retention](depot.md#retention) before collecting
stored results. Add `--json` to a command for its structured report.

## Next

- [Export stories and lives](export.md) for individual products.
- [Build a site](site.md) for charts, regional selections, language choices and hosting.
- [Depot](depot.md) for shared storage, verification and collection.
- [Japanese release](jp.md) for its catalog and APK requirements.
- [Commands](cli.md) for flags and exit statuses.
