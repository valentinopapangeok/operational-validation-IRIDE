import copy
import csv
import json
import tempfile
import unittest
from pathlib import Path
from s5validation.downloads import digest
from s5validation.hourly_reporting import hourly_results,summarize,comparison_html
ROOT=Path(__file__).resolve().parents[1]


class HourlyReportingTests(unittest.TestCase):
    def setUp(self):self.config=json.loads((ROOT/'config/validation.json').read_text())
    def row(self,hour,value,product='S5-02-04',status='DIAGNOSTIC',version=1,revision='a'):
        metric='f1' if product=='S5-02-06' else 'relative_rmse_pct'
        return {'product':product,'delivery_id':f'IRIDE-S_{product}_20261001{hour:02d}_V{version}','revision':revision,
            'acquired_at':f'2026-10-01T{hour:02d}:00:00Z','status':status,'reason':'fixture','metrics':{metric:value,'n':5,'subareas':{'North':{metric:value,'n':2}}},'provenance':{}}
    def test_no_pooling_or_daily_averages_one_pass_and_one_failure(self):
        rows=[self.row(0,10),self.row(1,200)]
        result=hourly_results(rows,self.config);whole=[r for r in result if r['area']=='Whole AOI']
        self.assertEqual([r['relative_rmse_pct'] for r in whole],[10,200]);self.assertEqual([r['passes'] for r in whole],[True,False])
        summary=next(s for s in summarize(result) if s['area']=='Whole AOI' and s['period']=='Campaign')
        self.assertEqual((summary['passing_hours'],summary['evaluated_hours']),(1,2))
    def test_cloud_counts_hourly_f1_not_pooled_confusion(self):
        rows=[self.row(0,1,'S5-02-06'),self.row(1,.1,'S5-02-06')]
        result=hourly_results(rows,self.config);whole=[r for r in result if r['area']=='Whole AOI']
        self.assertEqual([r['passes'] for r in whole],[True,False]);self.assertEqual([r['f1'] for r in whole],[1,.1])
    def test_undefined_missing_qa_errors_and_area_support_are_not_failures(self):
        rows=[self.row(0,None),self.row(1,None,status='PENDING_REFERENCE'),self.row(2,None,status='REJECTED_QA'),self.row(3,None,status='ERROR')]
        result=hourly_results(rows,self.config);whole=[r for r in result if r['area']=='Whole AOI']
        self.assertTrue(all(r['passes'] is None for r in whole))
        s=next(s for s in summarize(result) if s['area']=='Whole AOI' and s['period']=='Campaign')
        self.assertEqual(s['evaluated_hours'],0);self.assertEqual([s[k] for k in ('undefined_hours','pending_hours','qa_excluded_hours','error_hours')],[1,1,1,1])
    def test_superseded_revision_and_version_never_double_count(self):
        old=self.row(0,1);new=self.row(0,50,revision='b');latest=self.row(0,5,version=2)
        result=[r for r in hourly_results([old,new,latest],self.config) if r['area']=='Whole AOI']
        self.assertEqual(len(result),1);self.assertEqual(result[0]['relative_rmse_pct'],5)
        old['updated_at']='2026-10-05';new['updated_at']='2026-10-06'
        result=[r for r in hourly_results([new,old],self.config) if r['area']=='Whole AOI'];self.assertEqual(result[0]['revision'],'b')
    def test_solar_subareas_use_verified_pairs_for_same_hour(self):
        self.config['products']['S5-02-03']['adapter']='cams_bni'
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'matched_sites.csv'
            with path.open('w',newline='') as f:
                writer=csv.DictWriter(f,fieldnames=['latitude','predicted','reference']);writer.writeheader();writer.writerows([{'latitude':lat,'predicted':100,'reference':100} for lat in [45,45.2,45.4,43,40]])
            row=self.row(12,0,'S5-02-03',status='PROCESSED');row['metrics'].pop('subareas');row['provenance']={'artifacts':[str(path)],'artifact_sha256':{str(path):digest(path)}}
            result=hourly_results([row],self.config)
            north=next(r for r in result if r['area']=='North');self.assertTrue(north['passes']);self.assertEqual(north['reference_pairs'],3)
            center=next(r for r in result if r['area']=='Center');self.assertEqual(center['evaluation_status'],'INSUFFICIENT_PAIRS')
            row['provenance']['inputs']={'policy':{'minimum_pairs':4}}
            strict=hourly_results([row],self.config);self.assertEqual(next(r for r in strict if r['area']=='North')['evaluation_status'],'INSUFFICIENT_PAIRS')
            path.write_text('corrupt')
            with self.assertRaisesRegex(ValueError,'checksum'):hourly_results([row],self.config)
    def test_exports_show_scores_and_hour_counts_without_daily_files(self):
        with tempfile.TemporaryDirectory() as td:
            workdir=Path(td);html=comparison_html([self.row(0,10)],self.config,workdir)
            self.assertIn('passing hours / evaluated hours',html);self.assertNotIn('passing days',html)
            self.assertTrue((workdir/'report/hourly_scores.csv').exists());self.assertTrue((workdir/'report/hourly_summary.json').exists())
            self.assertFalse((workdir/'report/precipitation_daily.csv').exists())

    def test_pixel_support_is_visible_without_changing_point_pass(self):
        row=self.row(0,10)
        row['metrics']['pixel_area_support']={'rainy_station_hours_with_area_support':3,
            'rainy_station_hours_total':50,'area_supported_pixels':1,
            'subareas':{'North':{'rainy_station_hours_with_area_support':3,
                'rainy_station_hours_total':30,'area_supported_pixels':1,'pixel_reference_rrmse_pct':19}}}
        with tempfile.TemporaryDirectory() as td:
            comparison_html([row],self.config,Path(td))
            with (Path(td)/'report/hourly_scores.csv').open(newline='') as f:
                scores=list(csv.DictReader(f))
        whole=next(r for r in scores if r['area']=='Whole AOI')
        north=next(r for r in scores if r['area']=='North')
        self.assertEqual((whole['pixel_area_supported_station_hours'],whole['pixel_area_total_rainy_station_hours']),('3','50'))
        self.assertEqual((north['pixel_area_rrmse_pct'],north['evaluation_status']),('19','PASS'))

    def test_dry_hour_keeps_false_alarm_diagnostics_without_inventing_rrmse(self):
        row=self.row(0,None);row['metrics'].update(reference_mean_mm=0,rmse=.3,all_dry_agreement=False,event_detection={'tp':0,'fp':2,'fn':0,'tn':3,'false_alarm_ratio':1})
        whole=next(r for r in hourly_results([row],self.config) if r['area']=='Whole AOI')
        self.assertEqual(whole['evaluation_status'],'UNDEFINED');self.assertIsNone(whole['passes'])
        self.assertEqual(whole['fp'],2);self.assertEqual(whole['reference_mean'],0);self.assertEqual(whole['rmse'],.3)
