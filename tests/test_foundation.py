import copy
import json
import math
import tempfile
import unittest
from pathlib import Path
from s5validation.metrics import continuous, binary, precipitation
from s5validation.models import Outcome
from s5validation.pipeline import run
from s5validation.registry import Registry
from s5validation.report import progress, generate

BASE = Path(__file__).resolve().parents[1]

class MetricsTests(unittest.TestCase):
    def test_continuous(self):
        m = continuous([2,4,6],[1,3,5],3)
        self.assertEqual((m['rmse'],m['mae'],m['bias']),(1,1,1))
        self.assertAlmostEqual(m['relative_rmse_pct'],100/3)
        self.assertEqual(m['r2'],0.625)
        self.assertIsNone(continuous([1],[1])['r2'])
        self.assertIsNone(continuous([1],[1])['relative_rmse_pct'])
        self.assertLess(continuous([20,20],[1,2])['r2'],0)

    def test_binary(self):
        m = binary([1,1,0,0],[1,0,1,0])
        self.assertEqual([m[k] for k in ('tp','fp','fn','tn')],[1,1,1,1])
        self.assertEqual(m['f1'],0.5)
        self.assertIsNone(binary([0,0],[0,0])['f1'])

    def test_rain(self):
        m = precipitation([0,2,3],[0,0,2],1)
        self.assertEqual(m['event_detection']['fp'],1)
        self.assertEqual(m['both_wet']['n'],1)
        self.assertIsNone(precipitation([0],[0],1)['both_wet'])

    def test_invalid(self):
        for p,r in [([],[]),([1],[1,2]),([math.nan],[1]),([1],[math.inf])]:
            with self.assertRaises(ValueError): continuous(p,r)
        with self.assertRaises(ValueError): binary([2],[1])
        with self.assertRaises(ValueError): continuous([1],[1],0)
        with self.assertRaises(ValueError): precipitation([1],[1],0)

class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.reg = Registry(self.base/'validation.db')
        self.cfg = json.loads((BASE/'config/validation.json').read_text(encoding='utf-8'))
        self.cfg['delivery']['manifest']='deliveries.json'
        self.cfg['campaign'].update(start='2026-10-01T00:00:00Z',end='2026-12-30T00:00:00Z',aoi_file='fixture',sub_aoi_file='fixture',kpi_aggregation='fixture')
        self.cfg['products']['S5-02-01'].update(adapter='test',units='fixture',temporal_tolerance_seconds=3600,minimum_valid_pairs=1,minimum_valid_overlap_fraction=0.1,reference_quality_policy='fixture')
        self.d = dict(product='S5-02-01',delivery_id='d1',revision='v1',uri='fixture://product',acquired_at='2026-10-05T10:00:00Z')
        self.write([self.d])
    def tearDown(self):
        self.reg.close()
        self.temp.cleanup()
    def write(self,data):
        (self.base/'deliveries.json').write_text(json.dumps(data), encoding='utf-8')
    def complete(self):
        return Outcome('COMPLETE','valid','AVAILABLE',samples=[dict(sample_key='acquisition1',area='North',observed_at='2026-10-05T10:00:00Z',evidence={'source':'fixture'})],metrics={'n':1,'rmse':1},provenance={'method':'fixture'})
    def execute(self,out):
        class Fake:
            def validate(inner,*args): return copy.deepcopy(out)
        return run(self.reg,self.cfg,self.base,self.base,adapters={'test':Fake()})
    def test_retry_skip_revision(self):
        self.assertEqual(self.execute(Outcome('PENDING_REFERENCE','waiting','NOT_AVAILABLE'))['new_deliveries'],1)
        self.assertEqual(self.reg.rows()[0]['status'],'PENDING_REFERENCE')
        self.assertEqual(self.execute(self.complete())['new_deliveries'],0)
        self.assertEqual(self.execute(self.complete())['attempts'],0)
        self.d['revision']='v2'
        self.write([self.d])
        self.execute(Outcome('PENDING_REFERENCE','waiting','NOT_AVAILABLE'))
        self.assertEqual(progress(self.reg,self.cfg,'S5-02-01')[0],0)
        self.execute(self.complete())
        self.assertEqual(progress(self.reg,self.cfg,'S5-02-01')[0],1)
    def test_deduplication_area_requirement(self):
        self.execute(self.complete())
        self.write([self.d,dict(self.d,delivery_id='d2')])
        self.execute(self.complete())
        self.assertEqual(progress(self.reg,self.cfg,'S5-02-01')[0],1)
        self.cfg['products']['S5-02-01'].update(sample_target=1,per_area_target=1)
        self.assertEqual(progress(self.reg,self.cfg,'S5-02-01')[2],'Sample volume pending')
    def test_qa_and_configuration(self):
        self.execute(Outcome('REJECTED_QA','cloudy','AVAILABLE'))
        self.assertEqual(self.execute(self.complete())['attempts'],1)
        self.write([dict(self.d,delivery_id='d2')])
        run(self.reg,self.cfg,self.base,self.base,adapters={})
        self.assertEqual(self.reg.rows()[-1]['status'],'PENDING_CONFIGURATION')
    def test_no_evidence(self):
        self.execute(Outcome('COMPLETE','invalid','AVAILABLE'))
        self.assertEqual(self.reg.rows()[0]['status'],'ERROR')
        self.assertEqual(len(self.reg.samples('S5-02-01')),0)
    def test_campaign_and_html(self):
        out=self.complete()
        out.samples[0]['observed_at']='2025-10-05T10:00:00Z'
        self.execute(out)
        self.assertEqual(self.reg.rows()[0]['status'],'ERROR')
        self.execute(Outcome('ERROR','<script>alert(1)</script>'))
        page=generate(self.reg,self.cfg,self.base).read_text(encoding='utf-8')
        self.assertIn('&lt;script&gt;',page)
        self.assertNotIn('<script>',page)
    def test_failure_isolation(self):
        self.write([self.d,dict(self.d,delivery_id='d2')])
        class Flaky:
            def validate(inner,d,*args):
                if d.delivery_id=='d1': raise OSError('fixture failure')
                return self.complete()
        run(self.reg,self.cfg,self.base,self.base,adapters={'test':Flaky()})
        self.assertEqual([r['status'] for r in self.reg.rows()],['ERROR','COMPLETE'])

if __name__ == '__main__': unittest.main()
