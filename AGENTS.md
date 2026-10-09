# Repository instructions

- Preserve explicit user decisions and workbook source provenance.
- Read README.md and docs/ADDING_PRODUCTS.md before extending an adapter.
- Keep all runtime data under the configured workdir and initialize missing directories. Shared resources must be versioned in the repo or reproducibly downloaded.
- Never commit credentials, signed URLs, caches, private measurements, database files or workstation-specific paths. Use external default credential files with placeholder templates only.
- Keep normal local/status/report runs free of unexpected network access. Remote connectors are read-only.
- DIAGNOSTIC outcomes must not add accepted campaign samples. Pixels are not independent campaign acquisitions. COMPLETE is not automatic contractual acceptance.
- Add meaningful offline scientific/lifecycle tests for implementation changes; use the synthetic smoke test for end-to-end verification.
- Run `python -m unittest discover -s tests -v`, `python tools/smoke_test.py`, and `python -m pip check` before delivering code changes.
- Do not enable live-network tests in default CI. Document optional integration commands separately.
