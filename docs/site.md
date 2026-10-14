# Build an ournotes-player site

`nnnotes site build` writes a static site of charts, Live2D models and stories that
[ournotes-player](https://github.com/empty-sekai/ournotes-player) plays in the browser. The site is a directory of
plain files: the player's pages and bundles, one manifest per chart, model and story, and the files they read,
stored once by content hash.

## What it needs

- The settings of the export commands: keys, the region's CDN, `[paths] apk`, and the decoded master data of every
  region the site serves ([configuration](configuration.md)).
- A built ournotes-player: its checkout after `npm run build`, or its installed package, given with `--player` or
  `[paths] player`.
- Node.js 20+, which runs the player's read-set script to list the files each chart reads; only those are stored.
- vgmstream and FFmpeg for the audio.
- For stories: `pip install 'nnnotes[fonts]'` and a font file per story language (below).

## Build a few charts

```sh
nnnotes site build -o out/site --player path/to/ournotes-player \
    --pair 100001:expert --pair 100001:hard
```

`--pair MUSIC_ID:DIFFICULTY` (repeatable) adds charts; `--all` adds every chart of the master data. Serve the
directory with any static file server and open `index.html`: the chart list, with `?music=<id>&difficulty=<d>`
playing one chart.

The build is incremental. A chart, model or story whose manifest exists is skipped unless `--force`, and files no
manifest references any more are removed, so the same command run again after a game update adds what is new.
`--tmp DIR` (default `SITE.tmp`) keeps a cache of decoded audio, encoded images, shader dumps and read sets keyed by
everything they were made from; deleting it is safe outside a build.

## Add models and stories

```sh
nnnotes site build -o out/site --player path/to/ournotes-player \
    --all --all-live2d --all-stories \
    --font ja=fonts/NotoSansJP.otf --font en=fonts/NotoSans.ttf \
    --font zh-Hant=fonts/NotoSansTC.otf --font zh-Hans=fonts/NotoSansSC.otf --font ko=fonts/NotoSansKR.otf
```

- `--live2d MODEL` (repeatable) or `--all-live2d` adds Live2D models; the site gets the player's model page.
- `--story ADV_ID` (repeatable) or `--all-stories` adds story episodes, and the models they use; the site gets the
  player's story page.
- Story text is laid out with font assets generated from your font files, holding exactly the characters each
  episode shows. Give one per language of `--story-languages` (default all five) with `--font LANG=PATH` or
  `fonts.<lang>` in `[paths]`. `--font emoji=PATH` gives a colour emoji font for the emoji of chat and talk texts.
  `--fonts game` uses the game's own font assets instead.

## Several regions

```sh
nnnotes site build -o out/site --player path/to/ournotes-player --all --all-regions
```

`--site-region REGION` (repeatable) or `--all-regions` chooses the regions the site serves; each region's master
data is `[servers.<region>] master`. Regions whose chart tables are equal share one manifest per chart; a region
whose tables differ gets its own. The list texts are written in all five languages, and the chart list page switches
with `?region=<id>&lang=<language>`.

## Audio, compression and hosting

- `--format aac|opus|vorbis|mp3|flac` (default `aac`) is the BGM and story audio format; `--no-audio` stores none.
- `--compress gzip|br|none` (default `gzip`) stores the compressible files encoded when that makes them smaller. A
  gzip site works from any static host; a brotli site needs `Content-Encoding: br` on its `.br` files.
- `--workers N` sets the parallel music, model and story processes (`0` builds in this process) and
  `--read-workers N` the read sets run at a time.

## When something fails

Charts, models and stories that fail are listed in the summary and in `SITE.failures.json`,
`SITE.model-failures.json` and `SITE.story-failures.json`, and the exit status is 1; the others are built. Fix the
cause and run the same command again: what was built is skipped.

The full option list is in the [command reference](cli.md#nnnotes-site-build), the site layout and every rule in
[Command outputs](commands.md#site-build).
