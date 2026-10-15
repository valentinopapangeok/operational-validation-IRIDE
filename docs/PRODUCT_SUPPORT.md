# Delivered observation QA, time and wavelength

05 and 09 cannot infer valid observations or source times from a finite product value. Missing support stays **DIAGNOSTIC**, with explicit verification flags. Production may publish `<product-stem>.validation.json` beside the TIFF:

```json
{
  "schema_version": 1,
  "wavelength_nm": 550,
  "observation_time_raster": "<product-stem>.observation_time.tif",
  "quality_mask_raster": "<product-stem>.qa.tif"
}
```

These are **scientific delivery assets**, not privately shared credentials. They must be generated from actual production QA/lineage, not hand-created by operators. Existing downloads automatically include files starting with `<product-stem>.` and fingerprint them as part of the revision. Ordinary main.py runs require no new private files or path setup.

- Support rasters must match the single-band native grid's CRS, transform and shape exactly. Only adjacent filenames are accepted. Missing files, conflicting declarations and path traversal fail.
- QA is unscaled **1=valid observation, 0=invalid/uncertain**, with nodata masked. Production must document cloud/ambiguous/invalid/suspect and gap-fill-age handling. A finite value is not observation QA.
- Time rasters contain actual per-pixel **UTC Unix seconds since 1970-01-01**, including acquisition time for gap-filled data. Scale/offset and nodata are honored. Use Float64 or appropriate integer storage to avoid float32 timestamp quantization.
- A genuinely uniform single observation may instead declare `observation_time_utc`, e.g. `2026-10-09T10:00:00Z`. Never assign one overpass to an entire priority mosaic. Both time declarations together fail.
- AOD wavelength may come from the sidecar or final TIFF tag `AOD_OUTPUT_WAVELENGTH_NM`. Conflicts fail; a wavelength different from configured AERONET matching blocks comparison.

AOD selects observations within configurable **±30 minutes** of the actual pixel time, retaining one station-day. Snow checks source time against Sentinel-2 within a configurable **two-hour tolerance**. QA screens containing pixels and supplementary AOD neighborhoods. Missing metadata never becomes a verified match.

Support hashes enter result signatures, so corrected delivery metadata triggers recomputation. This interface does not establish acceptance; reference quality, wavelength, representativeness and campaign policy remain separate requirements.

## AOD source identity

AOD support may also declare `source_id_raster`, an adjacent, exactly aligned, single-band categorical raster: **1=MODIS, 2=GCOM-C, 3=Sentinel-3, 4=CAMS model**, nodata=0. No scale or offset is permitted. Its checksum participates in recomputation. Invalid IDs fail validation.

When source identity is supplied, primary AOD metrics use EO retrievals only. CAMS-filled station-days are retained separately in `model_fill_metrics`; `source_metrics` records each source subset. Neighborhood sensitivity uses the containing pixel's source. A model-only result is diagnostic with no EO rRMSE. Deliveries without source IDs retain the explicitly source-unverified legacy diagnostic; no model source is guessed.

Observation time and QA must be propagated alongside the exact selected value. The corrected product code uses nearest-neighbour resampling to preserve one observation per pixel, with MODIS orbit timestamps, native GCOM-C observation times, Sentinel-3 line times and CAMS model valid times. This avoids assigning a mean or arbitrary overpass to mixed observations. MODIS orbit times still have coarser precision than individual scan timestamps. All source and QA policies remain subject to scientific review; support does not make the adapter an acceptance test.
