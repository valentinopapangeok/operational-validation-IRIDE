import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.aod import AODAdapter
from s5validation.models import Delivery
from s5validation.downloads import save_json

BASE=Path(__file__).resolve().parents[1]
class SourceTests(unittest.TestCase):
    def test_model_fill_is_reported_separately_and_unmatched_time_excluded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config=json.loads((BASE/'config/validation.json').read_text());config['campaign']['aoi_file']='aoi.json';config['aeronet']['minimum_station_days_per_delivery']=1
            save_json(root/'aoi.json',{'type':'Polygon','coordinates':[[[10,43],[12,43],[12,45],[10,45],[10,43]]]})
            p=root/'aod.tif';profile=dict(driver='GTiff',width=2,height=2,count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(10,45,1,1),nodata=-999)
            with rasterio.open(p,'w',**profile) as ds:ds.write(np.array([[.1,.9],[.8,.2]],dtype='float32'),1)
            profile.update(dtype='uint8',nodata=0)
            with rasterio.open(root/'aod.source_id.tif','w',**profile) as ds:ds.write(np.array([[1,4],[2,3]],dtype='uint8'),1)
            profile.update(nodata=255)
            with rasterio.open(root/'aod.qa.tif','w',**profile) as ds:ds.write(np.ones((2,2),dtype='uint8'),1)
            save_json(p.with_suffix('.validation.json'),dict(schema_version=1,wavelength_nm=550,source_id_raster='aod.source_id.tif',quality_mask_raster='aod.qa.tif',observation_time_utc='2026-10-01T10:00:00Z'))
            obs=[]
            for name,lon,lat,hour in [('EO',10.5,44.5,'10'),('model',11.5,44.5,'10'),('unmatched',10.5,43.5,'06')]:
                for minute in ['00','05','10']:obs.append(dict(station=name,observed_at=f'2026-10-01T{hour}:{minute}:00Z',latitude=lat,longitude=lon,level=1.5,channels_nm={'550':.1}))
            save_json(root/'cache/reference/aeronet/index.json',{'days':{'2026-10-01':{'levels':{'15':obs},'receipts':[]}}})
            d=Delivery('S5-02-09','source-fixture','v1',str(p),'2026-10-01T00:00:00Z');out=AODAdapter(config,root).validate(d,{},root)
            self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.samples,[]);self.assertEqual(out.metrics['n'],1)
            self.assertAlmostEqual(out.metrics['relative_rmse_pct'],0,places=4)
            self.assertEqual(out.metrics['model_station_days'],1);self.assertAlmostEqual(out.metrics['model_fill_metrics']['relative_rmse_pct'],800,places=3)
            detail=json.loads(Path(out.provenance['artifacts'][0]).read_text());self.assertTrue(any(r['station']=='unmatched' and 'source-time window' in r['reason'] for r in detail['rejected_stations']))
            self.assertEqual({r['source_type'] for r in detail['pairs']},{'EO','model'})
            with rasterio.open(root/'aod.source_id.tif','r+') as ds:ds.write(np.full((2,2),4,dtype='uint8'),1)
            model_only=AODAdapter(config,root).validate(d,{},root)
            self.assertEqual(model_only.status,'DIAGNOSTIC');self.assertEqual(model_only.metrics['n'],0)
            self.assertEqual(model_only.metrics['model_station_days'],2);self.assertIsNone(model_only.metrics.get('relative_rmse_pct'))
            # A categorical support scale cannot silently change source identity.
            with rasterio.open(root/'aod.source_id.tif','r+') as ds:ds.scales=[2]
            with self.assertRaises(ValueError):AODAdapter(config,root).validate(d,{},root)
