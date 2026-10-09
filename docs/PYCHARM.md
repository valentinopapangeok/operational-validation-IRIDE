# Run from PyCharm

1. Open this repository as the project. Select a Python 3.11 or 3.12 environment with the scientific dependencies installed (`python -m pip install -e '.[scientific]'`). Install MinIO `mc` and make it available on the application PATH; restart PyCharm after changing PATH.
2. Put the privately supplied files at `credentials/bucket.env` and `credentials/downloaders.env`. See [Credentials](CREDENTIALS.md). The public Landsat downloader needs no account.
3. Keep `config/validation.json` and the bundled `aoi/` files in place. Connect to the approved IRIDE VPN/network.
4. Open root **`main.py`** and press **Run**. Leave script parameters empty.

The script locates the repository from its own filename, so the IDE working directory does not matter. Missing runtime directories and the delivery manifest are created automatically. Outputs remain under `validation/` unless `--workdir` is supplied. Nothing depends on the PyCharm API.

The same launch from a terminal is `python main.py`. The installed equivalent is `iride-validate --run`. To inspect local results without remote access, run `main.py --status` or `main.py --report --open-report`. `validate.py` retains its existing local default; use `validate.py --run` for network orchestration.

Each operational launch inventories all campaign partitions and selects the highest numeric version for each acquisition timestamp. Completed deliveries are skipped. Implemented products download verified inputs and refresh required references; pending results are retried. Unchanged thermal diagnostics and QA rejections reuse their existing evidence. Products with missing adapters are recorded as `PENDING_CONFIGURATION` without large asset downloads.

The console summary and `validation/inventory/operational_run.json` distinguish newly transferred production deliveries from verified cached deliveries, newly transferred reference scenes, current delivery states and errors. Successful execution can contain pending references or QA rejection; it does not establish scientific acceptance. The HTML report is rebuilt at the end, including partial results if a provider or delivery fails.

A failed production preflight stops before initializing or modifying the registry. An unavailable public reference provider is recorded as an operational error; usable cached references may still be processed. Exit codes: 0 completed orchestration, 1 configuration/preflight failure, 2 partial operational failures. Run only one process per workdir. No scheduler or automatic VPN control is included.
