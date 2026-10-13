# Credentials and connectivity

## Exactly what is required

| Operation | Files / access |
|---|---|
| Unit tests, synthetic smoke test, local empty status/report | None |
| Landsat search and reference download | Public internet HTTPS; no account credentials |
| Production inventory, latest download, matching S5 download | MinIO `mc`, IRIDE network/VPN and either the env file below or an existing MinIO alias |
| GitHub clone/push | Your own GitHub authentication; independent of runtime data access |
| Future reference adapters | Provider settings can be supplied in `credentials/downloaders.env`; implementation and provider-specific access checks are still needed |

An AWS credentials file, EarthExplorer account, Planetary Computer API key, GitHub token or colleague's VPN password is **not required by the current reference downloader**. Configure each person's access through the approved administrators; do not share private credential files through this repository.

## Preferred portable credentials file

Preferred zero-configuration file in each checkout:

```text
credentials/bucket.env
```

The folder is created automatically and entirely ignored by Git. Drop the privately supplied bucket file here. The external home-directory alternative is:

```text
~/.config/iride-s3/credentials.env
```

On Windows, `~` means the Python user home, usually `%USERPROFILE%`; the same `.config/iride-s3/credentials.env` relative location is used.

Create it from `examples/bucket.env.example`, outside the checkout. Required assignments:

```dotenv
IRIDE_S3_ENDPOINT="https://s3-servicesegment.iride.earth"
IRIDE_PROD_ACCESS_KEY="REPLACE_WITH_PRODUCTION_ACCESS_KEY"
IRIDE_PROD_SECRET_KEY="REPLACE_WITH_PRODUCTION_SECRET_KEY"
```

Replace both placeholders with production credentials supplied by the IRIDE access administrator. Quote values, especially secrets containing `#` or spaces. Values are literal: `$VARIABLE` and command substitutions are never expanded. Blank lines, comments and optional `export KEY=...` prefixes are accepted. The parser reads assignments; it never executes the file. Extra INT/UAT variables in an existing IRIDE credential file are ignored.

On POSIX systems, use `chmod 600 ~/.config/iride-s3/credentials.env`. On Windows, restrict the file to your account with filesystem permissions. Do not put real credentials into an example, tracked configuration, notebook, issue or CI log.

The program passes a percent-encoded `MC_HOST_<alias>` URL to MinIO **through the subprocess environment**. It does not run `mc alias set`, write credentials to the checkout, or put access keys/secrets in command arguments. Known private values are redacted from MinIO failure messages. This mode leaves your existing MinIO configuration unchanged.

## Selection order

1. `--credentials-file /absolute/path/bucket.env`.
2. `delivery.credentials_file` in JSON configuration (relative to `--base-dir`, or absolute; `~` is supported).
3. Environment variable `S5_VALIDATION_CREDENTIALS` pointing to the file.
4. `<repo>/credentials/bucket.env`, if it exists.
5. `~/.config/iride-s3/credentials.env`, if it exists.
6. If no file is selected, let MinIO use its existing alias/environment configuration.

Explicitly selected missing/invalid files produce a setup error. A selected file overrides the alias credentials for this process. It does not silently fall back if the file is malformed. The endpoint in the credential file is the connection endpoint in file mode; `delivery.endpoint_url` documents the expected service endpoint and does not override MinIO's selected connection configuration.

## Existing MinIO alias alternative

If your machine already has `iride-prod` in `~/.mc/config.json`, and none of the credential files above is selected, the pipeline uses that alias directly. MinIO's own `MC_CONFIG_DIR` and `MC_HOST_<alias>` environment mechanisms also work. The configurable name is `delivery.mc_alias`; default `iride-prod`.

For a new alias, follow MinIO's official setup and your organization's secret-handling procedures. The portable env-file mode avoids needing a persistent alias at all. Never copy a colleague's entire `~/.mc/config.json` into the repository.

## Minimum production permissions

Grant `ListBucket` for `ar_delivery/`, `GetBucketLocation` for MinIO region discovery, and `GetObject` (including object metadata/HEAD) in `ou-s5-02-01-prod` through `ou-s5-02-11-prod`, or just the buckets the person will test. No upload, delete or bucket-administration permissions are used. Input collection and service storage buckets are not read by this connector.

Connect to the approved VPN/network yourself before production commands. The application does not automate VPN sign-in, store VPN credentials, or disable TLS verification. Verify:

```bash
mc --version
iride-validate --inventory-only --product S5-02-02
```

A successful public Landsat query does not prove private S3 connectivity. Missing S5 objects are data-availability outcomes; access denied, unreachable endpoint and TLS errors are operational failures.

Official references: [MinIO client installation](https://github.com/minio/mc), [MinIO client environment settings](https://min.io/docs/minio/linux/reference/minio-mc/minio-client-settings.html).

## Downloader credential file

Preferred automatic location: **`credentials/downloaders.env`** in the checkout. It may be filled using `examples/downloaders.env.example` or supplied privately already populated. No path editing is required. Keep the two real files (`bucket.env`, `downloaders.env`) out of Git; the containing directory is ignored. Empty optional provider fields are allowed.

Selection order: `--downloader-credentials-file`, `references.credentials_file` in JSON, `S5_VALIDATION_DOWNLOADER_CREDENTIALS`, `<repo>/credentials/downloaders.env`, then `~/.config/iride-validation/downloaders.env`. Explicit missing/invalid files raise setup errors. The loader returns values only to provider code; secrets are never copied into scientific configuration or evidence. An empty template works for public Landsat. Separate auth checks must be implemented by each future provider; presence of a stored value is not proof the account is valid.

```bash
iride-validate --landsat --credentials-file /private/bucket.env --downloader-credentials-file /private/downloaders.env
```

Provider fields supported for consolidation:

| Provider | Fields | Current use |
|---|---|---|
| Planetary Computer | `PLANETARY_COMPUTER_SUBSCRIPTION_KEY` (optional) | Landsat token requests only; public access works without it |
| CDS | `CDS_URL`, `CDS_KEY` | ERA5-Land SSRD for product 03; dataset terms must be accepted in CDS |
| ADS | `ADS_URL`, `ADS_KEY` | Reserved for future atmospheric providers |
| EUMETSAT | `EUMETSAT_CONSUMER_KEY`, `EUMETSAT_CONSUMER_SECRET` | Reserved for MSG/SEVIRI/product 06 |
| Copernicus Data Space | `CDSE_USER`, `CDSE_PASSWORD` | Reserved for Sentinel-2/product 05 |
| NIES | `NIES_USER`, `NIES_PASSWORD` | Reserved for GOSAT/products 07/08 |
| Earthdata | `EARTHDATA_USER` or `EARTHDATA_USERNAME`, `EARTHDATA_PASSWORD` | Reserved for future integrations |
| CMEMS | `CMEMS_USER`, `CMEMS_PASSWORD` | Reserved for future integrations |
| G-Portal | `GPORTAL_USER`, `GPORTAL_PASSWORD` | Reserved for future integrations |
| MeteoHub | `METEOHUB_USER`, `METEOHUB_PASSWORD`, `METEOHUB_ARCO_ACCESS_KEY`, plus endpoint/dataset/limit fields in the template | Reserved for future integrations |

AERONET's currently used public endpoint needs no personal credential. Regional gauge-network access depends on the selected networks and must be established with their adapters; MeteoHub credentials do not automatically establish access to all regional gauge networks.

## Import existing private input-availability settings

This optional helper creates the two privately shareable files without logging values or uploading secrets to GitHub:

```bash
python tools/import_credentials.py /path/to/input_availability/config.json --bucket-file /private/credentials.env
```

It accepts the input-availability `providers.<name>.env` schema, copies supported assignments into `credentials/downloaders.env`, and selects only production variables for `credentials/bucket.env`. Existing destinations are not overwritten unless `--overwrite` is explicit. On POSIX, files are created with mode 600; apply equivalent account-only ACLs on Windows. This source repo is not a runtime dependency: import once or receive the files privately, then run independently.

## How Landsat access works

The provider searches the public Planetary Computer STAC API for Collection 2 Tier-1 L2SP Landsat 8/9 scenes. For each Azure container it requests a short-lived SAS read token, caches it in memory, and downloads unsigned catalogue asset URLs with that temporary token added only to the HTTP request. The optional subscription key follows the [official Planetary Computer SDK](https://github.com/microsoft/planetary-computer-sdk-for-python/blob/main/planetary_computer/sas.py). There is no need to copy, save or share a SAS token, EarthExplorer password or Azure storage key. Recorded catalogue URLs remain unsigned.

## Snow and AOD reference access

Sentinel-2 SCL uses public Planetary Computer STAC/signing, with the existing optional `PLANETARY_COMPUTER_SUBSCRIPTION_KEY`. AERONET is public and needs no login. No new private files or keys are needed for 05/09. Production access still requires the VPN and `bucket.env`.
