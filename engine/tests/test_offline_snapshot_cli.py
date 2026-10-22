"""Offline replacement for CI's live-provider snapshot smoke command.

The actual CLI, pipeline orchestration, ledger writers and unchanged validator
run. Only provider-facing model stages use synthetic fixtures. The separate
validate_model_artifacts.py CI gate still validates every real model checksum.
"""
import json
from pathlib import Path
from unittest import mock

import pytest

from engine import run_scan as cli
from engine.orographic.market_shock import MarketShockRegime
from engine.orographic.pipeline import _model_artifact_status
from engine.orographic.schemas import MarketRegime
from scripts.validate_snapshot_smoke import validate_snapshot_artifacts


def test_offline_cli_writes_valid_fail_closed_snapshot_and_ledgers(tmp_path):
    # Full checkout CI checks these files with validate_model_artifacts first.
    # Partial source reconstructions cannot honestly claim that model gate.
    missing = [name for name, row in _model_artifact_status().items()
               if row["required"] and not row["present"]]
    if missing:
        pytest.skip("Required model binaries not materialized: " + ", ".join(missing))
    snapshot = tmp_path / "latest.json"
    prospective = tmp_path / "prospective.json"
    diagnostics = tmp_path / "diagnostics"
    council = {"live_board": [], "shadow_board": [], "abstain": True,
               "summary": {"candidate_count": 0, "live_count": 0, "shadow_count": 0, "notes": []}}
    blocked = AssertionError("Offline CI smoke attempted an external operation")
    with (
        mock.patch("sys.argv", ["run_scan.py", "--symbols", "SYNTHETIC",
                   "--no-paired-side-capture", "--output", str(snapshot),
                   "--prospective-ledger-output", str(prospective)]),
        mock.patch("engine.orographic.pipeline.scan_symbols_with_diagnostics", return_value=(
            MarketRegime(mode="neutral", bias=0.0, source_symbol="SYNTHETIC"), [],
            {"pre_veto_direction_counts": {}, "final_direction_counts": {}, "counter_regime_survivors": 0},
        )) as scout,
        mock.patch("engine.orographic.pipeline.classify_current_market_shock", return_value=
            MarketShockRegime(label="synthetic", severity=0.0, stance="allow", global_abstain=False)),
        mock.patch("engine.orographic.pipeline.select_signals_for_forge", return_value=([], {})),
        mock.patch("engine.orographic.pipeline.rank_contracts_with_diagnostics", return_value=(
            [], {"waterfall": {}, "learned_ranker": {}})),
        mock.patch("engine.orographic.pipeline.select_board", return_value=
            mock.Mock(to_dict=lambda: council, live_board=[], abstain=True)),
        mock.patch("socket.socket.connect", side_effect=blocked),
        mock.patch("socket.socket.connect_ex", side_effect=blocked),
        mock.patch("socket.create_connection", side_effect=blocked),
        mock.patch("urllib.request.urlopen", side_effect=blocked),
        mock.patch("requests.sessions.Session.request", side_effect=blocked),
        mock.patch("curl_cffi.requests.Session.request", side_effect=blocked),
        mock.patch("subprocess.Popen", side_effect=blocked),
    ):
        assert cli.main() == 0
    scout.assert_called_once()
    result = validate_snapshot_artifacts(snapshot, prospective, diagnostics)
    assert result["scout_signal_count"] == result["forge_candidate_count"] == 0
    assert result["abstain"] is True
    assert json.loads(prospective.read_text())["artifact"] == "prospective_pick_ledger"
    assert (diagnostics / "research_run_ledger.json").is_file()


def test_ci_snapshot_step_has_no_live_provider_command():
    workflow = Path(".github/workflows/ci.yml").read_text()
    step = workflow.split("- name: Offline snapshot smoke test", 1)[1]
    assert "engine/tests/test_offline_snapshot_cli.py" in step
    assert "python engine/run_scan.py" not in workflow
    assert "python scripts/validate_model_artifacts.py" in workflow


def test_shared_mart_pull_requests_use_secret_free_offline_job():
    workflow = Path(".github/workflows/sync_shared_research_mart.yml").read_text()
    offline, live = workflow.split("  offline-pr-check:", 1)[1].split("  sync:", 1)
    assert "if: ${{ github.event_name == 'pull_request' }}" in offline
    assert "secrets." not in offline
    assert "contents: read" in offline
    assert "python -m pytest" in offline
    assert "if: ${{ github.event_name != 'pull_request' }}" in live.split("steps:", 1)[0]
