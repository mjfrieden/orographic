from __future__ import annotations

import unittest

from engine.orographic.payoff_challenger_evidence import build_payoff_challenger_evidence


def _label(net_return: float) -> dict:
    return {
        "label_contract": {"id": "orographic.executable_option_outcome.v2", "version": 2},
        "net_executable_return": net_return,
        "net_executable_pnl_usd": net_return * 100.0,
        "is_net_profitable": net_return > 0,
    }


def _pick(
    run: int,
    candidate: int,
    *,
    active_probability: float,
    shadow_probability: float,
    net_return: float | None,
) -> dict:
    outcomes = {"executable_labels": {}}
    if net_return is not None:
        outcomes["executable_labels"]["friday_close"] = _label(net_return)
        outcomes["capture_attempts"] = {"friday_close": {"status": "captured_valid"}}
    return {
        "recommendation_id": f"run-{run}|contract-{candidate}",
        "contract_symbol": f"RUN{run}C{candidate}",
        "lane": "live" if candidate == 0 else "shadow",
        "option_type": "call" if (run + candidate) % 2 == 0 else "put",
        "scores": {
            "prob_positive_option_pnl": active_probability,
            "payoff_shadow_prob_positive": shadow_probability,
            "payoff_shadow_disagreement": (active_probability >= 0.5) != (shadow_probability >= 0.5),
            "payoff_shadow_artifact_sha256": "test-shadow-v1",
        },
        "context": {"regime": {"mode": ("risk_on", "neutral", "risk_off")[run % 3]}},
        "outcomes": outcomes,
    }


class PayoffChallengerEvidenceTests(unittest.TestCase):
    def test_collects_evidence_when_no_new_shadow_scores_exist(self) -> None:
        artifact = build_payoff_challenger_evidence({"artifact": "prospective_pick_ledger", "entries": []})

        self.assertEqual(artifact["decision"], "collecting_evidence")
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 0)
        self.assertEqual(artifact["execution_effect"], "none_observation_only")
        self.assertFalse(artifact["gates"]["sample"]["resolved_recommendations"])

    def test_strong_paired_challenger_can_become_live_shadow_eligible(self) -> None:
        entries = []
        for run in range(40):
            entries.append({
                "run_generated_at_utc": f"2026-07-{run % 28 + 1:02d}T{13 + run // 28:02d}:00:00+00:00",
                "picks": [
                    _pick(run, 0, active_probability=0.10, shadow_probability=0.90, net_return=0.30),
                    _pick(run, 1, active_probability=0.90, shadow_probability=0.10, net_return=-0.20),
                    _pick(run, 2, active_probability=0.20, shadow_probability=0.70, net_return=0.10),
                ],
            })
        artifact = build_payoff_challenger_evidence({
            "artifact": "prospective_pick_ledger",
            "schema_version": 3,
            "updated_at_utc": "2026-08-08T00:00:00+00:00",
            "entries": entries,
        })

        self.assertEqual(artifact["decision"], "eligible_for_live_shadow")
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 120)
        self.assertEqual(artifact["rank_replay"]["eligible_complete_runs"], 40)
        self.assertEqual(artifact["rank_replay"]["selection_disagreements"], 40)
        self.assertGreater(artifact["rank_replay"]["paired_inference"]["confidence_interval_95"]["lower"], 0)
        self.assertTrue(all(artifact["gates"]["sample"].values()))
        self.assertTrue(all(artifact["gates"]["performance"].values()))

    def test_rank_replay_excludes_incompletely_resolved_candidate_sets(self) -> None:
        ledger = {"entries": [{
            "run_generated_at_utc": "2026-08-01T15:00:00+00:00",
            "picks": [
                _pick(1, 0, active_probability=0.3, shadow_probability=0.8, net_return=0.2),
                _pick(1, 1, active_probability=0.8, shadow_probability=0.2, net_return=None),
            ],
        }]}
        artifact = build_payoff_challenger_evidence(ledger)

        self.assertEqual(artifact["rank_replay"]["eligible_complete_runs"], 0)
        self.assertEqual(artifact["rank_replay"]["incomplete_runs_excluded"], 1)
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 1)

    def test_legacy_outcome_contracts_fail_closed(self) -> None:
        pick = _pick(1, 0, active_probability=0.3, shadow_probability=0.8, net_return=0.2)
        pick["outcomes"]["executable_labels"]["friday_close"]["label_contract"]["version"] = 1
        artifact = build_payoff_challenger_evidence({"entries": [{
            "run_generated_at_utc": "2026-08-01T15:00:00+00:00",
            "picks": [pick],
        }]})

        self.assertEqual(artifact["coverage"]["scored_recommendations"], 1)
        self.assertEqual(artifact["coverage"]["resolved_recommendations"], 0)

    def test_friction_vetoes_never_enter_counterfactual_ranking(self) -> None:
        pick = _pick(1, 0, active_probability=0.1, shadow_probability=0.99, net_return=5.0)
        pick["lane"] = "friction_veto"
        artifact = build_payoff_challenger_evidence({"entries": [{
            "run_generated_at_utc": "2026-08-01T15:00:00+00:00",
            "picks": [pick],
        }]})

        self.assertEqual(artifact["coverage"]["scored_recommendations"], 0)

    def test_model_versions_are_never_pooled(self) -> None:
        old = _pick(1, 0, active_probability=0.2, shadow_probability=0.8, net_return=0.2)
        old["scores"]["payoff_shadow_artifact_sha256"] = "old"
        new = _pick(2, 0, active_probability=0.2, shadow_probability=0.8, net_return=0.2)
        new["scores"]["payoff_shadow_artifact_sha256"] = "new"
        artifact = build_payoff_challenger_evidence({"entries": [
            {"run_generated_at_utc": "2026-08-01T15:00:00+00:00", "picks": [old]},
            {"run_generated_at_utc": "2026-08-02T15:00:00+00:00", "picks": [new]},
        ]})

        self.assertEqual(artifact["model_cohort"]["artifact_sha256"], "new")
        self.assertEqual(artifact["coverage"]["scored_recommendations"], 1)
        self.assertEqual(artifact["coverage"]["older_model_scored_recommendations_excluded"], 1)


if __name__ == "__main__":
    unittest.main()
