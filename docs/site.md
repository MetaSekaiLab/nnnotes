# Build an ournotes-player site

`nnnotes site build` publishes a static site from depot products. The site contains player pages, chart/model/story listings, public entry manifests, immutable product manifests, and content-addressed assets.

## Requirements

Configure `[paths] depot`, the catalog and APK, and the decoded master data for the selected charts and stories. Supply a built [ournotes-player](https://github.com/empty-sekai/ournotes-player) 0.2.x checkout or npm package through `--player` or `[paths] player`. Node.js 22 or newer runs chart read sets. Audio conversion uses vgmstream and FFmpeg. Text rendering needs the `fonts` extra and either `--fonts game` or a font file for every requested language.

Install the complete player package with `npm install ournotes-player@0.2.0`, then pass
`--player node_modules/ournotes-player`. The browser-only ZIP supplies bundles for embedding; the site builder
also needs the package metadata, scripts and example pages. It checks the package name, stable 0.2.x version
and required files before starting.

## Select products

```sh
nnnotes site build --name example -o out/site --player path/to/ournotes-player \
    --pair 100001:expert --font ja=fonts/NotoSansJP.otf
nnnotes site build --name example -o out/site --story 10004 --story-languages ja,en \
    --font ja=fonts/NotoSansJP.otf --font en=fonts/NotoSans.ttf
```

`--pair`, `--live2d`, and `--story` are repeatable. Each updates those topics and retains the site's other topics. `--all`, `--all-live2d`, and `--all-stories` replace the corresponding kind's complete selection in the requested regions. Story products include their model dependencies, which also appear in the model listing.

`--name` identifies the site in the depot and defaults to `default`. Use different names for independent sites sharing a depot. The latest successful run records the complete catalog and layout. A new output directory or machine can restore that state from the same depot, including its configured remote.

```sh
nnnotes site build --name example -o restored/site --player-only
```

`--player-only` publishes the saved products and current player pages. Product extraction and site transformations use task keys: unchanged inputs reuse their committed results, while changed inputs produce new manifests. Depot collection follows the successful layout's complete dependency closure.

## Regions and languages

`--site-region REGION` selects a configured region and is repeatable; `--all-regions` selects all configured regions. Each region uses its own master data. Equal product content and listing metadata share an alias; other regional versions get an alias under their region. Chart and story pages select them with `?region=<id>&lang=<language>`. JP stories/models and international stories/models require separate sites; differing content for one model ID also requires separate sites.

The catalog language supplies default text. `--story-languages ja,en,...` selects story text/font products and defaults to the catalog language. Supply fonts with `--font LANG=PATH` or `[paths.fonts]`; `--font emoji=PATH` supplies a colour emoji font. `--fonts game` selects the game's font assets.

## Player read sets and variants

A chart read set is a depot task whose inputs include the player sources, product files, requested settings and Node implementation identity. The first chart and a deterministic sample compare the player's plan with a full simulation. A failed plan uses a full simulation; a mismatch switches subsequent reads to full simulation. The site retains structured product documents, their image/audio/WebGL2 dependencies, and additional binary roles read by the selected settings.

`--live-option` adds note skins, effect sets, qualities, mirrored charts, bar lines and note sound sets. Read sets cover the default settings and offered combinations. Each chart manifest includes its immutable variant product paths and supported quality values.

## Publication and hosting

`--format aac|opus|vorbis|mp3|flac` selects audio encoding; `--no-audio` omits sound streams. `--compress gzip|br|none` selects storage encoding. gzip assets work as plain static files; brotli assets need `Content-Encoding: br`.

Publication writes assets, immutable products, aliases/pages, and then indexes. It collects retired owned files after successful replacements and retains unowned files. A failed publication keeps its journal and successful-run pointer for retry. Serve the directory with a static server and open `index.html`, `live2d/`, or `story/`.

`--workers`, `--memory`, and `--fetch-workers` control product task execution. `--stored-catalog LABEL|SHA` selects a catalog already registered in the depot. Reports include product task counts, site task counts, read-set checks, and file placement counts. See the [command reference](cli.md#nnnotes-site-build) for all flags and [site snapshots](products.md#site-snapshots) for product references.

## Validate and open

```sh
node node_modules/ournotes-player/scripts/validate-data.mjs out/site
python -m http.server 8000 --directory out/site
```

Open the chart page at `/`, the model page at `/live2d/` or the story page at `/story/`.
Supply Cubism Core and the MotionSync/Spine runtimes needed by the selected content through the player's page
configuration. See its [runtime guide](https://github.com/empty-sekai/ournotes-player/blob/main/docs/getting-started.md#browser-runtimes)
and [hosting guide](https://github.com/empty-sekai/ournotes-player/blob/main/docs/embedding.md#hosting-the-data).
