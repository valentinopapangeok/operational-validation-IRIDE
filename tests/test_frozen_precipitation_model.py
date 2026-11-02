import json
import tempfile
import unittest
from pathlib import Path

from s5validation.precipitation_uncertainty import _frozen_models


class FrozenModelTests(unittest.TestCase):
    def test_held_out_and_prospective_selection(self):
        policy={'method_id':'fixture','bootstrap_replicates':2,'bootstrap_seed':7}
        artifact={'schema_version':1,'method_id':'fixture','bootstrap_replicates':2,
                  'bootstrap_seed':7,'latest_training_date':'2026-10-07',
                  'held_out':{'2026-10-07':{'full':[1,2],'bootstrap':[[1,2],[2,3]],
                                          'training_pair_count':5}},
                  'prospective':{'full':[3,4],'bootstrap':[[3,4],[4,5]],
                                 'training_pair_count':8}}
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'model.json';path.write_text(json.dumps(artifact))
            models=_frozen_models(path,policy,['2026-10-07','2026-10-08','2026-10-06'])
            self.assertEqual(models['2026-10-07'][2],5)
            self.assertEqual(models['2026-10-08'][2],8)
            self.assertIsNone(models['2026-10-06'])
            with self.assertRaisesRegex(ValueError,'does not match'):
                _frozen_models(path,{**policy,'bootstrap_seed':8},['2026-10-08'])
