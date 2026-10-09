# Troubleshooting

| Symptom | Action |
|---|---|
| `iride-validate` not found | Activate `.venv` or use `.venv/bin/python validate.py` (Windows: `.venv\Scripts\python.exe validate.py`). Install the package from this repo. |
| `No module named rasterio/numpy/matplotlib` | Install `python -m pip install -e '.[scientific,dev]'` in the interpreter you run. Use `python tools/doctor.py`. |
| GDAL/compiler error during pip installation | Check Python 3.11/3.12, 64-bit OS and current pip; prefer binary wheels. See Installation. |
| MinIO `mc` missing or wrong program | Install the MinIO client, add it to PATH, verify `mc --version`. Linux's Midnight Commander package is a different program. |
| Credential placeholder/missing/quoting error | Use the external env-file template, replace placeholders, quote values and verify selection precedence. The parser does not execute shell expressions. |
| Existing alias works but pipeline credentials fail | A selected/default credential file takes precedence over the alias. Correct that file or remove the unwanted selection; no silent fallback occurs. |
| S3 timeout/DNS/unreachable endpoint | Connect the approved VPN/network and check endpoint reachability. Public internet alone may not reach IRIDE S3. |
| S3 access denied | Obtain ListBucket/GetObject for the relevant output bucket/prefix. INT/UAT credentials are not necessarily valid in PROD. |
| TLS certificate failure | Resolve trust/certificate configuration with the administrator; do not disable TLS verification. |
| Missing or changed bundled AOI | Restore the file and manifest from Git. Put intentional replacement geometry in a separate file referenced by a local config. |
| Runtime directory cannot be created | Select a writable local `--workdir`; check disk space, mounted drives and ownership. Avoid running two launchers against the same state. |
| Recent Landsat dates absent | Archive publication/processing latency, Tier-1/L2SP eligibility and exact AOI intersection determine candidates. Missing catalogue data do not prove no satellite overpass occurred. |
| HTTP 429 / expired signing token | Retry later; container tokens are cached in memory and refreshed on a new launch. Do not paste signed URLs into issues. |
| No matching S5 product for a reference day | Inspect `thermal_match_downloads.json`. Reference availability and production delivery availability are independent. |
| `PENDING_REFERENCE` | No downloaded usable same-day reference over the product's domain. Refresh with `--landsat`; inspect catalogue eligibility and scene receipts. |
| `REJECTED_QA` | Inspect scene pair counts and QA/support settings. Do not weaken filters solely to force an accepted sample. |
| `DIAGNOSTIC` and zero sample coverage | Expected current thermal behavior: timing, approved QA, sub-AOIs, spatial tolerance and campaign aggregation are not finalized. Metrics are preserved. |
| Negative R² | Predictive R² can be negative; it is not squared correlation. Review the reference spread, bias and distribution. |
| Missing rRMSE | Check normalization configuration, paired values and zero reference mean. Celsius mean normalization is explicit for current thermal products. |
| Opening a moved report breaks images | Evidence links are relative to the full workdir; keep reports/results together. Cached receipts contain absolute paths and need deliberate migration. |

When reporting a problem, include command options without credentials, Python/OS versions, state/reason, product/acquisition and a short sanitized error. Never attach env files, MinIO configuration, VPN information or signed URLs. Follow your organization's approved private incident channel if sensitive access details are required.
