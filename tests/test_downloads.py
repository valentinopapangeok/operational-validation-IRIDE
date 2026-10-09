import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from s5validation.downloads import latest_files, download_item, timestamp
from s5validation.metrics import positional_accuracy, evaluate_target

class FakeStorage:
    def __init__(self):
        self.inventory={};self.objects={};self.listings={};self.copies=0;self.fail_key=None
    def add(self,prefix,name,data=b'fixture'):
        key=prefix+name
        self.listings.setdefault(prefix,[]).append({'key':name,'size':len(data),'etag':hashlib.md5(data).hexdigest(),'lastModified':'2026-10-01T00:00:00Z','type':'file'})
        self.objects[key]=data
    def list(self,bucket,prefix): return self.listings.get(prefix,[])
    def stat(self,bucket,key):
        data=self.objects[key]
        return {'size':len(data),'etag':hashlib.md5(data).hexdigest()}
    def copy(self,bucket,key,path):
        self.copies+=1
        if key==self.fail_key: raise OSError('fixture connection failure')
        path.write_bytes(self.objects[key])

class DownloadTests(unittest.TestCase):
    def fixture(self):
        s=FakeStorage()
        s.listings['ar_delivery/']=[{'type':'folder','key':'YEAR=2026/'}]
        s.listings['ar_delivery/YEAR=2026/']=[{'type':'folder','key':'MONTH=09/'},{'type':'folder','key':'MONTH=10/'}]
        prefix='ar_delivery/YEAR=2026/MONTH=09/'
        for version in (1,2):
            stem=f'IRIDE-S_S5-02-01_20260912_V{version}'
            s.add(prefix,stem+'.tif');s.add(prefix,stem+'.xml')
        s.add(prefix,'IRIDE-S_S5-02-01_V2.qml')
        return s
    def test_latest_nonempty_and_numeric_version(self):
        item=latest_files(self.fixture(),'bucket','S5-02-01')
        self.assertEqual(item['version'],2);self.assertEqual(len(item['assets']),3)
        self.assertEqual(item['acquired_at'],'2026-09-12T00:00:00+00:00')
    def test_latest_hour_and_companions(self):
        s=FakeStorage()
        for hour in ('02','23'):
            s.add('ar_delivery/',f'IRIDE-S_S5-02-03_20261006{hour}_V1.tif')
            s.add('ar_delivery/',f'IRIDE-S_S5-02-03_20261006{hour}_V1.xml')
        item=latest_files(s,'bucket','S5-02-03')
        self.assertEqual(len(item['assets']),2);self.assertIn('T23:00:00',item['acquired_at'])
    def test_retry_and_cache_checksum(self):
        s=self.fixture();item=latest_files(s,'bucket','S5-02-01')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);s.fail_key=item['assets'][0]['key']
            first=download_item(s,item,base)
            self.assertEqual(first['status'],'DOWNLOAD_ERROR')
            s.fail_key=None;second=download_item(s,item,base)
            self.assertEqual(second['status'],'DOWNLOADED')
            self.assertEqual(sum(a['reused'] for a in second['assets']),2)
            count=s.copies;third=download_item(s,item,base)
            self.assertEqual(s.copies,count)
            Path(third['assets'][0]['local_path']).write_bytes(b'corrupt')
            fourth=download_item(s,item,base)
            record=next(a for a in fourth['assets'] if a['key']==third['assets'][0]['key'])
            self.assertFalse(record['reused'])
    def test_source_change_rejected(self):
        s=self.fixture();item=latest_files(s,'bucket','S5-02-01')
        s.objects[item['assets'][0]['key']]=b'changed'
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(download_item(s,item,Path(directory))['status'],'DOWNLOAD_ERROR')
    def test_timestamp_precision(self):
        self.assertEqual(timestamp('202608'),'2026-08-01T00:00:00+00:00')
        with self.assertRaises(ValueError): timestamp('202613')
    def test_target_boundaries_and_spatial_tolerance(self):
        self.assertEqual(evaluate_target(80,'>',80),'FAIL')
        self.assertEqual(evaluate_target(90,'>=',90),'PASS')
        self.assertEqual(evaluate_target(None,'<=',20),'UNAVAILABLE')
        self.assertEqual(positional_accuracy([0,1,2,3],2)['positional_accuracy_pct'],75)
        with self.assertRaises(ValueError): positional_accuracy([1],None)
    def test_imported_plan_provenance(self):
        base=Path(__file__).resolve().parents[1]
        cfg=json.loads((base/'config/validation.json').read_text())
        plan=json.loads((base/'config/validation_plan_source.json').read_text())
        self.assertEqual(len(plan['products']),11)
        self.assertEqual(cfg['products']['S5-02-02']['plan']['fields']['thematic_target']['cell'],'J11')
        self.assertEqual(cfg['products']['S5-02-09']['sample_target'],15)
        self.assertEqual(cfg['products']['S5-02-08']['target_type'],'availability_dependent_maximum')
        for product in cfg['products'].values():
            self.assertIsNone(product['relative_rmse_denominator'])
            self.assertIsNone(product['positional_target']['tolerance_m'])

if __name__=='__main__': unittest.main()
