"""Synthetic, offline regression coverage for primary Scout evaluation only."""
from datetime import date
import json

import joblib
import numpy as np
import pandas as pd
import pytest
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import brier_score_loss

from engine import train_scout_model as trainer


def synthetic_data(days=120):
    dates = np.repeat(pd.date_range("2025-01-01", periods=days, freq="D").to_numpy(), 2)
    row = np.arange(len(dates))
    X = np.column_stack([np.sin(row / 5), np.cos(row / 11), row % 2])
    y = ((row % 7) < 3).astype(int)
    regimes = np.where(row % 3, "risk_on", "risk_off")
    available = dates + np.timedelta64(4, "D")
    return X, y, dates, available, regimes


@pytest.fixture
def fast_model(monkeypatch):
    monkeypatch.setattr(trainer, "_primary_model", lambda: LogisticRegression(random_state=42))


def evaluate(values, *, method="isotonic", **kwargs):
    return trainer._primary_cv_predictions(
        *values, calibration_method=method, balance_weights=True,
        n_splits=3, embargo_days=2, **kwargs,
    )


@pytest.mark.parametrize("method", ["isotonic", "platt", "none"])
def test_outer_labels_cannot_change_frozen_model_calibrator_threshold_or_probability(monkeypatch, fast_model, method):
    values = synthetic_data()
    splits = trainer._primary_date_splits(values[2], values[3], n_splits=3, embargo_days=2)
    model_hashes, calibrator_hashes = [], []
    real_fit, real_calibrate = trainer._fit_primary_estimator, trainer._fit_calibrator
    def record_fit(*args, **kwargs):
        result = real_fit(*args, **kwargs)
        model_hashes.append(joblib.hash(result))
        return result
    def record_calibrate(*args, **kwargs):
        result = real_calibrate(*args, **kwargs)
        calibrator_hashes.append(joblib.hash(result))
        return result
    monkeypatch.setattr(trainer, "_fit_primary_estimator", record_fit)
    monkeypatch.setattr(trainer, "_fit_calibrator", record_calibrate)
    baseline = evaluate(values, method=method)
    baseline_model_hashes, baseline_calibrator_hashes = model_hashes[:], calibrator_hashes[:]
    model_hashes.clear()
    calibrator_hashes.clear()
    changed = list(values)
    changed[1] = values[1].copy()
    outer_val = splits[0][1]
    # Deliberately erase validation class balance. It must still be scored.
    changed[1][outer_val] = 1
    mutated = evaluate(changed, method=method)
    first = baseline["folds"][0]
    assert not first.get("skipped")
    assert mutated["folds"][0] == first
    fitted_first = 1 + sum(not row.get("skipped") for row in first["inner_folds"])
    assert model_hashes[:fitted_first] == baseline_model_hashes[:fitted_first]
    assert calibrator_hashes[0] == baseline_calibrator_hashes[0]
    for field in ("raw_probs", "calibrated_probs", "decision_thresholds"):
        np.testing.assert_array_equal(baseline[field][outer_val], mutated[field][outer_val])
    assert brier_score_loss(values[1][outer_val], baseline["calibrated_probs"][outer_val]) != brier_score_loss(changed[1][outer_val], mutated["calibrated_probs"][outer_val])


def test_balancing_weights_depend_only_on_current_training_subset(monkeypatch):
    X, y, _, _, regimes = synthetic_data()
    captured = []
    class Estimator:
        def fit(self, X, y, sample_weight):
            captured.append(sample_weight)
    monkeypatch.setattr(trainer, "_primary_model", Estimator)
    indices = np.arange(33)
    trainer._fit_primary_estimator(X[indices], y[indices], regimes[indices], balance_weights=True)
    np.testing.assert_array_equal(captured[0], trainer._balanced_sample_weights(y[indices], regimes[indices]))
    assert not np.array_equal(captured[0], trainer._balanced_sample_weights(y, regimes)[indices])


def test_both_split_levels_group_dates_and_purge_late_labels_with_embargo(fast_model):
    X, y, dates, available, regimes = synthetic_data()
    # An early observation's outcome is captured much later.
    available[0] = dates[150]
    outer = trainer._primary_date_splits(dates, available, n_splits=3, embargo_days=2)
    for training, validation in outer:
        assert available[training].max() < dates[validation].min() - np.timedelta64(2, "D")
        assert dates[training].max() < dates[validation].min()
        for observed in np.unique(dates[validation]):
            assert set(np.flatnonzero(dates == observed)) <= set(validation)
        inner = trainer._primary_date_splits(dates[training], available[training], n_splits=3, embargo_days=2)
        for inner_training, inner_validation in inner:
            fit_rows, calibrate_rows = training[inner_training], training[inner_validation]
            assert available[fit_rows].max() < dates[calibrate_rows].min() - np.timedelta64(2, "D")
            assert available[calibrate_rows].max() < dates[validation].min() - np.timedelta64(2, "D")
            assert set(fit_rows).isdisjoint(validation)
            assert set(calibrate_rows).isdisjoint(validation)
    assert 0 not in outer[0][0]
    baseline = evaluate((X, y, dates, available, regimes))
    y[0] = 1 - y[0]
    mutated = evaluate((X, y, dates, available, regimes))
    for field in ("raw_probs", "calibrated_probs", "decision_thresholds"):
        np.testing.assert_array_equal(baseline[field][outer[0][1]], mutated[field][outer[0][1]])


@pytest.mark.parametrize("bad", ["missing", "backwards", "length", "embargo"])
def test_invalid_date_contract_fails_before_estimator(monkeypatch, bad):
    X, y, dates, available, regimes = synthetic_data()
    embargo = 1
    if bad == "missing":
        available[0] = np.datetime64("NaT", "ns")
    elif bad == "backwards":
        available[0] = dates[0] - np.timedelta64(1, "D")
    elif bad == "length":
        available = available[:-1]
    else:
        embargo = -1
    monkeypatch.setattr(trainer, "_primary_model", lambda: pytest.fail("invalid dates reached fitting"))
    with pytest.raises(ValueError):
        trainer._primary_cv_predictions(X, y, dates, available, regimes, calibration_method="isotonic", embargo_days=embargo)


def test_shared_splitter_boundary_regression_is_rejected(monkeypatch):
    monkeypatch.setattr(trainer, "purged_date_splits", lambda *a, **kw: [(np.array([0]), np.array([1]))])
    with pytest.raises(ValueError, match="label-availability boundary"):
        trainer._primary_date_splits(np.array(["2025-01-01", "2025-01-02"]), np.array(["2025-01-02", "2025-01-03"]), n_splits=2, embargo_days=1)


def test_insufficient_inner_evidence_is_not_reported_as_calibrated_oos(fast_model):
    X, y, dates, available, regimes = synthetic_data(days=9)
    evaluation = evaluate((X, y, dates, available, regimes))
    assert not np.isfinite(evaluation["calibrated_probs"]).any()
    assert all(fold.get("skipped") for fold in evaluation["folds"])
    with pytest.raises(ValueError, match="no usable two-class purged OOF"):
        trainer._primary_calibration_report(
            evaluation, y, y.astype(float), calibration_method="isotonic", outcome_label="return",
        )


def test_report_uses_outer_predictions_and_distinguishes_production_fit(fast_model):
    values = synthetic_data()
    evaluation = evaluate(values)
    y = values[1]
    calibrator, threshold, report = trainer._primary_calibration_report(evaluation, y, y.astype(float), calibration_method="isotonic", outcome_label="return")
    valid = np.isfinite(evaluation["calibrated_probs"])
    assert report["calibrated_brier"] == round(brier_score_loss(y[valid], evaluation["calibrated_probs"][valid]), 4)
    assert report["raw_brier"] == round(brier_score_loss(y[valid], evaluation["raw_probs"][valid]), 4)
    assert report["oof_rows"] == valid.sum()
    assert report["decision_threshold_scope"] == "production_fit_only"
    assert report["production_fit_diagnostics"]["evaluation_scope"] == "calibrator_and_threshold_fit_sample_not_oos"
    assert report["production_fit_diagnostics"]["decision_threshold"] == threshold
    assert calibrator is not None
    changed = y.copy()
    changed[valid] = 1 - changed[valid]
    _, _, mutated = trainer._primary_calibration_report(evaluation, changed, y.astype(float), calibration_method="isotonic", outcome_label="return")
    assert report["production_fit_diagnostics"] != mutated["production_fit_diagnostics"]


def test_segment_diagnostics_use_each_frozen_fold_threshold():
    frame = pd.DataFrame({"spy_mom_20d": [0.0] * 40})
    report = trainer._segment_report(np.full(40, 0.6), np.tile([0, 1], 20), np.ones(40), frame, decision_threshold=np.r_[np.full(20, 0.7), np.full(20, 0.5)])
    assert report["by_side"]["put"]["rows"] == 20
    assert report["by_side"]["call"]["rows"] == 20


def test_option_availability_uses_latest_timestamp_for_cutoff_and_pair_grouping(tmp_path):
    trades = [
        {"symbol": "AAA", "entry_date": "2025-01-01", "exit_date": "2025-01-02", "option_type": side, "pnl_pct": pnl, "paired_observation_id": "pair", "label_available_at_utc": timestamp}
        for side, pnl, timestamp in [("call", 0.3, "2025-01-06T20:00:00Z"), ("put", -0.1, "2025-01-05T20:00:00Z")]
    ]
    path = tmp_path / "outcomes.json"
    path.write_text(json.dumps({"artifact": "option_outcome_dataset", "rows": trades}))
    frame, _ = trainer._load_option_outcome_labels([path])
    assert frame.iloc[0]["label_date"] == pd.Timestamp("2025-01-06")
    frame, metadata = trainer._load_option_outcome_labels([path], cutoff=date(2025, 1, 4))
    assert frame.empty
    assert metadata["skipped_after_cutoff"] == 2


@pytest.mark.parametrize("value", ["broken", "2025-01-03T20:00:00", "2025-01-01T20:00:00Z"])
def test_invalid_explicit_label_availability_is_not_silently_ignored(value):
    with pytest.raises(ValueError):
        trainer._option_label_available_date({"label_available_at_utc": value}, date(2025, 1, 2))


def test_synthetic_training_card_routes_nested_evidence_and_cli_accepts_embargo(tmp_path, monkeypatch):
    X, y, dates, available, _ = synthetic_data()
    frame = pd.DataFrame({"mom_5d": X[:, 0], "spy_mom_20d": X[:, 1], "label": y, "fwd_5d_return": np.where(y, 0.03, -0.03), "fwd_5d_label_date": available}, index=dates)
    monkeypatch.setattr(trainer, "fetch_history", lambda *args: frame)
    monkeypatch.setattr(trainer, "build_feature_matrix", lambda *args, **kwargs: frame.copy())
    monkeypatch.setattr(trainer, "load_event_feature_frame", lambda *args: pd.DataFrame())
    real_model = trainer.lgb.LGBMClassifier
    monkeypatch.setattr(trainer.lgb, "LGBMClassifier", lambda **kw: real_model(**{**kw, "n_estimators": 3, "min_child_samples": 2, "n_jobs": 1}))
    monkeypatch.setattr(trainer, "MODEL_DIR", tmp_path)
    for name in ("MODEL_PATH", "SIDE_MODEL_PATH", "SCALER_PATH", "MODEL_CARD_PATH", "HIERARCHICAL_SIDE_MODEL_PATH", "HIERARCHICAL_SIDE_CARD_PATH"):
        monkeypatch.setattr(trainer, name, tmp_path / getattr(trainer, name).name)
    monkeypatch.setattr(trainer, "_default_option_outcome_inputs", lambda: [])
    monkeypatch.setattr(trainer.sys, "argv", ["train_scout_model", "--symbols", "AAA", "--cutoff", "2025-12-31", "--embargo-days", "2"])
    trainer.main()
    card = json.loads(trainer.MODEL_CARD_PATH.read_text())
    assert card["cross_validation"]["embargo_calendar_days"] == 2
    assert card["cross_validation"]["evaluation_scope"] == "primary_binary_target_only_outer_oos"
    assert card["calibration"]["evaluation_scope"] == "outer_oos_nested_purged_walk_forward"
    assert card["calibration"]["oof_rows"] > 0
    artifact = joblib.load(trainer.SCALER_PATH)
    assert artifact["decision_threshold"] == card["calibration"]["production_fit_diagnostics"]["decision_threshold"]
    assert trainer.MODEL_PATH.parent == tmp_path


def test_missing_exit_date_cannot_be_treated_as_same_day_available(tmp_path):
    path = tmp_path / "missing-exit.json"
    path.write_text(json.dumps({"artifact": "option_outcome_dataset", "rows": [{"symbol": "AAA", "entry_date": "2025-01-01", "option_type": "call", "pnl_pct": 0.3}]}))
    frame, metadata = trainer._load_option_outcome_labels([path])
    assert frame.empty
    assert metadata["skipped_invalid_dates"] == 1


def test_zero_calibrated_oos_evidence_is_explicit_null_not_fit_score(fast_model):
    values = synthetic_data()
    evaluation = evaluate(values)
    evaluation["calibrated_probs"][:] = np.nan
    evaluation["decision_thresholds"][:] = np.nan
    _, _, report = trainer._primary_calibration_report(evaluation, values[1], values[1].astype(float), calibration_method="isotonic", outcome_label="return")
    assert report["evaluation_status"] == "insufficient_inner_evidence"
    assert report["calibrated_brier"] is None
    assert report["balanced_accuracy"] is None
    assert report["oof_rows"] == 0
    assert report["production_fit_diagnostics"]["calibrated_brier"] is not None
