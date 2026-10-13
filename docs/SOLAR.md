# Product 03 solar radiation

The `era5_land_ssrd` adapter ports `iride-input-availability` product 03 `validate_hour.py` into the common operational launcher, verified cache, SQLite registry and HTML report. The source script SHA-256 is recorded in `solar.source`; no other checkout or historical measurements are runtime dependencies.

## Method and limitations

An S5 TIFF labelled H covers H to H+1 UTC, in Wh/m² (hourly integrated direct normal radiation, DNI). ERA5-Land `ssrd` is total downward solar radiation accumulated in J/m². For hour 00, use validity 01 directly after the daily reset; otherwise subtract validity H from H+1, then divide by 3600. Missing hours remain PENDING_REFERENCE; malformed units/grids or negative differences below −1 J/m² produce ERROR. Tiny negative differences down to −1 are clipped to zero, as in the original script.

Hour 23 remains excluded (REJECTED_QA), because the original production pipeline integrated 23:00–23:30. This assumption needs review against current production before changing the policy. All timestamps must be exact hours with explicit offsets, normalized to UTC. Outside-campaign deliveries are skipped.

Average valid product values onto the regular ERA5 grid using Rasterio average resampling. Honor product scale/offset, nodata and finite nonnegative values, plus NetCDF fill/scale/offset. Select reference cell centres in the inherited Italy rectangle [6.5, 35.3, 18.7, 47.2] (west/south/east/north). This is deliberately not silently changed to an exact land mask or invented sub-AOIs. Coverage is the inherited approximate Rasterio valid-pixel fraction within intersecting support; it does not measure full-cell footprint overlap and defaults to >0.

Compute RMSE, MAE, signed bias, predictive R², and rRMSE = 100 × RMSE / abs(mean paired reference). A zero denominator yields unavailable rRMSE; no best-hour ranking or favourable subset selection is introduced. All valid candidate hours, including zero-reference hours, retain paired evidence.

DNI and SSRD are different physical quantities. The source workbook requests a directly comparable observable and daytime/cloud selection, which this inherited method does not establish. Successful comparisons are labelled PROCESSED in the application, with zero accepted campaign samples. This neutral processing status does not resolve the scientific limitations or constitute acceptance. QA/support thresholds, normalization, cloud/daylight policy, spatial/temporal representativeness, sub-AOIs and positional tolerance remain unresolved.

## Usage

Install the updated scientific extra, then connect the approved IRIDE VPN and run:

```bash
python -m pip install -e '.[scientific,dev]'
python main.py --product S5-02-03
```

The ordinary no-argument main.py also includes 03 with the other implemented products. Each delivery is an hour, not a whole-day raster. Only operational launches request references. For local recomputation/reporting:

```bash
iride-validate --product S5-02-03
iride-validate --report --open-report --product S5-02-03
```

CDS credentials are read through existing `credentials/downloaders.env` discovery (`CDS_KEY`, optional `CDS_URL`, default https://cds.climate.copernicus.eu/api). Accept the ERA5-Land dataset terms in the CDS account. Credential presence does not prove access. Recent dates may not yet be published; failed requests are operational errors retried at the next launch, never interpreted as zero radiation.

Requests are restricted to queued campaign delivery days and the configured rectangle. One daily file supplies 24 validity hours; adjacent hours reuse it. Verified unchanged files are reused; each request and response checksum is preserved, and incomplete files are not promoted. ZIP responses containing one NetCDF are supported without extracting untrusted paths. Only NetCDF4/HDF5 SSRD(time,latitude,longitude), descending latitude and ascending longitude are supported; unexpected formats fail explicitly. Secrets and signed response URLs are not written to evidence; request failures retain exception class plus a generic action hint.

`validation/cache/reference/era5_land/` stores daily references and receipts; `inventory/era5_land_refresh.json` summarizes downloads and failures. Revision/method-scoped `results/S5-02-03/` stores matched_cells.csv, metrics.json, comparison.json, plots and checksummed outcome.json. Report links and time trends use the existing shared evidence machinery. Use a fresh workdir for a different campaign/method and keep only one launcher per workdir.

## Verification

`python -m unittest discover -s tests -v` includes synthetic ERA5/HDF5 fixtures, de-accumulation/reset, units, masks, scaled rasters, undefined nighttime rRMSE, no accepted samples, safe cache/ZIP handling and mocked CDS refresh. No live provider is accessed by default.

Scientific reference: https://confluence.ecmwf.int/pages/viewpage.action?pageId=505384848
CDS dataset: https://cds.climate.copernicus.eu/datasets/reanalysis-era5-land

## Bounded operational trial

```bash
python main.py --product S5-02-03 --acquired-from 2026-10-01T10:00:00Z --max-deliveries 3
```

The optional inclusive acquisition filter is applied to campaign inventory, then at most N deliveries are selected in product/time order. Downloads, reference refresh and scientific processing are limited to that selection, including already cached deliveries; repeated runs reuse the same subset rather than taking N new acquisitions. Full inventory search still runs. Without these options the full campaign behavior is unchanged. Select daylight hours for meaningful solar diagnostics; selection is for a functional trial, not a representative campaign sample. The existing cache is reused; other manifest entries and historical results are retained, so the report may include earlier work. `--max-scenes` is a separate Landsat-only limit. Stop an existing launcher before starting a trial in the same workdir.

## Processing labels

At the user's request, the solar adapter uses PROCESSED and removes diagnostic flags, plot titles and scientific caveats from runtime output; methodological limitations remain documented here. The method version is era5-land-hourly-v2. Earlier stored DIAGNOSTIC outcomes and artifacts remain historical until an explicit rerun recomputes them; no historical results are silently relabelled or deleted. Shared infrastructure still understands earlier states and other products retain their existing labels. PROCESSED remains retryable for changed references or settings and contributes no accepted samples.
