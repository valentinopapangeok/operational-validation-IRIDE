import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from s5validation.downloads import digest
from tools.run_pushed_inputs import load_package,select_manifests,restore_request_state,save_request_state,validate_packages
from s5validation.models import Delivery


class PushedInputsTests(unittest.TestCase):
    def test_changed_pair_and_checksum(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'daily-inputs/2026-10-07/S5-02-04'
            (folder/'assets').mkdir(parents=True)
            asset=folder/'assets/rain.tif';asset.write_bytes(b'example raster bytes')
            manifest={'schema_version':1,'product':'S5-02-04','date':'2026-10-07',
                      'acquired_at':'2026-10-07T21:00:00+00:00','delivery_id':'IRIDE-S_S5-02-04_2026100721_V1',
                      'revision':'a'*64,'primary_path':'assets/rain.tif',
                      'assets':[{'path':'assets/rain.tif','bytes':asset.stat().st_size,'sha256':digest(asset)}]}
            (folder/'manifest.json').write_text(json.dumps(manifest))
            changed=root/'changed.txt';changed.write_text('daily-inputs/2026-10-07/S5-02-04/assets/rain.tif\n')
            selected=select_manifests(root,changed)
            self.assertEqual(selected,[Path('daily-inputs/2026-10-07/S5-02-04/manifest.json')])
            self.assertEqual(load_package(root,selected[0]).product,'S5-02-04')
            asset.write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'checksum mismatch'):
                load_package(root,selected[0])

    def test_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);folder=root/'daily-inputs/2026-10-07/S5-02-04';folder.mkdir(parents=True)
            (folder/'manifest.json').write_text(json.dumps({'schema_version':1,'product':'S5-02-04',
                'date':'2026-10-07','acquired_at':'2026-10-07T00:00:00+00:00',
                'delivery_id':'IRIDE-S_S5-02-04_2026100700_V1','revision':'a'*64,'primary_path':'../outside.tif',
                'assets':[{'path':'../outside.tif','bytes':1,'sha256':'x'}]}))
            with self.assertRaisesRegex(ValueError,'Invalid or duplicate'):
                load_package(root,Path('daily-inputs/2026-10-07/S5-02-04/manifest.json'))

    def test_only_pending_request_metadata_survives(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);work=root/'work';state=root/'state'
            index=work/'cache/reference/regional_gauges/index.json';index.parent.mkdir(parents=True)
            index.write_text(json.dumps({'jobs':[
                {'request_id':7,'status':'PENDING','from':'2026-10-07T00:00:00+00:00',
                 'to':'2026-10-07T01:00:00+00:00','payload':{'dataset_names':['gauges']}},
                {'request_id':8,'status':'SUCCESS','from':'2026-10-08T00:00:00+00:00',
                 'to':'2026-10-08T01:00:00+00:00','file':'/private/gauges.jsonl','sha256':'secret'}]}))
            save_request_state(work,state)
            saved=json.loads((state/'index.json').read_text())
            self.assertEqual([j['request_id'] for j in saved['jobs']],[7,8])
            self.assertEqual(saved['jobs'][1]['status'],'PENDING')
            self.assertNotIn('file',saved['jobs'][1]);self.assertNotIn('sha256',saved['jobs'][1])
            other=root/'other'
            delivery=Delivery('S5-02-04','id','rev','/unused','2026-10-07T00:00:00+00:00')
            restore_request_state(state,other,[delivery])
            restored=json.loads((other/'cache/reference/regional_gauges/index.json').read_text())
            self.assertEqual([j['request_id'] for j in restored['jobs']],[7])
            save_request_state(other,state)
            self.assertEqual(len(json.loads((state/'index.json').read_text())['jobs']),2)

    def test_staged_run_never_opens_production(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);(root/'config').mkdir()
            config={'schema_version':1,'campaign':{'start':'2026-10-01T00:00:00+00:00',
                    'end':'2027-01-01T00:00:00+00:00','aoi_file':None},
                    'delivery':{'mode':'manifest','manifest':'validation/deliveries.json'},
                    'products':{'S5-02-01':{'adapter':'unconfigured'}}}
            (root/'config/validation.json').write_text(json.dumps(config))
            folder=root/'daily-inputs/2026-10-07/S5-02-01';(folder/'assets').mkdir(parents=True)
            product=folder/'assets/product.tif';product.write_bytes(b'local product')
            (folder/'manifest.json').write_text(json.dumps({'schema_version':1,'product':'S5-02-01',
                'date':'2026-10-07','acquired_at':'2026-10-07T00:00:00+00:00',
                'delivery_id':'IRIDE-S_S5-02-01_20261007_V1','revision':'a'*64,'primary_path':'assets/product.tif',
                'assets':[{'path':'assets/product.tif','bytes':product.stat().st_size,'sha256':digest(product)}]}))
            with (patch('s5validation.downloads.production_client',side_effect=AssertionError('production opened')),
                  patch('tools.run_pushed_inputs.generate')):
                result=validate_packages(root,[Path('daily-inputs/2026-10-07/S5-02-01/manifest.json')],root/'work')
            self.assertEqual(result['deliveries'][0]['status'],'PENDING_CONFIGURATION')
