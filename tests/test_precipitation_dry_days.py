import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

from s5validation.downloads import digest
from s5validation.precipitation_dry_days import dry_day_results


class DryDayTests(unittest.TestCase):
    def make_hours(self, root, rainy_hour=None):
        start = datetime(2026, 10, 20, tzinfo=timezone.utc)
        outcomes = []
        for hour in range(24):
            stamp = start + timedelta(hours=hour)
            pairs = [dict(area="North", station=f"station-{i}", network="test-net",
                          reference=0.2 if hour == rainy_hour and i == 0 else 0.0,
                          predicted=0.5 if hour == 3 and i == 0 else 0.0,
                          product_row=0, product_col=i // 2)
                     for i in range(10)]
            path = root / f"hour-{hour}" / "matched_all_conditions.json"
            path.parent.mkdir()
            path.write_text(json.dumps(pairs))
            outcomes.append(dict(product="S5-02-04", acquired_at=stamp.isoformat(),
                                 status="REJECTED_QA" if hour != rainy_hour else "DIAGNOSTIC",
                                 provenance={"artifacts": [str(path)],
                                             "artifact_sha256": {str(path): digest(path)}}))
        return outcomes

    def test_complete_dry_day_reports_false_rain_separately(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = dry_day_results(self.make_hours(Path(folder)))
            north = next(row for row in rows if row["area"] == "North")
            self.assertEqual(north["status"], "SAMPLED_DRY_DAY")
            self.assertEqual(north["date_utc"], "2026-10-20")
            self.assertEqual(north["stable_gauges"], 10)
            self.assertEqual(north["gauge_hours"], 240)
            self.assertEqual(north["product_false_rain_gauge_hours"], 1)

    def test_missing_hour_or_reference_rain_cannot_be_a_dry_day(self):
        with tempfile.TemporaryDirectory() as folder:
            rows = self.make_hours(Path(folder), rainy_hour=5)
            north = next(row for row in dry_day_results(rows) if row["area"] == "North")
            self.assertEqual(north["status"], "NO_SUPPORTED_DRY_DAY")
            self.assertEqual(north["date_utc"], None)
            north = next(row for row in dry_day_results(rows[:-1]) if row["area"] == "North")
            self.assertEqual(north["status"], "PENDING_24H_PRODUCT")


if __name__ == "__main__":
    unittest.main()
