# Operator runbook

Run commands from the cloned repository root with the Python environment activated. Every CLI launch verifies configured AOI presence/format and bundled geometry checksums, checks campaign dates, and creates missing cache, results, report, inventory and per-product directories. The SQLite registry and an empty delivery manifest are initialized automatically. Required source/configuration/geometry files come with the repository; you do not create them by hand.

## First machine setup

1. Clone the repository and install `.[scientific,dev]` as in [Installation](INSTALLATION.md).
2. Run `python tools/doctor.py`, the unit tests, and `python tools/smoke_test.py`. No measurements or credentials need to be shared for these checks.
3. For production, install MinIO, connect to the approved network and drop the supplied `bucket.env` and `downloaders.env` into the automatically created `credentials/` folder. Landsat requires no personal credential, so its optional field may be blank. Future reference integrations must document their own external credential files and automatic locations.
4. Run `iride-validate --inventory-only --product S5-02-02` to check private access.

Sharing the necessary external credential file(s) is sufficient for data access once the code and dependencies are installed; there is no dependency on the original author's drive, virtual environment, cached downloads, AOI folder or MinIO helper scripts.

## Typical manual launch

```bash
iride-validate --download-latest
iride-validate --landsat
```

The first command downloads the newest recognized acquisition per product and the highest numeric revision. For hourly products this means one hour, not an entire day. The second searches campaign Landsat scenes through today, downloads eligible reference assets, retrieves available thermal S5 products on the reference dates, compares locally available products, and writes the report. It does not download every historic S5 acquisition indiscriminately.

For a bounded campaign snapshot:

```bash
iride-validate --landsat --as-of 2026-10-09
```

Public reference-only trial:

```bash
iride-validate --landsat --max-scenes 1 --skip-matching-deliveries
```

`--max-scenes` selects the first eligible scenes ordered by date/id and verifies/reuses them on later launches; it is not “N new scenes.” Matching S5 dates are derived from the complete catalogue search, not only the limited download batch. Use `--skip-matching-deliveries` for a small reference-only trial.

## Local modes and outputs

```bash
iride-validate --status
iride-validate --report --open-report
iride-validate --product S5-02-11
```

Status and report do not access providers. A normal local launch attempts pending/current revisions using local reference caches. Pending reference availability changes only after a fresh reference download launch. `--open-report` asks the OS browser to open the generated HTML.

| Location | Content |
|---|---|
| `validation/deliveries.json` | Normalized current delivery manifest, populated by downloads |
| `validation/validation.db` | Delivery states, attempt history and accepted sample evidence |
| `validation/inventory/` | Search snapshots, production inventories and download receipts |
| `validation/cache/s5/` | Revisioned production assets and SHA-256 receipts |
| `validation/cache/reference/landsat/` | Shared scene assets, unsigned metadata and receipts |
| `validation/results/<product>/.../` | Paired NPZ arrays, scene details, metrics and plots |
| `validation/report/validation_report.html` | Local report with links to evidence and metric trends |

All runtime files are ignored by Git. Do not move cached files individually: current receipts contain absolute local paths. Prefer fresh downloads on another machine. To archive/share a campaign, preserve the complete state plus configuration and document relocation limitations; raw data access rights still apply.

## Campaign isolation

Copy `config/validation.json` to ignored `config/validation.local.json`, edit campaign dates/policies, and use a fresh state directory:

```bash
iride-validate --config config/validation.local.json --workdir validation-campaign-b --landsat
```

The default manifest is placed in the chosen workdir automatically. If you explicitly configure another manifest path, that path is honored. Configuration paths resolve against the repository root (`--base-dir` can override it); command-line paths resolve against the invoking working directory.

Do not reuse a completed registry for changed scientific policy without an explicit revalidation decision. COMPLETE and OUTSIDE_CAMPAIGN entries are skipped; thermal DIAGNOSTIC entries are retryable and their method fingerprints change with relevant policy/code. A new workdir is the supported way to rerun a different campaign or method. Keep the configuration snapshot with your evidence.

## Retry and failures

Completed, checksum-valid downloads are reused. Interrupted assets restart individually; transfers do not resume at an arbitrary byte offset. `.part` files are never treated as complete. Failed production/reference assets and scientific outcomes are preserved for inspection. A later launch retries pending and failed work.

Exit codes:

- `0`: command completed; this includes pending configuration/reference, rejected QA and diagnostic scientific outcomes. It does not mean contractual acceptance.
- `1`: configuration, dependency or top-level setup failure.
- `2`: production/reference download failures or current delivery ERROR outcomes.
- Argparse also uses `2` for invalid command arguments; distinguish this by its usage/error message.

Never run concurrent launches sharing a manifest/workdir. Back up the registry and configuration before maintenance. Remote connectors list/read objects; they never modify production buckets.

## Operational campaign launch

`python main.py` (no parameters) and `iride-validate --run` perform production preflight, campaign-wide discovery, selective verified downloads, reference refresh, processing and report generation. See [PyCharm guide](PYCHARM.md). Specialist/local modes retain their existing behavior. A failed preflight makes no registry changes; individual provider or delivery failures are recorded and do not stop other deliveries.
