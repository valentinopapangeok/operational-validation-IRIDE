# Landsat thermal reference workflow

Run `./run_validation.sh --landsat` from the application directory to search the campaign, verify/reuse cached assets, download new Landsat 8/9 references, retrieve available S5 deliveries on matching dates, calculate preliminary comparisons and regenerate the report. `--landsat-search` only refreshes the catalogue. `--as-of YYYY-MM-DD` sets the search cutoff. Ordinary launches reuse local data. No scheduler is installed.

The launcher uses the repository-local `.venv`; the installed `iride-validate` command uses the active Python environment. Install `.[scientific]` for GIS and plotting dependencies. There is no dependency on another project or workstation environment.

## Campaign and exact geometries

The configured calendar trimester is 1 October through 31 December 2026, with 1 January 2027 excluded. This is 92 calendar days; the validation workbook describes a proposed 90-day sampling period. Product 01/10 uses the bundled `aoi/italy_land.geojson` polygon; 02/11 uses the bundled `aoi/marine.geojson` polygon. A catalogue bounding box is only a coarse search envelope; each scene must intersect the exact geometry. North/Center/South boundaries are unresolved and no area counts are invented.

## Reference selection and downloads

Microsoft Planetary Computer `landsat-c2-l2`, platforms Landsat 8 and 9, Collection 2 Tier 1 L2SP. The downloaded reference is ST_B10 (catalogue asset `lwir11`), with QA_PIXEL, QA_RADSAT, ST_QA, ST_CDIST and metadata JSON. L2SR-only scenes are excluded. Both satellites are queried even when one has no available scenes. Catalogue publication latency can leave recent dates empty.

Each scene has a revision derived from unsigned source metadata, six asset receipts, local SHA-256, byte counts and source URLs. Existing files are checked against recorded size and SHA-256 before reuse. Transfers use temporary files and HTTP size/MD5 verification when available. A shared expiring container token is kept only in memory. Remote sources are read only.

## Preliminary scientific policy

Convert valid ST DN 293–65535 to Celsius: `DN * 0.00341802 + 149 - 273.15`. Reject fill, dilated cloud, cirrus, cloud, cloud shadow, medium/high cloud confidence and any radiometric saturation. Draft thresholds: ST uncertainty at most 2 K, cloud distance at least 1 km, at least 50% valid reference coverage within a native S5 cell, at least 30 paired cells per scene. Land/water selection uses QA_PIXEL bit 7. Snow is retained as clear land where the other checks pass. These thresholds are adjustable configuration, not workbook acceptance requirements.

Aggregate the mean of valid 30 m reference grid centres into each native S5 cell; coverage is estimated from pixel area and projected S5 cell area. Landsat thermal resolution is 100 m despite its 30 m delivered grid. The number of grid pixels is never a campaign acquisition count. Overlapping WRS rows from the same satellite/date/path are deduplicated per S5 cell, preferring greater valid coverage then lower uncertainty.

Calculate RMSE, MAE, signed bias and predictive R² in Celsius. Relative RMSE is 100 × RMSE / absolute mean of the paired Landsat reference temperatures in Celsius, following the user’s instruction to normalize in the data unit. It is undefined at a zero reference mean. This convention depends on the temperature scale; Kelvin is not substituted. Store pairs, scene diagnostics, pooled metrics, scatter, error distribution and spatial difference plots. Use S5 temperature band 1; band 2 is not assumed to be a quality mask.

Same UTC day is a preliminary matching rule: S5 delivery metadata provides daily timestamps, not measured per-pixel time differences. Results remain `DIAGNOSTIC`, or `REJECTED_QA` if support is insufficient, and contribute zero accepted campaign samples. Marine comparisons are limited to QA-valid surface-temperature coverage; Landsat coastal water temperature equivalence to the S5 SST observable remains unresolved. No open-ocean completeness is inferred.

Acceptance additionally needs approved QA/support thresholds, justified timing, sub-AOI geometry, positional tolerance and campaign metric aggregation. Product adapters for 01/02/10/11 are implemented; availability of contemporaneous product/reference data determines which comparisons can run.

## Sources

- [USGS Level 2 scaling](https://www.usgs.gov/faqs/how-do-i-use-a-scale-factor-landsat-level-2-science-products)
- [USGS Collection 2 surface temperature and coverage limitations](https://www.usgs.gov/landsat-missions/landsat-collection-2-surface-temperature)
- [USGS QA bands](https://www.usgs.gov/landsat-missions/landsat-collection-2-quality-assessment-bands)
- [USGS band resolutions](https://www.usgs.gov/faqs/what-are-band-designations-landsat-satellites)
- [Planetary Computer SAS access](https://planetarycomputer.microsoft.com/docs/concepts/sas/)
