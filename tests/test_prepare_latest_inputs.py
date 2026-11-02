import json
import tempfile
import unittest
from pathlib import Path

from test_downloads import FakeStorage
from tools.prepare_latest_inputs import prepare,package_products


class PrepareLatestInputsTests(unittest.TestCase):
    def test_latest_per_product_and_reuses_download(self):
        storage=FakeStorage()
        storage.add('ar_delivery/','IRIDE-S_S5-02-01_20261007_V1.tif')
        storage.add('ar_delivery/','IRIDE-S_S5-02-01_20261008_V1.tif')
        config={'delivery':{'prefix':'ar_delivery/'},'products':{'S5-02-01':{}}}
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            first=prepare(config,root,root,storage)
            self.assertEqual(first['status'],'READY_FOR_REFERENCE_PACKAGING')
            self.assertEqual([(e['product'],e['date']) for e in first['entries']],
                             [('S5-02-01','2026-10-08')])
            self.assertTrue(Path(first['entries'][0]['primary_file']).is_file())
            self.assertEqual(json.loads((root/'inventory/package_candidates.json').read_text())['entries'],first['entries'])
            count=storage.copies
            prepare(config,root,root,storage)
            self.assertEqual(storage.copies,count)
            manifests=package_products(first['entries'],root/'daily-inputs')
            self.assertEqual(len(manifests),1)
            package=json.loads(manifests[0].read_text())
            self.assertEqual((package['date'],package['product']),('2026-10-08','S5-02-01'))
            self.assertEqual(len(package['assets']),1)
            self.assertEqual(package['assets'][0]['sha256'],first['entries'][0]['assets'][0]['sha256'])
            self.assertTrue((manifests[0].parent/package['primary_path']).is_file())
