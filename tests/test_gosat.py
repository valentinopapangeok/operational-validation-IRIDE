import copy
import io
import json
import tarfile
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import h5py
import rasterio
from rasterio.transform import from_origin
from s5validation.gosat import GOSATAdapter
from s5validation.models import Delivery
from s5validation.downloads import digest,save_json
from s5validation.references.gosat import cache_path,refresh_gosat,reference_month

ROOT=Path(__file__).resolve().parents[1]

class GOSATTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.base=Path(self.temp.name);self.config=json.loads((ROOT/'config/validation.json').read_text())
        self.config['campaign']['aoi_file']='aoi.json'
        (self.base/'aoi.json').write_text(json.dumps({'type':'Polygon','coordinates':[[[9.5,40.5],[11.5,40.5],[11.5,42.5],[9.5,42.5],[9.5,40.5]]]}))
    def fixture(self,gas='CH4'):
        num='07' if gas=='CH4' else '08';value=1800 if gas=='CH4' else 420
        tif=self.base/(num+'.tif')
        with rasterio.open(tif,'w',driver='GTiff',width=2,height=2,count=1,dtype='float32',crs='EPSG:4326',transform=from_origin(9.5,42.5,1,1)) as ds:ds.write(np.full((2,2),value,dtype='float32'),1)
        path=cache_path(self.base,gas,'2026-10','03.05');path.parent.mkdir(parents=True,exist_ok=True)
        stream=io.BytesIO()
        with h5py.File(stream,'w') as h:
            h['Data/geolocation/latitude']=np.array([[42.,42.],[41.,41.]])
            h['Data/geolocation/longitude']=np.array([[10.,11.],[10.,11.]])
            h['Data/mixingRatio/X'+gas]=np.full((2,2),value/(1000 if gas=='CH4' else 1))
            h['Data/maskInformation/X'+gas+'Mask']=np.zeros((2,2),dtype='int32')
        raw=stream.getvalue()
        with tarfile.open(path,'w') as archive:
            member=tarfile.TarInfo('test.h5');member.size=len(raw);archive.addfile(member,io.BytesIO(raw))
        save_json(path.with_suffix('.receipt.json'),{'sha256':digest(path)})
        return Delivery('S5-02-'+num,'delivery'+num,'rev',str(tif),'2026-10-01T00:00:00+00:00'),path
    def test_both_gases_units_matching_regions_and_no_accepted_samples(self):
        for gas in ('CH4','CO2'):
            d,path=self.fixture(gas)
            out=GOSATAdapter(self.config,self.base).validate(d,self.config['products'][d.product],self.base)
            self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.metrics['n'],4)
            self.assertAlmostEqual(out.metrics['relative_rmse_pct'],0)
            self.assertAlmostEqual(out.metrics['italy_valid_coverage_fraction'],1)
            self.assertEqual(out.samples,[])
            self.assertEqual(out.metrics['subareas']['South']['n'],2)
            self.assertEqual(out.metrics['subareas']['Center']['n'],2)
            with patch('s5validation.gosat_core.match_cells',side_effect=AssertionError('must reuse')):
                self.assertEqual(GOSATAdapter(self.config,self.base).validate(d,self.config['products'][d.product],self.base).metrics,out.metrics)
    def test_empty_queue_no_auth_and_verified_cache_reuse(self):
        with patch('s5validation.references.gosat.downloader_credentials',side_effect=AssertionError('no credentials needed')):
            self.assertEqual(refresh_gosat(self.config,self.base,self.base,[])['new_downloaded_references'],0)
            d,path=self.fixture()
            self.assertEqual(refresh_gosat(self.config,self.base,self.base,[d])['reused_references'],1)
    def test_missing_and_corrupt_reference_are_distinct(self):
        d,path=self.fixture();path.unlink()
        adapter=GOSATAdapter(self.config,self.base)
        self.assertEqual(adapter.validate(d,self.config['products'][d.product],self.base).status,'PENDING_REFERENCE')
        path.write_bytes(b'bad')
        with self.assertRaisesRegex(ValueError,'checksum'):adapter.validate(d,self.config['products'][d.product],self.base)
    def test_month_offset_explicit_year_boundary(self):
        d,_=self.fixture()
        self.assertEqual(reference_month(d,{'month_offset':-10}),'2025-12')
    def test_registration(self):
        from s5validation.adapters import register
        from s5validation.pipeline import ADAPTERS
        from s5validation.operational import REFERENCE_REFRESHERS
        register(self.config,self.base)
        self.assertIn('gosat_monthly',ADAPTERS);self.assertIn('gosat_monthly',REFERENCE_REFRESHERS)

    def test_masked_product_and_reference_mask_selection(self):
        from s5validation.gosat_core import match_cells
        d,path=self.fixture()
        with rasterio.open(d.uri,'r+') as ds:
            ds.nodata=-9999
            data=ds.read(1);data[0,0]=-9999;ds.write(data,1)
        rows=match_cells(Path(d.uri),path,'2026-10',self.config['gosat'],'CH4')
        self.assertEqual(len(rows),3)
        policy=copy.deepcopy(self.config['gosat']);policy['mask_value']=1
        self.assertEqual(match_cells(Path(d.uri),path,'2026-10',policy,'CH4'),[])

    def test_low_italy_coverage_rejected_with_metrics(self):
        d,path=self.fixture()
        (self.base/'aoi.json').write_text(json.dumps({'type':'Polygon','coordinates':[[[0,30],[30,30],[30,60],[0,60],[0,30]]]}))
        out=GOSATAdapter(self.config,self.base).validate(d,self.config['products'][d.product],self.base)
        self.assertEqual(out.status,'REJECTED_QA');self.assertLess(out.metrics['italy_valid_coverage_fraction'],.2)
