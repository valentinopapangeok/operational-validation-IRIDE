# Configuration and bundled resources

`config/validation.json` is the complete portable default for the existing October–December 2026 campaign. It includes all eleven product requirements, source-cell provenance, production bucket conventions and Landsat policy. Local overrides can be kept in ignored `config/validation.local.json`.

## Path resolution and initialization

Paths inside JSON are relative to the repository root by default, not the directory containing the JSON file. The CLI infers that root as the parent of the config directory; `--base-dir` explicitly selects another root. Absolute paths are supported for local data. Prefer repository-relative paths in shared configurations.

- Bundled land geometry: `aoi/italy_land.geojson` for products 01/10.
- Bundled marine geometry: `aoi/marine.geojson` for products 02/11. It is the existing service marine AOI spanning a broader Mediterranean region, not simply Italy's land bounding box.
- `aoi/manifest.json`: byte counts and SHA-256 for the exact imported geometry files.
- Default runtime manifest: `validation/deliveries.json`; it is redirected into `--workdir` if using another state directory.
- `delivery.credentials_file: null`: automatically discovers `credentials/bucket.env`, then the external home default credential file, or uses a MinIO alias. No path customization is needed for normal setup. `references.credentials_file: null` likewise discovers `credentials/downloaders.env` or the documented home default.

Every launch checks configured AOI files and campaign dates, and creates missing runtime directories and the manifest. The source geometries are bundled and verified, not fetched from an unversioned online boundary service. If a bundled file is missing or changed unexpectedly, the command gives a clear setup error. Restore it from Git; for intentional new geometry, add a separate file and set its path in a local configuration. North/Center/South boundaries are not invented; `campaign.sub_aoi_file` remains null until agreed.

## Campaign fields

`start` is inclusive; `end` is exclusive; both must have UTC offsets. The default is `[2026-10-01T00:00:00Z, 2027-01-01T00:00:00Z)`, 92 calendar days. The workbook's proposed 90-day wording is preserved in `duration_days` and explained in `duration_note`. `areas` contains North/Center/South; `kpi_aggregation` and sub-AOI geometry are unresolved.

## Production connection

`delivery.mc_alias` defaults to `iride-prod`. `delivery.endpoint_url` describes the expected endpoint. The actual connection is selected by the external credential file or MinIO configuration; see [Credentials](CREDENTIALS.md). Current output naming and grouping are specific to `ou-s5-02-NN-prod/ar_delivery/YEAR=.../MONTH=.../DAY=.../` and `IRIDE-S_S5-02-NN_TIMESTAMP_Vn.tif`.

`bucket_pattern`, `prefix`, `aws_profile`, `bucket` and some inherited metadata fields are descriptive/reserved; changing them does not create an arbitrary storage connector. To support another product naming/storage hierarchy, extend the connector and add grouping tests.

## Delivery manifest

The downloader writes entries like:

```json
[
  {
    "product": "S5-02-01",
    "delivery_id": "stable-delivery-id",
    "revision": "verified-content-hash-or-version",
    "uri": "/local/path/to/downloaded-temperature.tif",
    "acquired_at": "2026-10-02T00:00:00Z"
  }
]
```

This is a format example, not a measurement shipped with the repo. Keep one current revision per `(product, delivery_id)`. Thermal adapters require a readable local raster, not an S3 URI. The production downloader produces that local path automatically. Historical registry evidence remains available when current manifest entries change.

## Scientific policy

Each product includes its `kind`, `adapter`, units, reference, sample requirements, thematic/positional targets, required metrics and configuration placeholders. Products 01/02/10/11 use `landsat_l2`; other adapters are null.

Landsat policy is under `landsat`: catalogue/provider, platforms, required assets, exact AOIs, band number, QA flags, uncertainty/cloud-distance limits, minimum cell coverage, pair threshold and method version. QA is currently draft. Thermal rRMSE uses `relative_rmse_normalization: mean_reference_in_data_units`, equivalent to `100 * RMSE / abs(mean(paired reference °C))`. A zero denominator produces null; no unit conversion or near-zero epsilon is silently inserted. Fixed positive denominators are supported via the per-product `relative_rmse_denominator`, but do not configure both modes. Other products need their own normalization decisions.

When adding or changing a method, update its version, evidence and tests. `None`/JSON `null` means unresolved, not automatically disabled validation requirements. Do not set invented values just to allow COMPLETE outcomes.

## Validation-plan source

`config/validation_plan_source.json` records the supplied workbook's basename, SHA-256, sheet and original merged-cell addresses. A colleague can use the extracted requirements without obtaining the workbook. Re-importing is optional:

```bash
python tools/import_validation_plan.py /path/to/OSVP_Contrib.Partners_v2_S5-02_revised.xlsx
```

Install the `dev` extra first. The importer reads without changing the workbook and preserves source-cell relationships. Review the configuration diff before committing an updated plan. Local workbook binaries are ignored by Git. Re-importing targets does not itself establish an approved scientific method.

## Draft 05/09 policies

`snow` controls explicit product/SCL classes, same-day selection, valid cell coverage, snow fraction and minimum pairs. `aeronet` controls provisional wavelength, quality-level preference, spectral matching and minimum station-day observations. These settings produce diagnostic results and do not fill the unresolved contractual policy fields. See [Snow](SNOW.md) and [AOD](AOD.md). No-argument `main.py` picks both adapters automatically from product configuration.
