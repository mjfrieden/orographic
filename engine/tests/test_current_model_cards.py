import json
from pathlib import Path
import tempfile
import unittest
from scripts.build_current_model_cards import build, metric, quantity
ROOT = Path(__file__).resolve().parents[2]
class ModelCardsTests(unittest.TestCase):
    def fixture(self):
        tmp = tempfile.TemporaryDirectory(); root = Path(tmp.name)
        for path in ['web/data/latest_run.json', 'engine/orographic/models/scout_model_card.json', 'engine/orographic/models/production_payoff_ranker_card.json', 'web/data/model-evidence/cirrus_public_summary.json']:
            dest = root / path; dest.parent.mkdir(parents=True, exist_ok=True); dest.write_bytes((ROOT / path).read_bytes())
        return tmp, root
    def test_current_sources_match(self):
        cards = build(ROOT)['cards']
        self.assertEqual([x['identity_status'] for x in cards], ['matched_metadata'] * 3 + ['curated_snapshot'] * 5)
        source = json.loads((ROOT / 'engine/orographic/models/scout_model_card.json').read_text())
        self.assertEqual(cards[0]['metrics'][0]['value'], source['cross_validation']['mean_auc'])
        self.assertEqual(cards[1]['sample_size'], sum(f['validation_rows'] for f in source['side_aware_output']['training_metrics']['cross_validation']['fold_reports']))
        ranker = json.loads((ROOT / 'engine/orographic/models/production_payoff_ranker_card.json').read_text())
        self.assertEqual(cards[2]['sample_size'], sum(ranker['source_validation']['by_side'][side]['rows'] for side in ['call', 'put']))
        self.assertTrue(all(c['metrics'][-1]['value'] is None for c in cards[:3]))
        self.assertEqual(cards[2]['replay']['sample_size'], ranker['source_validation']['forward_policy']['selected_trades'])
    def test_changed_model_withholds_metrics(self):
        tmp, root = self.fixture()
        with tmp:
            path = root / 'web/data/latest_run.json'; run = json.loads(path.read_text()); run['model_artifacts']['scout_side_model']['sha256'] = '0' * 64; path.write_text(json.dumps(run))
            cards = build(root)['cards']; self.assertEqual(cards[1]['metrics'], []); self.assertTrue(cards[0]['metrics'])
    def test_changed_card_bytes_withholds_metrics(self):
        tmp, root = self.fixture()
        with tmp:
            path = root / 'engine/orographic/models/production_payoff_ranker_card.json'; path.write_text(path.read_text() + ' ')
            card = build(root)['cards'][2]; self.assertEqual(card['metrics'], []); self.assertNotIn('replay', card)
    def test_scaler_is_bound(self):
        tmp, root = self.fixture()
        with tmp:
            path = root / 'web/data/latest_run.json'; run = json.loads(path.read_text()); run['model_artifacts']['scout_scaler']['sha256'] = '0' * 64; path.write_text(json.dumps(run))
            self.assertEqual(build(root)['cards'][0]['metrics'], [])
    def test_malformed_shapes_fail_closed(self):
        for payload in [[], {'model_artifacts': None}, {'model_modes': None}, {'model_artifacts': {'scout_model': []}}]:
            tmp, root = self.fixture()
            with tmp:
                (root / 'web/data/latest_run.json').write_text(json.dumps(payload))
                self.assertTrue(all(not c['metrics'] for c in build(root)['cards']))
        for field in ['cross_validation', 'artifacts', 'observability', 'side_aware_output']:
            tmp, root = self.fixture()
            with tmp:
                path = root / 'engine/orographic/models/scout_model_card.json'; source = json.loads(path.read_text()); source[field] = None; path.write_text(json.dumps(source))
                self.assertTrue(all(not c['metrics'] for c in build(root)['cards']))
    def test_workflow_diagnostic_is_optional_and_clears_old_artifact(self):
        text = (ROOT / '.github/workflows/orographic_scan.yml').read_text()
        step = text.split('      - name: Build current model cards')[1].split('      - name:')[0]
        self.assertIn('continue-on-error: true', step)
        self.assertLess(step.index('rm -f web/data/diagnostics/current_model_cards_latest.json'), step.index('python scripts/build_current_model_cards.py'))
    def test_malformed_counts_are_not_numbers(self):
        for value in [True, False, -1, 1.5, '9', 9007199254740992]:
            self.assertIsNone(quantity(value))
        self.assertEqual(quantity(0), 0)
        tmp, root = self.fixture()
        with tmp:
            import hashlib
            path = root / 'engine/orographic/models/scout_model_card.json'; source = json.loads(path.read_text())
            side = source['side_aware_output']['training_metrics']; side['cross_validation']['fold_reports'][0]['validation_rows'] = True; side['class_counts']['put_edge'] = True
            path.write_text(json.dumps(source))
            ranker_path = root / 'engine/orographic/models/production_payoff_ranker_card.json'; ranker = json.loads(ranker_path.read_text())
            ranker['source_validation']['by_side']['call']['rows'] = True; ranker['source_validation']['forward_policy']['selected_trades'] = -1; ranker_path.write_text(json.dumps(ranker))
            run_path = root / 'web/data/latest_run.json'; run = json.loads(run_path.read_text())
            for file_path, key in [(path, 'scout_model_card'), (ranker_path, 'production_payoff_ranker_card')]: run['model_artifacts'][key]['sha256'] = hashlib.sha256(file_path.read_bytes()).hexdigest()
            run_path.write_text(json.dumps(run)); cards = build(root)['cards']
            self.assertIsNone(cards[1]['sample_size']); self.assertIsNone(cards[1]['training_class_counts']['put_edge']); self.assertIsNone(cards[2]['sample_size']); self.assertIsNone(cards[2]['replay']['sample_size'])
    def test_curated_review_dates_do_not_advance_with_scan(self):
        tmp, root = self.fixture()
        with tmp:
            path = root / 'web/data/latest_run.json'; run = json.loads(path.read_text()); run['generated_at_utc'] = '2099-01-01T00:00:00Z'; path.write_text(json.dumps(run))
            cards = build(root)['cards'][3:]
            self.assertEqual(len(cards), 5)
            self.assertTrue(all(card['verified_at_utc'] == '2026-10-10T15:30:00Z' for card in cards))
            self.assertTrue(all(card['metrics'] == [] and card['trained_at'] is None for card in cards))
            self.assertEqual(cards[-1]['evaluation_completed_at'], '2026-10-07T22:10:35.636552+00:00')
    def test_invalid_curated_source_does_not_hide_orographic(self):
        changes = [lambda s: s.update(cards=None), lambda s: s.update(verified_at_utc='2099-01-01T00:00:00Z'), lambda s: s.update(source_revision='bad'), lambda s: s['cards'][0].update(evidence_kind='live'), lambda s: s['cards'][-1].update(evaluation_completed_at=None), lambda s: s['cards'][0].update(evaluation_summary=['x'] * 9)]
        for change in changes:
            tmp, root = self.fixture()
            with tmp:
                path = root / 'web/data/model-evidence/cirrus_public_summary.json'; source = json.loads(path.read_text()); change(source); path.write_text(json.dumps(source))
                cards = build(root)['cards']; self.assertEqual(len(cards), 4); self.assertEqual(cards[-1]['identity_status'], 'unavailable'); self.assertTrue(cards[0]['metrics'])
    def test_curated_projection_never_copies_unapproved_fields(self):
        tmp, root = self.fixture()
        with tmp:
            path = root / 'web/data/model-evidence/cirrus_public_summary.json'; source = json.loads(path.read_text()); source['private_ledger'] = 'do not copy'; source['cards'][0]['raw_code'] = 'do not copy'; path.write_text(json.dumps(source))
            self.assertNotIn('do not copy', json.dumps(build(root)))
    def test_curated_studies_are_separate_from_current_models(self):
        cards = build(ROOT)['cards'][3:]
        self.assertEqual([c['evidence_kind'] for c in cards], ['registry_role'] * 3 + ['discovery_study'] * 2)
        self.assertTrue(all(c['evaluation_completed_at'] is None for c in cards[:3]))
        self.assertIn('rejected', cards[-1]['role'])
        self.assertIn('inactive', cards[-2]['role'])
    def test_missing_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            result = build(Path(folder)); self.assertTrue(all(not card['metrics'] for card in result['cards'])); self.assertIsNone(result['runtime_at_utc'])
    def test_missing_values_not_zero(self):
        for value in [None, True, '0.8', float('nan'), float('inf'), -1, 3]: self.assertIsNone(metric('F1', value, '')['value'])
        self.assertEqual(metric('F1', 0, '')['value'], 0)
if __name__ == '__main__': unittest.main()
