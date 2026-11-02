import copy
import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime,timezone
from unittest.mock import patch,MagicMock
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.models import Delivery
from s5validation.downloads import digest,save_json
from s5validation.precipitation_screening import catalogue,inspect_product,plan_requests,choose_target
from s5validation.references.regional_gauges import reference_receipts,refresh_regional_gauges
ROOT=Path(__file__).resolve().parents[1]


class ScreeningTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.base=Path(self.temp.name)
        self.config=json.loads((ROOT/'config/validation.json').read_text());self.config['precipitation']['reference_condition']='all_conditions';self.config['campaign']['aoi_file']='aoi.json'
        self.config['precipitation']['networks']=['simnbo','simnpr','dpcn-lazio','dpcn-sicilia']
        self.config['precipitation']['screening']['bootstrap_missing_networks']=False
        save_json(self.base/'aoi.json',{'type':'Polygon','coordinates':[[[9,39],[12,39],[12,46],[9,46],[9,39]]]})
        self.path=self.base/'product.tif';self.delivery=Delivery('S5-02-04','rain','revision',str(self.path),'2026-10-01T00:00:00Z')
        self.write([[0,0,0],[0,2,0],[0,0,0]])
        self.stations=[{'station':'simnbo:45:10','name':'N','network':'simnbo','region':'Emilia-Romagna','sub_aoi':'North','latitude':45.5,'longitude':10.5}]
    def write(self,values):
        with rasterio.open(self.path,'w',driver='GTiff',width=3,height=3,count=1,dtype='float32',nodata=-999,crs='EPSG:4326',transform=from_origin(9,46,1,1)) as ds:ds.write(np.asarray(values,dtype='float32'),1)
    def cached_catalogue(self):
        save_json(self.base/'cache/reference/regional_gauges/stations.json',{'stations':self.stations,'sources':[]})
    def test_neighbourhood_only_affects_request_screen_and_nodata_is_not_rain(self):
        one=inspect_product(self.path,self.stations,.2,1)[0];three=inspect_product(self.path,self.stations,.2,3)[0]
        self.assertFalse(one['product_rain_detected']);self.assertTrue(three['product_rain_detected'])
        self.write([[-999]*3]*3);self.assertFalse(inspect_product(self.path,self.stations,.2,3)[0]['product_rain_detected'])
    def test_requests_only_wet_region_plus_independent_controls(self):
        self.cached_catalogue();self.config['precipitation']['screening']['control_fraction']=1e-12
        targets=plan_requests(self.config,self.base,self.base,[self.delivery],{'jobs':[]})
        self.assertEqual(len(targets),1);self.assertEqual(targets[0]['networks'],['simnbo','simnpr']);self.assertEqual(targets[0]['stratum'],'product_rain')
        self.write([[0]*3]*3);self.config['precipitation']['screening']['control_fraction']=1
        targets=plan_requests(self.config,self.base,self.base,[self.delivery],{'jobs':[]});self.assertTrue(all(r['stratum']=='product_dry_control' for r in targets))
    def test_catalogue_extracted_once_and_classified_by_network_and_subarea(self):
        raw=self.base/'gauges.jsonl';raw.write_text(json.dumps({'network':'dpcn-lazio','lat':4120000,'lon':1050000,'data':[{'vars':{'B01019':{'v':'South Lazio'}}}]}))
        index={'jobs':[{'status':'SUCCESS','file':str(raw),'sha256':digest(raw)}]}
        cat=catalogue(self.base,index);self.assertEqual(cat['stations'][0]['region'],'Lazio');self.assertEqual(cat['stations'][0]['sub_aoi'],'South')
        with patch('s5validation.precipitation_screening.digest',side_effect=AssertionError('already indexed')):self.assertEqual(catalogue(self.base,index)['sha256'],cat['sha256'])
    def test_combined_regional_receipts_cover_full_hour_but_partial_does_not(self):
        raw=self.base/'raw';raw.write_text('x');start=datetime(2026,10,1,tzinfo=timezone.utc)
        def job(networks):return {'status':'SUCCESS','from':'2026-10-01T00:00:00Z','to':'2026-10-01T01:00:00Z','file':str(raw),'sha256':digest(raw),'payload':{'only_reliable':True,'output_format':'json','dataset_names':networks}}
        index={'jobs':[job(['simnbo','simnpr'])]}
        self.assertFalse(reference_receipts(index,start,['simnbo','simnpr','dpcn-lazio']))
        self.assertEqual(len(reference_receipts(index,start,['simnbo','simnpr','dpcn-lazio'],require_all=False)),1)
        index['jobs'].append(job(['dpcn-lazio']));self.assertEqual(len(reference_receipts(index,start,['simnbo','simnpr','dpcn-lazio'])),2)
    def test_controls_interleave_without_using_errors_or_passes(self):
        start=datetime(2026,10,1,tzinfo=timezone.utc)
        wet={'start':start,'region':'Sicily','networks':['dpcn-sicilia'],'stratum':'product_rain','priority':1,'maximum_mm':10}
        dry={**wet,'region':'Lazio','networks':['dpcn-lazio'],'stratum':'product_dry_control','priority':0,'maximum_mm':0}
        history={'jobs':[{'selection':{'stratum':'product_rain'}} for _ in range(4)]}
        self.assertEqual(choose_target([wet,dry],history)['stratum'],'product_dry_control')
    def test_targeted_api_request_has_only_selected_networks_and_one_hour(self):
        self.cached_catalogue();self.config['precipitation']['screening']['control_fraction']=1e-12
        save_json(self.base/'cache/reference/regional_gauges/index.json',{'jobs':[{'request_id':99,'status':'SUPERSEDED','remote_status_last_seen':'PENDING','from':'2026-10-01T00:00:00Z','to':'2026-10-01T01:00:00Z'}]})
        session=MagicMock();session.post.return_value.json.side_effect=['token',{'request_id':123}];session.get.return_value.json.side_effect=[{'quota':10**10,'used':0},{'remaining':10}]
        with patch('requests.Session',return_value=session),patch('s5validation.references.regional_gauges.downloader_credentials',return_value={'METEOHUB_BASE_URL':'https://example.invalid','METEOHUB_USER':'u','METEOHUB_PASSWORD':'p'}):
            result=refresh_regional_gauges(self.config,self.base,self.base,[self.delivery])
        payload=session.post.call_args.kwargs['json'];self.assertEqual(payload['dataset_names'],['simnbo','simnpr']);self.assertEqual(payload['reftime']['to'],'2026-10-01T01:00:00+00:00');self.assertEqual(result['pending_requests'],1)

    def test_partial_network_hour_is_not_counted_as_whole_aoi_pass(self):
        from s5validation.precipitation import PrecipitationAdapter
        from s5validation.hourly_reporting import hourly_results
        self.cached_catalogue();raw=self.base/'reference.jsonl'
        raw.write_text(json.dumps({'version':'0.1','network':'simnbo','lat':4550000,'lon':1050000,'date':'2026-10-01T01:00:00Z',
            'data':[{'level':[1,None,None,None],'timerange':[1,0,3600],'vars':{'B13011':{'v':0}}}]}))
        save_json(self.base/'cache/reference/regional_gauges/index.json',{'jobs':[{'status':'SUCCESS','from':'2026-10-01T00:00:00Z','to':'2026-10-01T01:00:00Z','file':str(raw),'sha256':digest(raw),'payload':{'only_reliable':True,'output_format':'json','dataset_names':['simnbo','simnpr']}}]})
        out=PrecipitationAdapter(self.config,self.base).validate(self.delivery,self.config['products']['S5-02-04'],self.base)
        self.assertEqual(out.status,'DIAGNOSTIC');self.assertTrue(out.metrics['partial_reference_coverage']);self.assertEqual(out.metrics['subareas']['North']['n'],1)
        row={'product':'S5-02-04','delivery_id':'rain','revision':'r','acquired_at':self.delivery.acquired_at,'status':out.status,'metrics':out.metrics,'provenance':out.provenance}
        scores=hourly_results([row],self.config);whole=next(r for r in scores if r['area']=='Whole AOI')
        self.assertEqual(whole['evaluation_status'],'PARTIAL_REFERENCE_COVERAGE');self.assertIsNone(whole['passes'])

    def test_deferred_hour_is_retryable_not_selected_not_failure(self):
        from s5validation.precipitation import PrecipitationAdapter
        from s5validation.pipeline import check_outcome,RETRYABLE
        save_json(self.base/'cache/reference/regional_gauges/index.json',{'jobs':[]})
        save_json(self.base/'inventory/precipitation_screening.json',{'catalogued_stations':1,'rows':[{'delivery_id':self.delivery.delivery_id,'revision':self.delivery.revision,'selected':False}]})
        out=PrecipitationAdapter(self.config,self.base).validate(self.delivery,self.config['products']['S5-02-04'],self.base)
        self.assertEqual(out.status,'NOT_SELECTED');self.assertIn(out.status,RETRYABLE);self.assertEqual(out.samples,[])
        check_outcome(out,self.config['products']['S5-02-04'],self.config['campaign'])

    def test_old_catalogue_bootstraps_only_new_network(self):
        self.cached_catalogue()
        self.config['precipitation']['networks']=['simnbo','dpcn-umbria']
        self.config['precipitation']['screening']['bootstrap_missing_networks']=True
        targets=plan_requests(self.config,self.base,self.base,[self.delivery],{'jobs':[]})
        self.assertEqual(targets[0]['networks'],['dpcn-umbria'])
        self.assertEqual(targets[0]['stratum'],'catalogue_bootstrap')

    def test_new_network_is_ingested_and_screened(self):
        regions={'Umbria':['dpcn-umbria']};raw=self.base/'new.jsonl'
        raw.write_text(json.dumps({'network':'dpcn-umbria','lat':4550000,'lon':1050000,'data':[{'vars':{'B01019':{'v':'U'}}}]}))
        cat=catalogue(self.base,{'jobs':[{'status':'SUCCESS','file':str(raw),'sha256':digest(raw)}]},regions)
        self.assertEqual(cat['stations'][0]['region'],'Umbria')
        self.assertTrue(inspect_product(self.path,cat['stations'],.2,3,regions)[0]['product_rain_detected'])

    def test_empty_successful_bootstrap_is_not_repeated(self):
        self.cached_catalogue();raw=self.base/'empty';raw.write_text('')
        self.config['precipitation']['networks']=['simnbo','dpcn-umbria']
        self.config['precipitation']['screening']['bootstrap_missing_networks']=True
        self.config['precipitation']['screening']['control_fraction']=1e-12
        job={'status':'SUCCESS','file':str(raw),'sha256':digest(raw),'payload':{'dataset_names':['dpcn-umbria']}}
        targets=plan_requests(self.config,self.base,self.base,[self.delivery],{'jobs':[job]})
        self.assertTrue(all(t['stratum']!='catalogue_bootstrap' for t in targets))

    def test_reference_rain_event_adds_product_dry_hour_without_using_errors(self):
        self.cached_catalogue();self.write([[0]*3]*3)
        self.config['precipitation']['screening']['control_fraction']=1e-12
        self.config['precipitation']['screening']['reference_event_hours']=[{'hour':self.delivery.acquired_at,'networks':['simnbo','simnpr']}]
        targets=plan_requests(self.config,self.base,self.base,[self.delivery],{'jobs':[]})
        self.assertEqual(len(targets),1);self.assertEqual(targets[0]['stratum'],'reference_rain_event')
        plan=json.loads((self.base/'inventory/precipitation_screening.json').read_text())
        self.assertTrue(next(r for r in plan['rows'] if r['region']=='Emilia-Romagna')['selected'])
