# Keeping and reusing reference data

Reference inputs are retained under `<workdir>/cache/reference/`. Normal runs do not delete this directory. Keep it together with its JSON indexes, per-scene receipts and original reference files. The default workdir is the repository's `validation/` directory; use the same workdir for subsequent launches.

- **Landsat and Sentinel-2:** completed assets with the expected size and SHA-256 are reused. New scenes, changed source revisions, missing files or files failing verification require downloads. Catalogue searches and local checksum verification are not raster downloads. Each revision keeps its own directory.
- **CAMS solar:** verified station time series are reused; requests cover missing date ranges. Existing verified historical files are preserved.
- **AERONET:** public text responses are refreshed because recent observations can change and Level 2.0 can become available later. Responses are stored by content hash; unchanged content reuses the stored file. This refresh transfers small text responses, not the satellite raster cache.

For a large external-drive cache, keep the drive mounted. A local filesystem link from `validation/cache/reference` to an existing persistent reference directory lets the default PyCharm `main.py` run reuse that directory. Establish the link only when the default directory is empty; do not replace a populated directory or discard its receipts. Do not run two validation processes against the same cache/workdir concurrently.

The cache is private runtime data and is ignored by Git. Cloning the repository does not download historical caches automatically. Do not delete or clean the cache to rerun metrics: method signatures and evidence hashes distinguish recalculated results from existing inputs. If you relocate the cache to another machine, retain its files and receipts; some indexes contain absolute local paths and require a reviewed relocation rather than simply copying the index.
