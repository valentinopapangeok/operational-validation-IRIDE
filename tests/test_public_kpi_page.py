import json
import tempfile
import unittest
from pathlib import Path
from tools.public_kpi_page import export_summary, merge_summaries


class PublicKpiPageTests(unittest.TestCase):
    def test_export_omits_reference_measurements(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report = root / 'report'
            report.mkdir()
            (report / 'kpi_dates.json').write_text(json.dumps([{
                'product': 'S5-02-04', 'date': '2026-10-07', 'area': 'North',
                'evaluation_status': 'PASS', 'passing_hours': 1,
                'evaluated_hours': 2, 'secret_station_value': 1234}]))
            (report / 'kpi_monthly_summary.json').write_text('[]')
            result = {'deliveries': [{'product': 'S5-02-04', 'date': '2026-10-07',
                                     'status': 'DIAGNOSTIC', 'reference_status': 'AVAILABLE'}]}
            destination = root / 'summary.json'
            export_summary(root, result, destination)
            output = destination.read_text()
            self.assertNotIn('secret_station_value', output)
            self.assertIn('"passing_hours": 1', output)

    def test_monthly_counts_recomputed_from_distinct_pushed_dates(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            files = []
            for i, area in enumerate(('North', 'Center', 'South', 'North', 'Center'), 1):
                path = root / f'{i}.json'
                path.write_text(json.dumps({'schema_version': 1, 'deliveries': [{
                    'product': 'S5-02-04', 'date': f'2026-10-{i:02d}',
                    'processing_status': 'DIAGNOSTIC', 'reference_status': 'AVAILABLE',
                    'areas': {a: ([{'date': f'2026-10-{i:02d}', 'result': 'PASS',
                                    'passing_hours': 1, 'evaluated_hours': 1}] if a == area else [])
                              for a in ('Whole AOI', 'North', 'Center', 'South')},
                    'monthly': None}]}))
                files.append(path)
            merged = merge_summaries({}, files)
            self.assertEqual(merged['deliveries'][0]['monthly']['qualifying_count'], 5)
            self.assertTrue(merged['deliveries'][0]['monthly']['monthly_target_met'])
