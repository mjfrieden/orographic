"""Pure offline synthetic release projection tests; no private model inputs."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from scripts.build_current_model_cards import CIRRUS_IDS, CIRRUS_SOURCE, cirrus_cards, reviewed_release
ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / 'tests/fixtures/cirrus_reviewed_release.synthetic.json').read_text())

def row(model_id='cirrus-hybrid'):
    return {'id': model_id, 'evidence_kind': 'registry_role', 'reviewed_release_evidence': copy.deepcopy(FIXTURE)}

class ReviewedReleaseTests(unittest.TestCase):
    def test_separate_dates_counts_and_allowlist(self):
        source = row(); source['reviewed_release_evidence']['private_field'] = 'omit-me'
        result = reviewed_release(source)
        self.assertEqual(result, FIXTURE)
        self.assertEqual(result['fitted_feature_count'], 9)
        self.assertEqual(result['candidate_pool_count'], 22)
        self.assertNotIn('trained_at', result)
        for model_id in CIRRUS_IDS[:3]: self.assertEqual(reviewed_release(row(model_id)), FIXTURE)
        for model_id in CIRRUS_IDS[3:] + ['scout-direction', 'unknown']: self.assertIsNone(reviewed_release(row(model_id)))
        source['evidence_kind'] = 'discovery_study'; self.assertIsNone(reviewed_release(source))
    def test_missing_malformed_or_contradictory_evidence_withheld(self):
        for key in FIXTURE:
            if key == 'candidate_pool_count': continue
            source = row(); del source['reviewed_release_evidence'][key]
            self.assertIsNone(reviewed_release(source), key)
        for key, values in {
            'fitted_feature_count': [True, -1, 0, 1.5, '9', 65],
            'candidate_pool_count': [True, -1, 8, 1.5, '22', 10001],
            'fitted_feature_names': [None, [], ['same'] * 9, ['<script>'] * 9],
            'model_sha256': ['a' * 63, 5, 'A' * 64],
            'release_id': ['', '<script>', 'x' * 97],
            'built_at_utc': ['2020-02-30T00:00:00Z', '2099-01-01T00:00:00Z', '2020-02-04', '2020-02-07T00:00:00Z'],
            'source_data_end': ['2020-02-30', '2020-02-05'],
            'training_label_cutoff': ['2020-02-04'],
            'reviewed_at_utc': ['2020-02-04T00:00:00Z'],
        }.items():
            for value in values:
                source = row(); source['reviewed_release_evidence'][key] = value
                self.assertIsNone(reviewed_release(source), (key, value))
        for value in [None, [], True, 'invalid']:
            source = row(); source['reviewed_release_evidence'] = value; self.assertIsNone(reviewed_release(source))
        source = row(); del source['reviewed_release_evidence']['candidate_pool_count']
        self.assertIsNone(reviewed_release(source)['candidate_pool_count'])
    def test_optional_failure_preserves_all_cards_and_dates(self):
        source = {'schema_version': 1, 'verified_at_utc': '2020-02-07T00:00:00Z', 'source_revision': 'b' * 40, 'source_description': 'Synthetic source', 'refresh_policy': 'Manual review', 'cards': []}
        for index, model_id in enumerate(CIRRUS_IDS):
            item = row(model_id)
            item.update({key: 'Synthetic context' for key in ['title', 'role', 'how_it_works', 'evaluation_window', 'population']})
            item.update(evaluation_completed_at=None, limitations=['Synthetic limitation'], evaluation_summary=['Synthetic result'])
            if index >= 3: item.update(evidence_kind='discovery_study', evaluation_completed_at='2020-01-01T00:00:00Z')
            source['cards'].append(item)
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder); path = root / CIRRUS_SOURCE; path.parent.mkdir(parents=True)
            path.write_text(json.dumps(source)); cards = cirrus_cards(root)
            self.assertEqual(len(cards), 5)
            self.assertTrue(all('reviewed_release_evidence' in c for c in cards[:3]))
            self.assertTrue(all('reviewed_release_evidence' not in c for c in cards[3:]))
            self.assertTrue(all(c['trained_at'] is None and c['version'] is None and c['metrics'] == [] for c in cards))
            source['cards'][0]['reviewed_release_evidence']['fitted_feature_count'] = 22
            source['verified_at_utc'] = '2020-03-01T00:00:00Z'; path.write_text(json.dumps(source)); cards = cirrus_cards(root)
            self.assertEqual(len(cards), 5); self.assertNotIn('reviewed_release_evidence', cards[0])
            self.assertEqual(cards[1]['reviewed_release_evidence'], FIXTURE)
if __name__ == '__main__': unittest.main()
