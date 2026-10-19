# User decisions — 19 October 2026

NASA's MODIS/AERONET method is a starting reference, not a project acceptance standard. The user approves the evaluated IRIDE adaptation: fixed 27.5 km circles, spatial arithmetic mean, ≥20% valid full-circle pixel coverage, four-channel quadratic log-log interpolation to 550 nm, and at least two compatible AERONET observations in the same UTC day. Prefer Level 2.0 when available; Level 1.5 daily means are acceptable for this working method. Missing NASA-specific Level 2.0, overpass-window and MODIS QA requirements remain documented differences, not unresolved user approval gates.

Report the all-eligible pooled rRMSE alongside the passing-only summary. A station-day qualifies for the passing-only summary when its absolute percentage error is ≤30%. Preserve all failed/omitted station-days and reasons. The monthly working objective is five passing station-days with distribution across North, Center and South where available. Passing-only metrics are conditional on selection and do not estimate accuracy of all pixels or all stations. Pixel coverage means the union of sampled valid cells, not independently validated cells.

## Chosen sub-AOIs

Divide the existing exact land and marine AOIs using the same latitude cuts: South below 41.5°N; Center from 41.5°N to below 44°N; North at or above 44°N. The two bundled GeoJSON files contain the AOI intersections with these bands. Their union equals the respective original AOI; positive-area overlaps are absent. Shared boundary points are assigned northward using the stated half-open latitude rule.

This is a project-defined geographic split, not the earlier provisional administrative-region proposal. For example, Rome and L'Aquila lie in Center, while Potenza and Lecce lie in South. Recompute historical area counts from coordinates; do not relabel old provisional counts as confirmed. Land and marine geometries are versioned and checksummed in the AOI manifest.

## Positional accuracy

The approved positional distance tolerance is strictly less than the product's declared spatial resolution. The original per-product required percentage of samples within that tolerance is preserved. A declared resolution/tolerance is not a measured positional accuracy score. No arbitrary metre value is filled where product resolution is not yet read; angular resolution must be converted appropriately if a metre-based calculation is used.

## Implementation status

The approved 09 method is operative through the registered `aeronet_circular` adapter and runs automatically with `main.py`. The legacy containing-pixel adapter remains available for explicit comparisons. New results retain every eligible failure, a separate passing-only summary, daily sub-AOI counts and unique sampled-pixel coverage. Operational status is PROCESSED: the method is approved, and no unresolved NASA certification gate is applied. Formal positional measurements are not invented or inferred from declared resolution. Old evidence files remain available under their original method signatures.
