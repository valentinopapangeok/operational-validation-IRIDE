# Product-only push validation

The local preparer uses the IRIDE VPN to find the newest delivery **independently
for each product**. It retains verified downloads in `validation/push_staging` and
copies only the primary production raster into `daily-inputs/YYYY-MM-DD/S5-02-NN/assets/`.
Each adjacent `manifest.json` records the UTC acquisition, revision, size and
SHA-256 of every asset. Dates can differ between products. The preparer does not
commit, push, or copy reference observations into `daily-inputs`.

```bash
python tools/prepare_latest_inputs.py
git status --short daily-inputs
```

Git LFS tracks package assets. The push workflow runs when `daily-inputs/**`
changes on `main`, finds every changed product/date pair, checks its hashes,
downloads its references from the providers, and validates without contacting
the IRIDE production bucket. A manual workflow input accepts one manifest path
for retry. Provider credentials belong in the GitHub Actions secret
`S5_DOWNLOADER_ENV`, using the `downloaders.env` format documented in
`docs/CREDENTIALS.md`. Never commit that file or put credentials in a manifest.

**Repository visibility:** the current remote was verified public on 2026-11-02,
and the user explicitly chose it for product packages and fitted coefficients.
Confirm the repository's Git LFS quota before pushing a large daily set.

The hosted runner uses temporary storage for reference measurements and deletes
that workdir after the job. Only MeteoHub request metadata, without gauge
observations, is retained in an Actions cache so a later retry can download from
the same remote request ID. A cache eviction or remote output expiry can still require
manual reconciliation. No raw product or reference files are uploaded as Actions
artifacts. The job prints processing states; `COMPLETE` remains distinct from
contractual KPI acceptance.

## Product 04 uncertainty

`models/product04_frozen_20261030.json` contains fitted coefficients and SHA-256
provenance for 55 verified hours across 17 dates, through 2026-10-25. It contains
no gauge observations. For a training date the stored date-held-out fit is used;
for later dates the prospective fit uses only earlier training dates. The export
was checked against the existing model calculations exactly. New gauge observations
are still downloaded on the hosted runner for each pushed product hour. The frozen
model must be renewed deliberately when the scientific baseline is revised; a
historical date absent from its held-out set remains unevaluated.

## Trial readiness

Offline tests and a synthetic smoke test verify the code path, including that it
does not open production storage. A live hosted trial still requires: Git LFS
capacity, `S5_DOWNLOADER_ENV` for non-public providers, and a provider connectivity
test from a GitHub-hosted runner. Some references are
asynchronous; a first run may return `PENDING_REFERENCE` and need a manual retry.
