import json,tempfile,unittest
from pathlib import Path
from s5validation.kpi_reporting import daily_scores,monthly_summary
ROOT=Path(__file__).resolve().parents[1]
class KPITests(unittest.TestCase):
 def setUp(self):self.cfg=json.loads((ROOT/'config/validation.json').read_text())
 def row(self,day,hour,areas):
  return {'product':'S5-02-06','delivery_id':f'C_{day}{hour}_V1','revision':'a','acquired_at':f'2026-10-{day:02d}T{hour:02d}:00:00Z','status':'DIAGNOSTIC','metrics':{'f1':.1,'n':10,'subareas':{a:{'f1':v,'n':10} for a,v in areas.items()}},'provenance':{}}
 def test_many_areas_and_hours_on_same_date_count_one_kpi(self):
  rows=[self.row(1,8,{'North':1,'Center':1,'South':1}),self.row(1,9,{'North':1,'Center':1,'South':1})]
  summary=next(s for s in monthly_summary(daily_scores(rows,self.cfg),self.cfg) if s['product']=='S5-02-06')
  self.assertEqual(summary['qualifying_count'],1);self.assertTrue(summary['geographic_target_met']);self.assertFalse(summary['count_target_met'])
 def test_five_dates_in_only_one_area_do_not_meet_geographic_target(self):
  rows=[self.row(d,8,{'North':1}) for d in range(1,6)]
  s=next(s for s in monthly_summary(daily_scores(rows,self.cfg),self.cfg) if s['product']=='S5-02-06')
  self.assertTrue(s['count_target_met']);self.assertFalse(s['monthly_target_met'])
  rows.extend([self.row(1,9,{'Center':1}),self.row(2,9,{'South':1})])
  s=next(s for s in monthly_summary(daily_scores(rows,self.cfg),self.cfg) if s['product']=='S5-02-06')
  self.assertEqual(s['qualifying_count'],5);self.assertTrue(s['monthly_target_met'])
 def test_partial_and_undefined_hours_do_not_qualify(self):
  r=self.row(1,8,{'North':None});r['metrics'].update(partial_reference_coverage=True)
  scores=daily_scores([r],self.cfg);whole=next(s for s in scores if s['area']=='Whole AOI')
  self.assertIsNone(whole['passes']);self.assertEqual(whole['partial_hours'],1)
  s=next(s for s in monthly_summary(scores,self.cfg) if s['product']=='S5-02-06');self.assertEqual(s['qualifying_count'],0)
 def test_monthly_coverage_gate_is_required(self):
  r=self.row(1,8,{});r['product']='S5-02-07';r['metrics']={'n':20,'relative_rmse_pct':0,'coverage_meets_requirement':False}
  s=next(s for s in monthly_summary(daily_scores([r],self.cfg),self.cfg) if s['product']=='S5-02-07')
  self.assertEqual(s['target'],1);self.assertFalse(s['monthly_target_met'])
  r['metrics']['coverage_meets_requirement']=True
  s=next(s for s in monthly_summary(daily_scores([r],self.cfg),self.cfg) if s['product']=='S5-02-07')
  self.assertTrue(s['monthly_target_met']);self.assertIsNone(s['geographic_target_met'])
 def test_aod_regional_score_includes_failing_stations(self):
  import csv
  from s5validation.kpi_reporting import pair_areas
  from s5validation.downloads import digest
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'paired_station_days.csv'
   with path.open('w',newline='') as f:
    w=csv.DictWriter(f,fieldnames=['latitude','predicted','reference','passes']);w.writeheader();w.writerows([{'latitude':45,'predicted':100,'reference':100,'passes':True},{'latitude':45,'predicted':100,'reference':200,'passes':False}])
   r={'product':'S5-02-09','provenance':{'artifacts':[str(path)],'artifact_sha256':{str(path):digest(path)}}}
   m=pair_areas(r)['North'];self.assertEqual(m['n'],2);self.assertGreater(m['relative_rmse_pct'],30)
   path.write_text('corrupt')
   with self.assertRaisesRegex(ValueError,'checksum'):pair_areas(r)
