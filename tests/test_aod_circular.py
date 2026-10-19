import json
import tempfile
import unittest
from pathlib import Path
import numpy as np
import rasterio
from rasterio.transform import from_origin
from s5validation.aod_circular import CircularAODAdapter,circle_mask,quadratic_550
from s5validation.models import Delivery
from s5validation.downloads import save_json,digest
BASE=Path(__file__).resolve().parents[1]
class CircularTests(unittest.TestCase):
    def test_four_channel_power_law_missing_and_invalid_channels(self):
        channels={str(w):.2*(w/550)**-1.3 for w in [440,500,675,870]}
        value,evidence=quadratic_550(channels);self.assertAlmostEqual(value,.2,places=12)
        self.assertEqual(evidence['channels_nm'],[440.,500.,675.,870.])
        for value in [0,-1,float('nan')]:
            self.assertEqual(quadratic_550(dict(channels,**{'440':value})),(None,None))
        del channels['500'];self.assertEqual(quadratic_550(channels),(None,None))

    def test_real_adapter_union_failures_boundaries_cache_and_checksums(self):
        with tempfile.TemporaryDirectory() as temp:
            w=Path(temp);c=json.loads((BASE/'config/validation.json').read_text());c['campaign']['aoi_file']='aoi.json';c['campaign']['sub_aoi_file']='sub.json'
            polygon={'type':'Polygon','coordinates':[[[12,41.49],[13.01,41.49],[13.01,42.5],[12,42.5],[12,41.49]]]}
            save_json(w/'aoi.json',polygon);save_json(w/'sub.json',{'type':'FeatureCollection','features':[{'type':'Feature','properties':{'area':name},'geometry':polygon if name=='Center' else {'type':'Polygon','coordinates':[[[0,0],[1,0],[1,1],[0,1],[0,0]]]}} for name in ['North','Center','South']]})
            p=w/'product.tif'
            with rasterio.open(p,'w',driver='GTiff',width=101,height=101,count=1,dtype='float32',nodata=-999,crs='EPSG:4326',transform=from_origin(12,42.5,.01,.01)) as d:d.write(np.full((101,101),.1,dtype='float32'),1)
            obs=[]
            for station,ref in [('pass',.1),('fail',.2)]:
                for minute in [0,15]:obs.append(dict(station=station,observed_at=f'2026-10-01T10:{minute:02d}:00+00:00',latitude=42.005,longitude=12.505,level=1.5,channels_nm={str(wave):ref*(wave/550)**-1 for wave in [440,500,675,870]}))
            raw=w/'raw.txt';raw.write_text('frozen reference fixture');save_json(w/'cache/reference/aeronet/index.json',{'days':{'2026-10-01':{'levels':{'15':obs,'20':[]},'receipts':[{'file':str(raw),'sha256':digest(raw)}]}}})
            delivery=Delivery('S5-02-09','fixture','revision',str(p),'2026-10-01T00:00:00+00:00');adapter=CircularAODAdapter(c,w);out=adapter.validate(delivery,{},w)
            self.assertEqual(out.status,'PROCESSED');self.assertEqual(out.samples,[])
            self.assertEqual(out.metrics['n'],2);self.assertEqual(out.metrics['passing_station_days'],1)
            self.assertEqual(out.metrics['passing_area_counts'],{'Center':1})
            self.assertAlmostEqual(out.metrics['relative_rmse_pct'],100*(.01/2)**.5/.15,places=4)
            self.assertEqual(out.metrics['all_eligible_sampled_pixels'],out.metrics['passing_sampled_pixels'])  # coincident circles must not double count
            self.assertLess(out.metrics['passing_sampled_pixels'],101*101)
            again=adapter.validate(delivery,{},w);self.assertEqual(out.provenance['method_signature'],again.provenance['method_signature'])
            with rasterio.open(p) as ds:
                bounds,full,mask=circle_mask(ds,12.005,42.495,27.5)
                self.assertGreater(full,int(mask.sum()))  # off-raster portion remains in denominator
            # Exercise the operative registration, registry and report path offline.
            from s5validation.pipeline import run
            from s5validation.registry import Registry
            from s5validation.aod_reporting import circular_comparison_html
            c['delivery']['manifest']=str(w/'deliveries.json')
            save_json(w/'deliveries.json',[vars(delivery)])
            registry=Registry(w/'state.db')
            try:
                self.assertEqual(run(registry,c,w,w,'S5-02-09')['attempts'],1)
                row=dict(registry.rows('S5-02-09')[0]);self.assertEqual(row['status'],'PROCESSED')
                self.assertEqual(registry.samples('S5-02-09'),[])
                self.assertIn('Passing-only rRMSE',circular_comparison_html(registry.rows('S5-02-09')))
            finally:registry.close()
            raw.write_text('changed reference')
            with self.assertRaisesRegex(ValueError,'checksum'):adapter.validate(delivery,{},w)
