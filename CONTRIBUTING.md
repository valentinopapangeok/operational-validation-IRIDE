# Contributing

Create a branch, install `.[scientific,dev]` and run the offline checks in [Testing](docs/TESTING.md). Product additions should follow [Adding products](docs/ADDING_PRODUCTS.md), with explicit scientific policy, source provenance, credential templates and fixture-based tests.

Keep code/resources portable: repository-relative configured paths, automatically initialized runtime directories, versioned static geometries, external credentials, and no workstation-specific imports. Runtime results, caches, env files, notebooks containing secrets and private data must remain outside Git. The source workbook can stay private; the extracted requirement provenance is sufficient for routine runs.

A pull request should state the product/observable, method and resulting behavior, scientific assumptions/limitations, independent sample definition and checks performed. Distinguish processing completeness, accuracy thresholds and campaign coverage. Update documentation/support status with the final implementation.

No software license is currently assigned. Obtain the repository owner's decision before adding a redistribution license or third-party datasets with new terms. Do not change accepted scientific policy solely to make tests or campaign counts pass.
