import tempfile
import unittest
from pathlib import Path
from tools.list_changed_manifests import manifests


class ChangedManifestMatrixTests(unittest.TestCase):
    def test_multiple_pairs_in_one_push(self):
        with tempfile.TemporaryDirectory() as directory:
            repo=Path(directory)
            for product in ('S5-02-04','S5-02-05'):
                folder=repo/'daily-inputs/2026-10-29'/product;folder.mkdir(parents=True)
                (folder/'manifest.json').write_text('{}')
            changes=['daily-inputs/2026-10-29/S5-02-04/assets/a.tif',
                     'daily-inputs/2026-10-29/S5-02-05/manifest.json',
                     'docs/README.md']
            self.assertEqual(manifests(repo,changes),[
                'daily-inputs/2026-10-29/S5-02-04/manifest.json',
                'daily-inputs/2026-10-29/S5-02-05/manifest.json'])
