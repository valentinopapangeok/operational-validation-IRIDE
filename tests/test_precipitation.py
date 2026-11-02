import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch,MagicMock
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.models import Delivery
from s5validation.downloads import save_json,digest
from s5validation.precipitation_core import parse_gauges,hourly_gauges,score_pairs,pixel_area_support
from s5validation.precipitation import PrecipitationAdapter
from s5validation.precipitation_reporting import daily_results
from s5validation.references.regional_gauges import refresh_regional_gauges
ROOT=Path(__file__).resolve().parents[1]


def message(end='2026-10-01T01:00:00Z',seconds=3600,mm=2,lat=45,attrs=None):
    return {'version':'0.1','network':'simnbo','lat':int(lat*100000),'lon':1000000,'date':end,'data':[
        {'vars':{'B01019':{'v':'Test'}}},
        {'level':[1,None,None,None],'timerange':[1,0,seconds],'vars':{'B13011':{'v':mm,'a':attrs or {}}}}]}


class PrecipitationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.config=json.loads((ROOT/'config/validation.json').read_text());self.config['precipitation']['reference_condition']='all_conditions';self.config['precipitation']['screening']['enabled']=False;self.config['precipitation']['networks']=['simnbo','simnpr','dpcn-lazio','dpcn-sicilia'];self.config['campaign']['aoi_file']='aoi.json'
        save_json(self.base/'aoi.json',{'type':'Polygon','coordinates':[[[9,39],[11,39],[11,46],[9,46],[9,39]]]})
        self.product=self.base/'product.tif';self.write_product([2,0,1])
        self.delivery=Delivery('S5-02-04','IRIDE-S_S5-02-04_2026100100_V1','revision',str(self.product),'2026-10-01T00:00:00+00:00')
        self.start=datetime(2026,10,1,tzinfo=timezone.utc)
    def write_product(self,values):
        with rasterio.open(self.product,'w',driver='GTiff',height=3,width=1,count=1,dtype='float32',nodata=-999,crs='EPSG:4326',transform=from_origin(9,46,2,2)) as ds:ds.write(np.asarray(values,dtype='float32')[:,None],1)
    def fixture(self):
        folder=self.base/'cache/reference/regional_gauges';folder.mkdir(parents=True,exist_ok=True)
        path=folder/'sample.jsonl';path.write_text('\n'.join(json.dumps(message(mm=mm,lat=lat)) for mm,lat in [(2,45),(0,43),(1,41)]))
        job={'status':'SUCCESS','payload':{'only_reliable':True,'output_format':'json','dataset_names':['simnbo','simnpr','dpcn-lazio','dpcn-sicilia']},'from':self.start.isoformat(),'to':(self.start+timedelta(hours=1)).isoformat(),'file':str(path),'sha256':digest(path)}
        save_json(folder/'index.json',{'jobs':[job]});return job
    def validate(self):return PrecipitationAdapter(self.config,self.base).validate(self.delivery,self.config['products']['S5-02-04'],self.base)
    def test_end_stamped_mm_and_qc(self):
        obj=message();rows,excluded=parse_gauges(json.dumps(obj));self.assertEqual(rows[0]['mm'],2);self.assertEqual(rows[0]['start'],self.start.isoformat())
        out,_=hourly_gauges(rows,self.start);self.assertEqual(len(out),1)
        self.assertEqual(hourly_gauges(rows,self.start+timedelta(hours=1))[0],[])
        self.assertEqual(parse_gauges(json.dumps(message(attrs={'B33192':0})))[0],[])
        self.assertEqual(parse_gauges(json.dumps(message(mm=-1)))[0],[])
        obj['data'][1]['timerange'][0]=0;self.assertEqual(parse_gauges(json.dumps(obj))[0],[])
    def test_complete_subhour_partitions_only_and_conflicts(self):
        text='\n'.join(json.dumps(message(end=f'2026-10-01T00:{m:02d}:00Z',seconds=900,mm=1)) for m in (15,30,45))+'\n'+json.dumps(message(seconds=900,mm=1))
        rows,_=parse_gauges(text);out,_=hourly_gauges(rows,self.start);self.assertEqual(out[0]['mm'],4);self.assertEqual(out[0]['periods'],4)
        self.assertEqual(hourly_gauges(rows[:-1],self.start)[0],[])
        conflict={**rows[0],'mm':9};self.assertEqual(hourly_gauges(rows+[conflict],self.start)[0],[])
        exact,_=parse_gauges(json.dumps(message(mm=3)));self.assertEqual(hourly_gauges(rows+exact,self.start)[0][0]['mm'],3)
        self.assertEqual(hourly_gauges(rows+rows,self.start)[0][0]['mm'],4)
    def test_all_pairs_include_dry_false_alarms_and_zero_denominator(self):
        m=score_pairs([{'predicted':1,'reference':1},{'predicted':2,'reference':0}])
        self.assertGreater(m['relative_rmse_pct'],20);self.assertEqual(m['event_detection']['fp'],1)
        self.assertIsNone(score_pairs([{'predicted':0,'reference':0}])['meets_threshold'])
        self.assertTrue(score_pairs([{'predicted':0,'reference':0}])['all_dry_agreement'])
    def test_pixel_area_support_counts_distinct_sites_and_keeps_dry_neighbours(self):
        def row(station,lon,mm,col=4):
            return {'station':station,'latitude':43.0,'longitude':lon,'reference':mm,
                    'predicted':3.0,'product_row':7,'product_col':col,'area':'Center'}
        matched=[row('a',10.0000,2),row('duplicate-a',10.0002,4),
                 row('b',10.0020,0),row('c',10.0040,6),row('isolated',10.01,2,5)]
        rainy=[r for r in matched if r['reference']>0]
        support=pixel_area_support(matched,rainy)
        pixel=next(p for p in support['pixels'] if p['product_col']==4)
        self.assertTrue(pixel['supported'])
        self.assertEqual(pixel['independent_locations'],3)
        self.assertEqual(pixel['site_reference_mm'],[0,3,6])
        self.assertEqual(pixel['reference_mm'],3)
        self.assertFalse(next(p for p in support['pixels'] if p['product_col']==5)['supported'])
        self.assertEqual(sum(r['supported'] for r in support['rainy_station_hours']),3)
        self.assertEqual(len(rainy),4)  # Point comparison still retains every rainy gauge.
    def test_adapter_lifecycle_cache_subareas_nodata_and_units(self):
        self.assertEqual(self.validate().status,'PENDING_REFERENCE');self.fixture();out=self.validate()
        self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.samples,[]);self.assertEqual(out.metrics['relative_rmse_pct'],0)
        self.assertEqual(out.metrics['pixel_area_support']['area_supported_pixels'],0)
        self.assertTrue(any(p.endswith('pixel_area_support.json') for p in out.provenance['artifacts']))
        self.assertIsNone(out.metrics['subareas']['Center']['relative_rmse_pct'])
        with patch('s5validation.precipitation.hourly_gauges',wraps=hourly_gauges):self.assertEqual(self.validate().metrics,out.metrics)
        self.write_product([2,-999,0]);self.assertEqual(self.validate().metrics['n'],2)
        with rasterio.open(self.product,'r+') as ds:ds.set_band_unit(1,'m')
        with self.assertRaisesRegex(ValueError,'unit conflicts'):self.validate()
    def test_cache_reuse_no_network_corrupt_cache_rejected(self):
        self.fixture()
        with patch('s5validation.references.regional_gauges.downloader_credentials',side_effect=AssertionError('cache only')):
            self.assertEqual(refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])['reused_references'],1)
        (self.base/'cache/reference/regional_gauges/sample.jsonl').write_text('corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):self.validate()
    def test_daily_pool_includes_failed_hour_not_average_percent(self):
        self.fixture();out=self.validate()
        def row(out,stamp,version=1):return {'product':'S5-02-04','delivery_id':f'IRIDE-S_S5-02-04_{datetime.fromisoformat(stamp).strftime("%Y%m%d%H")}_V{version}','acquired_at':stamp,'status':out.status,'provenance':out.provenance}
        first=row(out,self.delivery.acquired_at)
        self.delivery=Delivery('S5-02-04','IRIDE-S_S5-02-04_2026100101_V1','revision',str(self.product),'2026-10-01T01:00:00+00:00')
        # Separate fixture at next hour, all reference zero and product positive.
        folder=self.base/'cache/reference/regional_gauges';path=folder/'second.jsonl'
        path.write_text(json.dumps(message(end='2026-10-01T02:00:00Z',mm=0)))
        job={'status':'SUCCESS','payload':{'only_reliable':True,'output_format':'json','dataset_names':['simnbo','simnpr','dpcn-lazio','dpcn-sicilia']},'from':self.delivery.acquired_at,'to':'2026-10-01T02:00:00+00:00','file':str(path),'sha256':digest(path)}
        save_json(folder/'index.json',{'jobs':[job]});out2=self.validate()
        result=[r for r in daily_results([first,row(out2,self.delivery.acquired_at)]) if r['area']=='Whole AOI' and not r['station']][0]
        self.assertEqual(result['station_hours'],4);self.assertAlmostEqual(result['relative_rmse_pct'],100/0.75);self.assertFalse(result['passes'])
    def test_pending_job_is_polled_and_not_resubmitted(self):
        path=self.base/'cache/reference/regional_gauges/index.json';save_json(path,{'jobs':[{'request_id':123,'signature':'x','status':'PENDING','from':self.start.isoformat(),'to':(self.start+timedelta(hours=1)).isoformat()}]})
        session=MagicMock();session.post.return_value.json.return_value='token'
        session.get.return_value.json.return_value=[{'id':999,'status':'SUCCESS'},{'id':123,'status':'PENDING'}]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'user','METEOHUB_PASSWORD':'private'}):
            result=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        self.assertEqual(result['pending_requests'],1);self.assertEqual(session.post.call_count,1)
    def test_ambiguous_submission_is_not_repeated(self):
        save_json(self.base/'cache/reference/regional_gauges/submission_intent.json',{'payload':{}})
        session=MagicMock();session.post.return_value.json.return_value='token'
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            with self.assertRaisesRegex(ValueError,'Unresolved'):refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        self.assertEqual(session.post.call_count,1)

    def test_successful_job_download_is_verified_and_reused(self):
        path=self.base/'cache/reference/regional_gauges/index.json'
        payload={'only_reliable':True,'output_format':'json','dataset_names':self.config['precipitation']['networks']}
        save_json(path,{'jobs':[{'request_id':123,'signature':'sample','status':'PENDING','from':self.start.isoformat(),'to':(self.start+timedelta(hours=1)).isoformat(),'payload':payload}]})
        session=MagicMock();session.post.return_value.json.return_value='token'
        listed=MagicMock();listed.json.return_value=[{'id':123,'status':'SUCCESS','fileoutput':'sample.json'}]
        download=MagicMock();download.__enter__.return_value=download;download.iter_content.return_value=[json.dumps(message()).encode()]
        session.get.side_effect=[listed,download]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            result=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        self.assertEqual(result['new_downloaded_references'],1)
        self.assertEqual(self.validate().metrics['relative_rmse_pct'],0)
        with patch('requests.Session',side_effect=AssertionError('cache must be reused')):
            self.assertEqual(refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])['reused_references'],1)

    def test_registry_keeps_diagnostics_out_of_accepted_samples(self):
        from s5validation.registry import Registry
        self.fixture();out=self.validate();r=Registry(self.base/'validation.db');self.addCleanup(r.close)
        r.discover(self.delivery);r.record(self.delivery,out);self.assertEqual(r.samples('S5-02-04'),[])
        from s5validation.precipitation_reporting import comparison_html
        self.assertIn('passing hours / evaluated hours',comparison_html(r.rows(),self.base))
        self.assertTrue((self.base/'report/hourly_scores.csv').is_file())

    def test_quota_blocks_new_submission_without_deleting_jobs(self):
        session=MagicMock();session.post.return_value.json.return_value='token';session.get.return_value.json.return_value={'quota':100,'used':99}
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            out=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        self.assertTrue(out['blocked_reference_quota']);self.assertEqual(session.post.call_count,1);session.delete.assert_not_called()

    def test_submission_is_precipitation_only_and_intent_is_resolved(self):
        session=MagicMock();session.post.return_value.json.side_effect=['token',{'request_id':123}];session.get.return_value.json.side_effect=[{'quota':10**10,'used':0},{'remaining':10}]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            out=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        payload=session.post.call_args.kwargs['json'];self.assertEqual(payload['filters'],{'product':[{'code':'B13011'}]})
        self.assertEqual(out['pending_requests'],1);self.assertFalse((self.base/'cache/reference/regional_gauges/submission_intent.json').exists())

    def test_opt_in_archive_keeps_raw_data_and_receipts(self):
        import hashlib
        from s5validation.references.regional_gauges import archive_cached_extracts
        self.fixture();path=self.base/'cache/reference/regional_gauges/index.json';index=json.loads(path.read_text());job=index['jobs'][0]
        payload=job['payload'];payload.update(request_name='IRIDE_validation_gauges_test',reftime={'from':job['from'],'to':job['to']})
        job.update(signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),request_id=123)
        session=MagicMock();session.get.return_value.json.return_value=[{'id':123,'name':payload['request_name'],'args':{k:v for k,v in payload.items() if k!='request_name'},'status':'SUCCESS','fileoutput':'data.json'}]
        self.assertEqual(archive_cached_extracts(session,'https://example.invalid',index,path),1)
        session.put.assert_called_once();session.delete.assert_not_called()
        self.assertTrue(Path(job['file']).is_file());self.assertEqual(digest(Path(job['file'])),job['sha256']);self.assertTrue(Path(job['archive_receipt']).is_file())
        self.assertEqual(archive_cached_extracts(session,'https://example.invalid',index,path),0)
        with patch('requests.Session',side_effect=AssertionError('must reuse local reference')):
            self.assertEqual(refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])['reused_references'],1)

    def test_cleanup_refuses_unverified_and_foreign_requests(self):
        import hashlib
        from s5validation.references.regional_gauges import archive_cached_extracts
        self.fixture();path=self.base/'cache/reference/regional_gauges/index.json';index=json.loads(path.read_text());job=index['jobs'][0]
        payload=job['payload'];payload.update(request_name='IRIDE_validation_gauges_test',reftime={'from':job['from'],'to':job['to']})
        job.update(signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest(),request_id=123)
        session=MagicMock();session.get.return_value.json.return_value=[{'id':123,'name':'Someone elses extract','args':{}}]
        with self.assertRaisesRegex(ValueError,'does not match'):archive_cached_extracts(session,'https://example.invalid',index,path)
        session.put.assert_not_called()
        Path(job['file']).write_text('corrupted')
        with self.assertRaisesRegex(ValueError,'verified local'):archive_cached_extracts(session,'https://example.invalid',index,path)
        session.put.assert_not_called()

    def test_rate_limit_is_resumable_and_does_not_leave_submission_intent(self):
        session=MagicMock();session.post.return_value.json.return_value='token';session.get.return_value.json.side_effect=[{'quota':10**10,'used':0},{'remaining':0}]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            out=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        self.assertTrue(out['blocked_reference_rate']);self.assertEqual(session.post.call_count,1)
        self.assertFalse((self.base/'cache/reference/regional_gauges/submission_intent.json').exists())

    def test_campaign_continuation_uses_cached_data_offline(self):
        from tools.continue_precipitation import main
        self.fixture();self.config['campaign']['sub_aoi_file']=None;self.config['campaign']['marine_sub_aoi_file']=None
        self.config['landsat']['land_aoi_file']='aoi.json';self.config['landsat']['marine_aoi_file']='aoi.json'
        folder=self.base/'cache/s5'/self.delivery.product/self.delivery.delivery_id/self.delivery.revision
        save_json(folder/'download_receipt.json',{'status':'DOWNLOADED','revision':self.delivery.revision,'assets':[{'key':'primary.tif','status':'DOWNLOADED','local_path':str(self.product)}]})
        inventory=self.base/'inventory/campaign_products.json'
        save_json(inventory,{'items':[{'product':self.delivery.product,'delivery_id':self.delivery.delivery_id,'revision':self.delivery.revision,'acquired_at':self.delivery.acquired_at,'primary_key':'primary.tif'}],'errors':[]})
        save_json(self.base/'config.json',self.config)
        with patch('requests.Session',side_effect=AssertionError('must reuse local data')):
            result=main(['--config',str(self.base/'config.json'),'--base-dir',str(self.base),'--workdir',str(self.base),'--once'])
        self.assertEqual(result,0)
        state=json.loads((self.base/'inventory/precipitation_continuation.json').read_text());self.assertEqual(state['state'],'COMPLETE')
        self.assertTrue((self.base/'registry_snapshot.db').exists());self.assertTrue((self.base/'report/hourly_scores.json').exists())

    def test_completed_remote_output_missing_is_replaced_without_losing_receipt(self):
        import hashlib
        policy=self.config['precipitation']
        payload={'request_name':'IRIDE_validation_gauges_2026100100','reftime':{'from':self.start.isoformat(),'to':(self.start+timedelta(hours=1)).isoformat()},'dataset_names':policy['networks'],'output_format':'json','only_reliable':True,'filters':{'product':[{'code':'B13011'}]}}
        signature=hashlib.sha256(json.dumps(payload,sort_keys=True).encode()).hexdigest()
        path=self.base/'cache/reference/regional_gauges/index.json';save_json(path,{'jobs':[{'request_id':123,'signature':signature,'status':'PENDING','from':payload['reftime']['from'],'to':payload['reftime']['to'],'payload':payload}]})
        session=MagicMock();session.post.return_value.json.side_effect=['token',{'request_id':456}]
        session.get.return_value.json.side_effect=[[],[{'id':123,'status':'SUCCESS'}],{'quota':10**10,'used':0},{'remaining':10}]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            out=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        jobs=json.loads(path.read_text())['jobs'];self.assertEqual(jobs[0]['status'],'REMOTE_OUTPUT_UNAVAILABLE');self.assertEqual(jobs[1]['request_id'],456);self.assertEqual(out['pending_requests'],1)

    def test_campaign_recovers_after_three_connection_failures_without_redownload(self):
        from tools.continue_precipitation import main
        import requests
        self.fixture();self.config['campaign']['sub_aoi_file']=None;self.config['campaign']['marine_sub_aoi_file']=None
        self.config['landsat']['land_aoi_file']='aoi.json';self.config['landsat']['marine_aoi_file']='aoi.json'
        folder=self.base/'cache/s5'/self.delivery.product/self.delivery.delivery_id/self.delivery.revision
        save_json(folder/'download_receipt.json',{'status':'DOWNLOADED','revision':self.delivery.revision,'assets':[{'key':'primary.tif','status':'DOWNLOADED','local_path':str(self.product)}]})
        save_json(self.base/'inventory/campaign_products.json',{'items':[{'product':self.delivery.product,'delivery_id':self.delivery.delivery_id,'revision':self.delivery.revision,'acquired_at':self.delivery.acquired_at,'primary_key':'primary.tif'}],'errors':[]})
        save_json(self.base/'config.json',self.config)
        with patch('tools.continue_precipitation.refresh_regional_gauges',side_effect=[requests.ConnectionError(),requests.ConnectionError(),requests.ConnectionError(),{'new_downloaded_references':0,'reused_references':1}]) as refresh,patch('tools.continue_precipitation.time.sleep'):
            result=main(['--config',str(self.base/'config.json'),'--base-dir',str(self.base),'--workdir',str(self.base),'--retry-network-errors'])
        self.assertEqual(result,0);self.assertEqual(refresh.call_count,4)
        self.assertEqual(json.loads((self.base/'inventory/precipitation_continuation.json').read_text())['state'],'COMPLETE')

    def test_rainy_only_excludes_dry_keeps_product_zero_and_failures(self):
        from s5validation.precipitation_core import evaluable_pairs
        rows=[{'reference':0,'predicted':9},{'reference':2,'predicted':0},{'reference':.01,'predicted':3}]
        selected=evaluable_pairs(rows,'positive_gauge_rainfall')
        self.assertEqual(selected,rows[1:]);self.assertGreater(score_pairs(selected)['relative_rmse_pct'],20)
        with self.assertRaises(ValueError):evaluable_pairs(rows,'best_matches')

    def test_rainy_adapter_retains_all_condition_evidence_and_changes_cache_identity(self):
        self.fixture();old=self.validate()
        self.config['precipitation']['reference_condition']='positive_gauge_rainfall'
        self.write_product([0,9,1]);out=self.validate()
        self.assertEqual(out.metrics['n'],2);self.assertEqual(out.metrics['excluded_dry_station_hours'],1)
        self.assertEqual(out.metrics['matched_station_hours'],3)
        self.assertGreater(out.metrics['relative_rmse_pct'],20)
        self.assertNotEqual(old.provenance['method_signature'],out.provenance['method_signature'])
        matched=next(p for p in out.provenance['artifacts'] if p.endswith('matched_all_conditions.json'))
        self.assertEqual(len(json.loads(Path(matched).read_text())),3)
