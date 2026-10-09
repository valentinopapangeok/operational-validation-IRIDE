# Validation metrics and reference requirements

Source: user-supplied validation plan `OSVP_Contrib.Partners_v2_S5-02_revised.xlsx`, sheet `Batch1`, S5-02 rows 11–21. The application preserves the exact method, target, sampling, spatial coverage and clarification text, together with merged-cell source addresses and the workbook SHA-256 in `config/validation_plan_source.json`. The source workbook is read without modification. The application does not assume the proposed sample volumes are acceptance criteria.

## Campaign decisions before production validation

- Specify the 90-day campaign interval explicitly, using timezone-aware start (inclusive) and end (exclusive); do not infer it from launch time.
- Supply versioned Italy AOI and North/Center/South geometries. Thermal land and marine products require different sub-AOIs.
- Confirm delivery catalogue, logical product IDs, revision fingerprints and acquisition times.
- Agree what makes an independent observation unit; count acquisitions, hours, months or station-days, never pixels as independent campaign units.
- Define per-product minimum valid pair count, overlap, temporal tolerance, quality flags, units, class mappings, spatial weighting and representativeness strata.
- Define relative-RMSE normalization, its treatment near zero and physical unit conventions (especially temperature). Thermal products use the agreed paired reference mean in Celsius; other products remain unresolved.
- Agree KPI temporal/spatial aggregation and positional accuracy tolerance in metres or pixels. A percentage alone is insufficient to calculate positional accuracy.
- Track insufficient reference availability separately from failed accuracy and insufficient sample coverage.

## Product requirements

| Product | Reference | Proposed observation volume | Thematic target | Positional target |
|---|---|---|---|---|
| 01 LST | Landsat 8/9 | 15 acquisitions; 5 per land sub-AOI | Relative RMSE ≤20% | ≥90% |
| 02 SST | Landsat 8/9 | 15 acquisitions; 5 per marine sub-AOI | Relative RMSE ≤20% | >80% |
| 03 Solar radiation | ERA5-Land | 30 hourly productions | Relative RMSE ≤20% | ≥90% |
| 04 Precipitation | Emilia-Romagna, Lazio and Sicily gauge networks | 30 hourly productions | Relative RMSE ≤20% | ≥90% |
| 05 Snow cover | Sentinel-2 SCL | 15 acquisitions; 5 per sub-AOI | F1 ≥0.80 | >80% |
| 06 Cloud cover | MSG/SEVIRI Cloud Mask | 30 hourly productions | F1 ≥0.80 | ≥90% |
| 07 CH4 | GOSAT FTS L3 CH4 | Up to 3 usable months | Relative RMSE ≤20% | >80% |
| 08 CO2 | GOSAT FTS L3 CO2 | Up to 3 usable months | Relative RMSE ≤20% | >80% |
| 09 AOD | AERONET | 15 station-days; 5 per sub-AOI | Relative RMSE ≤30% | >80% |
| 10 Downscaled LST | Landsat 8/9 | 15 acquisitions; 5 per land sub-AOI | Relative RMSE ≤20% | ≥90% |
| 11 Downscaled SST | Landsat 8/9 | 15 acquisitions; 5 per marine sub-AOI | Relative RMSE ≤20% | >80% |

CH4/CO2 availability is not a mandatory three-month minimum. A usable month requires ≥20% of Italy AOI covered by valid GOSAT cells and, where possible, coverage in at least two sub-AOIs. Include all valid spatial matchups within that month. Month validity and KPI periodicity are separate decisions.

## Metric definitions implemented in the foundation

For paired S5 value p and reference r, error e=p−r:

- RMSE = sqrt(mean(e²)); MAE = mean(abs(e)); bias = mean(e).
- Predictive R² = 1−sum(e²)/sum((r−mean(r))²). It may be negative; it is undefined for constant reference. It is not squared correlation.
- Relative RMSE (%) = 100 × RMSE / explicitly supplied positive denominator. Thermal adapters select the absolute mean paired reference temperature in Celsius, following the explicit campaign instruction. Other products need their own agreed positive normalization.
- Positional accuracy is computed as the percentage of supplied positional distances within an explicitly configured tolerance in metres. The workbook defines the required percentage but not this tolerance or its test population. This helper remains unavailable for real evaluation until those are agreed.
- Binary confusion counts use predicted rows and reference columns: TP, FP, FN, TN.
- Accuracy=(TP+TN)/N; precision=TP/(TP+FP); recall/POD=TP/(TP+FN); F1=2TP/(2TP+FP+FN); IoU=TP/(TP+FP+FN); false alarm ratio=FP/(TP+FP). Undefined zero-denominator scores are null, never perfect scores.
- Precipitation: compute all-pair intensity metrics, binary event detection using an explicit positive rain threshold, and intensity metrics for pairs where both detect rain. No both-wet observations gives null metrics. Report each subset's sample count.

Metric helpers require finite, equally sized, nonempty paired arrays. Nodata handling belongs to explicit QA before calculation. This stage does not pool metrics across deliveries: pooled pair statistics, per-acquisition weighting, spatial weights and uncertainty require an agreed methodology. Reference measurements have uncertainty and are not assumed to be absolute truth.

## Reference data to supply or connect for each adapter

These are integration requirements, not claims about current online availability or final provider settings.

| Reference | Required metadata and scientific decisions |
|---|---|
| Landsat 8/9 | Collection/version, thermal variable and derivation, acquisition time, scaling/offset and units, CRS/footprint, pixel QA and thermal uncertainty flags, land/water masks. Verify suitability of thermal retrieval over marine areas for SST. Compare valid coincident pixels at an agreed common spatial support; downscaled products also need a separate spatial-detail assessment. |
| ERA5-Land | Exact radiation variable/component, units, accumulation interval/reset convention, hourly timestamps, grid and coverage. Resolve energy-to-flux conversion and alignment to S5 instantaneous/hourly definition. Sample daylight with meaningful radiation across solar elevation and cloud conditions. |
| Regional gauges | Provider/network, station IDs and coordinates, accumulation period/timezone, QC, missing-value codes, units, relocation history and access permissions. Agree gauge-to-grid representation, event/region unit and threshold. Include rainy and dry conditions; one network may not cover every area. |
| Sentinel-2 SCL | Product/processing baseline, acquisition time, class mapping to snow/no snow, valid/invalid and cloud/shadow masks, resolution and footprint. Do not classify invalid pixels as no-snow. Include snow where available and snow-free cases, with seasonality retained. |
| MSG/SEVIRI Cloud Mask | Provider/version, mask class mapping including uncertain/invalid states, acquisition time and product interval, geolocation/projection, QA. Compare matched cloud definitions and common support, considering viewing geometry/parallax. Sample clear, mixed and cloudy conditions. |
| GOSAT FTS L3 | CH4/CO2 variable, column/surface definition, units, monthly interval, grid/cell bounds, valid flags and uncertainty. Verify compatibility with S5 physical quantity. Compute Italy coverage using valid cell/AOI intersections; retain all valid cells and areas. |
| AERONET | Station IDs, coordinates, dataset/version and quality level, timestamps, cloud screening, wavelengths, uncertainty and daily aggregation rules. Agree wavelength conversion and time window against S5 AOD. Preserve station and sub-AOI identity in every matchup; count station-day once, use all extra valid matchups. |

## Per-product implementation gate

The workbook specifies that thermal acquisitions feed one overall evaluation per product (`Batch1!K11`, merged through K14). Cross-acquisition weighting remains unresolved. The current thermal rRMSE denominator is the absolute mean paired reference temperature in Celsius; this does not settle the campaign aggregation rule. For hourly products, gases and AOD, the workbook explicitly leaves KPI aggregation or reporting periodicity to be agreed. The production frequency column says daily while several compared outputs are hourly; the importer preserves both source wording and sampling units without changing either.

Before enabling an adapter, record: source access instructions, one representative delivery, reference query and sample, data reader and scaling, explicit QA masks, temporal/spatial collocation, sample identity, metrics and target interpretation, provenance format, plots, and checks using a small real matchup. Freeze the method configuration with the result. First implement one product end to end; reuse its connector components only where appropriate for subsequent products.
