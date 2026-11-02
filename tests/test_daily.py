import unittest
import tempfile
import json
from pathlib import Path
from datetime import datetime, timezone
from unittest.mock import patch

from tools.run_daily import window, main_daily
from test_downloads import FakeStorage
from s5validation.downloads import inventory_campaign


class DailyWindowTests(unittest.TestCase):
    def test_utc_day_and_leap_boundary(self):
        self.assertEqual(window('2028-02-29'),
                         ('2028-02-29T00:00:00+00:00','2028-03-01T00:00:00+00:00'))

    def test_inventory_is_bounded(self):
        storage=FakeStorage()
        for day in ('20261006','20261007','20261008'):
            storage.add('ar_delivery/',f'IRIDE-S_S5-02-01_{day}_V1.tif')
        config={'campaign':{'start':'2026-10-01T00:00:00+00:00','end':'2027-01-01T00:00:00+00:00'},
                'products':{'S5-02-01':{}}}
        start,end=window('2026-10-07')
        items,errors=inventory_campaign(storage,config,start_bound=datetime.fromisoformat(start),
                                        end_bound=datetime.fromisoformat(end))
        self.assertEqual(errors,[])
        self.assertEqual([item['acquired_at'][:10] for item in items],['2026-10-07'])

    def test_invalid_date(self):
        with self.assertRaises(ValueError):window('2026-02-30')

    def test_existing_product04_evidence_is_passed_to_daily_config(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);evidence=root/'evidence';(evidence/'results/S5-02-04').mkdir(parents=True)
            workdir=root/'daily'
            with patch('tools.run_daily.main',return_value=0) as launch:
                self.assertEqual(main_daily(['--date','2026-10-07','--workdir',str(workdir),
                                             '--product04-evidence-workdir',str(evidence)]),0)
            config=json.loads((workdir/'daily_config.json').read_text())
            self.assertEqual(config['precipitation']['uncertainty_adjustment']['additional_evidence_workdirs'],
                             [str(evidence.resolve())])
            self.assertIn('--acquired-before',launch.call_args.args[0])
