# Architecture and integration contract

## Launch sequence

1. Read explicit local configuration; verify configured/bundled AOIs and campaign, create missing runtime directories and manifest.
2. Discover logical deliveries using a normalized manifest. The production downloader groups recognized dated rasters and companions into logical products, then populates this manifest. Individual bucket objects are not samples.
3. Upsert `(product, delivery_id, revision)` in SQLite.
4. Retry current revisions in DISCOVERED, PENDING_CONFIGURATION, PENDING_REFERENCE, REJECTED_QA, DIAGNOSTIC or ERROR.
5. The registered product adapter searches the reference, downloads to cache, applies QA, collocates and computes results.
6. Validate adapter output and commit results, samples and attempt history in one transaction.
7. Regenerate HTML from stored state. Completed current revisions are skipped on later launches.

A crash before commit leaves the delivery retryable. Cache artifacts should be written atomically by adapters. New revisions have separate state; obsolete revisions remain in history but stop contributing to coverage as soon as a replacement is discovered. Acquisition time is distinct from delivery/first-seen time. SQLite is local and intended for one launcher at a time.

## States

| State | Meaning | Next launch |
|---|---|---|
| DISCOVERED | Delivery known; no outcome yet | Attempt |
| PENDING_CONFIGURATION | Adapter or scientific policy missing | Recheck |
| PENDING_REFERENCE | Reference search found no usable candidate yet | Search again |
| REJECTED_QA | Candidates fail QA or collocation | Recheck and retain reason |
| ERROR | Operational failure | Retry |
| DIAGNOSTIC | Provisional metrics/evidence; no accepted samples | Retry/cache reuse |
| OUTSIDE_CAMPAIGN | Delivery acquisition outside configured interval | Skip; use a fresh workdir for a changed campaign |
| COMPLETE | Matchups and metrics committed with provenance | Skip current revision |

Reference status is independent: UNKNOWN, NOT_AVAILABLE, AVAILABLE (adapters must distinguish not searched from searched/unavailable). The initial scaffold uses UNKNOWN when the adapter is absent. A COMPLETE comparison does not mean the thematic target passed or the campaign is sufficient.

## Adapter interface

Implement `validate(delivery, product_config, workdir) -> Outcome` and register the adapter instance in `pipeline.ADAPTERS`; then set the product's `adapter` key. The runner catches adapter exceptions, records ERROR, and continues other deliveries.

For COMPLETE, return AVAILABLE reference status, nonempty metrics, provenance and sample units. Each sample contains:

```text
sample_key      canonical reference observation unit, stable across S5 deliveries
area            North / Center / South / Italy
observed_at     timezone-aware reference observation timestamp within [start,end)
evidence        nonempty JSON object with source IDs and QA/collocation evidence
```

Acquisitions use acquisition ID + sub-AOI; station-days use stable station ID + UTC date; hourly products use the agreed production/event key; GOSAT uses YYYY-MM for the canonical monthly unit and includes Italy coverage fraction. Preserve all valid spatial pairs separately; a sample unit is not a pixel. The adapter must establish a consistent area assignment. Report coverage deduplicates sample_key + area; months count distinct keys across areas.

Provenance must preserve S5 and reference URIs/versions, local checksums, retrieval time, QA policy/version, variable names and units, CRS, collocation settings, pair counts before/after QA, method version and configuration fingerprint. Save paired data under `results/<product>/<safe-delivery-id>/<revision>/` with station identity, coordinates and time where available. Product adapters must sanitize path components. Downloads must not contain credentials in metadata or logs.

## Later integrations

The current S3 connector uses the existing MinIO production alias with external credentials, date-prefix listings under `ar_delivery/`, and explicit inventory/download modes. MinIO handles listing pagination. Bucket connection failures are recorded per product for retry. Future catalogue formats need separate grouping rules. No embedded credentials or VPN automation. Reference providers need separate search/download/QA interfaces behind the product adapter, reproducible cache keys and reusable reference assets.

Plot modules will read persisted pair tables: scatter with identity line, difference histogram, spatial differences and acquisition-level metric trends; confusion and correct/false-positive/false-negative maps for binary products; separate precipitation event and both-wet intensity plots; station-level summaries for AOD. Plot generation and pair-table schema belong to each product implementation; `--report` regenerates coverage, stored metrics, artifact links and thermal metric trends.

## Scientific limitations

The thermal adapter enforces the documented draft Landsat QA/support filters. Other products still require dataset-specific QA, weighting, positional accuracy, class mappings, sample representativeness and target evaluation. The adapter boundary ensures basic evidence and campaign containment, not scientific correctness. Configuration changes do not silently invalidate completed deliveries: use a separate work directory until a methodology-version/revalidation migration is implemented. No overall campaign pass is produced. Launch exit code 1 indicates setup/discovery failure; exit code 2 indicates download or current per-delivery ERROR failures. Scientific pending/diagnostic outcomes may still return 0.

## Thermal adapters

The Landsat 8/9 adapter is registered for 01/02/10/11. Explicit `--landsat` launches populate the shared reference cache and matching S5 deliveries; ordinary runs compare local data. `DIAGNOSTIC` preserves metrics and evidence but writes no campaign samples; `OUTSIDE_CAMPAIGN` excludes deliveries before October. Details and scientific limitations are in [LANDSAT.md](LANDSAT.md).
