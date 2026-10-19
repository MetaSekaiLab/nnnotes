# Depot reference

The depot stores task results and their content-addressed files. Set `[paths] depot` to its directory.
Commands share it across executions; an output layout is a separately materialized selection of its results.

| Directory | Contents |
|---|---|
| `cas/sha256/` | File bytes indexed by SHA-256 |
| `ac/` | Task result documents indexed by task key |
| `runs/` | Run manifests, logs and latest-selection pointers |
| `catalogs/` | Catalog versions, labels and verified catalog bytes |
| `inputs/` | File identity records and named input identities |
| `costs/` | Measured task costs used by scheduling |
| `downloads/` | Recoverable source downloads |
| `leases/` | Active command leases and the exclusive collection lock |

## Remote storage

Set `[depot] remote` to a `file:///...` directory or an `s3://bucket/prefix` URL. S3 storage requires
the `remote` extra. See [configuration](configuration.md) for endpoint and credential settings.

Results and content are fetched when needed. Publishing a run first uploads its completed results, available
stored inputs and layout contents. This also shares results computed before remote storage was configured.
A run pointer alone is not evidence that a result exists: planning checks the result itself.
S3 listings follow continuation tokens and deletion requests use batches of at most 1,000 objects.
A per-object deletion error stops collection and reports failure, even when the service returns HTTP success.
Catalog discovery merges version metadata and shares locally imported versions with their catalog bytes.

`depot verify` checks local content and results. When a local result refers to content held only by the remote,
verification checks that remote object without adding it to the local cache. Full verification checks hashes;
`--quick` checks sizes.

## Retention

Each command using the depot records its outcome and the stored content and task results it accessed. These
command runs are independent of product and site selections, so they retain inputs without replacing those
selections' latest layout. Commands that only use downloads or catalog files still leave an execution record;
those source directories are outside CAS collection. Dry runs leave no execution record.

`depot gc` retains the results, stored inputs and layouts reachable from kept runs. By default all runs are kept;
`--keep N` retains the latest N runs per selection and always the selection's latest run.

Results and content younger than `--grace SECONDS` are also retained. The default is 86400 seconds, in both local
and remote storage. A recent result protects even older inputs and artifacts, covering the interval before its
run record is written. `--grace 0` removes this time-based protection. `--remote` additionally collects remote
storage from its own run records. `--dry-run` only counts removals and does not fetch remote content into the cache.

Collection requires an exclusive lease. A command waiting for an exclusive holder reports the wait and fails
after 30 seconds if the holder is still active; it leaves that holder's lock intact. Download retention is
separate: `[depot] downloads` sets its byte budget, with `all` keeping downloads.

For task planning and execution, see [asset commands](assets.md). For identities and document shapes, see
[contracts](contracts.md) and [formats](formats.md).
