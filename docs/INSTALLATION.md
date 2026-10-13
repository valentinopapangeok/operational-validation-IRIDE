# Installation

## Requirements

- Python 3.11 or 3.12, Git, and a writable local disk. Python 3.10 is not supported by this distribution; newer Python versions are not yet CI-tested.
- Scientific extra: NumPy, Rasterio (GDAL wheels), PyProj, Shapely, Requests and Matplotlib. The `dev` extra adds OpenPyXL for optional workbook imports.
- MinIO client **`mc`** on PATH only for private production inventory/downloads. This is the MinIO client, not Midnight Commander.
- Production access: approved IRIDE VPN/network, endpoint DNS/TLS reachability, and read-only S3 credentials or a configured alias.
- Landsat: internet HTTPS access to `planetarycomputer.microsoft.com` and provider asset hosts under `*.blob.core.windows.net`. No personal Landsat account or credential file is currently required.
- Budget disk space for your campaign. The first observed reference batch of twelve scenes occupied about 1.58 GB; future campaigns can grow much larger. Downloaded results stay local.

## macOS / Linux

Install Python through your preferred package manager or python.org. On macOS, Homebrew installations commonly use `brew install python@3.11 minio/stable/mc`. On Linux, install Python and its venv support, then install the MinIO client using the [official client instructions](https://github.com/minio/mc). Verify `mc --version` reports MinIO.

```bash
python3.11 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[scientific,dev]'
python tools/doctor.py
python -m unittest discover -s tests -v
python tools/smoke_test.py
```

Run from the repository root. The optional `./run_validation.sh` wrapper uses the repository's `.venv/bin/python`; `S5_VALIDATION_PYTHON` can override that interpreter. The installed `iride-validate` command works with the active environment.

## Windows PowerShell

Install Python 3.11/3.12 and Git. Download `mc.exe` using MinIO's official instructions and add its directory to PATH. WSL with the Linux workflow is also an option.

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[scientific,dev]"
.\.venv\Scripts\python.exe tools\doctor.py
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
.\.venv\Scripts\python.exe tools\smoke_test.py
.\.venv\Scripts\iride-validate.exe --status
```

Activation is optional when you use these explicit paths. `tzdata` is installed on Windows for Europe/Rome timestamps. The shell wrapper is for POSIX systems; use the Python/CLI entry point on Windows.

## Dependency reproducibility

`pyproject.toml` defines compatible dependency ranges. `requirements-tested.txt` records the exact scientific/development dependency versions from the verified clean installation. For that snapshot:

```bash
python -m pip install -r requirements-tested.txt
python -m pip install --no-deps -e .
```

Binary wheel availability differs by OS/architecture. If pip starts building GDAL/Rasterio/NumPy from source, first check that you are using a supported Python version and current pip on a mainstream 64-bit OS. A managed conda environment is acceptable if it supplies the declared versions; editable-install this package with `--no-deps` only after checking dependencies.

## Minimal local inspection

`python -m pip install -e .` installs just the registry/CLI functionality. `iride-validate --status` and an empty `--report` work without scientific libraries. Running thermal comparisons, plotting existing diagnostics, the full tests or smoke test requires the scientific extra. The repository configuration and AOIs must remain available; installed commands do not bundle or invent a campaign.

## Product 03 solar radiation

See [Solar methodology](SOLAR.md). Install updated `.[scientific]` dependencies (h5py and cdsapi). `main.py --product S5-02-03` refreshes needed CDS ERA5-Land daily files before local comparisons. Ordinary `iride-validate --product S5-02-03` remains offline. Results are diagnostic only.
