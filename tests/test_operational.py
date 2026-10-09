import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch
from s5validation.downloads import campaign_files
from s5validation.operational import execute, preflight
from s5validation.registry import Registry
from s5validation.models import Outcome
from s5validation.pipeline import ADAPTERS
from test_downloads import FakeStorage


class OperationalTests(unittest.TestCase):
    def storage(self):
        s=FakeStorage()
        for day,version in [('20260930',1),('20261001',1),('20261001',10),('20261005',1),('20270101',1)]:
            s.add('ar_delivery/',f'IRIDE-S_S5-02-01_{day}_V{version}.tif')
        return s

    def config(self):
        return {'campaign':{'start':'2026-10-01T00:00:00+00:00','end':'2027-01-01T00:00:00+00:00'},
                'delivery':{'mode':'manifest','manifest':'deliveries.json'},
                'products':{'S5-02-01':{'adapter':'fixture'}}}

    def test_all_dates_campaign_boundary_and_versions(self):
        items=campaign_files(self.storage(),'bucket','S5-02-01',datetime(2026,10,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc))
        self.assertEqual([(i['acquired_at'][:10],i['version']) for i in items],[('2026-10-01',10),('2026-10-05',1)])

    def test_partition_catch_up(self):
        s=FakeStorage();s.listings['ar_delivery/']=[{'type':'folder','key':'YEAR=2026/'}]
        s.listings['ar_delivery/YEAR=2026/']=[{'type':'folder','key':'MONTH=10/'},{'type':'folder','key':'MONTH=11/'}]
        for month in ('10','11'):
            s.add(f'ar_delivery/YEAR=2026/MONTH={month}/',f'IRIDE-S_S5-02-01_2026{month}01_V1.tif')
        items=campaign_files(s,'bucket','S5-02-01',datetime(2026,10,1,tzinfo=timezone.utc),datetime(2027,1,1,tzinfo=timezone.utc))
        self.assertEqual(len(items),2)

    def test_idempotent_downloads_pending_retry_and_failure_isolation(self):
        class Adapter:
            calls=[]
            def validate(self,d,cfg,workdir):
                self.calls.append(d.acquired_at)
                self.assert_local=Path(d.uri).is_file()
                if not self.assert_local:raise ValueError('not local')
                if '2026-10-01' in d.acquired_at:raise ValueError('scientific fixture failure')
                return Outcome('PENDING_REFERENCE','not published','NOT_AVAILABLE')
        adapter=Adapter()
        with tempfile.TemporaryDirectory() as directory,patch.dict(ADAPTERS,{'fixture':adapter},clear=True):
            base=Path(directory);(base/'deliveries.json').write_text('[]',encoding='utf-8')
            registry=Registry(base/'validation.db');s=self.storage();config=self.config()
            first=execute(registry,config,base,base,s)
            self.assertEqual(first['new'],2)
            self.assertEqual(first['delivery_states'],{'ERROR':1,'PENDING_REFERENCE':1})
            count=s.copies
            second=execute(registry,config,base,base,s)
            self.assertEqual(second['new'],0);self.assertEqual(s.copies,count)
            self.assertEqual(len(adapter.calls),4)
            self.assertTrue(adapter.assert_local)
            self.assertEqual(len(registry.samples('S5-02-01')),0)
            registry.close()

    def test_unimplemented_products_are_discovered_without_download(self):
        with tempfile.TemporaryDirectory() as directory,patch.dict(ADAPTERS,{},clear=True):
            base=Path(directory);(base/'deliveries.json').write_text('[]',encoding='utf-8')
            registry=Registry(base/'validation.db');s=self.storage()
            config=self.config();config['products']['S5-02-01']['adapter']='missing'
            result=execute(registry,config,base,base,s)
            self.assertEqual(result['pending_configuration'],2);self.assertEqual(s.copies,0)
            registry.close()

    def test_preflight_failure_does_not_create_registry(self):
        class Offline:
            def list(self,*args):raise OSError('private endpoint exception')
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory)
            with self.assertRaisesRegex(OSError,'No registry changes'):
                preflight(self.config(),base,client=Offline())
            self.assertFalse((base/'validation.db').exists())

    def test_cli_preflight_happens_before_initialize(self):
        from s5validation.cli import main
        base=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory,patch('s5validation.operational.preflight',side_effect=OSError('VPN unavailable')):
            workdir=Path(directory)/'state'
            self.assertEqual(main(['--run','--workdir',str(workdir)],default_base=base),1)
            self.assertFalse(workdir.exists())

    def test_completed_delivery_is_not_downloaded_or_validated(self):
        from s5validation.models import Delivery
        class Adapter:
            def validate(self,*args):raise AssertionError('completed delivery processed')
        with tempfile.TemporaryDirectory() as directory,patch.dict(ADAPTERS,{'fixture':Adapter()},clear=True):
            base=Path(directory);(base/'deliveries.json').write_text('[]',encoding='utf-8')
            registry=Registry(base/'validation.db');storage=self.storage();config=self.config()
            from s5validation.downloads import inventory_campaign
            items,_=inventory_campaign(storage,config)
            for item in items:
                d=Delivery(item['product'],item['delivery_id'],item['revision'],item['primary_uri'],item['acquired_at'])
                registry.discover(d);registry.record(d,Outcome('COMPLETE','fixture','AVAILABLE'))
            result=execute(registry,config,base,base,storage)
            self.assertEqual(result['already_complete'],2);self.assertEqual(storage.copies,0)
            registry.close()

    def test_reference_download_is_filtered_by_queue_day_and_domain(self):
        from s5validation.references.landsat import refresh_references
        class Provider:
            downloaded=[]
            def __init__(self,*args):pass
            def search(self,*args):
                return {'provider':'fixture','collection':'fixture','platform_counts':{},'candidates':[
                    {'id':'needed','properties':{'datetime':'2026-10-02T10:00:00Z'},'validation:domains':['marine']},
                    {'id':'wrong-day','properties':{'datetime':'2026-10-03T10:00:00Z'},'validation:domains':['marine']},
                    {'id':'wrong-domain','properties':{'datetime':'2026-10-02T10:00:00Z'},'validation:domains':['land']}]}
            def download_scene(self,item,workdir):
                self.downloaded.append(item['id'])
                return {'scene_id':item['id'],'status':'DOWNLOADED','assets':{'thermal':{'reused':False}}}
        with tempfile.TemporaryDirectory() as directory,patch('s5validation.references.landsat.LandsatProvider',Provider):
            base=Path(directory)
            result=refresh_references(self.config(),base,base,'2026-10-09',requests={('2026-10-02','marine')})
            self.assertEqual(Provider.downloaded,['needed']);self.assertEqual(result['new_downloaded_scenes'],1)

    def test_main_no_arguments_selects_run_and_repository_base(self):
        import runpy,sys
        base=Path(__file__).resolve().parents[1]
        with patch.object(sys,'argv',[str(base/'main.py')]),patch('s5validation.cli.main',return_value=0) as entry:
            with self.assertRaises(SystemExit):runpy.run_path(str(base/'main.py'),run_name='__main__')
            entry.assert_called_once_with(['--run'],default_base=base)

if __name__=='__main__':unittest.main()
