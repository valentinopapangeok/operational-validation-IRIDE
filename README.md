# Operational Validation — IRIDE

Manually launched scientific validation for IRIDE S5-02 products. The pipeline downloads production deliveries and reference data, applies quality filters, collocates observations, calculates metrics, and keeps a local SQLite registry with reproducible evidence and HTML reports.

**Implemented:** production downloads for all eleven products; Landsat 8/9 thermal reference downloads and preliminary comparisons for **01 LST, 02 SST, 10 downscaled LST and 11 downscaled SST**. Products 03–09 have imported requirements and shared metric helpers; their scientific adapters still need implementation.

Thermal results are currently **DIAGNOSTIC**. They do not count as accepted campaign acquisitions or establish overall compliance. The shipped campaign is **1 October–31 December 2026**. Thermal rRMSE uses the absolute mean paired reference temperature **in Celsius**; this convention is explicit and scale dependent.

## Quick start

Python **3.11 or 3.12** is tested. From a terminal:

```bash
git clone https://github.com/valentinopapangeok/operational-validation-IRIDE.git
cd operational-validation-IRIDE
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[scientific,dev]'
python tools/doctor.py
python -m unittest discover -s tests -v
python tools/smoke_test.py
iride-validate --status
iride-validate --report --open-report
```

The synthetic smoke test runs the actual thermal adapter, collocation, metrics, plots and registry **without credentials or network access**. Its generated report is `validation/smoke/report/validation_report.html`; these are explicitly synthetic measurements.

Windows and MinIO installation instructions are in [Installation](docs/INSTALLATION.md). The repository includes the exact land and marine AOI geometries and extracted validation-plan requirements. No private production data, caches, local absolute paths or real credentials are needed to clone and run the tests.

## PyCharm: connect VPN and press Run

After installing dependencies and MinIO `mc`, place the supplied private credential files in `credentials/`, connect the VPN, and run root **`main.py` with no arguments**. It discovers every campaign delivery, downloads inputs for implemented adapters, refreshes needed references, validates, and rebuilds the report. Paths are resolved from the repository, independent of the IDE working directory. See [PyCharm guide](docs/PYCHARM.md).

```bash
python main.py
# Installed equivalent:
iride-validate --run
```

Products 03–09 remain `PENDING_CONFIGURATION` until their scientific adapters are implemented. An operationally successful run does not imply accepted validation samples.

## Run against production

Install MinIO **`mc`**, connect to the approved IRIDE network/VPN, and obtain read-only production access. Create the external credential file described in [Credentials](docs/CREDENTIALS.md); drop the privately supplied files into `credentials/bucket.env` and `credentials/downloaders.env` (automatically discovered, ignored by Git). Alternatively reuse an existing `iride-prod` MinIO alias.

```bash
# Read-only connectivity/inventory check; no asset downloads
iride-validate --inventory-only --product S5-02-02

# Latest production acquisition per product, followed by local validation
iride-validate --download-latest

# Search only: public Landsat catalogue, no production credentials needed
iride-validate --landsat-search --as-of 2026-10-09

# Download references and matching production dates; then compare
iride-validate --landsat --as-of 2026-10-09

# Modest initial reference-only trial; skip private production access
iride-validate --landsat --max-scenes 1 --skip-matching-deliveries

# Reuse local data and refresh comparisons without network downloads
iride-validate --product S5-02-02
```

`--product` restricts S5 downloads/comparisons; the Landsat reference search/cache is shared across thermal products. `--max-scenes` limits reference downloads, not the search or all matching S5 dates. No background scheduler is installed. Do not launch two processes against the same state directory.

## Guides

| Guide | Purpose |
|---|---|
| [Installation](docs/INSTALLATION.md) | Operating systems, Python environment, GIS libraries and MinIO |
| [Credentials](docs/CREDENTIALS.md) | Exact files/variables, precedence, network and permissions |
| [Runbook](docs/RUNBOOK.md) | Daily commands, outputs, retries, campaign changes and exit codes |
| [Configuration](docs/CONFIGURATION.md) | AOIs, manifest paths, scientific policies and reference settings |
| [Adding products](docs/ADDING_PRODUCTS.md) | Adapter contract, registration, evidence, metrics and tests |
| [Architecture](docs/ARCHITECTURE.md) | Registry states and integration contracts |
| [Landsat methodology](docs/LANDSAT.md) | Scaling, QA, spatial support, rRMSE and limitations |
| [Requirements](docs/REQUIREMENTS.md) | Imported workbook targets and sampling requirements |
| [Testing](docs/TESTING.md) | Offline checks, clean installs and optional integration tests |
| [Troubleshooting](docs/TROUBLESHOOTING.md) | Installation, credentials, VPN, availability and scientific states |
| [Contributing](CONTRIBUTING.md) | Development workflow and review expectations |

## Repository layout

```text
s5validation/       installed Python package, CLI and scientific adapters
config/             portable campaign defaults and extracted workbook provenance
aoi/                exact versioned land/marine GeoJSON geometries
docs/               operator and contributor guides
examples/           credential template and adapter skeleton
tools/              offline doctor, synthetic smoke test, workbook importer
tests/              network-free scientific and lifecycle tests
validation/         ignored local manifest, cache, database, results and reports
```

The original validation workbook is not distributed. `config/validation_plan_source.json` preserves its filename, SHA-256, exact requirement text and merged-cell addresses. Runtime folders are checked/created on every launch. Raw data, populated environment files and runtime outputs stay outside Git. Software licensing has not been assigned; repository access does not grant rights to redistribute underlying data.
