import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from shapely.geometry import box
from s5validation.thermal import qa_valid, temperature_celsius, collocate, deduplicate_pairs, array_metrics

class ThermalTests(unittest.TestCase):
    def setUp(self):
        self.policy=json.loads((Path(__file__).resolve().parents[1]/'config/validation.json').read_text())['landsat']
        self.asset={'href':'https://example.blob.core.windows.net/data/X_ST_B10.TIF','raster:bands':[{'scale':0.00341802,'offset':149,'unit':'kelvin'}]}

    def test_scaling_and_invalid_dn(self):
        values=temperature_celsius(np.array([0,292,293,44000]),self.asset)
        self.assertTrue(np.isnan(values[:2]).all())
        self.assertAlmostEqual(values[3],44000*.00341802+149-273.15)
        with self.assertRaises(ValueError):
            temperature_celsius(np.array([44000]),{'href':'BT.TIF','raster:bands':[{'scale':1}]})

    def test_qa_domain_and_uncertainty(self):
        raw=np.full(8,44000);qa=np.array([64,64|8,64|128,64,64,64,64|(2<<8),64])
        saturation=np.array([0,0,0,1,0,0,0,0]);unc=np.array([100,100,100,100,201,100,100,100]);dist=np.array([200,200,200,200,200,99,200,200])
        actual=qa_valid(raw,qa,saturation,unc,dist,self.policy['qa'],'land')
        np.testing.assert_array_equal(actual,[True,False,False,False,False,False,False,True])
        water=qa_valid(raw,qa,saturation,unc,dist,self.policy['qa'],'marine')
        self.assertEqual(np.flatnonzero(water).tolist(),[2])

    def test_native_cell_collocation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);receipt={'assets':{}}
            transform=from_origin(500000,4750000,30,30)
            for key,value in [('lwir11',44000),('qa_pixel',64),('qa_radsat',0),('qa',100),('cdist',200)]:
                path=root/(key+'.tif')
                with rasterio.open(path,'w',driver='GTiff',height=4,width=4,count=1,dtype='uint16',crs='EPSG:32633',transform=transform) as ds:
                    ds.write(np.full((4,4),value,dtype='uint16'),1)
                receipt['assets'][key]={'local_path':str(path)}
            reference=44000*.00341802+149-273.15
            s5=root/'s5.tif'
            with rasterio.open(s5,'w',driver='GTiff',height=2,width=2,count=1,dtype='float32',crs='EPSG:32633',transform=from_origin(500000,4750000,60,60)) as ds:
                ds.write(np.full((2,2),reference+2,dtype='float32'),1)
            pairs,info=collocate(s5,{'assets':{'lwir11':self.asset}},receipt,'land',box(10,35,20,50),self.policy)
            self.assertEqual(info['paired_s5_cells'],4)
            self.assertEqual(info['reference_pixels_in_target_aoi'],16)
            np.testing.assert_allclose(pairs['difference'],2,atol=1e-5)
            np.testing.assert_allclose(pairs['coverage_fraction'],1)
            self.assertAlmostEqual(array_metrics(pairs['predicted'],pairs['reference'])['rmse'],2,places=5)

    def test_overlapping_rows_use_best_coverage(self):
        def part(value,coverage):
            return {'predicted':np.array([value]),'reference':np.array([10.]),'row':np.array([1]),'column':np.array([2]),'coverage_fraction':np.array([coverage]),'reference_uncertainty_kelvin':np.array([1.])}
        pooled=deduplicate_pairs([part(12,.6),part(13,.9)],['overpass','overpass'])
        self.assertEqual(pooled['predicted'].tolist(),[13])
        separate=deduplicate_pairs([part(12,.6),part(13,.9)],['pass1','pass2'])
        self.assertEqual(len(separate['predicted']),2)

    def test_relative_rmse_reference_mean_in_celsius(self):
        metrics=array_metrics(np.array([22.,26.]),np.array([20.,24.]),normalization='mean_reference_in_data_units')
        self.assertAlmostEqual(metrics['relative_rmse_pct'],100*2/22)
        self.assertEqual(metrics['relative_rmse_denominator'],22)
        zero=array_metrics(np.array([1.,1.]),np.array([-1.,1.]),normalization='mean_reference_in_data_units')
        self.assertIsNone(zero['relative_rmse_pct'])
