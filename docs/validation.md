# Validation

Validation has three distinct scopes: synthetic contract tests, checks over generated files, and browser playback.
Use the checks that match the output and consumer you are working on.

## Synthetic tests

```sh
pip install -e '.[test,fonts,remote,deck]'
python -m pytest
python -m ruff check .
python -m pyright
```

The tests exercise object conversion, product assembly, language selection, regional aliases, reference closure,
task identities and retained layouts. Directory remotes exercise sharing between depots. S3 protocol tests use
botocore stubs to check request parameters, pagination, deletion batches and errors without an account.
Publication tests inject failures between file replacements and check recovery and index ordering.

Every registered output and site stage has a golden record. With the dependency versions in
`.github/goldens-constraints.txt`, run:

```sh
python -m pytest tests/pipeline/test_goldens.py tests/pipeline/test_objexport.py
```

Set `GOLDENS_STRICT=1` for a matching pinned environment so a dependency mismatch fails the check.
The records include native codec identities; see [determinism](determinism.md).

## Check stored content

```sh
nnnotes depot verify
nnnotes depot verify --quick
nnnotes depot gc --dry-run
```

Full verification checks hashes; quick verification checks sizes. Referenced content held only by the remote
is checked there. A collection dry run reports eligible removals under the configured retention policy.
See [depot](depot.md) for run roots, grace periods and remote collection.

## Check exported products

Build a selected site with your configured inputs, then use the compatible player's validator:

```sh
nnnotes site build --name example -o out/site --story 10462 --fonts game --player node_modules/ournotes-player
node node_modules/ournotes-player/scripts/validate-data.mjs out/site
```

The validator checks product schemas, shared objects, file references, chart metadata, unique asset byte counts,
language-specific resources and model dependencies. By default it checks every declared language.
Chart site builds also compare sampled read-set plans with full simulation and fall back to full simulation
when a plan fails or disagrees. See [site](site.md#player-read-sets-and-variants).

For your own corpus, enumerate subjects from matching master data and catalogs, preserve command reports,
and record the configuration and tool versions used. Compare normalized JSON with references resolved;
compare decoded image pixels and audio samples when encoder differences affect stored bytes.

## Check playback and hosting

Serve the output over HTTP, load required runtimes and exercise a chart, a model and the story features in your
selection. Check language changes, seek, completion and browser network errors. Structural validation and rendered
appearance answer different questions. Hosting instructions cover CORS, compression and cache headers in the
[player documentation](https://github.com/empty-sekai/ournotes-player/blob/main/docs/embedding.md).

An S3 endpoint also has its own permissions, consistency and gateway behavior. Check reads and writes under an
isolated test prefix when configuring a new endpoint, and scope collection to the depot prefix.
