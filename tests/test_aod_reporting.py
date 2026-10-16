import csv
import json
import tempfile
import unittest
from pathlib import Path
from s5validation.aod_reporting import spatial_comparison,comparison_html

class AODReportingTests(unittest.TestCase):
    def test_identical_pairs_exclusions_model_fill_and_pooled_metrics(self):
        with tempfile.TemporaryDirectory() as tmp:
            rows=[]
            for day,pairs in [('01',[(1,1,1,'EO'),(9,1,'','EO'),(100,1,100,'model')]),('02',[(3,2,2,'unverified')])]:
                p=Path(tmp)/day/'paired_station_days.csv';p.parent.mkdir()
                with p.open('w',newline='') as stream:
                    w=csv.writer(stream);w.writerow(['sample_key','predicted','reference','neighborhood_mean','source_type'])
                    for i,(pred,ref,neighbor,source) in enumerate(pairs):w.writerow([day+str(i),pred,ref,neighbor,source])
                rows.append(dict(product='S5-02-09',status='DIAGNOSTIC',acquired_at='2026-10-'+day,provenance=json.dumps({'artifacts':[str(p)]})))
            result=spatial_comparison(rows)
            self.assertEqual(result['daily'][0]['primary']['n'],2)
            self.assertEqual(result['daily'][0]['excluded_neighborhood'],1)
            common=result['pooled']['common_containing_pixel']
            self.assertEqual(common['n'],2)
            self.assertAlmostEqual(common['rmse'],(1/2)**.5)
            self.assertAlmostEqual(common['relative_rmse_pct'],100*(1/2)**.5/1.5)
            self.assertEqual(result['pooled']['neighborhood']['rmse'],0)
            self.assertIn('containing pixel remains primary',comparison_html(rows))
            # Repeated evidence must not double count station-days.
            self.assertEqual(spatial_comparison(rows+rows)['pooled']['neighborhood']['n'],2)

    def test_missing_evidence_and_non_diagnostic_not_promoted(self):
        row=dict(product='S5-02-09',status='REJECTED_QA',acquired_at='2026-10-01',provenance='{}')
        self.assertEqual(comparison_html([row]),'')
        row['status']='DIAGNOSTIC'
        self.assertEqual(comparison_html([row]),'')
