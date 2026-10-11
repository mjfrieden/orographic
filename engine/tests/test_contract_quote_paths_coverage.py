"""Coverage sidecars preserve frozen replay semantics and bounded work."""
import ast
import copy
from dataclasses import replace
import hashlib
import json
from pathlib import Path
import unittest

from engine.orographic import contract_quote_paths as replay
from test_contract_quote_paths import rec, quote, POLICY, ASOF
from test_contract_quote_paths_scaling import inputs, parity_cases, scale_inputs


class CoverageTest(unittest.TestCase):
    def run_join(self, r, q, artifacts, **kwargs):
        return replay.join_quote_paths(r, q, policy=POLICY, as_of_utc=ASOF,
                                       artifacts=artifacts, **kwargs)

    def assert_reconciles(self, report, sidecar):
        totals = sidecar['totals']
        for field in ('recommendation_rows', 'eligible_quote_replays', 'excluded_recommendation_rows'):
            self.assertEqual(totals[field], sum(c[field] for c in sidecar['cohorts']))
        self.assertEqual(totals['recommendation_rows'], totals['eligible_quote_replays'] + totals['excluded_recommendation_rows'])
        self.assertEqual(totals['first_exclusions_by_reason'], report['coverage']['excluded_by_reason'])
        self.assertEqual(sidecar['global_quotes']['first_exclusions_by_reason'], report['coverage']['invalid_quotes_by_reason'])
        self.assertEqual(sidecar['replay_report_sha256'], report['report_sha256'])
        self.assertEqual(sidecar['policy_sha256'], report['policy_sha256'])
        self.assertEqual(sidecar['as_of_utc'], report['as_of_utc'])
        for c in sidecar['cohorts']:
            self.assertEqual(c['recommendation_rows'], c['eligible_quote_replays'] + c['excluded_recommendation_rows'])
            self.assertEqual(c['excluded_recommendation_rows'], sum(c['first_exclusions_by_reason'].values()))
        digest = replay.digest({k: v for k, v in sidecar.items() if k != 'diagnostics_sha256'})
        self.assertEqual(digest, sidecar['diagnostics_sha256'])

    def test_frozen_reports_and_operations_identical_with_sidecar(self):
        expected = json.loads((Path(__file__).parent / 'fixtures/quote_path_v1_report_hashes.json').read_text())['reports']
        for name, (recs, quotes) in parity_cases().items():
            with self.subTest(name=name):
                r, q, artifacts = inputs(recs, quotes)
                before = copy.deepcopy((r, q, artifacts))
                ops, with_ops, sidecar = {}, {}, {'old': True}
                plain = self.run_join(r, q, artifacts, operation_counts=ops)
                with_sidecar = self.run_join(r, q, artifacts, operation_counts=with_ops, coverage_diagnostics=sidecar)
                self.assertEqual(plain, with_sidecar)
                self.assertEqual(plain['report_sha256'], expected[name])
                self.assertEqual(ops, with_ops)
                self.assertEqual(before, (r, q, artifacts))
                self.assertNotIn('old', sidecar)
                self.assert_reconciles(plain, sidecar)

    def test_utc_dates_and_verified_namespaces_are_separate(self):
        artifacts, rows = {}, []
        for namespace in ('z', 'a', 'unverified_source'):
            raw = json.dumps([rec(source_namespace=namespace,
                decision_at_utc='2026-10-08T23:30:00-02:00', available_at_utc='2026-10-09T01:00:00Z')]).encode()
            artifacts[hashlib.sha256(raw).hexdigest()] = raw
            rows += replay.retained_json(raw, source_namespace=namespace)
        unverified = rec(source_namespace='a')
        sidecar = {}
        result = self.run_join(rows + [unverified], [], artifacts, coverage_diagnostics=sidecar)
        verified = [c for c in sidecar['cohorts'] if c['source_status'] == 'verified_lineage']
        self.assertEqual([c['source_namespace'] for c in verified], ['a', 'unverified_source', 'z'])
        self.assertEqual({c['decision_date_utc'] for c in verified}, {'2026-10-09'})
        self.assertEqual(len(sidecar['cohorts']), 4)
        self.assert_reconciles(result, sidecar)

    def test_invalid_dates_and_unverified_lineage_never_supply_cohort_labels(self):
        for date in (None, [], 'bad', '2026-10-09', '2026-10-09T10:00:00', '0001-01-01T00:00:00+23:00'):
            with self.subTest(date=date):
                r, q, artifacts = inputs([rec(decision_at_utc=date)], [])
                sidecar = {}
                report = self.run_join(r, q, artifacts, coverage_diagnostics=sidecar)
                cohort = sidecar['cohorts'][0]
                self.assertEqual(cohort['decision_date_status'], 'missing_or_invalid_date')
                self.assertIsNone(cohort['decision_date_utc'])
                self.assert_reconciles(report, sidecar)
        r, q, artifacts = inputs([rec()], [])
        r[0]['source_namespace'] = 'forged'
        sidecar = {}
        self.run_join(r, q, artifacts, coverage_diagnostics=sidecar)
        c = sidecar['cohorts'][0]
        self.assertEqual(c['source_status'], 'unverified_source')
        self.assertIsNone(c['source_namespace']); self.assertIsNone(c['decision_date_utc'])

    def test_duplicates_and_permutations_count_recommendation_rows(self):
        r, q, artifacts = inputs([rec(), rec(), rec(recommendation_id='r2')], [quote('10'), quote('11'), quote('10', bid=0)])
        a, b = {}, {}
        report = self.run_join(r, q, artifacts, coverage_diagnostics=a)
        other = self.run_join(list(reversed(r)), list(reversed(q)), artifacts, coverage_diagnostics=b)
        self.assertEqual(report, other); self.assertEqual(a, b)
        self.assertEqual(a['totals']['recommendation_rows'], 3)
        self.assertEqual(a['totals']['first_exclusions_by_reason']['duplicate_recommendation_id'], 2)
        self.assertEqual(a['global_quotes']['first_exclusions_by_reason'], {'nonpositive_or_crossed_market': 1})
        self.assert_reconciles(report, a)

    def test_first_blocker_only_and_no_quote_cohort_attribution(self):
        r, q, artifacts = inputs([rec(contract_id=None, decision_at_utc=None)], [quote('10', bid=0)])
        sidecar = {}
        self.run_join(r, q, artifacts, coverage_diagnostics=sidecar)
        self.assertEqual(sidecar['totals']['first_exclusions_by_reason'], {'missing_exact_contract_identity': 1})
        self.assertEqual(sidecar['exclusion_semantics'], 'first_blocker_only')
        self.assertEqual(sidecar['evidence_class'], 'quote_replay_only')
        self.assertNotIn('global_quotes', sidecar['cohorts'][0])
        self.assertEqual(sidecar['global_quotes']['attribution'], 'global_only_no_recommendation_cohort_assignment')

    def test_all_current_exclusions_have_guidance_and_unknown_is_explicit(self):
        tree = ast.parse(Path(replay.__file__).read_text())
        functions = {'_lineage_reason', '_identity_reason', '_quote_reason', 'join_quote_paths', 'select', 'restore', 'root'}
        reasons = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name in functions:
                for child in ast.walk(node):
                    if isinstance(child, ast.Constant) and isinstance(child.value, str):
                        value = child.value
                        if value.startswith(('missing_', 'source_', 'unverified_', 'invalid_', 'unsupported_', 'conflicting_', 'duplicate_', 'recommendation_not_', 'quote_not_', 'quote_after_', 'window_after_', 'immature_', 'capture_availability_', 'stale_quote', 'nonpositive_')):
                            if value not in {'source_namespace', 'source_record_id', 'source_artifacts', 'invalid_quotes_by_reason'}:
                                reasons.add(value)
        reasons.discard('missing_valid_'); reasons.discard('conflicting_')
        reasons.update({'missing_valid_entry_quote', 'missing_valid_exit_quote', 'conflicting_entry_time_quotes', 'conflicting_exit_time_quotes'})
        self.assertTrue(reasons <= replay._REASON_EVIDENCE.keys(), reasons - replay._REASON_EVIDENCE.keys())
        r, q, artifacts = inputs([rec()], [])
        report = self.run_join(r, q, artifacts)
        report['coverage']['excluded_by_reason'] = {'future_unknown_reason': 1}
        sidecar = replay._CoverageDiagnostics().finish(report)
        self.assertEqual(sidecar['next_evidence_by_reason']['future_unknown_reason'], 'unknown_reason_review_required')

    def test_bounded_failure_keeps_both_output_dicts_untouched(self):
        r, q, artifacts = inputs([rec(), rec(recommendation_id='r2')], [quote('10'), quote('11')])
        for change in (dict(max_artifacts=1), dict(max_artifact_bytes=10), dict(max_total_artifact_bytes=10),
                       dict(max_input_rows=2), dict(max_row_bytes=10), dict(max_total_row_bytes=10),
                       dict(max_json_nodes=2), dict(max_json_depth=1), dict(max_reconstructed_rows=3)):
            with self.subTest(change=change):
                sidecar, ops = {'sentinel': 1}, {'sentinel': 2}
                with self.assertRaises(replay.ReplayLimitError):
                    self.run_join(r, q, artifacts, limits=replace(replay.ReplayLimits(), **change),
                                  operation_counts=ops, coverage_diagnostics=sidecar)
                self.assertEqual(sidecar, {'sentinel': 1}); self.assertEqual(ops, {'sentinel': 2})

    def test_invalid_and_aliased_outputs_fail_before_mutation(self):
        r, q, artifacts = inputs([rec()], [])
        for sink in ([], True, r[0], r[0]['_lineage'], r[0]['_lineage']['field_map'],
                     r[0]['identity_evidence'], artifacts):
            before = copy.deepcopy((r, q, artifacts))
            with self.assertRaises(ValueError):
                self.run_join(r, q, artifacts, coverage_diagnostics=sink)
            self.assertEqual(before, (r, q, artifacts))
        sink = {}
        with self.assertRaises(ValueError):
            self.run_join(r, q, artifacts, operation_counts=sink, coverage_diagnostics=sink)

    def test_no_extra_decode_reconstruction_validation_or_quote_work_at_scale(self):
        for n in (64, 256, 1024):
            r, q, artifacts = scale_inputs(n)
            a, b, sidecar = {}, {}, {}
            self.run_join(r, q, artifacts, operation_counts=a)
            report = self.run_join(r, q, artifacts, operation_counts=b, coverage_diagnostics=sidecar)
            self.assertEqual(a, b)
            self.assertEqual(b['artifact_decodes'], 2)
            self.assert_reconciles(report, sidecar)

    def test_empty_sidecar_and_valid_date_with_invalid_availability(self):
        sidecar = {}
        report = self.run_join([], [], {}, coverage_diagnostics=sidecar)
        self.assertEqual(sidecar['cohorts'], [])
        self.assert_reconciles(report, sidecar)
        r, q, artifacts = inputs([rec(available_at_utc=None)], [])
        self.run_join(r, q, artifacts, coverage_diagnostics=sidecar)
        self.assertEqual(sidecar['cohorts'][0]['decision_date_utc'], '2026-10-09')

    def test_nested_sinks_cannot_mutate_input_or_replay_witnesses(self):
        r, q, artifacts = inputs([rec(extra={'nested': [{'sink': {}}]})], [quote('10'), quote('11')])
        for sink in (q[0]['identity_evidence'], r[0]['extra']['nested'][0]['sink']):
            before = copy.deepcopy((r, q, artifacts))
            with self.assertRaises(ValueError):
                self.run_join(r, q, artifacts, coverage_diagnostics=sink)
            self.assertEqual(before, (r, q, artifacts))
        with self.assertRaises(ValueError):
            self.run_join(r, q, artifacts, coverage_diagnostics={}, operation_counts=q[0]['identity_evidence'])

    def test_unused_artifact_alias_and_hostile_output_subclass_are_atomic(self):
        r, q, artifacts = inputs([rec()], [])
        for nested in (False, True):
            sink = {'sentinel': 1}
            artifacts['unused'] = {'nested': [sink]} if nested else sink
            before = copy.deepcopy((r, q, artifacts))
            with self.assertRaises(ValueError):
                self.run_join(r, q, artifacts, coverage_diagnostics=sink)
            self.assertEqual(before, (r, q, artifacts))
        artifacts.pop('unused')
        before = copy.deepcopy(artifacts)
        with self.assertRaises(ValueError):
            self.run_join(r, q, artifacts, coverage_diagnostics={}, operation_counts=artifacts)
        self.assertEqual(before, artifacts)
        empty_recs = {}
        with self.assertRaises(ValueError):
            self.run_join(empty_recs, [], {}, coverage_diagnostics={}, operation_counts=empty_recs)
        self.assertEqual(empty_recs, {})
        class Hostile(dict):
            def clear(self):
                raise RuntimeError('must not call')
        sidecar = {'sentinel': 1}
        with self.assertRaises(ValueError):
            self.run_join(r, q, artifacts, coverage_diagnostics=sidecar, operation_counts=Hostile())
        self.assertEqual(sidecar, {'sentinel': 1})

    def test_malformed_unused_artifact_cycle_is_bounded(self):
        sink, cycle = {'sentinel': 1}, {}
        cycle['cycle'] = cycle
        with self.assertRaises(replay.ReplayLimitError):
            self.run_join([], [], {'unused': cycle}, coverage_diagnostics=sink)
        self.assertEqual(sink, {'sentinel': 1})
