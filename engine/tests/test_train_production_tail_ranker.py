from __future__ import annotations

from datetime import date, timedelta
import hashlib
import json
from pathlib import Path
import sys

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.ensemble import HistGradientBoostingClassifier

from scripts import train_production_tail_ranker as trainer


class ConstantModel:
    classes_ = np.arange(4)

    def predict_proba(self, X):
        return np.tile([0.1, 0.1, 0.2, 0.6], (len(X), 1))


def rows(*, start=date(2026, 1, 1), days=18):
    result = []
    for day in range(days):
        entry = start + timedelta(days=day)
        for outcome, realized in enumerate([-0.75, -0.25, 0.25, 1.5]):
            result.append({
                "symbol": f"SYM{outcome}",
                "contract_symbol": f"CONTRACT-{entry}-{outcome}",
                "option_type": "call" if outcome % 2 else "put",
                "strike": 100.0 + outcome,
                "expiry": (entry + timedelta(days=14)).isoformat(),
                "entry_date": entry.isoformat(),
                "exit_date": entry.isoformat(),
                "entry_spot": 100.0,
                "entry_price": 2.0,
                "entry_bid": 1.9,
                "entry_ask": 2.0,
                "entry_spread_pct": 0.05,
                "entry_open_interest": 900,
                "entry_volume": 300,
                "last_trade_age_seconds": 0.0,
                "hold_period_return_after_friction_pct": realized,
                "run_generated_at_utc": f"{entry}T15:00:00Z",
                "final_candidate_score": 0.9,
            })
    return result


def dataset(path, values):
    path.write_text(json.dumps({"artifact": "option_outcome_dataset", "rows": values}))
    return path


@pytest.fixture
def fast_model(monkeypatch):
    monkeypatch.setattr(trainer, "_fit", lambda X, y: ConstantModel())
    monkeypatch.setattr(trainer, "_integrated_forward_replay", lambda frame, path: {"available_scans": len(frame)})


def train_at(tmp_path, development, forward, name):
    return trainer.train(
        dataset(tmp_path / f"{name}-development.json", development),
        dataset(tmp_path / f"{name}-forward.json", forward),
        tmp_path / f"{name}.pkl", tmp_path / f"{name}.json",
    )


def test_future_same_class_returns_do_not_change_earlier_oof_picks(tmp_path, fast_model):
    development = rows()
    forward = rows(start=date(2026, 3, 1), days=2)
    original = train_at(tmp_path, development, forward, "original")
    changed = [dict(row) for row in development]
    # Same class labels and features; only future payoff magnitudes change.
    for row in changed:
        if row["entry_date"] >= "2026-01-04" and row["hold_period_return_after_friction_pct"] >= 0.5:
            row["hold_period_return_after_friction_pct"] = 0.5
    mutated = train_at(tmp_path, changed, forward, "mutated")
    np.testing.assert_array_equal(
        trainer._outcome_classes(np.array([row["hold_period_return_after_friction_pct"] for row in development])),
        trainer._outcome_classes(np.array([row["hold_period_return_after_friction_pct"] for row in changed])),
    )
    first_fold_contracts = {row["contract_symbol"] for row in development if "2026-01-04" <= row["entry_date"] <= "2026-01-06"}
    before = set(original["source_validation"]["development_policy"]["selected_contracts"]) & first_fold_contracts
    after = set(mutated["source_validation"]["development_policy"]["selected_contracts"]) & first_fold_contracts
    assert len(before) == 3
    assert after == before
    assert original["source_validation"]["folds"][0]["bucket_values"] == mutated["source_validation"]["folds"][0]["bucket_values"]
    assert original["tail_contract"]["bucket_values"] != mutated["tail_contract"]["bucket_values"]


def test_fold_utilities_use_only_purged_training_outcomes(fast_model):
    frame = pd.DataFrame(rows())
    feature_dates, labels_available = trainer._validate_frame(frame, "development", forward=False)
    returns = frame["hold_period_return_after_friction_pct"].to_numpy()
    # A large payoff's exit is early, but its label is not available until later.
    returns[3] = 3.0
    labels_available[3] = date(2026, 1, 12)
    X = trainer._feature_matrix(frame)
    probabilities, utilities, folds = trainer._development_predictions(X, returns, feature_dates, labels_available)
    splits = list(trainer.purged_date_splits(feature_dates, labels_available, n_splits=5))
    for (training, validation), fold in zip(splits, folds):
        expected = trainer._bucket_values(returns[training])
        np.testing.assert_allclose(utilities[validation], probabilities[validation] @ expected)
        assert fold["bucket_values"] == expected.tolist()
        assert fold["training_label_end"] < fold["validation_start"]
        assert fold["tail_gate"] == trainer.TAIL_GATE
    assert folds[0]["bucket_values"][3] == 1.5


def test_missing_training_bucket_is_finite_and_has_zero_probability():
    X = np.arange(60, dtype=float).reshape(30, 2)
    returns = np.tile([-0.8, -0.2, 0.2], 10)
    model = HistGradientBoostingClassifier(max_iter=2, min_samples_leaf=2, random_state=42).fit(X, trainer._outcome_classes(returns))
    probabilities = trainer._aligned_probabilities(model, X)
    values = trainer._bucket_values(returns)
    assert values[3] == 0.0
    assert np.all(probabilities[:, 3] == 0.0)
    assert np.isfinite(probabilities @ values).all()


def test_forward_outcomes_cannot_change_model_or_development_policy(tmp_path, fast_model):
    development, forward = rows(), rows(start=date(2026, 3, 1), days=2)
    original = train_at(tmp_path, development, forward, "original")
    mutated = train_at(tmp_path, development, [{**row, "hold_period_return_after_friction_pct": 2.5} for row in forward], "mutated")
    assert original["source_validation"]["development_policy"] == mutated["source_validation"]["development_policy"]
    assert original["tail_contract"] == mutated["tail_contract"]
    assert original["source_validation"]["forward_policy"]["selected_contracts"] == mutated["source_validation"]["forward_policy"]["selected_contracts"]
    assert original["model_sha256"] == mutated["model_sha256"]


@pytest.mark.parametrize("change,match", [
    (lambda dev, fwd: fwd.__setitem__(0, dict(dev[0])), "outcome identities overlap"),
    (lambda dev, fwd: dev[0].update(exit_date="2026-03-01"), "available before the forward"),
    (lambda dev, fwd: dev[0].update(executable_label_available_at_utc="2026-03-01T20:00:00Z"), "available before the forward"),
    (lambda dev, fwd: dev[0].update(exit_date="2025-12-31"), "must not precede entry"),
    (lambda dev, fwd: dev[0].update(exit_date="not-a-date"), "valid ISO"),
    (lambda dev, fwd: fwd[0].update(run_generated_at_utc="2026-03-01T15:00:00"), "timezone-aware"),
    (lambda dev, fwd: fwd[0].update(run_generated_at_utc="2026-02-01T15:00:00Z"), "match its entry date"),
    (lambda dev, fwd: dev[0].update(label_available_at_utc="2025-12-31T20:00:00Z"), "cannot precede the exit"),
    (lambda dev, fwd: dev[0].update(hold_period_return_after_friction_pct=float("nan")), "finite"),
    (lambda dev, fwd: fwd[0].update(hold_period_return_after_friction_pct=float("inf")), "finite"),
    (lambda dev, fwd: dev[0].update(implied_volatility=float("inf")), "finite"),
    (lambda dev, fwd: fwd[0].update(entry_ask=float("nan")), "finite"),
    (lambda dev, fwd: dev[0].update(entry_spread_pct="bad"), "finite"),
])
def test_invalid_sources_fail_before_fit_or_output(tmp_path, monkeypatch, change, match):
    development, forward = rows(), rows(start=date(2026, 3, 1), days=2)
    change(development, forward)
    def forbidden_fit(*args):
        pytest.fail("invalid sources reached model fitting")
    monkeypatch.setattr(trainer, "_fit", forbidden_fit)
    model_path = tmp_path / "protected.pkl"
    card_path = tmp_path / "protected.json"
    model_path.write_bytes(b"existing model")
    card_path.write_text("existing card")
    with pytest.raises(ValueError, match=match):
        trainer.train(dataset(tmp_path / "dev.json", development), dataset(tmp_path / "forward.json", forward), model_path, card_path)
    assert model_path.read_bytes() == b"existing model"
    assert card_path.read_text() == "existing card"


def test_reused_recommendation_id_is_not_a_new_forward_observation():
    development, forward = pd.DataFrame(rows()), pd.DataFrame(rows(start=date(2026, 3, 1), days=2))
    development.loc[0, "recommendation_id"] = "same-recommendation"
    forward.loc[0, "recommendation_id"] = "same-recommendation"
    with pytest.raises(ValueError, match="recommendation identities overlap"):
        trainer._validate_sources(development, forward)


def test_label_availability_uses_latest_recorded_capture_date():
    frame = pd.DataFrame(rows())
    frame.loc[0, "executable_label_available_at_utc"] = "2026-01-08T20:00:00Z"
    frame.loc[0, "exit_quote_observed_at_utc"] = "2026-01-03T20:00:00Z"
    _, availability = trainer._validate_frame(frame, "development", forward=False)
    assert availability[0] == date(2026, 1, 8)


def test_no_usable_folds_fails_closed():
    with pytest.raises(ValueError, match="no usable purged"):
        trainer._development_predictions(np.ones((2, 2)), np.array([-0.8, 0.9]), np.array([date(2026, 1, 1)] * 2), np.array([date(2026, 1, 2)] * 2))


def test_splitter_regression_cannot_silently_cross_label_boundary(monkeypatch):
    monkeypatch.setattr(trainer, "purged_date_splits", lambda *args, **kwargs: [(np.array([0]), np.array([1]))])
    with pytest.raises(ValueError, match="label-availability boundary"):
        trainer._development_predictions(np.ones((2, 2)), np.array([-0.8, 0.9]), np.array([date(2026, 1, 1), date(2026, 1, 2)]), np.array([date(2026, 1, 2)] * 2))


def test_nonfinite_feature_output_is_rejected(monkeypatch):
    monkeypatch.setattr(trainer, "feature_row", lambda *args, **kwargs: {column: float("nan") for column in trainer.FEATURE_COLS})
    with pytest.raises(ValueError, match="feature matrix must contain only finite"):
        trainer._feature_matrix(pd.DataFrame(rows(days=1)))


def test_card_records_real_sources_hashes_and_fold_policy(tmp_path, fast_model):
    card = train_at(tmp_path, rows(), rows(start=date(2026, 3, 1), days=2), "audit")
    assert card["status"] == "research_candidate"
    for source in ("development", "forward"):
        path = Path(card["sources"][source])
        assert card["sources"][f"{source}_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
    assert card["source_validation"]["source_checks"]["outcome_identity_overlap"] == 0
    assert card["source_validation"]["oof_utility_policy"] == "training_fold_only_clipped_bucket_means"
    assert card["source_validation"]["tail_gate_policy"] == "fixed_predeclared_thresholds_no_fold_or_forward_tuning"
    artifact = joblib.load(tmp_path / "audit.pkl")
    assert artifact["bucket_values"] == [-0.75, -0.25, 0.25, 1.5]


@pytest.mark.parametrize("explicit", [False, True])
def test_cli_defaults_are_research_only_and_explicit_outputs_are_honored(tmp_path, monkeypatch, capsys, explicit):
    monkeypatch.chdir(tmp_path)
    dev = dataset(tmp_path / "dev.json", rows())
    fwd = dataset(tmp_path / "fwd.json", rows(start=date(2026, 3, 1), days=2))
    # Exercise fitting, serialization, and the integrated replay with a small
    # real model, without touching production paths or any network source.
    monkeypatch.setattr(trainer, "_model", lambda: HistGradientBoostingClassifier(max_iter=2, min_samples_leaf=2, random_state=42))
    argv = ["trainer", "--development", str(dev), "--forward", str(fwd)]
    model, card = trainer.DEFAULT_MODEL, trainer.DEFAULT_CARD
    if explicit:
        model, card = tmp_path / "custom.pkl", tmp_path / "custom.json"
        argv += ["--output-model", str(model), "--output-card", str(card)]
    monkeypatch.setattr(sys, "argv", argv)
    assert trainer.main() == 0
    assert model.is_file() and card.is_file()
    assert not (tmp_path / "engine/orographic/models").exists()
    assert json.loads(capsys.readouterr().out)["output_model"] == str(model)
