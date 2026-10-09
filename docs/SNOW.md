# S5-02-05 snow validation

`main.py` discovers every in-campaign snow delivery and downloads only the Sentinel-2 L2A **SCL** tiles intersecting the bundled Italy land AOI on each delivery's UTC date. Public Planetary Computer STAC and short-lived signing are used; no additional account is needed. Signed URLs and secrets are never persisted. The product is explicitly mapped from its delivered style: **0 no snow, 1 snow**; nodata is masked, unexpected classes fail instead of being silently remapped.

The default method is **draft and DIAGNOSTIC**. Same-day matching is appropriate for an initial comparison but is not a confirmed acquisition-time tolerance for a daily composite. All scene searches, unsigned metadata, verified file receipts and revisions remain under the workdir. Public reference errors are reported; cached inputs can still support diagnostic processing.

## Draft QA and spatial support

SCL **11** represents snow/ice. Classes **4 and 5** are clear non-snow land. Classes 0–3 and 6–10 are excluded (nodata, defective, shadows, water, unclassified, cloud and cirrus). This intentionally conservative land-only mapping needs scientific approval. The official [Copernicus SCL documentation](https://sentiwiki.copernicus.eu/web/s2-processing) defines these classes; the [product documentation](https://sentiwiki.copernicus.eu/web/s2-products) describes known SCL limitations.

The 20 m QA-valid SCL pixel centres are accumulated into each **native S5 cell**. At least 50% approximate clear reference area is required. A native cell is reference snow when at least 50% of its valid SCL centres are class 11. Defaults require 30 pairs per tile and per delivery. These thresholds are configurable draft settings under `snow`, not requirements inferred from the workbook.

Overlapping tiles/overpasses produce **one pair per S5 cell per day**: the tile with highest clear reference coverage wins, with scene ID as deterministic tie-breaker. Tile fragments are not combined. Reference centre area is approximate across projections, and results only describe the matched footprint. Cloud coverage is screened per pixel; no scene cloud-percentage shortcut removes snowy scenes.

## Evidence and metrics

Each revision/method folder contains per-scene pair archives, pooled `paired_data.npz`, scene QA statistics, `metrics.json`, confusion matrix and classification difference map. Pair archives preserve product/reference class, reference snow fraction, clear coverage, row/column, longitude/latitude and source scene ID. The map is thinned for display only; the archive preserves all pairs. Artifact checksums and method signature are in `outcome.json`.

F1 is the required thematic metric (target ≥0.8). Precision, recall, accuracy, IoU and false-alarm ratio are diagnostic. With no positive snow in either input, F1 is **unavailable**, not automatically 1. A snow-free comparison can remain useful evidence but does not independently establish snow detection performance. Pixels and tiles are not independent campaign acquisitions.

North/Center/South boundaries, temporal and QA policies, campaign KPI aggregation and positional tolerance remain unresolved. DIAGNOSTIC outcomes add **no accepted samples**. The plan proposes 15 valid acquisitions, five per sub-AOI; spatial distribution and snowy/snow-free seasonal representation must still be assessed.
