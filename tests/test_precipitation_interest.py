import json
import tempfile
import unittest
from pathlib import Path

from tools.precipitation_interest import score, select


class PrecipitationInterestTest(unittest.TestCase):
    def test_selection_ignores_product_values_and_scores_all_rainy_pairs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            pairs = root / "paired_data.json"
            rows = [dict(area="North", station=f"S{i}", reference=2.0,
                         predicted=2.0, product_row=0, product_col=i)
                    for i in range(10)]
            pairs.write_text(json.dumps(rows))
            results = root / "results.json"
            results.write_text(json.dumps([dict(status="DIAGNOSTIC", acquired_at="2026-10-07T20:00:00+00:00",
                                                delivery_id="example", provenance=json.dumps(dict(
                                                    method="regional-gauges-hourly-v3-rainy-only",
                                                    artifacts=[str(pairs)])))]))
            manifest = select(results)
            self.assertEqual(manifest["selection"][0]["tier"], "well_supported")
            self.assertNotIn("predicted", json.dumps(manifest))
            self.assertTrue(score(manifest)[0]["passes_20pct"])

            rows[0]["predicted"] = 20.0
            pairs.write_text(json.dumps(rows))
            altered_selection = select(results)
            self.assertEqual(altered_selection["selection"][0]["tier"], "well_supported")
            with self.assertRaises(ValueError):
                score(manifest)
            self.assertFalse(score(altered_selection)[0]["passes_20pct"])


if __name__ == "__main__":
    unittest.main()
