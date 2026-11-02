import copy
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.models import Delivery
from s5validation.downloads import digest,save_json
from s5validation.cloud import CloudAdapter,hourly_reference
from s5validation.cloud_reporting import daily_results
from s5validation.references.msg_cloud import decode_msg,hour_key,nominal_time,refresh_msg_cloud,decoder_signature
ROOT=Path(__file__).resolve().parents[1]


class CloudTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.base=Path(self.tmp.name)
        self.config=json.loads((ROOT/'config/validation.json').read_text())
        self.config['cloud']['minimum_pairs']=1
        self.config['campaign']['aoi_file']='aoi.json'
        self.aoi=self.base/'aoi.json';self.aoi.write_text(json.dumps({'type':'Polygon','coordinates':[[[9,39],[11,39],[11,46],[9,46],[9,39]]]}))
        self.product=self.base/'product.tif'
        self.delivery=Delivery('S5-02-06','IRIDE-S_S5-02-06_2026100112_V1','revision',str(self.product),'2026-10-01T12:00:00+00:00')
        self.write_product([1,0,1])
    def write_product(self,values):
        with rasterio.open(self.product,'w',driver='GTiff',height=3,width=1,count=1,dtype='float32',nodata=-999,crs='EPSG:4326',transform=from_origin(9,46,2,2)) as ds:ds.write(np.array(values,dtype='float32')[:,None],1)
    def fixture(self,classes=None):
        folder=self.base/'cache/reference/msg_cloud';folder.mkdir(parents=True,exist_ok=True);scans=[]
        for minute in (0,15,30,45):
            raw=folder/f'{minute}.zip';raw.write_bytes(b'rawfixture')
            decoded=folder/f'{minute}.npz'
            np.savez(decoded,native_index=np.arange(3),latitude=np.array([45.,43.,41.]),longitude=np.array([10.,10.,10.]),
                **{'class':np.array(classes or [1,0,1],dtype='int8')})
            scans.append({'raw_file':str(raw),'raw_file_sha256':digest(raw),'decoded_file':str(decoded),'decoded_file_sha256':digest(decoded),
                'aoi_sha256':digest(self.aoi),'decoder_sha256':decoder_signature(),'nominal_time':f'2026-10-01T12:{minute:02d}:00+00:00'})
        save_json(folder/'index.json',{'hours':{hour_key(self.delivery.acquired_at):{'scans':scans}}})
        return scans
    def validate(self):return CloudAdapter(self.config,self.base).validate(self.delivery,self.config['products']['S5-02-06'],self.base)
    def test_perfect_and_subareas_and_no_accepted_samples(self):
        self.fixture();out=self.validate();self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.metrics['f1'],1)
        self.assertEqual(out.metrics['n'],3);self.assertEqual(out.samples,[])
        from s5validation.registry import Registry
        registry=Registry(self.base/'validation.db');self.addCleanup(registry.db.close)
        registry.discover(self.delivery);registry.record(self.delivery,out)
        self.assertEqual(registry.samples(self.delivery.product),[])
        self.assertIsNone(out.metrics['subareas']['Center']['f1']) # all clear is undefined, never perfect
        for a in ('North','South'):self.assertEqual(out.metrics['subareas'][a]['f1'],1)
        with patch('s5validation.cloud.hourly_reference',side_effect=AssertionError('cache required')):self.assertEqual(self.validate().metrics,out.metrics)
    def test_nodata_is_excluded_but_wrong_classes_are_errors(self):
        self.fixture();self.write_product([1,-999,0]);out=self.validate()
        self.assertEqual(out.metrics['n'],2);self.assertEqual(out.metrics['fn'],1)
        self.write_product([1,255,0])
        with self.assertRaisesRegex(ValueError,'Unexpected product'):self.validate()
    def test_missing_reference_corruption_and_partial_hour(self):
        self.assertEqual(self.validate().status,'PENDING_REFERENCE')
        scans=self.fixture();Path(scans[0]['decoded_file']).write_bytes(b'corrupt')
        with self.assertRaisesRegex(ValueError,'checksum'):self.validate()
        scans=self.fixture();p=self.base/'cache/reference/msg_cloud/index.json'
        save_json(p,{'hours':{self.delivery.acquired_at:{'scans':scans[:2]}}})
        self.assertEqual(self.validate().status,'PENDING_REFERENCE')
    def test_reference_qa_and_minimum_pairs(self):
        self.fixture([-1,-1,-1]);self.assertEqual(self.validate().status,'REJECTED_QA')
    def test_hourly_frequency_minimum_valid_scans_and_grid_mismatch(self):
        a={'class':np.array([1,0,-1]),'native_index':np.arange(3),'latitude':np.arange(3),'longitude':np.arange(3)}
        b={**a,'class':np.array([0,1,-1])}
        cls,n,f=hourly_reference([a,a,b,b],3,.5)
        np.testing.assert_array_equal(cls,[1,1,-1]);np.testing.assert_array_equal(n,[4,4,0])
        with self.assertRaisesRegex(ValueError,'grids differ'):hourly_reference([a,{**a,'native_index':np.array([0,1,4])}],1,.5)
    def test_cache_only_refresh_does_not_request_credentials_or_network(self):
        self.fixture()
        with patch('s5validation.references.msg_cloud.downloader_credentials',side_effect=AssertionError('must reuse cache')):
            result=refresh_msg_cloud(self.config,self.base,self.base,[self.delivery]);self.assertEqual(result['reused_references'],4)
            self.assertEqual(refresh_msg_cloud(self.config,self.base,self.base,[])['new_downloaded_references'],0)
    def test_timestamp_and_registration(self):
        self.assertEqual(nominal_time('MSG3-SEVI-MSGCLMK-0100-0100-20261001123000.000Z-NA').minute,30)
        with self.assertRaisesRegex(ValueError,'hour start'):hour_key('2026-10-01T12:01:00+00:00')
        from s5validation.adapters import register
        from s5validation.pipeline import ADAPTERS
        from s5validation.operational import REFERENCE_REFRESHERS
        register(self.config,self.base);self.assertIn('msg_cloud_hourly',ADAPTERS);self.assertIn('msg_cloud_hourly',REFERENCE_REFRESHERS)
    def test_daily_pool_includes_failures_and_replaces_superseded_version(self):
        def row(hour,version,tp,fn):return {'product':'S5-02-06','delivery_id':f'IRIDE-S_S5-02-06_20261001{hour}_V{version}',
            'acquired_at':f'2026-10-01T{hour}:00:00+00:00','status':'DIAGNOSTIC','metrics':json.dumps({'tp':tp,'fp':0,'fn':fn,'tn':0,'subareas':{}})}
        rows=[row('12',1,100,0),row('12',2,1,0),row('13',1,0,9)]
        result=daily_results(rows)[0];self.assertAlmostEqual(result['f1'],2/11);self.assertFalse(result['passes']);self.assertEqual(result['hours'],2)
    def test_binary_grib_decode_header_classes_and_time(self):
        import eccodes as ec
        h=ec.codes_grib_new_from_samples('regular_ll_sfc_grib2')
        try:
            ec.codes_set(h,'Ni',2);ec.codes_set(h,'Nj',2)
            for k,v in {'latitudeOfFirstGridPointInDegrees':44,'latitudeOfLastGridPointInDegrees':42,
                'longitudeOfFirstGridPointInDegrees':9,'longitudeOfLastGridPointInDegrees':11,
                'iDirectionIncrementInDegrees':2,'jDirectionIncrementInDegrees':2,'dataDate':20261001,'dataTime':1200,
                'discipline':3,'parameterCategory':0,'parameterNumber':7}.items():ec.codes_set(h,k,v)
            ec.codes_set_values(h,np.array([0.,1.,2.,3.]));payload=ec.codes_get_message(h)
        finally:ec.codes_release(h)
        raw=self.base/'reference.zip'
        with zipfile.ZipFile(raw,'w') as z:z.writestr('payload.grb',b'disseminationheader'+payload)
        stamp=datetime(2026,10,1,12,tzinfo=timezone.utc)
        from s5validation.references.msg_cloud import _GRID_CACHE
        _GRID_CACHE.clear()
        with patch('eccodes.codes_get_array',wraps=ec.codes_get_array) as get_array:
            data,meta=decode_msg(raw,(8,40,12,46),stamp)
            second,_=decode_msg(raw,(8,40,12,46),stamp)
            names=[call.args[1] for call in get_array.call_args_list]
            self.assertEqual(names.count('latitudes'),1);self.assertEqual(names.count('longitudes'),1)
            self.assertEqual(names.count('values'),2)
            np.testing.assert_array_equal(second['class'],data['class'])
        np.testing.assert_array_equal(data['class'],[0,0,1,-1]);self.assertEqual(meta['nominal_time'],stamp.isoformat())
        with self.assertRaisesRegex(ValueError,'time differs'):decode_msg(raw,(8,40,12,46),datetime(2026,10,1,13,tzinfo=timezone.utc))

    def test_empty_catalogue_is_pending_and_missing_scans_are_recorded(self):
        from unittest.mock import MagicMock
        store=MagicMock();store.get_collection.return_value.search.return_value=[]
        with patch('s5validation.references.msg_cloud.downloader_credentials',return_value={'EUMETSAT_CONSUMER_KEY':'test','EUMETSAT_CONSUMER_SECRET':'test'}), patch('eumdac.AccessToken'), patch('eumdac.DataStore',return_value=store):
            out=refresh_msg_cloud(self.config,self.base,self.base,[self.delivery])
        self.assertEqual(len(out['missing_scans']),4);self.assertEqual(self.validate().status,'PENDING_REFERENCE')

    def test_duplicate_and_wrong_hour_references_cannot_inflate_support(self):
        scans=self.fixture();p=self.base/'cache/reference/msg_cloud/index.json'
        save_json(p,{'hours':{self.delivery.acquired_at:{'scans':[scans[0],scans[0],scans[1]]}}})
        with self.assertRaisesRegex(ValueError,'Duplicate'):self.validate()
        wrong=[{**r,'nominal_time':r['nominal_time'].replace('T12:','T13:')} for r in scans]
        save_json(p,{'hours':{self.delivery.acquired_at:{'scans':wrong}}})
        self.assertEqual(self.validate().status,'PENDING_REFERENCE')

    def test_download_timeout_retries_are_finite_and_do_not_leak_auth(self):
        import requests
        from unittest.mock import MagicMock
        from s5validation.references.msg_cloud import download_product
        product=MagicMock();product.datastore.urls.get.return_value='https://example.invalid/msg'
        with patch('requests.get',side_effect=requests.Timeout('private-auth-value')) as get:
            with self.assertRaisesRegex(OSError,'bounded retries') as caught:
                download_product(product,'private-auth-value',self.base/'download.part')
            self.assertEqual(get.call_count,3)
            self.assertEqual(get.call_args.kwargs['timeout'],(15,90))
            self.assertNotIn('private-auth-value',str(caught.exception))
