# S5-02-09 AOD validation

`main.py` searches all campaign production dates, then downloads public [AERONET V3 all-point observations](https://aeronet.gsfc.nasa.gov/print_web_data_help_v3.html) for those UTC dates and the Italy AOI bounding box. Returned stations are additionally filtered by the exact bundled land polygon, so nearby foreign stations are not counted. No AERONET credential is needed.

## Provisional settings

The current product wavelength is **550 nm, provisionally estimated by the user**. Delivered XML does not confirm it. `aeronet.wavelength_status` records this uncertainty. Set `wavelength_nm` to null to block spectral comparisons until confirmed. Changing the wavelength/policy changes the method signature; cached outcomes are recomputed. Units are dimensionless AOD; raster scale/offset and nodata are honored, and negative/nonfinite AOD is rejected.

A valid exact wavelength is preferred. Otherwise, positive observations at the nearest bracketing wavelengths are interpolated linearly in **log wavelength/log AOD** (a local Ångström power-law approximation). No spectral extrapolation is allowed. Direct zero AOD is retained; zero cannot be used in log interpolation. The selected channels and transformation for every contributing observation are retained in `station_days.json`.

The provider retrieves Level 2.0 and Level 1.5 separately. Level 2.0 is preferred per station-day if at least three compatible observations exist; otherwise the draft diagnostic method uses Level 1.5. [NASA defines Level 1.5 as cloud-screened and Level 2.0 as quality-assured](https://aeronet.gsfc.nasa.gov/). **The levels are never relabelled or mixed within a station-day.** Current-day data can change; public responses are refreshed on each operational run, content-addressed files are reused when unchanged, and previous verified reference data may be retained after a failed refresh with an explicit error. Level 1.5 comparisons never imply Level 2.0 acceptance.

## Matching and metrics

Each station's same-UTC-day compatible observations are averaged arithmetically. Duplicate station/timestamps are removed. At least three observations are required by default; moved station coordinates are rejected. This is a **draft daily aggregation**, not confirmation of temporal representativeness of the fused daily S5 product.

One containing native S5 pixel is sampled at the station coordinates, with no interpolation or neighborhood averaging. Stations outside the grid, on masked pixels or with invalid product AOD are excluded and logged. Point-to-pixel representativeness remains unresolved. Each station contributes **one station-day**, irrespective of the number of subdaily observations.

RMSE, MAE, bias and predictive R² use the paired station-days. rRMSE = `100 × RMSE / abs(mean paired station-day reference AOD)` in the data's units. A zero mean yields an unavailable rRMSE; one station-day can produce error metrics but cannot produce predictive R². The target ≤30% is a thematic diagnostic comparison, not overall acceptance.

## Evidence and outstanding decisions

Each revision/method folder contains `paired_station_days.csv`, `station_days.json` with all contributing UTC times and spectral methods, `metrics.json`, scatter, difference histogram and station difference map. Source raw response files, query parameters, levels and checksums are preserved under `cache/reference/aeronet`; the outcome records them and artifact hashes. The report includes diagnostic trends when multiple dates exist.

Confirm the product wavelength, temporal/spatial matching and QA policies, North/Center/South boundaries, KPI aggregation and positional tolerance before counting accepted validation samples. All current outcomes are **DIAGNOSTIC** with no accepted samples. The plan proposes 15 valid station-days, five per sub-AOI, not 15 individual AERONET observations.
