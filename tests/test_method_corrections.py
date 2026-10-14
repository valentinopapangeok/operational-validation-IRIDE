import copy
import json
import tempfile
import unittest
from datetime import datetime,timedelta,timezone
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.downloads import digest,save_json
from s5validation.models import Delivery
from s5validation.snow import reference_class,deduplicate_snow
from s5validation.solar import SolarAdapter
from s5validation.solar_core import deaccumulate
from s5validation.solar_bni import SolarBNIAdapter
from s5validation.references.cams_solar import parse_csv,sites_for,refresh_cams_solar,load_site
from s5validation.product_support import read_support
from s5validation.aod import station_days,AODAdapter

BASE=Path(__file__).resolve().parents[1]


def cams_csv(site,day='2026-10-01',bni=100.,reliability=1.):
    lines=['# Time reference: Universal time (UT)','# uom:"Wh m-2" [unit]',
        f'# Latitude (positive North): {site["latitude"]}',f'# Longitude (positive East): {site["longitude"]}',
        '# Observation period;TOA;Clear sky BNI;BNI;Reliability']
    start=datetime.fromisoformat(day)
    for h in range(24):
        t=start+timedelta(hours=h);end=t+timedelta(hours=1)
        lines.append(f'{t.isoformat()}/{end.isoformat()};400;200;{bni};{reliability}')
    return '\n'.join(lines)+'\n'


class MethodCorrectionTests(unittest.TestCase):
    def fixture(self,root):
        config=json.loads((BASE/'config/validation.json').read_text())
        config['campaign']['aoi_file']='aoi.geojson'
        save_json(root/'aoi.geojson',{'type':'Polygon','coordinates':[[[10,43],[12,43],[12,45],[10,45],[10,43]]]})
        p=root/'product.tif'
        with rasterio.open(p,'w',driver='GTiff',width=2,height=2,count=1,dtype='float32',
                           crs='EPSG:4326',transform=from_origin(10,45,1,1),nodata=-999.) as ds:
            ds.write(np.full((2,2),120,dtype='float32'),1)
        d=Delivery('S5-02-03','fixture','v1',str(p),'2026-10-01T10:00:00Z')
        return config,p,d

    def cached_cams(self,root,config,bni=100.,reliability=1.):
        index={'sites':{}}
        for site in sites_for(config,root):
            p=root/(site['site']+'.csv');p.write_text(cams_csv(site,bni=bni,reliability=reliability))
            index['sites'][site['site']]=[dict(site,file=str(p),sha256=digest(p))]
        save_json(root/'cache/reference/cams_solar/index.json',index)

    def test_full_cell_snow_bounds_do_not_treat_cloud_as_no_snow(self):
        # 60% snow among 60% clear area is only 36–76% snow in the whole cell.
        snow,known,lower,upper=reference_class(np.array([.6,1.,0.]),np.array([.6,.6,.6]),.5)
        self.assertEqual(known.tolist(),[False,True,True])
        self.assertEqual(snow.tolist(),[False,True,False])
        self.assertAlmostEqual(lower[0],.36);self.assertAlmostEqual(upper[0],.76)

    def test_overpass_conflict_excluded_without_choosing_favorable_label(self):
        parts=[{'row':np.array([0,1]),'column':np.array([0,0]),'reference':np.array(labels),
                'scene_id':np.array([scene,scene]),'coverage_fraction':np.array([coverage,coverage])}
               for labels,scene,coverage in [([0,1],'a',.9),([1,1],'b',1.)]]
        pairs,info=deduplicate_snow(parts,return_info=True)
        self.assertEqual(pairs['row'].tolist(),[1]);self.assertEqual(info['conflicting_reference_cells_excluded'],1)

    def test_legacy_incompatible_reference_is_blocked_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            config,p,d=self.fixture(Path(tmp));out=SolarAdapter(config,Path(tmp)).validate(d,{},Path(tmp))
            self.assertEqual(out.status,'PENDING_CONFIGURATION');self.assertEqual(out.metrics,{})

    def test_legacy_small_negative_noise_is_bounded(self):
        prior=np.array([20000000.,1000.]);current=np.array([19999998.,999.])
        self.assertEqual(deaccumulate(current,prior).tolist(),[0.,0.])
        with self.assertRaises(ValueError):deaccumulate(np.array([19999990.]),np.array([20000000.]))
        with self.assertRaises(ValueError):deaccumulate(np.array([990.]),np.array([1000.]))

    def test_cams_parser_equivalent_quantity_units_location_and_interval(self):
        site={'latitude':44.5,'longitude':10.5};text=cams_csv(site)
        data=parse_csv(text,site);self.assertEqual(len(data),24);self.assertEqual(data['2026-10-01T10:00:00+00:00']['bni'],100)
        bad=[text.replace('Wh m-2','W m-2'),text.replace('Universal time (UT)','True solar time'),
             text.replace('10.5','11.5'),text.replace('Clear sky BNI;BNI','Clear sky BNI;GHI'),
             text.replace('T11:00:00','T11:30:00'),cams_csv(site,bni=-1)]
        for value in bad:
            with self.assertRaises(ValueError):parse_csv(value,site)

    def test_cams_bni_like_for_like_metrics_and_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,p,d=self.fixture(root);self.cached_cams(root,config)
            adapter=SolarBNIAdapter(config,root);out=adapter.validate(d,{},root)
            self.assertEqual(out.status,'PROCESSED');self.assertEqual(out.samples,[])
            self.assertEqual(out.metrics['n'],4);self.assertEqual(out.metrics['rmse'],20.)
            self.assertEqual(out.metrics['relative_rmse_pct'],20.)
            self.assertEqual(out.metrics['daylight_all_conditions']['n'],4)
            with patch('s5validation.solar_bni.plot_pairs',side_effect=AssertionError('cached result recomputed')):
                self.assertEqual(adapter.validate(d,{},root).metrics,out.metrics)
            self.cached_cams(root,config,bni=40.)
            self.assertEqual(adapter.validate(d,{},root).status,'REJECTED_QA')
            self.cached_cams(root,config,reliability=.5)
            self.assertEqual(adapter.validate(d,{},root).status,'REJECTED_QA')
            config['solar']['product_quantity']='global_horizontal'
            self.assertEqual(adapter.validate(d,{},root).status,'PENDING_CONFIGURATION')

    def test_cams_refresh_verified_reuse_and_error_redaction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,p,d=self.fixture(root)
            with patch('s5validation.references.cams_solar.downloader_credentials',return_value={'ADS_KEY':'secret'}),patch('cdsapi.Client') as client:
                def retrieve(dataset,request,target):
                    self.assertEqual(dataset,'cams-solar-radiation-timeseries')
                    Path(target).write_text(cams_csv(request['location']))
                client.return_value.retrieve.side_effect=retrieve
                result=refresh_cams_solar(config,root,root,[d]);self.assertEqual(result['new_downloaded_references'],4)
                self.assertEqual(refresh_cams_solar(config,root,root,[d])['reused_references'],4)
                self.assertEqual(client.return_value.retrieve.call_count,4)
                # Different sampling lattice must not reuse an old site key at new coordinates.
                config['solar']['sampling_grid_degrees']=2.
                client.return_value.retrieve.side_effect=RuntimeError('secret signed-url')
                with self.assertRaises(OSError):refresh_cams_solar(config,root,root,[d])
                report=(root/'inventory/cams_solar_refresh.json').read_text()
                self.assertNotIn('secret',report);self.assertNotIn('signed-url',report)

    def test_source_time_window_filters_daily_observations(self):
        from shapely.geometry import box
        from test_snow_aod import SnowAODTests
        with tempfile.TemporaryDirectory() as tmp:
            policy=SnowAODTests().config(Path(tmp))['aeronet']
            obs=[{'station':'s','observed_at':f'2026-10-05T{t}+00:00','latitude':42.5,'longitude':9.5,
                  'level':1.5,'channels_nm':{'550':value}} for t,value in [('09:40:00',.1),('10:00:00',.2),('10:20:00',.3),('15:00:00',1.)]]
            record={'levels':{'15':obs}}
            pairs=station_days(record,policy,'2026-10-05',box(8,41,11,44),{'s':'2026-10-05T10:00:00Z'})
            self.assertEqual(len(pairs),1);self.assertEqual(pairs[0]['observation_count'],3)
            self.assertAlmostEqual(pairs[0]['reference'],.2);self.assertEqual(pairs[0]['matching'],'source_time_window')
            self.assertEqual(station_days(record,policy,'2026-10-05',box(8,41,11,44),{}),[])

    def test_delivered_support_conflict_and_path_escape_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,p,d=self.fixture(root)
            with rasterio.open(p,'r+') as ds:ds.update_tags(AOD_OUTPUT_WAVELENGTH_NM='550')
            self.assertEqual(read_support(p)[0]['wavelength_nm'],550)
            save_json(p.with_suffix('.validation.json'),{'schema_version':1,'wavelength_nm':470})
            with self.assertRaises(ValueError):read_support(p)

    def test_aod_adapter_applies_source_time_and_product_qa(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);config,p,d=self.fixture(root)
            config['aeronet']['minimum_station_days_per_delivery']=1
            obs=[{'station':'s','observed_at':f'2026-10-01T{time}Z','latitude':44.5,'longitude':10.5,
                  'level':1.5,'channels_nm':{'550':value}} for time,value in
                  [('09:40:00',.1),('10:00:00',.2),('10:20:00',.3),('15:00:00',1.)]]
            save_json(root/'cache/reference/aeronet/index.json',{'days':{'2026-10-01':{'levels':{'15':obs},'receipts':[]}}})
            qa=root/'product.qa.tif'
            with rasterio.open(p) as ds:profile=ds.profile.copy()
            profile.update(dtype='uint8',nodata=255)
            with rasterio.open(qa,'w',**profile) as ds:ds.write(np.ones((2,2),dtype='uint8'),1)
            save_json(p.with_suffix('.validation.json'),{'schema_version':1,'wavelength_nm':550,
                'observation_time_utc':'2026-10-01T10:00:00Z','quality_mask_raster':qa.name})
            delivery=Delivery('S5-02-09','aod','v1',str(p),'2026-10-01T00:00:00Z')
            out=AODAdapter(config,root).validate(delivery,{},root)
            self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.metrics['n'],1)
            self.assertAlmostEqual(out.metrics['relative_rmse_denominator'],.2)
            self.assertTrue(out.metrics['temporal_matching_verified']);self.assertTrue(out.metrics['wavelength_verified'])
            self.assertTrue(out.metrics['product_qa_verified'])
            # A 2x2 corner cannot satisfy 75% of an expected 3x3 neighborhood.
            self.assertIsNone(out.metrics['spatial_neighborhood_metrics'])
            with rasterio.open(qa,'r+') as ds:v=ds.read(1);v[0,0]=0;ds.write(v,1)
            self.assertEqual(AODAdapter(config,root).validate(delivery,{},root).status,'REJECTED_QA')
            save_json(p.with_suffix('.validation.json'),{'schema_version':1,'quality_mask_raster':'../escape.tif'})
            with self.assertRaises(ValueError):read_support(p)

if __name__=='__main__':unittest.main()
