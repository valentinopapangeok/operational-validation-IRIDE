# Changelog

## 2026-10-19 — Recorded campaign decisions

- Added versioned North/Center/South land and marine AOI partitions at 41.5°N and 44°N, preserving the original AOIs.
- Recorded user approval of the evaluated 27.5 km AOD adaptation; NASA is provenance rather than a certification gate.
- Defined positional tolerance as strictly less than declared product resolution, without asserting measured positional compliance.
- Kept the current operational AOD adapter separate from the approved trial pending integration.


## 2026-10-16 — AOD validation reporting

- Display containing-pixel and supplementary 3×3 AOD metrics on identical station-days, with coverage exclusions and pair-pooled metrics.
- Preserve the primary method, acceptance status, product files and production chain.


## Validation method corrections

- Default solar reference is now equivalent hourly CAMS all-sky BNI, using existing ADS credentials, exact-AOI fixed points, explicit screening and all-condition audit metrics; incompatible SSRD comparisons are blocked by default.
- Legacy small negative energy handling is precision-aware and capped.
- Snow uses full-cell snow-area bounds, 90% clear coverage and excludes conflicting overpasses.
- Snow/AOD use optional delivered QA/time/wavelength support; missing support stays diagnostic. AOD adds supplementary 3×3 metrics without replacing the baseline.
- Added regression tests, equivalent-observable smoke coverage and portable guides; no accepted samples are added.


## 0.1.0

- Standalone portable package and `iride-validate` command.
- Automatic resource checks and runtime initialization on every launch.
- Bundled exact land/marine AOIs, checksums and extracted S5-02 validation requirements.
- Read-only production inventory/downloads for eleven output products.
- Shared Landsat 8/9 L2SP cache and diagnostic thermal comparisons for 01/02/10/11.
- Explicit rRMSE normalization by absolute paired reference mean in Celsius.
- External production credential-file support with process-environment transport and error redaction; existing MinIO aliases remain supported.
- Scientific metric helpers, revisioned SQLite evidence and HTML/plot reports.
- Offline synthetic smoke test, fixture-based tests and cross-platform CI.
- Installation, credential, runbook, configuration and product-extension guides.

## Operational campaign orchestration

- Added no-argument `main.py` for PyCharm and `--run` for the CLI.
- Added all-campaign discovery with date boundaries and highest numeric versions, selective downloads, pending retries, reference-date/domain filtering, preflight and error isolation.
- Preserved scientific states, method-aware thermal cache, offline modes and accepted-sample safeguards.
- Added console/JSON run summary, report integration and PyCharm guide.

## Snow and AOD adapters

- Added public Sentinel-2 SCL-only download, conservative pixel QA, native snow-cell aggregation, overlap deduplication, binary metrics and evidence.
- Added AERONET V3 download, explicit quality levels, provisional 550 nm spectral matching and station-day aggregation with paired CSV and diagnostic plots.
- Integrated both into the no-argument main workflow and product-specific report trends; added scientific tests and methodology/credential guides.

## Solar radiation adapter

- Integrated the existing product 03 hourly diagnostic DNI/SSRD method with operational CDS downloads, verified daily reference cache, registry, paired CSV, plots and report trends.
- Preserved original rectangle, reference-mean normalization, midnight reset and hour-23 exclusion. Added scaled-raster handling and offline scientific/provider/lifecycle tests.

## Solar processing label

- Replaced solar runtime DIAGNOSTIC labels with PROCESSED; removed diagnostic metric flag and runtime scientific caveats, preserving methodology documentation and zero accepted-sample accounting. Historical results are retained; shared retry and report handling support PROCESSED.

## AOD source provenance correction

- Accept aligned delivered source IDs and report EO, individual sources and CAMS fill separately. Keep source-unverified historical deliveries explicitly diagnostic.
- Record station exclusions caused by missing source times or insufficient time-window observations; use the same source for neighborhood sensitivity.
- Add offline coverage for model separation, model-only outcomes, source identity validation and source-time exclusions. Normal PyCharm `main.py` launch and credential files are unchanged.
