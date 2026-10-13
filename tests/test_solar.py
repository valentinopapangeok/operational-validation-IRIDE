"""Scientific and operational regressions; no real CDS calls or credentials."""
import copy
import csv
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch
import h5py
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.models import Delivery
from s5validation.downloads import digest, save_json
from s5validation.pipeline import run
from s5validation.registry import Registry
from s5validation.report import generate
from s5validation.solar import SolarAdapter
from s5validation.solar_core import read_ssrd, utc_timestamp, grid_transform
from s5validation.references.era5_land import required_times, refresh_era5_land, unpack_download

BASE = Path(__file__).resolve().parents[1]
DAY = '2026-10-05'


def reference_file(path, zero=False, missing=None, invalid_units=False, negative=False):
    hours = [h for h in range(24) if h != missing]
    with h5py.File(path, 'w') as f:
        f['valid_time'] = [int(datetime(2026, 10, 5, h, tzinfo=timezone.utc).timestamp()) for h in hours]
        f['valid_time'].attrs['units'] = 'seconds since 1970-01-01'
        f['latitude'] = [44., 43.]; f['longitude'] = [10., 11.]
        # 00 is the previous day's accumulated total; 01 resets.
        values = np.array([np.full((2, 2), 0 if zero else (24 if h == 0 else h)*3600.) for h in hours])
        if negative and 3 in hours: values[hours.index(3)] = 0
        f['ssrd'] = values
        f['ssrd'].attrs['units'] = 'W m-2' if invalid_units else 'J m**-2'
        f['ssrd'].attrs['_FillValue'] = -9999.


class SolarTests(unittest.TestCase):
    def fixture(self, root, hour=2, zero=False):
        config = json.loads((BASE/'config/validation.json').read_text())
        product = root/'product.tif'
        with rasterio.open(product, 'w', driver='GTiff', width=2, height=2, count=1, dtype='float32',
                           crs='EPSG:4326', transform=from_origin(9.5,44.5,1,1), nodata=-9999.) as f:
            f.write(np.full((2,2), 2., dtype='float32'),1)
        ref = root/'reference.nc'; reference_file(ref,zero)
        receipt = {'file': str(ref), 'sha256': digest(ref), 'dataset': 'synthetic ERA5-Land', 'request': {}}
        save_json(root/'cache/reference/era5_land/index.json', {'days': {DAY: receipt}})
        d = Delivery('S5-02-03', 'synthetic-hour', 'fixture-v1', str(product), f'{DAY}T{hour:02d}:00:00Z')
        return config, product, ref, d

    def test_hourly_deaccumulation_and_midnight_reset(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); config,p,ref,d=self.fixture(root)
            for hour in (0,1,2,22):
                d=Delivery(d.product,d.delivery_id,d.revision,d.uri,f'{DAY}T{hour:02d}:00:00Z')
                out=SolarAdapter(config,root).validate(d,config['products'][d.product],root)
                self.assertEqual(out.status,'PROCESSED'); self.assertEqual(out.samples,[])
                self.assertNotIn('diagnostic_only',out.metrics);self.assertNotIn('acceptance_blockers',out.provenance)
                self.assertAlmostEqual(out.metrics['reference_mean_wh_m2'],1.)
                self.assertAlmostEqual(out.metrics['rmse'],1.)
                self.assertAlmostEqual(out.metrics['relative_rmse_pct'],100.)
                self.assertEqual(out.metrics['n'],4)
                with next(Path(x) for x in out.provenance['artifacts'] if x.endswith('.csv')).open() as handle:
                    rows=list(csv.DictReader(handle))
                self.assertEqual(len(rows),4); self.assertEqual(rows[0]['interval_start_utc'],f'{DAY}T{hour:02d}:00:00+00:00')

    def test_zero_reference_has_undefined_rrmse(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t); config,p,ref,d=self.fixture(root,zero=True)
            out=SolarAdapter(config,root).validate(d,config['products'][d.product],root)
            self.assertEqual(out.status,'PROCESSED'); self.assertIsNone(out.metrics['relative_rmse_pct'])
            self.assertEqual(out.metrics['rmse'],2.); self.assertIsNone(out.metrics['r2'])

    def test_scale_offset_nodata_and_spatial_qa(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            with rasterio.open(p,'r+') as ds:
                ds.scales=(2.,);ds.offsets=(1.,);v=ds.read(1);v[0,0]=-9999.;ds.write(v,1)
            adapter=SolarAdapter(config,root);out=adapter.validate(d,config['products'][d.product],root)
            self.assertEqual(out.metrics['n'],3); self.assertAlmostEqual(out.metrics['rmse'],4.)
            config['solar']['minimum_pairs']=4
            self.assertEqual(adapter.validate(d,config['products'][d.product],root).status,'REJECTED_QA')
            config['solar']['bounds']=[20,35,21,36]
            self.assertEqual(adapter.validate(d,config['products'][d.product],root).status,'REJECTED_QA')

    def test_pending_corruption_wrong_units_negative_and_grid(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            adapter=SolarAdapter(config,root);cfg=config['products'][d.product]
            reference_file(ref,missing=3)
            index=root/'cache/reference/era5_land/index.json'
            def update():save_json(index,{'days':{DAY:{'file':str(ref),'sha256':digest(ref)}}})
            update();self.assertEqual(adapter.validate(d,cfg,root).status,'PENDING_REFERENCE')
            reference_file(ref,invalid_units=True);update()
            with self.assertRaisesRegex(ValueError,'units'):adapter.validate(d,cfg,root)
            reference_file(ref,negative=True);update()
            with self.assertRaisesRegex(ValueError,'Negative'):adapter.validate(d,cfg,root)
            ref.write_bytes(b'corrupted')
            with self.assertRaisesRegex(ValueError,'checksum'):adapter.validate(d,cfg,root)
            index.unlink(); self.assertEqual(adapter.validate(d,cfg,root).status,'PENDING_REFERENCE')
        with self.assertRaisesRegex(ValueError,'descending'):grid_transform(np.array([43,44]),np.array([10,11]))

    def test_reference_packing_fill_and_duplicate_timestamps(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            with h5py.File(ref,'r+') as f:
                f['ssrd'].attrs['scale_factor']=2.;f['ssrd'].attrs['add_offset']=10.
                f['ssrd'][2,0,0]=-9999.
            values,lat,lon=read_ssrd(ref,utc_timestamp(d.acquired_at))
            self.assertTrue(np.isnan(values[0,0]));self.assertEqual(values[1,1],14410.)
            with h5py.File(ref,'r+') as f:f['valid_time'][3]=f['valid_time'][2]
            with self.assertRaisesRegex(ValueError,'Duplicate'):read_ssrd(ref,utc_timestamp(d.acquired_at))

    def test_timestamp_campaign_and_hour23_policy(self):
        self.assertEqual(utc_timestamp('2026-10-05T02:00:00+02:00').hour,0)
        self.assertEqual([t.hour for t in required_times('2026-10-05T00:00:00Z')],[1])
        for value in ('2026-10-05T02:30:00Z','2026-10-05T02:00:00'):
            with self.assertRaises(ValueError):utc_timestamp(value)
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root,hour=23)
            self.assertEqual(SolarAdapter(config,root).validate(d,config['products'][d.product],root).status,'REJECTED_QA')
            config['campaign']['end']=DAY+'T00:00:00Z'
            self.assertEqual(SolarAdapter(config,root).validate(d,config['products'][d.product],root).status,'OUTSIDE_CAMPAIGN')

    def test_shared_pipeline_cache_registry_and_report(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            save_json(root/'deliveries.json',[vars(d)])
            config['delivery']['manifest']=str(root/'deliveries.json');(root/'report').mkdir()
            registry=Registry(root/'validation.db')
            try:
                run(registry,config,root,root,product=d.product)
                self.assertEqual(registry.rows(d.product)[0]['status'],'PROCESSED')
                self.assertEqual(registry.samples(d.product),[])
                with patch('s5validation.solar_core.product_on_reference_grid',side_effect=AssertionError('unnecessary recomputation')):
                    run(registry,config,root,root,product=d.product)
                report=generate(registry,config,root,d.product).read_text()
                self.assertIn('matched_cells.csv',report);self.assertIn('scatter.png',report)
                self.assertNotIn('DIAGNOSTIC',report);self.assertNotIn('diagnostic_only',report)
            finally:registry.close()

    def test_safe_zip_daily_refresh_reuse_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            (root/'cache/reference/era5_land/index.json').unlink()
            from s5validation.references.era5_land import request_for
            with patch('s5validation.references.era5_land.downloader_credentials',return_value={'CDS_KEY':'synthetic-secret'}),patch('cdsapi.Client') as client:
                def retrieve(dataset,request,target):
                    with zipfile.ZipFile(target,'w') as z:z.write(ref,'../../ssrd.nc')
                client.return_value.retrieve.side_effect=retrieve
                result=refresh_era5_land(config,root,root,[d,d])
                self.assertEqual(result['new_downloaded_references'],1)
                self.assertEqual(client.return_value.retrieve.call_count,1)
                again=refresh_era5_land(config,root,root,[d]);self.assertEqual(again['reused_references'],1)
                self.assertFalse((root.parent/'ssrd.nc').exists())
                # Partial response is rejected; no promotion, old receipt retained.
                config['solar']['bounds']=[6,35,19,48]
                client.return_value.retrieve.side_effect=RuntimeError('synthetic-secret signed-url')
                with self.assertRaises(OSError):refresh_era5_land(config,root,root,[d])
                text=(root/'inventory/era5_land_refresh.json').read_text()
                self.assertNotIn('synthetic-secret',text);self.assertNotIn('signed-url',text)
                self.assertFalse(list((root/'cache/reference/era5_land').rglob('*.part')))

    def test_operational_queue_uses_shared_refresher_and_adapter(self):
        from test_downloads import FakeStorage
        from s5validation.operational import execute, REFERENCE_REFRESHERS
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            config['delivery']['manifest']='deliveries.json';save_json(root/'deliveries.json',[])
            storage=FakeStorage();storage.add('ar_delivery/','IRIDE-S_S5-02-03_2026100502_V1.tif')
            def downloaded(client,item,workdir):
                return {'status':'DOWNLOADED','assets':[{'key':item['primary_key'],'local_path':str(p),'reused':True}]}
            def refreshed(config,base,workdir,deliveries):
                self.assertEqual(len(deliveries),1);self.assertEqual(deliveries[0].product,'S5-02-03')
                return {'new_downloaded_references':0}
            from s5validation.adapters import register
            register(config,root)
            registry=Registry(root/'validation.db')
            try:
                with patch.dict(REFERENCE_REFRESHERS,{'era5_land_ssrd':refreshed}),patch('s5validation.operational.download_item',side_effect=downloaded),patch('s5validation.adapters.register'):
                    result=execute(registry,config,root,root,storage,product='S5-02-03')
                self.assertEqual(result['delivery_states'],{'PROCESSED':1})
                self.assertEqual(result['errors'],[]);self.assertEqual(registry.samples('S5-02-03'),[])
            finally:registry.close()

    def test_partial_cds_response_not_promoted(self):
        with tempfile.TemporaryDirectory() as t:
            root=Path(t);config,p,ref,d=self.fixture(root)
            index=root/'cache/reference/era5_land/index.json';index.unlink()
            reference_file(ref,missing=12)
            with patch('s5validation.references.era5_land.downloader_credentials',return_value={'CDS_KEY':'fixture'}),patch('cdsapi.Client') as client:
                import shutil
                client.return_value.retrieve.side_effect=lambda dataset,request,target:shutil.copyfile(ref,target)
                with self.assertRaises(OSError):refresh_era5_land(config,root,root,[d])
                self.assertFalse(index.exists());self.assertFalse(list(index.parent.rglob('*.nc')))

if __name__=='__main__':unittest.main()
