# Testing

## Offline verification

Use a clean environment installed from the checkout, then run:

```bash
python -m pip check
python tools/doctor.py
python -m unittest discover -s tests -v
python tools/smoke_test.py
iride-validate --status
iride-validate --report
```

No test contacts providers or uses real credentials. Tests cover metric boundaries/undefined cases, revision lifecycle, sample deduplication, error isolation, HTML escaping, source-cell provenance, source-change/checksum download rejection, Landsat scaling/QA, native-cell aggregation, duplicate overpasses, rRMSE normalization, credential parsing/redaction and portable initialization.

The synthetic smoke test uses 4×4 Landsat-like reference arrays and 2×2 S5-like cells with a known +2 °C difference. It runs the actual adapter and plot/report code and asserts four diagnostic pairs, expected absolute/relative RMSE, and zero accepted samples. It writes only into its chosen ignored workdir. Synthetic results are never presented as product performance.

GitHub Actions runs the offline suite and smoke test on Linux, macOS and Windows with Python 3.11 and 3.12. No S3, VPN, GitHub or reference credentials are injected into CI. The CI configuration is part of the repository; a passing local test run is distinct from a passing remote workflow.

## Optional integration checks

Production access and public provider availability are intentionally opt-in:

```bash
iride-validate --landsat-search --as-of 2026-10-09
iride-validate --inventory-only --product S5-02-02
iride-validate --landsat --max-scenes 1 --skip-matching-deliveries
```

Public catalogue results can change because of archive latency/reprocessing; do not assert exact scene counts in offline tests. Use small bounded downloads first. Record acquisition dates, provider revisions, cache integrity and scientific states, not just process exit status. A real full comparison may require downloaded matching S5 data.

## Extending tests

Add fixture-based tests that challenge the method: invalid nodata/classes, masks, grid mismatch, no overlap, exact time boundaries, insufficient support, sample identity and expected metrics. Preserve reasons for rejected cases. Test credentials using invented values in temporary files; never copy a production file into a fixture. Do not introduce live-network tests into the default suite.
