import csv
import io
import json
import math
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box,mapping
from s5validation.references.aeronet import parse_observations,spectral_aod
from s5validation.aod import station_days,AODAdapter
from s5validation.snow import snow_collocate,deduplicate_snow,SnowAdapter,scl_valid
from s5validation.models import Delivery
from s5validation.downloads import digest,save_json


def raster(path,values,transform,crs='EPSG:32632',nodata=-999):
    with rasterio.open(path,'w',driver='GTiff',width=values.shape[1],height=values.shape[0],count=1,
        dtype=values.dtype,transform=transform,crs=crs,nodata=nodata) as ds:ds.write(values,1)


class SnowAODTests(unittest.TestCase):
    def config(self,base):
        config=json.loads((Path(__file__).resolve().parents[1]/'config/validation.json').read_text(encoding='utf-8'))
        config['campaign']['aoi_file']='aoi.geojson'
        save_json(base/'aoi.geojson',mapping(box(8,41,11,44)))
        return config

    def observations(self,station='fixture',level=1.5):
        return [{'station':station,'observed_at':f'2026-10-05T0{hour}:00:00+00:00','latitude':42.5,'longitude':9.5,
            'level':level,'channels_nm':{'550':.2}} for hour in (6,7,8)]

    def test_real_aeronet_header_labels_missing_values_and_duplicates(self):
        header=['AERONET_Site','Date(dd:mm:yyyy)','Time(hh:mm:ss)','AOD_500nm','AOD_675nm','Data_Quality_Level','Site_Latitude(Degrees)','Site_Longitude(Degrees)']
        stream=io.StringIO();stream.write('AERONET Data Download (Version 3 Direct Sun)\n')
        w=csv.writer(stream);w.writerow(header)
        w.writerow(['fixture','05:10:2026','06:00:00','.2','.1','lev15','42.5','9.5'])
        w.writerow(['fixture','05:10:2026','06:00:00','.2','.1','lev15','42.5','9.5'])
        w.writerow(['invalid','05:10:2026','07:00:00','-999','nan','lev15','42.5','9.5'])
        w.writerow(['wrong_day','04:10:2026','07:00:00','.2','.1','lev15','42.5','9.5'])
        parsed=parse_observations(stream.getvalue(),'2026-10-05',15)
        self.assertEqual(len(parsed),1)
        self.assertEqual(parse_observations(stream.getvalue(),'2026-10-05',20),[])
        self.assertEqual(parse_observations('AERONET Data Download (Version 3 Direct Sun)\n','2026-10-05',20),[])
        with self.assertRaises(ValueError):parse_observations('<html>Service error</html>','2026-10-05',15)

    def test_spectral_interpolation_direct_zero_and_no_extrapolation(self):
        observation={'channels_nm':{'500':.2,'675':.1}}
        actual,method=spectral_aod(observation,550)
        expected=.2*(550/500)**(math.log(.1/.2)/math.log(675/500))
        self.assertAlmostEqual(actual,expected);self.assertEqual(method['method'],'log_log_interpolation')
        self.assertEqual(spectral_aod(observation,900),(None,None))
        self.assertEqual(spectral_aod({'channels_nm':{'550':0}},550)[0],0)
        self.assertIsNone(spectral_aod({'channels_nm':{'500':0,'675':.1}},550)[0])

    def test_station_day_quality_preference_and_no_observation_overcount(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config=self.config(base);policy=config['aeronet']
            record={'levels':{'20':self.observations(level=2),'15':self.observations()}}
            output=station_days(record,policy,'2026-10-05',box(8,41,11,44))
            self.assertEqual(len(output),1);self.assertEqual(output[0]['level'],2)
            self.assertEqual(output[0]['observation_count'],3)
            record['levels']['20']=record['levels']['20'][:1]
            self.assertEqual(station_days(record,policy,'2026-10-05',box(8,41,11,44))[0]['level'],1.5)
            self.assertEqual(station_days(record,policy,'2026-10-05',box(12,41,13,44)),[])

    def test_snow_qa_excludes_cloud_shadow_water_unclassified(self):
        with tempfile.TemporaryDirectory() as directory:
            config=self.config(Path(directory))
            self.assertEqual(scl_valid(np.arange(12),config['snow']).tolist(),[False,False,False,False,True,True,False,False,False,False,False,True])

    def snow_fixture(self,base):
        config=self.config(base);config['snow'].update(minimum_pairs_per_scene=1,minimum_pairs_per_delivery=1)
        product=base/'snow.tif';reference=base/'scl.tif'
        raster(product,np.array([[1,0],[0,1]],dtype='float32'),from_origin(500000,4640000,100,100))
        scl=np.full((10,10),5,dtype='uint8');scl[:5,:]=11
        raster(reference,scl,from_origin(500000,4640000,20,20),nodata=0)
        record={'scene_id':'synthetic','status':'DOWNLOADED','revision':'fixture',
            'assets':{'SCL':{'local_path':str(reference),'sha256':digest(reference)}}}
        return config,product,reference,record

    def test_native_snow_aggregation_and_cloud_coverage(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config,product,reference,record=self.snow_fixture(base)
            pairs,info=snow_collocate(product,record,box(8,41,11,44),config['snow'])
            self.assertEqual(pairs['reference'].tolist(),[1,1,0,0]);self.assertEqual(pairs['predicted'].tolist(),[1,0,0,1])
            self.assertTrue(np.allclose(pairs['coverage_fraction'],1))
            with rasterio.open(reference,'r+') as ds:
                v=ds.read(1);v[:5,:5]=9;ds.write(v,1)
            pairs,info=snow_collocate(product,record,box(8,41,11,44),config['snow'])
            self.assertEqual(len(pairs['predicted']),3)

    def test_overlapping_snow_tiles_are_not_double_counted(self):
        parts=[{'row':np.array([0]),'column':np.array([0]),'coverage_fraction':np.array([coverage]),
            'scene_id':np.array([name]),'reference':np.array([value])} for coverage,name,value in ((.6,'a',0),(.9,'b',1))]
        result=deduplicate_snow(parts)
        self.assertEqual(len(result['row']),1);self.assertEqual(result['reference'][0],1)

    def test_snow_adapter_metrics_evidence_and_cached_qa(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config,product,reference,record=self.snow_fixture(base)
            save_json(base/'cache/reference/sentinel2/index.json',{'days':{'2026-10-05':{'candidate_count':1,'scenes':[record]}}})
            d=Delivery('S5-02-05','synthetic','v1',str(product),'2026-10-05T00:00:00+00:00')
            adapter=SnowAdapter(config,base);out=adapter.validate(d,config['products'][d.product],base)
            self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.samples,[])
            self.assertEqual(out.metrics['f1'],.5);self.assertEqual(out.metrics['n'],4)
            with patch('s5validation.snow.snow_collocate',side_effect=AssertionError('recomputed')):
                self.assertEqual(adapter.validate(d,config['products'][d.product],base).metrics,out.metrics)
            config['snow']['minimum_pairs_per_delivery']=5
            rejected=adapter.validate(d,config['products'][d.product],base);self.assertEqual(rejected.status,'REJECTED_QA')
            with patch('s5validation.snow.snow_collocate',side_effect=AssertionError('recomputed rejection')):
                self.assertEqual(adapter.validate(d,config['products'][d.product],base).status,'REJECTED_QA')

    def test_aod_adapter_station_day_metric_and_nodata(self):
        with tempfile.TemporaryDirectory() as directory:
            base=Path(directory);config=self.config(base);product=base/'aod.tif'
            raster(product,np.array([[.4,.8],[1.,1.2]],dtype='float32'),from_origin(9,43,1,1),crs='EPSG:4326')
            save_json(base/'cache/reference/aeronet/index.json',{'days':{'2026-10-05':{'levels':{'15':self.observations()},'receipts':[]}}})
            d=Delivery('S5-02-09','synthetic','v1',str(product),'2026-10-05T00:00:00+00:00')
            adapter=AODAdapter(config,base);out=adapter.validate(d,config['products'][d.product],base)
            self.assertEqual(out.status,'DIAGNOSTIC');self.assertEqual(out.samples,[]);self.assertEqual(out.metrics['n'],1)
            self.assertAlmostEqual(out.metrics['rmse'],.2,places=6);self.assertAlmostEqual(out.metrics['relative_rmse_pct'],100,places=5)
            with rasterio.open(product,'r+') as ds:v=ds.read(1);v[0,0]=-999;ds.write(v,1)
            self.assertEqual(adapter.validate(d,config['products'][d.product],base).status,'REJECTED_QA')
            config['aeronet']['wavelength_nm']=None
            self.assertEqual(adapter.validate(d,config['products'][d.product],base).status,'PENDING_CONFIGURATION')

if __name__=='__main__':unittest.main()
