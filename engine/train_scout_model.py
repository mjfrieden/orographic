"""
engine/train_scout_model.py

One-time training script for the Orographic Scout signal model.

Trains a LightGBM binary classifier to predict whether a stock's
5-day forward return will be positive (label=1) or negative (label=0).

The resulting probability p(label=1) replaces the hardcoded linear
scout_score at inference time.

Usage:
    cd /Users/mjfrieden/Desktop/2026/Orographic/engine
    python train_scout_model.py [--years 2] [--symbols AAPL,MSFT,...]
"""
from __future__ import annotations

import argparse
import hashlib
import json
import logging
import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
import joblib
import yfinance as yf
from sklearn.metrics import (
    balanced_accuracy_score,
    brier_score_loss,
    classification_report,
    log_loss,
    roc_auc_score,
)
from sklearn.preprocessing import RobustScaler
from sklearn.isotonic import IsotonicRegression
from sklearn.linear_model import LogisticRegression

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not installed. Run: pip install lightgbm")
    sys.exit(1)

from engine.orographic.event_features import (
    build_event_feature_history,
    load_event_feature_frame,
)
from engine.orographic.validation import purged_date_splits

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[1]
MODEL_DIR = Path(__file__).parent / "orographic" / "models"
MODEL_PATH = MODEL_DIR / "scout_model.pkl"
SIDE_MODEL_PATH = MODEL_DIR / "scout_side_model.pkl"
SCALER_PATH = MODEL_DIR / "scout_scaler.pkl"
MODEL_CARD_PATH = MODEL_DIR / "scout_model_card.json"
TRAINING_UNIVERSE_FILE = Path(__file__).with_name("sample_universe.txt")
DEFAULT_OPTION_OUTCOME_INPUT_CANDIDATES = [
    Path("output/option_outcomes_live_recommendations.json"),
    Path("output/backtest_results_2026-06-20_blended_target_dte_7_14_strict_real_execution_stress_6mo_end_2026-04-13.json"),
    Path("output/backtest_results_2026-04-16_blended_target_dte_7_14_strict_real_6mo.json"),
    Path("output/backtest_results_2026-04-16_blended_target_dte_7_14_strict_real_3mo.json"),
]
PRIMARY_TARGET_UNDERLYING = "underlying_forward_return"
PRIMARY_TARGET_OPTION_DIRECTION = "strict_real_option_direction"
NON_SEC_EVENT_PREFIXES = ("fnspid_", "edt_", "mirai_", "stocktwits_")
NARRATIVE_TRAINING_FEATURE_COLUMNS = [
    "narrative_attention_1d",
    "narrative_attention_3d",
    "narrative_attention_acceleration_3d",
    "narrative_source_diversity_1d",
    "narrative_duplicate_ratio_1d",
    "narrative_novelty_mean_1d",
    "narrative_directional_intensity_1d",
    "narrative_confirmation_score_1d",
]
SEC_CURATED_EVENT_FEATURE_COLUMNS = [
    "sec_8k_flag",
    "sec_10q_flag",
    "sec_10k_flag",
    "sec_offering_flag",
    "sec_proxy_flag",
    "sec_signal_count_1d",
    "sec_signal_count_5d",
    "sec_material_event_score",
    "sec_material_event_score_5d",
    "sec_signal_ratio",
]
SEC_FALLBACK_EVENT_FEATURE_COLUMNS = [
    "sec_8k_count",
    "sec_10q_count",
    "sec_10k_count",
    "sec_offering_count",
    "sec_proxy_count",
]


def _load_training_universe() -> list[str]:
    if not TRAINING_UNIVERSE_FILE.exists():
        raise FileNotFoundError(f"Training universe file not found: {TRAINING_UNIVERSE_FILE}")
    symbols: list[str] = []
    for line in TRAINING_UNIVERSE_FILE.read_text(encoding="utf-8").splitlines():
        cleaned = line.strip().upper()
        if cleaned and not cleaned.startswith("#"):
            symbols.append(cleaned)
    if not symbols:
        raise ValueError(f"Training universe file is empty: {TRAINING_UNIVERSE_FILE}")
    return symbols


TRAINING_UNIVERSE = _load_training_universe()
SIDE_CLASS_MAP = {0: "put_edge", 1: "no_trade", 2: "call_edge"}
SIDE_CLASS_TO_ID = {value: key for key, value in SIDE_CLASS_MAP.items()}


def _artifact_path_for_card(path: Path) -> str:
    resolved = path.resolve()
    try:
        return str(resolved.relative_to(REPO_ROOT))
    except ValueError:
        return str(resolved)


def _default_option_outcome_inputs() -> list[Path]:
    return [path for path in DEFAULT_OPTION_OUTCOME_INPUT_CANDIDATES if path.exists()]


def _selected_event_feature_columns(columns: pd.Index | list[str]) -> list[str]:
    available_columns = [str(column) for column in columns]
    event_columns = [column for column in available_columns if column.startswith(NON_SEC_EVENT_PREFIXES)]
    event_columns += [column for column in NARRATIVE_TRAINING_FEATURE_COLUMNS if column in available_columns]
    curated_sec = [column for column in SEC_CURATED_EVENT_FEATURE_COLUMNS if column in available_columns]
    if curated_sec:
        return event_columns + curated_sec
    fallback_sec = [column for column in SEC_FALLBACK_EVENT_FEATURE_COLUMNS if column in available_columns]
    return event_columns + fallback_sec


# ── Feature engineering ──────────────────────────────────────────────────────

def _rsi(close: pd.Series, period: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0).rolling(period).mean()
    down = -delta.clip(upper=0.0).rolling(period).mean()
    rs = up / down.replace(0.0, float("nan"))
    return 100 - (100 / (1 + rs))


def _atr_pct(df: pd.DataFrame, period: int = 14) -> pd.Series:
    high = pd.to_numeric(df["High"], errors="coerce")
    low  = pd.to_numeric(df["Low"],  errors="coerce")
    close = pd.to_numeric(df["Close"], errors="coerce")
    prev_close = close.shift(1)
    tr = pd.concat(
        [(high - low).abs(), (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean() / close


def build_feature_matrix(
    df: pd.DataFrame,
    spy_df: pd.DataFrame | None = None,
    *,
    symbol: str | None = None,
    event_feature_frame: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Engineer per-bar features from OHLCV + optional SPY overlay.
    Returns a DataFrame with NaN rows dropped.
    """
    # Flatten MultiIndex from modern yfinance
    if isinstance(df.columns, pd.MultiIndex):
        df = df.copy()
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]

    close = pd.to_numeric(df["Close"], errors="coerce")
    vol   = pd.to_numeric(df.get("Volume", pd.Series(dtype=float)), errors="coerce")

    features = pd.DataFrame(index=df.index)

    # Momentum
    features["mom_5d"]  = close.pct_change(5)
    features["mom_10d"] = close.pct_change(10)
    features["mom_20d"] = close.pct_change(20)
    features["mom_60d"] = close.pct_change(60)

    # Realized vol
    rv20 = close.pct_change().rolling(20).std() * (252 ** 0.5)
    rv60 = close.pct_change().rolling(60).std() * (252 ** 0.5)
    features["rv20"] = rv20
    features["rv60"] = rv60

    # Vol-adjusted momentum (the Sharpe of the recent move)
    features["vol_adj_mom_5d"]  = features["mom_5d"]  / rv20.replace(0, float("nan"))
    features["vol_adj_mom_20d"] = features["mom_20d"] / rv20.replace(0, float("nan"))

    # RSI
    features["rsi_14"] = _rsi(close, 14)
    features["rsi_7"]  = _rsi(close, 7)

    # ATR%
    features["atr_pct_14d"] = _atr_pct(df, 14)

    # Mean-reversion signal (distance from 20d MA in ATR units)
    ma20 = close.rolling(20).mean()
    features["price_vs_ma20"] = (close - ma20) / (features["atr_pct_14d"] * close).replace(0, float("nan"))

    # Volume trend
    if vol.notna().sum() > 20:
        features["volume_ratio"] = vol / vol.rolling(20).mean()
    else:
        features["volume_ratio"] = 1.0

    # Volatility regime (rv20 vs rv60 ratio — expansion or contraction)
    features["vol_regime"] = rv20 / rv60.replace(0, float("nan"))

    # Cross-asset SPY context
    if spy_df is not None:
        if isinstance(spy_df.columns, pd.MultiIndex):
            spy_df = spy_df.copy()
            spy_df.columns = [c[0] if isinstance(c, tuple) else c for c in spy_df.columns]
        spy_close = pd.to_numeric(spy_df["Close"], errors="coerce").reindex(df.index, method="ffill")
        features["spy_mom_5d"]  = spy_close.pct_change(5)
        features["spy_mom_20d"] = spy_close.pct_change(20)
        features["spy_rv20"]    = spy_close.pct_change().rolling(20).std() * (252 ** 0.5)
        # Relative strength vs SPY
        features["rel_strength_20d"] = features["mom_20d"] - features["spy_mom_20d"]

    if symbol:
        event_history = build_event_feature_history(symbol, features.index, event_feature_frame)
        for column in event_history.columns:
            features[column] = event_history[column]

    # ── Forward return label (5d) ──
    features["fwd_5d_return"] = close.pct_change(5).shift(-5)
    features["fwd_5d_label_date"] = pd.Series(close.index, index=df.index).shift(-5)
    features["label"] = (features["fwd_5d_return"] > 0).astype(int)

    event_columns = [
        column
        for column in features.columns
        if column.startswith(("fnspid_", "edt_", "mirai_", "sec_", "stocktwits_"))
    ]
    if "dataset_tags" in features.columns:
        features["dataset_tags"] = features["dataset_tags"].fillna("").astype(str)
    if event_columns:
        features[event_columns] = features[event_columns].fillna(0.0)

    return features.dropna()


# ── Data fetch ───────────────────────────────────────────────────────────────

def fetch_history(symbol: str, years: int) -> pd.DataFrame:
    end   = date.today()
    start = end - timedelta(days=years * 365 + 90)  # extra headroom for rolling calcs
    log.info("  Fetching %s …", symbol)
    ticker = yf.Ticker(symbol)
    df = ticker.history(
        start=start.isoformat(),
        end=(end + timedelta(days=1)).isoformat(),
        interval="1d",
        auto_adjust=False,
    )
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = [c[0] if isinstance(c, tuple) else c for c in df.columns]
    if df.empty:
        raise RuntimeError(f"No data for {symbol}")
    return df


# ── Training ─────────────────────────────────────────────────────────────────


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _fit_calibrator(raw_probs: np.ndarray, y: np.ndarray, method: str) -> object | None:
    if method == "none":
        return None
    if method == "isotonic":
        calibrator = IsotonicRegression(out_of_bounds="clip")
        calibrator.fit(raw_probs, y)
        return calibrator
    if method == "platt":
        calibrator = LogisticRegression(solver="lbfgs")
        calibrator.fit(raw_probs.reshape(-1, 1), y)
        return calibrator
    raise ValueError(f"Unsupported calibration method: {method}")


def _apply_calibrator(raw_probs: np.ndarray, calibrator: object | None, method: str) -> np.ndarray:
    if calibrator is None or method == "none":
        return raw_probs
    if method == "isotonic":
        return np.asarray(calibrator.predict(raw_probs), dtype=float)
    if method == "platt":
        return np.asarray(calibrator.predict_proba(raw_probs.reshape(-1, 1))[:, 1], dtype=float)
    return raw_probs


def _probability_buckets(
    probs: np.ndarray,
    y: np.ndarray,
    realized_outcomes: np.ndarray,
    *,
    outcome_label: str = "realized_target_value",
    buckets: int = 10,
) -> list[dict[str, float | int | str]]:
    frame = pd.DataFrame(
        {
            "prob": probs,
            "label": y,
            "realized_outcome": realized_outcomes,
        }
    ).dropna()
    if frame.empty:
        return []
    q = min(buckets, max(2, len(frame) // 25))
    frame["bucket"] = pd.qcut(frame["prob"], q=q, duplicates="drop")
    rows: list[dict[str, float | int | str]] = []
    grouped = frame.groupby("bucket", observed=True)
    for bucket, group in grouped:
        rows.append(
            {
                "bucket": str(bucket),
                "rows": int(len(group)),
                "mean_pred_prob": round(float(group["prob"].mean()), 4),
                "realized_positive_rate": round(float(group["label"].mean()), 4),
                f"avg_{outcome_label}": round(float(group["realized_outcome"].mean()), 4),
            }
        )
    return rows


def _optimal_decision_threshold(probs: np.ndarray, y: np.ndarray) -> float:
    frame = pd.DataFrame({"prob": probs, "label": y}).dropna()
    if frame.empty or frame["label"].nunique() < 2:
        return 0.5
    candidates = sorted({0.05, 0.10, 0.15, 0.20, 0.25, 0.33, 0.40, 0.50, 0.60, 0.67, 0.75, 0.80, 0.85, 0.90, 0.95, *frame["prob"].round(4).tolist()})
    best_threshold = 0.5
    best_score = float("-inf")
    best_distance = float("inf")
    for threshold in candidates:
        preds = (frame["prob"] >= threshold).astype(int)
        if preds.nunique() < 2:
            continue
        score = float(balanced_accuracy_score(frame["label"], preds))
        distance = abs(float(threshold) - 0.5)
        if score > best_score or (score == best_score and distance < best_distance):
            best_score = score
            best_threshold = float(threshold)
            best_distance = distance
    return round(best_threshold, 4)


def _safe_binary_log_loss(y_true: np.ndarray, probs: np.ndarray) -> float:
    clipped = np.clip(probs, 1e-6, 1 - 1e-6)
    return float(log_loss(y_true, clipped, labels=[0, 1]))


def _segment_report(
    probs: np.ndarray,
    y: np.ndarray,
    realized_outcomes: np.ndarray,
    combined: pd.DataFrame,
    *,
    outcome_label: str = "realized_target_value",
    class_names: dict[int, str] | None = None,
    decision_threshold: float = 0.5,
) -> dict[str, Any]:
    class_names = class_names or {0: "put", 1: "call"}
    actual_side = np.array([class_names.get(int(label), str(label)) for label in y], dtype=object)
    frame = pd.DataFrame(
        {
            "prob": probs,
            "label": y,
            "realized_outcome": realized_outcomes,
            "predicted_side": np.where(probs >= decision_threshold, "call", "put"),
            "actual_side": actual_side,
            "regime": np.where(
                combined.get("spy_mom_20d", pd.Series(0.0, index=combined.index)).values >= 0.02,
                "risk_on",
                np.where(
                    combined.get("spy_mom_20d", pd.Series(0.0, index=combined.index)).values <= -0.02,
                    "risk_off",
                    "neutral",
                ),
            ),
        }
    ).replace([np.inf, -np.inf], np.nan).dropna()
    report: dict[str, Any] = {
        "by_side": {},
        "by_regime": {},
        "by_side_regime": {},
        "by_actual_side": {},
        "by_actual_side_regime": {},
    }
    for key, target in (
        ("predicted_side", "by_side"),
        ("regime", "by_regime"),
        ("actual_side", "by_actual_side"),
    ):
        for value, group in frame.groupby(key):
            if len(group) < 10:
                continue
            report[target][str(value)] = {
                "rows": int(len(group)),
                "positive_rate": round(float(group["label"].mean()), 4),
                "avg_pred_prob": round(float(group["prob"].mean()), 4),
                "brier": round(float(brier_score_loss(group["label"], group["prob"])), 4),
                f"avg_{outcome_label}": round(float(group["realized_outcome"].mean()), 4),
            }
    for (side, regime), group in frame.groupby(["predicted_side", "regime"]):
        if len(group) < 10:
            continue
        report["by_side_regime"][f"{side}_{regime}"] = {
            "rows": int(len(group)),
            "positive_rate": round(float(group["label"].mean()), 4),
            "avg_pred_prob": round(float(group["prob"].mean()), 4),
            "brier": round(float(brier_score_loss(group["label"], group["prob"])), 4),
            f"avg_{outcome_label}": round(float(group["realized_outcome"].mean()), 4),
        }
    for (side, regime), group in frame.groupby(["actual_side", "regime"]):
        if len(group) < 10:
            continue
        report["by_actual_side_regime"][f"{side}_{regime}"] = {
            "rows": int(len(group)),
            "positive_rate": round(float(group["label"].mean()), 4),
            "avg_pred_prob": round(float(group["prob"].mean()), 4),
            "brier": round(float(brier_score_loss(group["label"], group["prob"])), 4),
            f"avg_{outcome_label}": round(float(group["realized_outcome"].mean()), 4),
        }
    report["target_value_field"] = outcome_label
    return report


def _coverage_report(combined: pd.DataFrame) -> dict[str, Any]:
    symbol_counts = combined["symbol"].value_counts().to_dict() if "symbol" in combined else {}
    date_series = pd.Series(dtype="datetime64[ns]")
    for key in ("primary_label_date", "fwd_5d_label_date", "date"):
        if key in combined.columns:
            date_series = pd.to_datetime(combined[key], errors="coerce").dropna()
            if not date_series.empty:
                break
    if date_series.empty and isinstance(combined.index, pd.DatetimeIndex):
        date_series = pd.Series(combined.index).dropna()
    return {
        "symbols": int(len(symbol_counts)),
        "rows": int(len(combined)),
        "rows_by_symbol": {str(k): int(v) for k, v in sorted(symbol_counts.items())},
        "date_start": str(date_series.min().date()) if not date_series.empty else None,
        "date_end": str(date_series.max().date()) if not date_series.empty else None,
    }


def _infer_regime_labels(frame: pd.DataFrame) -> np.ndarray:
    spy_mom = pd.to_numeric(frame.get("spy_mom_20d", pd.Series(0.0, index=frame.index)), errors="coerce").fillna(0.0)
    return np.where(
        spy_mom.values >= 0.02,
        "risk_on",
        np.where(spy_mom.values <= -0.02, "risk_off", "neutral"),
    )


def _balanced_sample_weights(y: np.ndarray, regime_labels: np.ndarray) -> np.ndarray:
    if len(y) == 0:
        return np.array([], dtype=float)
    weights = np.ones(len(y), dtype=float)

    class_counts = pd.Series(y).value_counts().to_dict()
    total = max(len(y), 1)
    class_weight_map = {
        int(cls): total / (max(len(class_counts), 1) * count)
        for cls, count in class_counts.items()
        if count > 0
    }
    for idx, cls in enumerate(y):
        weights[idx] *= class_weight_map.get(int(cls), 1.0)

    grouped = pd.DataFrame({"label": y, "regime": regime_labels})
    for cls in sorted(grouped["label"].unique().tolist()):
        class_mask = grouped["label"] == cls
        class_total = int(class_mask.sum())
        if class_total <= 0:
            continue
        regime_counts = grouped.loc[class_mask, "regime"].value_counts().to_dict()
        regime_weight_map = {
            str(regime): class_total / (max(len(regime_counts), 1) * count)
            for regime, count in regime_counts.items()
            if count > 0
        }
        for idx in grouped.index[class_mask]:
            weights[int(idx)] *= regime_weight_map.get(str(grouped.at[idx, "regime"]), 1.0)

    mean_weight = float(np.mean(weights)) if len(weights) else 1.0
    if mean_weight > 0:
        weights = weights / mean_weight
    return weights


def _class_balance_report(y: np.ndarray, regime_labels: np.ndarray, *, class_names: dict[int, str]) -> dict[str, Any]:
    if len(y) == 0:
        return {
            "rows": 0,
            "class_counts": {},
            "class_shares": {},
            "minority_share": None,
            "regime_counts": {},
            "class_regime_counts": {},
        }
    label_series = pd.Series(y, name="label")
    regime_series = pd.Series(regime_labels, name="regime")
    class_counts_raw = label_series.value_counts().sort_index().to_dict()
    class_counts = {
        class_names.get(int(cls), str(cls)): int(count)
        for cls, count in class_counts_raw.items()
    }
    total = max(int(len(label_series)), 1)
    class_shares = {
        name: round(count / total, 4)
        for name, count in class_counts.items()
    }
    minority_share = min(class_shares.values()) if class_shares else None
    regime_counts_raw = regime_series.value_counts().to_dict()
    class_regime_counts: dict[str, dict[str, int]] = {}
    for cls, name in class_names.items():
        mask = label_series == cls
        if not mask.any():
            continue
        class_regime_counts[name] = {
            str(regime): int(count)
            for regime, count in regime_series[mask].value_counts().to_dict().items()
        }
    return {
        "rows": int(len(label_series)),
        "class_counts": class_counts,
        "class_shares": class_shares,
        "minority_share": round(float(minority_share), 4) if minority_share is not None else None,
        "regime_counts": {str(regime): int(count) for regime, count in regime_counts_raw.items()},
        "class_regime_counts": class_regime_counts,
    }


def _drift_baseline(combined: pd.DataFrame, feature_cols: list[str]) -> dict[str, Any]:
    baseline: dict[str, Any] = {}
    for col in feature_cols:
        series = pd.to_numeric(combined[col], errors="coerce").replace([np.inf, -np.inf], np.nan).dropna()
        if series.empty:
            continue
        baseline[col] = {
            "mean": round(float(series.mean()), 6),
            "std": round(float(series.std(ddof=0)), 6),
            "p05": round(float(series.quantile(0.05)), 6),
            "p50": round(float(series.quantile(0.50)), 6),
            "p95": round(float(series.quantile(0.95)), 6),
        }
    return baseline


def _event_feature_activation_report(frame: pd.DataFrame, event_feature_cols: list[str]) -> dict[str, Any]:
    if not event_feature_cols:
        return {
            "feature_cols": [],
            "rows_with_any_event_feature": 0,
            "row_coverage_pct": 0.0,
            "by_feature": {},
        }
    numeric = frame[event_feature_cols].apply(pd.to_numeric, errors="coerce").fillna(0.0)
    active_mask = numeric.abs().gt(0).any(axis=1)
    by_feature: dict[str, Any] = {}
    total_rows = max(int(len(frame)), 1)
    for column in event_feature_cols:
        nonzero_rows = int(numeric[column].abs().gt(0).sum())
        by_feature[column] = {
            "nonzero_rows": nonzero_rows,
            "row_coverage_pct": round(nonzero_rows / total_rows, 4),
        }
    return {
        "feature_cols": list(event_feature_cols),
        "rows_with_any_event_feature": int(active_mask.sum()),
        "row_coverage_pct": round(float(active_mask.mean()), 4) if len(active_mask) else 0.0,
        "by_feature": by_feature,
    }


def _safe_float(value: object, default: float = 0.0) -> float:
    try:
        if value is None:
            return default
        result = float(value)
        if not np.isfinite(result):
            return default
        return result
    except (TypeError, ValueError):
        return default


def _load_option_outcome_labels(input_paths: list[Path], cutoff: date | None = None) -> tuple[pd.DataFrame, dict[str, Any]]:
    """
    Build symbol/date side labels from strict-real option outcomes.

    A profitable call labels that symbol/date as call_edge; a profitable put
    labels it as put_edge; losing or flat observed expressions become no_trade.
    When both sides exist for the same symbol/date, the better positive side wins.
    """
    rows: list[dict[str, Any]] = []
    skipped_after_cutoff = 0
    for path in input_paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        artifact = str(payload.get("artifact") or "").strip()
        trade_rows = payload.get("rows") if artifact == "option_outcome_dataset" else payload.get("all_trades", [])
        if not isinstance(trade_rows, list):
            continue
        for trade in trade_rows:
            try:
                entry_date = date.fromisoformat(str(trade["entry_date"]))
                exit_date = date.fromisoformat(str(trade.get("exit_date") or trade["entry_date"]))
            except (KeyError, TypeError, ValueError):
                continue
            if cutoff is not None and (entry_date > cutoff or exit_date > cutoff):
                skipped_after_cutoff += 1
                continue
            side = str(trade.get("option_type", "")).lower()
            if side not in {"call", "put"}:
                continue
            rows.append(
                {
                    "symbol": str(trade.get("symbol", "")).upper(),
                    "date": pd.Timestamp(entry_date),
                    "label_date": pd.Timestamp(exit_date),
                    "option_type": side,
                    "pnl_pct": _safe_float(trade.get("pnl_pct")),
                    "pnl": _safe_float(trade.get("pnl")),
                    "source_file": str(path),
                }
            )

    if not rows:
        return pd.DataFrame(), {
            "input_files": [str(path) for path in input_paths],
            "trade_rows": 0,
            "labeled_symbol_dates": 0,
            "skipped_after_cutoff": skipped_after_cutoff,
        }

    trades = pd.DataFrame(rows)
    grouped_rows: list[dict[str, Any]] = []
    for (symbol, entry_date), group in trades.groupby(["symbol", "date"], sort=True):
        call_returns = group.loc[group["option_type"] == "call", "pnl_pct"]
        put_returns = group.loc[group["option_type"] == "put", "pnl_pct"]
        call_score = float(call_returns.mean()) if not call_returns.empty else float("-inf")
        put_score = float(put_returns.mean()) if not put_returns.empty else float("-inf")
        best_side = "call_edge" if call_score >= put_score else "put_edge"
        best_score = max(call_score, put_score)
        label = best_side if best_score > 0.0 else "no_trade"
        grouped_rows.append(
            {
                "symbol": symbol,
                "date": entry_date,
                "label_date": pd.to_datetime(group["label_date"]).max(),
                "side_label": label,
                "side_label_id": SIDE_CLASS_TO_ID[label],
                "call_avg_pnl_pct": None if call_score == float("-inf") else round(call_score, 6),
                "put_avg_pnl_pct": None if put_score == float("-inf") else round(put_score, 6),
                "trade_count": int(len(group)),
            }
        )

    labeled = pd.DataFrame(grouped_rows)
    metadata = {
        "input_files": [str(path) for path in input_paths],
        "trade_rows": int(len(trades)),
        "labeled_symbol_dates": int(len(labeled)),
        "skipped_after_cutoff": int(skipped_after_cutoff),
        "class_counts": {
            label: int((labeled["side_label"] == label).sum())
            for label in SIDE_CLASS_TO_ID
        },
    }
    return labeled, metadata


def _merge_option_outcome_labels(combined: pd.DataFrame, option_labels: pd.DataFrame) -> pd.DataFrame:
    if option_labels.empty:
        return pd.DataFrame()
    mergeable = combined.copy()
    feature_dates = pd.to_datetime(mergeable.index)
    if getattr(feature_dates, "tz", None) is not None:
        feature_dates = feature_dates.tz_convert(None)
    mergeable["date"] = feature_dates.normalize()
    return mergeable.merge(
        option_labels,
        on=["symbol", "date"],
        how="inner",
        validate="many_to_one",
    )


def _directional_option_training_frame(merged: pd.DataFrame) -> pd.DataFrame:
    if merged.empty:
        return merged.copy()
    directional = merged.loc[merged["side_label"].isin({"call_edge", "put_edge"})].copy()
    if directional.empty:
        return directional.copy()
    call_pnl = pd.to_numeric(directional["call_avg_pnl_pct"], errors="coerce").fillna(0.0)
    put_pnl = pd.to_numeric(directional["put_avg_pnl_pct"], errors="coerce").fillna(0.0)
    directional["primary_label"] = (directional["side_label"] == "call_edge").astype(int)
    directional["primary_outcome_value"] = call_pnl - put_pnl
    directional["primary_label_date"] = pd.to_datetime(
        directional["label_date"] if "label_date" in directional.columns else directional["date"],
        errors="coerce",
    )
    return directional


def _side_cv_report(
    X: np.ndarray,
    y: np.ndarray,
    dates: np.ndarray,
    label_dates: np.ndarray,
) -> dict[str, Any]:
    if len(X) < 80 or len(set(y.tolist())) < 2:
        return {"folds": 0, "reason": "insufficient_examples_or_classes"}
    order = np.argsort(pd.to_datetime(dates).view("int64"), kind="stable")
    X_sorted = X[order]
    y_sorted = y[order]
    dates_sorted = np.asarray(dates)[order]
    label_dates_sorted = np.asarray(label_dates)[order]
    splits = list(
        purged_date_splits(
            dates_sorted,
            label_dates_sorted,
            n_splits=min(5, max(2, len(X_sorted) // 120)),
        )
    )
    rows: list[dict[str, Any]] = []
    balanced_scores: list[float] = []
    for fold, (train_idx, val_idx) in enumerate(splits, start=1):
        if len(set(y_sorted[train_idx].tolist())) < 2 or len(set(y_sorted[val_idx].tolist())) < 2:
            rows.append(
                {
                    "fold": fold,
                    "train_rows": int(len(train_idx)),
                    "validation_rows": int(len(val_idx)),
                    "skipped": True,
                    "reason": "single_class_train_or_validation",
                }
            )
            continue
        scaler = RobustScaler()
        X_train = scaler.fit_transform(X_sorted[train_idx])
        X_val = scaler.transform(X_sorted[val_idx])
        model = lgb.LGBMClassifier(
            objective="multiclass",
            num_class=3,
            n_estimators=260,
            learning_rate=0.04,
            max_depth=4,
            num_leaves=15,
            subsample=0.85,
            colsample_bytree=0.85,
            min_child_samples=10,
            class_weight="balanced",
            random_state=43 + fold,
            verbose=-1,
        )
        model.fit(X_train, y_sorted[train_idx])
        preds = model.predict(X_val)
        score = float(balanced_accuracy_score(y_sorted[val_idx], preds))
        balanced_scores.append(score)
        rows.append(
            {
                "fold": fold,
                "train_rows": int(len(train_idx)),
                "validation_rows": int(len(val_idx)),
                "validation_start": str(pd.Timestamp(dates_sorted[val_idx].min()).date()),
                "training_label_end": str(pd.Timestamp(label_dates_sorted[train_idx].max()).date()),
                "balanced_accuracy": round(score, 4),
            }
        )
    return {
        "folds": int(len(splits)),
        "split_policy": "date_grouped_purged_by_outcome_date",
        "mean_balanced_accuracy": round(float(np.mean(balanced_scores)), 4) if balanced_scores else None,
        "fold_reports": rows,
    }


def train(
    symbols: list[str],
    years: int,
    cutoff: date | None = None,
    *,
    calibration_method: str = "isotonic",
    option_outcome_inputs: list[Path] | None = None,
    primary_target: str = PRIMARY_TARGET_UNDERLYING,
    event_features_path: Path | None = None,
) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    
    if cutoff is None:
        cutoff = date.today()
    
    log.info("Training with cutoff: %s", cutoff)

    log.info("Fetching SPY for cross-asset context …")
    try:
        spy_df = fetch_history("SPY", years)
    except Exception as e:
        log.warning("SPY fetch failed (%s) — cross-asset features disabled", e)
        spy_df = None
    event_feature_frame = load_event_feature_frame(event_features_path)
    if not event_feature_frame.empty:
        log.info(
            "Loaded event-feature store: %d rows across %d symbols",
            len(event_feature_frame),
            int(event_feature_frame["symbol"].nunique()),
        )

    all_features: list[pd.DataFrame] = []

    for symbol in symbols:
        try:
            df = fetch_history(symbol, years)
            feat = build_feature_matrix(
                df,
                spy_df,
                symbol=symbol,
                event_feature_frame=event_feature_frame,
            )
            feat["symbol"] = symbol
            if len(feat) < 60:
                log.warning("  Skipping %s — insufficient rows after feature engineering", symbol)
                continue
            all_features.append(feat)
            log.info("  ✓ %s  %d rows", symbol, len(feat))
        except Exception as exc:
            log.warning("  ✗ %s  %s", symbol, exc)

    if not all_features:
        log.error("No data collected — aborting.")
        sys.exit(1)

    combined = pd.concat(all_features, axis=0).sort_index()
    # Apply cutoff to the realized label date, not just the feature date.
    # fwd_5d_return uses shift(-5), so feature rows near the cutoff would
    # otherwise include post-cutoff returns.
    mask = combined.index.date <= cutoff
    if "fwd_5d_label_date" in combined.columns:
        label_dates = pd.to_datetime(combined["fwd_5d_label_date"], errors="coerce").dt.date
        mask = mask & (label_dates <= cutoff)
    combined = combined[mask].copy()
    
    log.info("Combined dataset: %d rows across %d symbols (after cutoff)", len(combined), len(all_features))

    FEATURE_COLS = [
        "mom_5d", "mom_10d", "mom_20d", "mom_60d",
        "rv20", "rv60", "vol_adj_mom_5d", "vol_adj_mom_20d",
        "rsi_14", "rsi_7", "atr_pct_14d",
        "price_vs_ma20", "volume_ratio", "vol_regime",
    ]
    if spy_df is not None:
        FEATURE_COLS += ["spy_mom_5d", "spy_mom_20d", "spy_rv20", "rel_strength_20d"]
    event_feature_cols = _selected_event_feature_columns(combined.columns)
    FEATURE_COLS += event_feature_cols

    available = [c for c in FEATURE_COLS if c in combined.columns]
    option_labels = pd.DataFrame()
    option_label_metadata: dict[str, Any] = {}
    if option_outcome_inputs:
        option_labels, option_label_metadata = _load_option_outcome_labels(option_outcome_inputs, cutoff=cutoff)

    primary_training_frame = combined.copy()
    primary_target_effective = primary_target
    target_description = "probability that forward 5-day underlying return is positive"
    positive_class_name = "bullish"
    primary_outcome_label = "fwd_5d_return"
    primary_source_metadata: dict[str, Any] = {}
    if primary_target == PRIMARY_TARGET_OPTION_DIRECTION:
        merged_option_labels = _merge_option_outcome_labels(combined, option_labels)
        directional_frame = _directional_option_training_frame(merged_option_labels)
        directional_class_counts = directional_frame["primary_label"].value_counts().to_dict() if "primary_label" in directional_frame else {}
        directional_minority_count = min(directional_class_counts.values()) if directional_class_counts else 0
        directional_minority_share = (
            directional_minority_count / max(len(directional_frame), 1)
            if directional_class_counts
            else 0.0
        )
        if (
            len(directional_frame) >= 80
            and directional_frame["primary_label"].nunique() >= 2
            and directional_minority_share >= 0.19
        ):
            primary_training_frame = directional_frame
            target_description = "probability that strict-real call-side option edge beats put-side option edge"
            positive_class_name = "call_edge"
            primary_outcome_label = "directional_option_edge_spread"
            primary_source_metadata = {
                **option_label_metadata,
                "merged_symbol_dates": int(len(merged_option_labels)),
                "directional_symbol_dates": int(len(directional_frame)),
                "class_counts": {
                    "put_edge": int((directional_frame["primary_label"] == 0).sum()),
                    "call_edge": int((directional_frame["primary_label"] == 1).sum()),
                },
            }
        else:
            log.warning(
                "Primary option-direction target requested but strict-real labels were too sparse or imbalanced (%d directional rows, %d classes, minority_count=%d, minority_share=%.3f). Falling back to underlying direction target.",
                len(directional_frame),
                int(directional_frame["primary_label"].nunique()) if "primary_label" in directional_frame else 0,
                directional_minority_count,
                directional_minority_share,
            )
            primary_target_effective = PRIMARY_TARGET_UNDERLYING

    if primary_target_effective == PRIMARY_TARGET_UNDERLYING:
        primary_training_frame["primary_label"] = primary_training_frame["label"].astype(int)
        primary_training_frame["primary_outcome_value"] = primary_training_frame["fwd_5d_return"]
        primary_training_frame["primary_label_date"] = pd.to_datetime(
            primary_training_frame["fwd_5d_label_date"],
            errors="coerce",
        )
        primary_source_metadata = {
            "rows": int(len(primary_training_frame)),
            "positive_label_rate": round(float(primary_training_frame["primary_label"].mean()), 4),
        }

    X = primary_training_frame[available].values
    y = primary_training_frame["primary_label"].to_numpy(dtype=int)
    realized_target_values = primary_training_frame["primary_outcome_value"].to_numpy(dtype=float)
    primary_regime_labels = _infer_regime_labels(primary_training_frame)
    primary_class_names = (
        {0: "put_edge", 1: positive_class_name}
        if primary_target_effective == PRIMARY_TARGET_OPTION_DIRECTION
        else {0: "bearish", 1: "bullish"}
    )
    primary_health = _class_balance_report(y, primary_regime_labels, class_names=primary_class_names)
    primary_sample_weights = (
        _balanced_sample_weights(y, primary_regime_labels)
        if primary_target_effective == PRIMARY_TARGET_OPTION_DIRECTION
        else np.ones(len(y), dtype=float)
    )
    primary_source_metadata["balance_report"] = primary_health

    log.info(
        "Primary target: %s (%d rows, %.1f%% positive %s)",
        primary_target_effective,
        len(primary_training_frame),
        100 * y.mean(),
        positive_class_name,
    )

    # Time-series aware cross-validation (no lookahead)
    split_count = min(5, max(2, len(X) // 40))
    primary_feature_dates = pd.to_datetime(
        primary_training_frame["date"]
        if "date" in primary_training_frame.columns
        else primary_training_frame.index,
        errors="coerce",
    ).to_numpy()
    primary_label_dates = pd.to_datetime(
        primary_training_frame["primary_label_date"],
        errors="coerce",
    ).to_numpy()
    primary_splits = list(
        purged_date_splits(
            primary_feature_dates,
            primary_label_dates,
            n_splits=split_count,
        )
    )
    auc_scores: list[float] = []
    ic_scores: list[float] = []

    oof_raw_probs = np.full(len(X), np.nan, dtype=float)
    fold_reports: list[dict[str, Any]] = []

    log.info("Running %d-fold purged walk-forward cross-validation …", len(primary_splits))
    for fold, (train_idx, val_idx) in enumerate(primary_splits):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]
        weight_tr = primary_sample_weights[train_idx]
        if len(np.unique(y_tr)) < 2 or len(np.unique(y_val)) < 2:
            fold_reports.append(
                {
                    "fold": fold + 1,
                    "train_rows": int(len(train_idx)),
                    "validation_rows": int(len(val_idx)),
                    "skipped": True,
                    "reason": "single_class_train_or_validation",
                }
            )
            log.warning(
                "  Fold %d skipped due to single-class train/validation window (train classes=%s, validation classes=%s)",
                fold + 1,
                sorted(np.unique(y_tr).tolist()),
                sorted(np.unique(y_val).tolist()),
            )
            continue

        scaler = RobustScaler()
        X_tr_s  = scaler.fit_transform(X_tr)
        X_val_s = scaler.transform(X_val)

        model = lgb.LGBMClassifier(
            n_estimators=400,
            learning_rate=0.05,
            max_depth=5,
            num_leaves=31,
            subsample=0.8,
            colsample_bytree=0.8,
            min_child_samples=20,
            class_weight="balanced",
            random_state=42,
            verbose=-1,
        )
        model.fit(X_tr_s, y_tr, sample_weight=weight_tr)
        probs = model.predict_proba(X_val_s)[:, 1]
        oof_raw_probs[val_idx] = probs
        auc = float(roc_auc_score(y_val, probs))
        # IC = Pearson correlation between predicted proba and realized fwd return
        target_values = realized_target_values[val_idx]
        if len(probs) > 1:
            ic = float(np.corrcoef(probs, target_values)[0, 1])
            if not np.isfinite(ic):
                ic = 0.0
        else:
            ic = 0.0

        auc_scores.append(auc)
        ic_scores.append(ic)
        fold_brier = brier_score_loss(y_val, probs)
        fold_log_loss = _safe_binary_log_loss(y_val, probs)
        fold_reports.append(
            {
                "fold": fold + 1,
                "train_rows": int(len(train_idx)),
                "validation_rows": int(len(val_idx)),
                "auc": round(float(auc), 4),
                "ic": round(float(ic), 4),
                "brier": round(float(fold_brier), 4),
                "log_loss": round(float(fold_log_loss), 4),
                "validation_start": str(pd.Timestamp(primary_feature_dates[val_idx].min()).date()),
                "training_label_end": str(pd.Timestamp(primary_label_dates[train_idx].max()).date()),
            }
        )
        log.info(
            "  Fold %d — AUC: %.4f  IC: %.4f  Brier: %.4f",
            fold + 1,
            auc,
            ic,
            fold_brier,
        )

    mean_auc = float(np.mean(auc_scores)) if auc_scores else float("nan")
    mean_ic = float(np.mean(ic_scores)) if ic_scores else float("nan")
    log.info("Mean AUC: %.4f  |  Mean IC: %.4f", mean_auc, mean_ic)

    valid_oof = np.isfinite(oof_raw_probs)
    oof_y = y[valid_oof]
    has_oof_class_balance = valid_oof.any() and len(np.unique(oof_y)) >= 2
    calibrator = _fit_calibrator(oof_raw_probs[valid_oof], oof_y, calibration_method) if has_oof_class_balance else None
    oof_calibrated = np.full(len(X), np.nan, dtype=float)
    oof_calibrated[valid_oof] = _apply_calibrator(
            oof_raw_probs[valid_oof],
            calibrator,
            calibration_method,
        )
    decision_threshold = _optimal_decision_threshold(oof_calibrated[valid_oof], oof_y)
    raw_brier = round(float(brier_score_loss(oof_y, oof_raw_probs[valid_oof])), 4) if valid_oof.any() else None
    calibrated_brier = round(float(brier_score_loss(oof_y, oof_calibrated[valid_oof])), 4) if valid_oof.any() else None
    raw_log = round(_safe_binary_log_loss(oof_y, oof_raw_probs[valid_oof]), 4) if valid_oof.any() else None
    calibrated_log = round(_safe_binary_log_loss(oof_y, oof_calibrated[valid_oof]), 4) if valid_oof.any() else None
    calibration_metrics = {
        "method": calibration_method,
        "decision_threshold": decision_threshold,
        "oof_rows": int(valid_oof.sum()),
        "raw_brier": raw_brier,
        "calibrated_brier": calibrated_brier,
        "raw_log_loss": raw_log,
        "calibrated_log_loss": calibrated_log,
        "probability_buckets": _probability_buckets(
            oof_calibrated[valid_oof],
            oof_y,
            realized_target_values[valid_oof],
            outcome_label=primary_outcome_label,
        ),
    }
    observability = {
        "coverage": _coverage_report(primary_training_frame),
        "segments": _segment_report(
            oof_calibrated[valid_oof],
            y[valid_oof],
            realized_target_values[valid_oof],
            primary_training_frame.iloc[np.where(valid_oof)[0]],
            outcome_label=primary_outcome_label,
            class_names=primary_class_names,
            decision_threshold=decision_threshold,
        ),
        "feature_drift_baseline": _drift_baseline(primary_training_frame, available),
        "primary_target": {
            "mode": primary_target_effective,
            "description": target_description,
            "positive_class_name": positive_class_name,
            "decision_threshold": decision_threshold,
            "rows": int(len(primary_training_frame)),
            "source_metadata": primary_source_metadata,
            "balance_report": primary_health,
        },
        "event_feature_store": {
            "configured": bool(event_features_path or not event_feature_frame.empty),
            "rows": int(len(event_feature_frame)),
            "symbols": int(event_feature_frame["symbol"].nunique()) if not event_feature_frame.empty else 0,
            "feature_cols": event_feature_cols,
            "activation": _event_feature_activation_report(primary_training_frame, event_feature_cols),
        },
    }

    # ── Final model: train on all data ──
    log.info("Training final model on full dataset …")
    final_scaler = RobustScaler()
    X_final = final_scaler.fit_transform(X)

    final_model = lgb.LGBMClassifier(
        n_estimators=500,
        learning_rate=0.04,
        max_depth=5,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=20,
        class_weight="balanced",
        random_state=42,
        verbose=-1,
    )
    final_model.fit(X_final, y, sample_weight=primary_sample_weights)

    side_threshold = 0.01
    side_target = "underlying_forward_return"
    side_source_metadata: dict[str, Any] = {}
    side_training_frame = combined.copy()
    side_training_dates = pd.to_datetime(side_training_frame.index).to_numpy()
    side_training_label_dates = pd.to_datetime(
        side_training_frame["fwd_5d_label_date"],
        errors="coerce",
    ).to_numpy()
    if option_outcome_inputs:
        option_labels, side_source_metadata = _load_option_outcome_labels(option_outcome_inputs, cutoff=cutoff)
        if not option_labels.empty:
            mergeable = combined.copy()
            feature_dates = pd.to_datetime(mergeable.index)
            if getattr(feature_dates, "tz", None) is not None:
                feature_dates = feature_dates.tz_convert(None)
            mergeable["date"] = feature_dates.normalize()
            merged = mergeable.merge(
                option_labels,
                on=["symbol", "date"],
                how="inner",
                validate="many_to_one",
            )
            if len(merged) >= 80 and merged["side_label_id"].nunique() >= 2:
                side_training_frame = merged
                side_training_dates = pd.to_datetime(merged["date"]).to_numpy()
                side_training_label_dates = pd.to_datetime(merged["label_date"]).to_numpy()
                side_y = merged["side_label_id"].to_numpy(dtype=int)
                side_target = "strict_real_option_payoff"
            else:
                log.warning(
                    "Option-outcome side labels were insufficient (%d rows, %d classes); falling back to underlying side target.",
                    len(merged),
                    int(merged["side_label_id"].nunique()) if "side_label_id" in merged else 0,
                )
                fwd = combined["fwd_5d_return"].values
                side_y = np.where(fwd > side_threshold, 2, np.where(fwd < -side_threshold, 0, 1))
        else:
            fwd = combined["fwd_5d_return"].values
            side_y = np.where(fwd > side_threshold, 2, np.where(fwd < -side_threshold, 0, 1))
    else:
        fwd = combined["fwd_5d_return"].values
        side_y = np.where(fwd > side_threshold, 2, np.where(fwd < -side_threshold, 0, 1))

    side_X_raw = side_training_frame[available].values
    side_scaler = RobustScaler()
    side_X = side_scaler.fit_transform(side_X_raw)
    side_model = lgb.LGBMClassifier(
        objective="multiclass",
        num_class=3,
        n_estimators=450,
        learning_rate=0.04,
        max_depth=5,
        num_leaves=31,
        subsample=0.8,
        colsample_bytree=0.8,
        min_child_samples=20,
        class_weight="balanced",
        random_state=43,
        verbose=-1,
    )
    side_model.fit(side_X, side_y)
    side_preds = side_model.predict(side_X)
    side_cv = _side_cv_report(
        side_X_raw,
        side_y,
        side_training_dates,
        side_training_label_dates,
    )
    side_training_metrics = {
        "target": side_target,
        "label_threshold_abs_fwd_5d_return": side_threshold if side_target == "underlying_forward_return" else None,
        "classes": SIDE_CLASS_MAP,
        "class_counts": {
            SIDE_CLASS_MAP[int(cls)]: int((side_y == cls).sum())
            for cls in sorted(set(side_y.tolist()))
        },
        "rows": int(len(side_y)),
        "training_balanced_accuracy": round(float(balanced_accuracy_score(side_y, side_preds)), 4),
        "cross_validation": side_cv,
        "source_metadata": side_source_metadata,
    }

    # Save artifacts
    joblib.dump(final_model, MODEL_PATH)
    joblib.dump(
        {
            "model": side_model,
            "scaler": side_scaler,
            "feature_cols": available,
            "class_map": SIDE_CLASS_MAP,
            "target": side_target,
            "label_threshold_abs_fwd_5d_return": side_threshold if side_target == "underlying_forward_return" else None,
            "source_metadata": side_source_metadata,
        },
        SIDE_MODEL_PATH,
    )
    joblib.dump(
        {
            "scaler": final_scaler,
            "feature_cols": available,
            "calibrator": calibrator,
            "calibration_method": calibration_method,
            "decision_threshold": decision_threshold,
            "primary_target": primary_target_effective,
            "target_description": target_description,
            "positive_class_name": positive_class_name,
        },
        SCALER_PATH,
    )
    log.info("✅  Model saved  → %s", MODEL_PATH)
    log.info("✅  Side model saved → %s", SIDE_MODEL_PATH)
    log.info("✅  Scaler saved → %s", SCALER_PATH)

    importances = sorted(
        zip(available, final_model.feature_importances_),
        key=lambda x: x[1], reverse=True
    )
    model_card = {
        "artifact": "scout_model",
        "version": 3,
        "model_card_schema_version": 2,
        "trained_at": date.today().isoformat(),
        "training_cutoff": cutoff.isoformat(),
        "years_requested": years,
        "symbols_requested": symbols,
        "symbols_trained": sorted({str(frame["symbol"].iloc[0]) for frame in all_features if "symbol" in frame}),
        "rows": int(len(X)),
        "positive_label_rate": round(float(y.mean()), 4),
        "target": target_description,
        "primary_target": {
            "mode": primary_target_effective,
            "description": target_description,
            "positive_class_name": positive_class_name,
            "outcome_value_field": primary_outcome_label,
            "decision_threshold": decision_threshold,
            "rows": int(len(primary_training_frame)),
            "source_metadata": primary_source_metadata,
            "balance_report": primary_health,
        },
        "side_aware_output": {
            "mode": "trained_option_payoff_three_class"
            if side_target == "strict_real_option_payoff"
            else "trained_underlying_three_class",
            "classes": ["call_edge", "put_edge", "no_trade"],
            "probability_fields": ["call_edge_prob", "put_edge_prob", "no_trade_prob"],
            "derived_fields": ["direction", "scout_score"],
            "label_definition": (
                "call_edge/put_edge/no_trade from strict-real option PnL by symbol/date"
                if side_target == "strict_real_option_payoff"
                else "call_edge if fwd_5d_return > +1%, put_edge if fwd_5d_return < -1%, otherwise no_trade"
            ),
            "decision_contract": (
                "When OROGRAPHIC_SIDE_MODEL_MODE=active, Scout direction and scout_score are derived from "
                "the three-class probabilities and no_trade becomes a first-class abstain."
            ),
            "training_metrics": side_training_metrics,
        },
        "activation_policy": {
            "default": "active",
            "active_env": "OROGRAPHIC_SIDE_MODEL_MODE=active",
            "shadow_behavior": "Set OROGRAPHIC_SIDE_MODEL_MODE=shadow to observe disagreements and no-trade vetoes without changing live routing.",
            "active_behavior": "Three-class Scout becomes the canonical call/put/no-trade policy and may abstain before Forge.",
        },
        "feature_cols": available,
        "feature_importances": [
            {"feature": feature, "importance": int(importance)}
            for feature, importance in importances
        ],
        "cross_validation": {
            "split_policy": "date_grouped_purged_by_outcome_date",
            "folds": fold_reports,
            "mean_auc": round(mean_auc, 4) if np.isfinite(mean_auc) else None,
            "mean_ic": round(mean_ic, 4) if np.isfinite(mean_ic) else None,
        },
        "calibration": calibration_metrics,
        "observability": observability,
        "artifacts": {
            "model_path": _artifact_path_for_card(MODEL_PATH),
            "model_sha256": _sha256_file(MODEL_PATH),
            "side_model_path": _artifact_path_for_card(SIDE_MODEL_PATH),
            "side_model_sha256": _sha256_file(SIDE_MODEL_PATH),
            "scaler_path": _artifact_path_for_card(SCALER_PATH),
            "scaler_sha256": _sha256_file(SCALER_PATH),
        },
        "limitations": [
            (
                "Primary Scout target is strict-real option-direction edge; no-trade is a first-class Scout abstain in the production default."
                if primary_target_effective == PRIMARY_TARGET_OPTION_DIRECTION
                else "Directional Scout target is underlying stock return, not option payoff."
            ),
            "Payoff-aware contract ranking is handled by the second-stage payoff model when available.",
        ],
    }
    MODEL_CARD_PATH.write_text(json.dumps(model_card, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    log.info("✅  Model card saved → %s", MODEL_CARD_PATH)

    # Final classification report on training data (sanity check, not a backtest)
    preds = final_model.predict(X_final)
    print("\n" + "═" * 50)
    print("  SCOUT MODEL TRAINING SUMMARY")
    print("═" * 50)
    print(f"  Symbols trained:  {len(all_features)}")
    print(f"  Total samples:    {len(X)}")
    print(f"  Features:         {len(available)}")
    print(f"  Primary target:   {primary_target_effective}")
    print(f"  Mean AUC (CV):    {np.mean(auc_scores):.4f}")
    print(f"  Mean IC  (CV):    {np.mean(ic_scores):.4f}")
    print(f"  Calibration:      {calibration_method}")
    print(f"  OOF Brier:        {calibration_metrics['calibrated_brier']:.4f}")
    print(f"  Side model BAcc:  {side_training_metrics['training_balanced_accuracy']:.4f}")
    print()
    target_names = ["put_edge", positive_class_name] if primary_target_effective == PRIMARY_TARGET_OPTION_DIRECTION else ["bearish", "bullish"]
    print(classification_report(y, preds, target_names=target_names))
    print(f"\n  Feature importances (top 10):")
    for feat, imp in importances[:10]:
        print(f"    {feat:<25s}  {imp:>6.0f}")
    print("═" * 50 + "\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train the Orographic Scout ML model")
    parser.add_argument("--years",   type=int, default=2, help="Years of training history (default: 2)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols to train on (default: engine/sample_universe.txt)")
    parser.add_argument("--cutoff",  type=str, default="2026-01-01",
                        help="Cutoff date for training (default: 2026-01-01)")
    parser.add_argument(
        "--calibration",
        choices=["isotonic", "platt", "none"],
        default="isotonic",
        help="Walk-forward probability calibration method (default: isotonic)",
    )
    parser.add_argument(
        "--option-outcome-input",
        action="append",
        type=Path,
        default=None,
        help="Strict-real backtest JSON used to train Scout targets from option payoff outcomes. May be repeated.",
    )
    parser.add_argument(
        "--primary-target",
        choices=[PRIMARY_TARGET_UNDERLYING, PRIMARY_TARGET_OPTION_DIRECTION],
        default=None,
        help="Primary Scout target. Defaults to strict-real option direction when option-outcome inputs are available.",
    )
    parser.add_argument(
        "--event-features-path",
        type=Path,
        default=None,
        help="Optional canonical event-feature store (.parquet/.csv/.json/.jsonl).",
    )
    args = parser.parse_args()

    cutoff_dt = date.fromisoformat(args.cutoff) if args.cutoff else date.today()
    option_inputs = args.option_outcome_input or _default_option_outcome_inputs()
    primary_target = (
        args.primary_target
        or (PRIMARY_TARGET_OPTION_DIRECTION if option_inputs else PRIMARY_TARGET_UNDERLYING)
    )

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else TRAINING_UNIVERSE
    )
    train(
        symbols,
        args.years,
        cutoff_dt,
        calibration_method=args.calibration,
        option_outcome_inputs=option_inputs,
        primary_target=primary_target,
        event_features_path=args.event_features_path,
    )


if __name__ == "__main__":
    main()
