import json
import unittest
from pathlib import Path
try:
    from shapely.geometry import shape,Point
    from shapely.ops import unary_union
except ImportError:
    shape=None
BASE=Path(__file__).resolve().parents[1]
@unittest.skipIf(shape is None,'scientific dependencies required')
class CampaignDecisionsTests(unittest.TestCase):
    def test_subareas_preserve_original_land_and_marine_coverage(self):
        def geometry(path):
            d=json.loads(path.read_text())
            if d['type']=='FeatureCollection':return unary_union([shape(f['geometry']) for f in d['features']])
            return shape(d['geometry'] if d['type']=='Feature' else d)
        for domain,original in [('land','italy_land.geojson'),('marine','marine.geojson')]:
            payload=json.loads((BASE/f'aoi/italy_{domain}_subareas.geojson').read_text());features=payload['features']
            self.assertEqual({f['properties']['area'] for f in features},{'North','Center','South'})
            parts=[shape(f['geometry']) for f in features]
            self.assertLess(unary_union(parts).symmetric_difference(geometry(BASE/'aoi'/original)).area,1e-9)
            for i in range(3):
                for j in range(i):self.assertLess(parts[i].intersection(parts[j]).area,1e-12)
        land={f['properties']['area']:shape(f['geometry']) for f in json.loads((BASE/'aoi/italy_land_subareas.geojson').read_text())['features']}
        for name,lon,lat in [('Center',12.51577,41.90169),('South',15.7237,40.60125),('North',10.94827,44.62909)]:self.assertTrue(land[name].covers(Point(lon,lat)))
    def test_method_approval_does_not_claim_measured_position_or_NASA_certification(self):
        c=json.loads((BASE/'config/validation.json').read_text());method=c['aeronet']['approved_working_method']
        self.assertEqual(method['radius_km'],27.5)
        self.assertEqual(method['minimum_valid_circle_fraction'],.2)
        self.assertEqual(method['minimum_observations_per_station_day'],2)
        self.assertIn('not a certification gate',method['acceptance_authority'])
        self.assertEqual(c['campaign']['positional_accuracy_policy']['distance_operator'],'<')
        self.assertIn('not a measured result',c['campaign']['positional_accuracy_policy']['measurement_status'])
