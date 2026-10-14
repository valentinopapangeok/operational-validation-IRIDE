# Equivalent hourly solar radiation: DNI / CAMS BNI

The default `cams_bni` adapter compares hourly integrated **DNI with CAMS all-sky BNI**, both in **Wh/m²**, on identical UTC intervals. This replaces the incompatible DNI/ERA5-Land SSRD comparison. Original workbook requirements remain unchanged: CAMS is a **documented reference deviation**, not a claim of plan approval. [ECMWF radiation definitions](https://www.ecmwf.int/sites/default/files/2023-06/Part-IV-Physical-Processes.pdf) distinguish global horizontal SSRD, direct horizontal FDIR and direct-normal radiation; neither SSRD nor an unconverted FDIR is equivalent to DNI.

## Run and credentials

Use `ADS_KEY` and optional `ADS_URL=https://ads.atmosphere.copernicus.eu/api` in the existing private `credentials/downloaders.env`. Accept the CAMS solar-radiation time-series licence in ADS. No new private file is needed. Connect the VPN and run **main.py in PyCharm with no arguments**, or `python main.py --product S5-02-03`.

The operational provider requests observed-cloud hourly UTC CSV time series from `cams-solar-radiation-timeseries`, checks units/location/intervals/duplicates and all 24 intervals per requested day, and saves checksummed raw responses and receipts. Requests cover missing production-date ranges and use at most four concurrent downloads. Verified responses are reused; current-day references remain pending. Local validation/report/status modes stay offline.

## Draft sampling and selection

`sampling_grid_degrees=1.0` gives a fixed geographic lattice inside the exact bundled Italy polygon (currently 36 sites). Sites are selected before looking at errors; they are model locations, not ground stations, full Italy coverage or confirmed sub-AOIs. Each samples its containing native product cell; nodata/scale/offset are honored and duplicate native cells excluded.

Defaults require reliability ≥0.90, TOA irradiation ≥100 Wh/m², all-sky BNI ≥50 Wh/m², BNI/clear-sky BNI ≥0.50 and at least three sites per hour. These are **provisional configurable policies**, not workbook requirements or thresholds tuned to pass. Transmission is a cloud-effect proxy, not cloud-cover fraction. `selection.json` retains reliable daytime pairs before irradiation/transmission filters, every rejection reason and missing/expected sites. `daylight_all_conditions` metrics accompany selected metrics. Hour 23 remains excluded because inspected production integrates only 23:00–23:30.

## Evidence and interpretation

`results/S5-02-03/<delivery>/<revision>/cams-bni-hourly-v1/<signature>/` contains CSV matches, selection audit, metrics, plots, checksums and receipts. rRMSE remains **100 × RMSE / abs(mean paired reference)** in Wh/m². Spatial pairs are not independent campaign samples.

Successful comparisons retain **PROCESSED** and add **zero accepted samples**. CAMS and CM SAF share satellite information: this is an equivalent-observable **cross-check**, not independent ground validation. Sampling, QA, point/native-cell representativeness, reference deviation, sub-AOIs and KPI aggregation need scientific review. [CAMS documentation](https://www.ecmwf.int/en/forecasts/datasets/cams-solar-radiation-time-series-0) describes BNI and inputs.

## Retained legacy method

`era5_land_ssrd` is blocked by default. Explicit historical reproduction requires a separate config selecting that adapter, `solar.allow_incompatible_ssrd_crosscheck=true`, and a distinct method version/workdir. Such results cannot establish DNI accuracy. Tiny negative legacy increments use two local float32 spacings, a minimum 1 J/m² allowance and hard 5 J/m² cap; material negatives still fail. [Legacy accumulation method](SOLAR.md) is retained for provenance.

Offline regressions cover equivalent quantities, time/units/location rejection, screening, cache/download reuse, secret redaction, incompatible-method blocking and sample safeguards. The synthetic smoke test now exercises DNI/BNI.
