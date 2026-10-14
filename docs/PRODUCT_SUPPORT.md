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
