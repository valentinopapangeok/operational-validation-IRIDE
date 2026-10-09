# Adding a validation product

The repository contains all eleven S5-02 requirement definitions. Products 03–09 need scientific adapters. Start with one product end to end, using synthetic/offline fixtures before optional real-data integration. Product frequency, reference availability, proposed observation volume and scientific accuracy are separate concerns.

## 1. Define the observable and method

Record variable, units, source/reference version, scaling, nodata, temporal interval/timezone, quality flags, geometry, spatial support, minimum valid overlap/pairs, uncertainty, independent sample identity, normalization, weighting and positional tolerance. Use [Requirements](REQUIREMENTS.md) and the source cells in each product's `plan` field. Do not infer scientific decisions from a percentage target alone.

Examples of required reference decisions: ERA5 accumulation-to-flux/time alignment; gauge accumulation windows and station QC; Sentinel-2 snow/cloud class mappings; MSG uncertain masks and parallax; GOSAT column quantity and valid Italy coverage; AERONET wavelength/time matching. Land and marine thermal observables may also require different validation policies.

## 2. Implement the provider and adapter

Copy `examples/product_adapter.py` to a new module under `s5validation/`; replace the skeleton's pending outcome with actual QA, collocation and metrics. Providers belong in `s5validation/references/` when reusable. Download references through an explicit mode or a clearly documented provider interface; ordinary local launches should stay reproducible and predictable.

Return `Outcome(status, reason, reference_status, samples, metrics, provenance)` from `validate(delivery, product_config, workdir)`. Delivery supplies product, stable ID, revision, local URI and timezone-aware acquisition date. See [Architecture](ARCHITECTURE.md).

- Missing usable reference: PENDING_REFERENCE / NOT_AVAILABLE, with reason.
- Reference found but insufficient QA/support: REJECTED_QA / AVAILABLE, with diagnostics.
- Operational failure: exception (runner records ERROR) or ERROR.
- Scientific method still provisional: DIAGNOSTIC / AVAILABLE, with metrics/evidence and **no accepted samples**.
- Fully configured matchups: COMPLETE / AVAILABLE, with metrics, evidence and sample units. COMPLETE means processing/evidence are complete, not that target thresholds passed.

Write artifacts to a safe revision/method-specific path under the supplied workdir. Reject unsafe path components, write metadata atomically, retain checksums and never serialize authentication tokens. Preserve full paired data with coordinates, time, reference identity, QA selection and units so metrics can be reproduced.

## 3. Register it explicitly

Edit `s5validation/adapters.py`:

```python
from .my_product import MyAdapter
from .pipeline import ADAPTERS, register_adapter

def register(config, base):
    if 'my_product_v1' not in ADAPTERS:
        register_adapter('my_product_v1', MyAdapter(config, base))
```

Then set the relevant product's `adapter` to `my_product_v1` in a local config for trials, and update shared defaults only when ready. Registration is explicit Python, not module names evaluated from untrusted configuration. `landsat_l2` is reserved for the built-in thermal factory. Avoid live network access during module import or registration.

## 4. Count independent observations correctly

Pixels are paired observations for metrics, not campaign sample units. Samples should identify acquisitions with sub-AOI, station-days, hours/events or months as appropriate. Every accepted sample needs `sample_key`, `area`, `observed_at` and nonempty `evidence`. `area` must match campaign areas or Italy; time must be inside `[start,end)`.

The registry stores accepted samples only for COMPLETE outcomes. Coverage deduplicates canonical keys/areas and ignores superseded delivery revisions. GOSAT months must additionally satisfy configured Italy coverage; proposed up-to-three months are not a mandatory three-month minimum. Do not count repeated deliveries, overlapping reference tiles or 30 m resampled pixels as independent acquisitions.

## 5. Use shared metrics and add artifacts

`metrics.py` implements continuous RMSE/MAE/bias/predictive R², binary confusion/F1/IoU and precipitation subsets. Thermal NumPy metrics are in `thermal.py`. Inputs must already be finite, valid and collocated; uncertainty and area weighting need explicit methodology.

Expose required target metrics and diagnostic metrics separately. Persist unit/normalization details. Put artifact paths in `provenance.artifacts` to get report links; PNGs display inline. Product-specific charts can use Matplotlib Agg; do not require an interactive display. The current generic time trend assumes temperature metrics and should be extended before enabling nonthermal diagnostic trends.

## 6. Make access portable

Document new provider credential variables, the default external file location, permissions, optional account setup and whether the downloader is public. Supply placeholder-only files under `examples/`. Use a parser/provider SDK; never execute credential files or embed keys in commands/logs/artifacts. Auto-create missing cache/output directories on every relevant launch. Bundle versioned static resources in the repo or implement a reproducible explicit download with provenance; do not require paths from another developer's workstation.

## 7. Test and document

Add offline tests for actual failure modes: units/scaling, nodata/cloud/invalid classes, no overlap, time boundaries, duplicates, zero denominators, sample identity and a small known collocation result. Test incomplete/diagnostic outcomes do not add accepted samples and one failure does not stop other deliveries. External calls must be mocked or explicitly opt-in.

Update configuration, provider/credential docs, methodology, README support status and CHANGELOG. Run the complete offline checks before opening a pull request. Use a fresh workdir for method/campaign changes; do not relabel a provisional adapter COMPLETE to make coverage look sufficient.
