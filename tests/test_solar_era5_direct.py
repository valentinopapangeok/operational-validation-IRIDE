import json,tempfile,unittest
from pathlib import Path
from datetime import datetime,timezone,timedelta
from unittest.mock import patch
import h5py,numpy as np,rasterio
from rasterio.transform import from_origin
from s5validation.solar_era5 import read_fdir,projection_factors,solar_cosine,ERA5DirectAdapter
from s5validation.references.era5_direct import required_times,request_for,refresh_era5_direct,DATASET,VARIABLE
from s5validation.downloads import digest,save_json
from s5validation.models import Delivery
ROOT=Path(__file__).resolve().parents[1]
class ERA5DirectTests(unittest.TestCase):
 def fixture(self,root):
  cfg=json.loads((ROOT/'config/validation.json').read_text());cfg['campaign']['aoi_file']='aoi.json';cfg['solar']['minimum_pairs']=1
  save_json(root/'aoi.json',{'type':'Polygon','coordinates':[[[9,40],[12,40],[12,46],[9,46],[9,40]]]})
  nc=root/'fdir.nc'
  with h5py.File(nc,'w') as f:
   f['valid_time']=[int(datetime(2026,10,1,h,tzinfo=timezone.utc).timestamp()) for h in range(24)];f['valid_time'].attrs['units']='seconds since 1970-01-01';f['latitude']=[45.,43.];f['longitude']=[10.,11.]
   f['fdir']=np.array([np.full((2,2),(100+h)*3600.) for h in range(24)]);f['fdir'].attrs['units']='J m**-2'
  p=root/'product.tif'
  with rasterio.open(p,'w',driver='GTiff',width=2,height=2,count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(9.5,46,1,2),nodata=-999) as f:f.write(np.full((2,2),300,dtype='float32'),1)
  receipt={'file':str(nc),'sha256':digest(nc),'dataset':DATASET,'variable':VARIABLE,'request':{}}
  save_json(root/'cache/reference/era5_direct/index.json',{'days':{'2026-10-01':receipt}})
  d=Delivery('S5-02-03','synthetic','r1',str(p),'2026-10-01T10:00:00Z');return cfg,nc,d
 def test_era5_hour_is_not_differenced_like_era5_land(self):
  with tempfile.TemporaryDirectory() as t:
   _,nc,_=self.fixture(Path(t));a,_,_=read_fdir(nc,datetime(2026,10,1,11,tzinfo=timezone.utc));np.testing.assert_allclose(a,111)
 def test_request_is_direct_single_levels_and_end_validity(self):
  self.assertEqual(required_times('2026-10-01T23:00:00Z'),[datetime(2026,10,2,tzinfo=timezone.utc)])
  r=request_for('2026-10-01',[6,35,19,48]);self.assertEqual(r['variable'],[VARIABLE]);self.assertEqual(r['product_type'],['reanalysis']);self.assertNotEqual(DATASET,'reanalysis-era5-land')
 def test_geometry_bounds_cover_any_nonnegative_node_distribution(self):
  t=datetime(2026,10,1,8,tzinfo=timezone.utc);m,lo,hi=projection_factors(t,np.array([40.,45.]),np.array([10.,15.]))
  for dni in ([100.,0.,0.],[0.,100.,0.],[0.,0.,100.],[100.,200.,500.]):
   c=np.array([solar_cosine(t+timedelta(minutes=k),np.array([40.,45.]),np.array([10.,15.])) for k in (0,30,60)])
   weights=np.array([.25,.5,.25]);energy=float(np.dot(weights,dni));actual=np.sum(np.asarray(dni)[:,None]*weights[:,None]*c,axis=0)
   self.assertTrue(np.all(actual>=energy*lo-1e-9));self.assertTrue(np.all(actual<=energy*hi+1e-9))
  self.assertTrue(np.all((lo<=m)&(m<=hi)))
 def test_nocturnal_geometry_is_zero(self):
  self.assertEqual(float(solar_cosine(datetime(2026,10,1,0,tzinfo=timezone.utc),43,11)),0)
 def test_diagnostic_support_covers_subareas_without_accepting_samples(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cfg,_,d=self.fixture(root);cfg['solar']['method_acceptance']['status']='diagnostic';o=ERA5DirectAdapter(cfg,root).validate(d,cfg['products'][d.product],root)
   self.assertEqual(o.status,'DIAGNOSTIC');self.assertEqual(o.samples,[]);self.assertTrue(o.metrics['approximate_geometry']);self.assertIn('North',o.metrics['subareas']);self.assertEqual(o.provenance['interval_end_utc'],'2026-10-01T11:00:00+00:00')
   from s5validation.hourly_reporting import hourly_results
   scores=hourly_results([{'product':d.product,'delivery_id':d.delivery_id,'revision':d.revision,'acquired_at':d.acquired_at,**vars(o)}],cfg)
   self.assertEqual(scores[0]['evaluation_status'],'APPROXIMATE_GEOMETRY');self.assertIsNone(scores[0]['passes'])
 def test_operative_approval_is_recorded_and_old_trials_are_not_promoted(self):
  from s5validation.hourly_reporting import hourly_results
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cfg,_,d=self.fixture(root);o=ERA5DirectAdapter(cfg,root).validate(d,cfg['products'][d.product],root)
   self.assertEqual(o.status,'PROCESSED');self.assertEqual(o.samples,[])
   row={'product':d.product,'delivery_id':d.delivery_id,'revision':d.revision,'acquired_at':d.acquired_at,**vars(o)}
   score=hourly_results([row],cfg)[0];self.assertIn(score['evaluation_status'],['PASS','FAIL']);self.assertIsInstance(score['passes'],bool)
   row['provenance']=dict(o.provenance,method='era5-direct-normal-reference-v2')
   self.assertEqual(hourly_results([row],cfg)[0]['evaluation_status'],'APPROXIMATE_GEOMETRY')
 def test_reference_conversion_uses_integrated_cosine(self):
  from s5validation.solar_era5 import reference_normal
  instant=datetime(2026,10,1,10,tzinfo=timezone.utc)
  latitude=np.array([[43.]]);longitude=np.array([[11.]])
  dense=np.array([solar_cosine(instant+timedelta(seconds=i),latitude,longitude) for i in range(3601)])
  mean=np.sum((dense[:-1]+dense[1:])/2,axis=0)/3600
  ref,lo,hi,minimum=reference_normal(300*mean,instant,latitude,longitude)
  np.testing.assert_allclose(ref,300,rtol=1e-6)
  self.assertTrue(np.all((lo<=ref)&(ref<=hi)));self.assertTrue(np.all(minimum>0))
 def test_product_is_not_geometrically_transformed(self):
  from s5validation.solar_era5 import product_normal
  from s5validation.solar_core import grid_transform
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);_,nc,d=self.fixture(root)
   _,lat,lon=read_fdir(nc,datetime(2026,10,1,11,tzinfo=timezone.utc))
   actual,coverage=product_normal(d.uri,(2,2),grid_transform(lat,lon))
   np.testing.assert_allclose(actual,300);np.testing.assert_allclose(coverage,1)
 def test_corrupt_reference_is_not_used(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cfg,nc,d=self.fixture(root);nc.write_bytes(b'corrupt')
   with self.assertRaisesRegex(ValueError,'checksum'):ERA5DirectAdapter(cfg,root).validate(d,cfg['products'][d.product],root)
 def test_disable_trial_requires_original_observations(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cfg,_,d=self.fixture(root);cfg['solar']['era5_direct']['enable_reference_conversion']=False
   self.assertEqual(ERA5DirectAdapter(cfg,root).validate(d,cfg['products'][d.product],root).status,'PENDING_CONFIGURATION')
 def test_offline_provider_reuses_verified_dataset_specific_receipt(self):
  with tempfile.TemporaryDirectory() as t:
   root=Path(t);cfg,nc,d=self.fixture(root);p=root/'cache/reference/era5_direct/index.json';index=json.loads(p.read_text());index['days']['2026-10-01']['request']=request_for('2026-10-01',cfg['solar']['bounds']);save_json(p,index)
   with patch('cdsapi.Client',side_effect=AssertionError('no network')):r=refresh_era5_direct(cfg,root,root,[d])
   self.assertEqual(r['reused_references'],1)
