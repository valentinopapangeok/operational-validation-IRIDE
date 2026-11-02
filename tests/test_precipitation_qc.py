import unittest
from s5validation.precipitation_qc import review_reference,radar_discrepancy
class NeighbourQCTests(unittest.TestCase):
 def gauge(self,key,lat,lon,mm):return {'station':key,'name':key,'latitude':lat,'longitude':lon,'mm':mm}
 def test_isolated_rain_flags_without_product_or_radar(self):
  g=self.gauge('a',42,12,20);nn=[self.gauge(str(i),42+.01*i,12,0) for i in [1,2,3]]
  self.assertIn('isolated_wet_reference',review_reference(g,nn)['flags'])
 def test_neighbouring_rain_prevents_isolated_flag(self):
  g=self.gauge('a',42,12,20);nn=[self.gauge(str(i),42+.01*i,12,15) for i in [1,2,3]]
  self.assertEqual(review_reference(g,nn)['flags'],[])
 def test_colocated_sites_do_not_manufacture_three_neighbours(self):
  g=self.gauge('a',42,12,20);nn=[self.gauge(str(i),42.01,12+i*.00001,0) for i in [1,2,3,4]]
  r=review_reference(g,nn);self.assertEqual(r['neighbour_count'],1);self.assertEqual(r['flags'],[])
 def test_missing_radar_never_becomes_disagreement(self):
  self.assertIsNone(radar_discrepancy(20,None));self.assertTrue(radar_discrepancy(20,2));self.assertFalse(radar_discrepancy(20,18))
 def test_radar_difference_does_not_enter_neighbour_decision(self):
  g=self.gauge('a',42,12,20);nn=[self.gauge(str(i),42+.01*i,12,20) for i in [1,2,3]]
  self.assertTrue(radar_discrepancy(20,0));self.assertEqual(review_reference(g,nn)['flags'],[])
