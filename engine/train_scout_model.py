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
from sklearn.model_selection import TimeSeriesSplit
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

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "orographic" / "models"
MODEL_PATH = MODEL_DIR / "scout_model.pkl"
SIDE_MODEL_PATH = MODEL_DIR / "scout_side_model.pkl"
SCALER_PATH = MODEL_DIR / "scout_scaler.pkl"
MODEL_CARD_PATH = MODEL_DIR / "scout_model_card.json"
TRAINING_UNIVERSE_FILE = Path(__file__).with_name("sample_universe.txt")


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


def build_feature_matrix(df: pd.DataFrame, spy_df: pd.DataFrame | None = None) -> pd.DataFrame:
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

    # ── Forward return label (5d) ──
    features["fwd_5d_return"] = close.pct_change(5).shift(-5)
    features["label"] = (features["fwd_5d_return"] > 0).astype(int)

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


def _probability_buckets(probs: np.ndarray, y: np.ndarray, fwd_returns: np.ndarray, buckets: int = 10) -> list[dict[str, float | int | str]]:
    frame = pd.DataFrame(
        {
            "prob": probs,
            "label": y,
            "fwd_return": fwd_returns,
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
                "avg_fwd_5d_return": round(float(group["fwd_return"].mean()), 4),
            }
        )
    return rows


def _segment_report(
    probs: np.ndarray,
    y: np.ndarray,
    fwd_returns: np.ndarray,
    combined: pd.DataFrame,
) -> dict[str, Any]:
    frame = pd.DataFrame(
        {
            "prob": probs,
            "label": y,
            "fwd_return": fwd_returns,
            "predicted_side": np.where(probs >= 0.5, "call", "put"),
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
    report: dict[str, Any] = {"by_side": {}, "by_regime": {}, "by_side_regime": {}}
    for key, target in (("predicted_side", "by_side"), ("regime", "by_regime")):
        for value, group in frame.groupby(key):
            if len(group) < 10:
                continue
            report[target][str(value)] = {
                "rows": int(len(group)),
                "positive_rate": round(float(group["label"].mean()), 4),
                "avg_pred_prob": round(float(group["prob"].mean()), 4),
                "brier": round(float(brier_score_loss(group["label"], group["prob"])), 4),
                "avg_fwd_5d_return": round(float(group["fwd_return"].mean()), 4),
            }
    for (side, regime), group in frame.groupby(["predicted_side", "regime"]):
        if len(group) < 10:
            continue
        report["by_side_regime"][f"{side}_{regime}"] = {
            "rows": int(len(group)),
            "positive_rate": round(float(group["label"].mean()), 4),
            "avg_pred_prob": round(float(group["prob"].mean()), 4),
            "brier": round(float(brier_score_loss(group["label"], group["prob"])), 4),
            "avg_fwd_5d_return": round(float(group["fwd_return"].mean()), 4),
        }
    return report


def _coverage_report(combined: pd.DataFrame) -> dict[str, Any]:
    symbol_counts = combined["symbol"].value_counts().to_dict() if "symbol" in combined else {}
    return {
        "symbols": int(len(symbol_counts)),
        "rows": int(len(combined)),
        "rows_by_symbol": {str(k): int(v) for k, v in sorted(symbol_counts.items())},
        "date_start": str(combined.index.min().date()) if len(combined) else None,
        "date_end": str(combined.index.max().date()) if len(combined) else None,
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


def train(
    symbols: list[str],
    years: int,
    cutoff: date | None = None,
    *,
    calibration_method: str = "isotonic",
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

    all_features: list[pd.DataFrame] = []

    for symbol in symbols:
        try:
            df = fetch_history(symbol, years)
            feat = build_feature_matrix(df, spy_df)
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
    # Apply cutoff: only use rows strictly <= cutoff date
    # (Forward return label was computed by build_feature_matrix using shift(-5))
    mask = combined.index.date <= cutoff
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

    available = [c for c in FEATURE_COLS if c in combined.columns]
    X = combined[available].values
    y = combined["label"].values

    log.info("Label distribution: %.1f%% positive (bullish)", 100 * y.mean())

    # Time-series aware cross-validation (no lookahead)
    tscv = TimeSeriesSplit(n_splits=5)
    auc_scores: list[float] = []
    ic_scores: list[float] = []

    oof_raw_probs = np.full(len(X), np.nan, dtype=float)
    fold_reports: list[dict[str, float | int]] = []

    log.info("Running 5-fold walk-forward cross-validation …")
    for fold, (train_idx, val_idx) in enumerate(tscv.split(X)):
        X_tr, X_val = X[train_idx], X[val_idx]
        y_tr, y_val = y[train_idx], y[val_idx]

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
        model.fit(X_tr_s, y_tr)
        probs = model.predict_proba(X_val_s)[:, 1]
        oof_raw_probs[val_idx] = probs
        auc = roc_auc_score(y_val, probs)
        # IC = Pearson correlation between predicted proba and realized fwd return
        fwd_returns = combined["fwd_5d_return"].values[val_idx]
        ic = float(np.corrcoef(probs, fwd_returns)[0, 1]) if len(probs) > 1 else 0.0

        auc_scores.append(auc)
        ic_scores.append(ic)
        fold_brier = brier_score_loss(y_val, probs)
        fold_log_loss = log_loss(y_val, np.clip(probs, 1e-6, 1 - 1e-6))
        fold_reports.append(
            {
                "fold": fold + 1,
                "train_rows": int(len(train_idx)),
                "validation_rows": int(len(val_idx)),
                "auc": round(float(auc), 4),
                "ic": round(float(ic), 4),
                "brier": round(float(fold_brier), 4),
                "log_loss": round(float(fold_log_loss), 4),
            }
        )
        log.info(
            "  Fold %d — AUC: %.4f  IC: %.4f  Brier: %.4f",
            fold + 1,
            auc,
            ic,
            fold_brier,
        )

    log.info("Mean AUC: %.4f  |  Mean IC: %.4f", np.mean(auc_scores), np.mean(ic_scores))

    valid_oof = np.isfinite(oof_raw_probs)
    calibrator = _fit_calibrator(oof_raw_probs[valid_oof], y[valid_oof], calibration_method)
    oof_calibrated = np.full(len(X), np.nan, dtype=float)
    oof_calibrated[valid_oof] = _apply_calibrator(
        oof_raw_probs[valid_oof],
        calibrator,
        calibration_method,
    )
    calibration_metrics = {
        "method": calibration_method,
        "oof_rows": int(valid_oof.sum()),
        "raw_brier": round(float(brier_score_loss(y[valid_oof], oof_raw_probs[valid_oof])), 4),
        "calibrated_brier": round(float(brier_score_loss(y[valid_oof], oof_calibrated[valid_oof])), 4),
        "raw_log_loss": round(float(log_loss(y[valid_oof], np.clip(oof_raw_probs[valid_oof], 1e-6, 1 - 1e-6))), 4),
        "calibrated_log_loss": round(float(log_loss(y[valid_oof], np.clip(oof_calibrated[valid_oof], 1e-6, 1 - 1e-6))), 4),
        "probability_buckets": _probability_buckets(
            oof_calibrated[valid_oof],
            y[valid_oof],
            combined["fwd_5d_return"].values[valid_oof],
        ),
    }
    observability = {
        "coverage": _coverage_report(combined),
        "segments": _segment_report(
            oof_calibrated[valid_oof],
            y[valid_oof],
            combined["fwd_5d_return"].values[valid_oof],
            combined.iloc[np.where(valid_oof)[0]],
        ),
        "feature_drift_baseline": _drift_baseline(combined, available),
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
    final_model.fit(X_final, y)

    side_threshold = 0.01
    fwd = combined["fwd_5d_return"].values
    side_y = np.where(fwd > side_threshold, 2, np.where(fwd < -side_threshold, 0, 1))
    side_model = lgb.LGBMClassifier(
        objective="multiclass",
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
    side_model.fit(X_final, side_y)
    side_preds = side_model.predict(X_final)
    side_training_metrics = {
        "label_threshold_abs_fwd_5d_return": side_threshold,
        "classes": SIDE_CLASS_MAP,
        "class_counts": {
            SIDE_CLASS_MAP[int(cls)]: int((side_y == cls).sum())
            for cls in sorted(set(side_y.tolist()))
        },
        "training_balanced_accuracy": round(float(balanced_accuracy_score(side_y, side_preds)), 4),
    }

    # Save artifacts
    joblib.dump(final_model, MODEL_PATH)
    joblib.dump(
        {
            "model": side_model,
            "scaler": final_scaler,
            "feature_cols": available,
            "class_map": SIDE_CLASS_MAP,
            "label_threshold_abs_fwd_5d_return": side_threshold,
        },
        SIDE_MODEL_PATH,
    )
    joblib.dump(
        {
            "scaler": final_scaler,
            "feature_cols": available,
            "calibrator": calibrator,
            "calibration_method": calibration_method,
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
        "target": "probability that forward 5-day underlying return is positive",
        "side_aware_output": {
            "mode": "trained_three_class",
            "classes": ["call_edge", "put_edge", "no_trade"],
            "label_definition": "call_edge if fwd_5d_return > +1%, put_edge if fwd_5d_return < -1%, otherwise no_trade",
            "training_metrics": side_training_metrics,
        },
        "feature_cols": available,
        "feature_importances": [
            {"feature": feature, "importance": int(importance)}
            for feature, importance in importances
        ],
        "cross_validation": {
            "folds": fold_reports,
            "mean_auc": round(float(np.mean(auc_scores)), 4),
            "mean_ic": round(float(np.mean(ic_scores)), 4),
        },
        "calibration": calibration_metrics,
        "observability": observability,
        "artifacts": {
            "model_path": str(MODEL_PATH),
            "model_sha256": _sha256_file(MODEL_PATH),
            "side_model_path": str(SIDE_MODEL_PATH),
            "side_model_sha256": _sha256_file(SIDE_MODEL_PATH),
            "scaler_path": str(SCALER_PATH),
            "scaler_sha256": _sha256_file(SCALER_PATH),
        },
        "limitations": [
            "Directional Scout target is underlying stock return, not option payoff.",
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
    print(f"  Mean AUC (CV):    {np.mean(auc_scores):.4f}")
    print(f"  Mean IC  (CV):    {np.mean(ic_scores):.4f}")
    print(f"  Calibration:      {calibration_method}")
    print(f"  OOF Brier:        {calibration_metrics['calibrated_brier']:.4f}")
    print(f"  Side model BAcc:  {side_training_metrics['training_balanced_accuracy']:.4f}")
    print()
    print(classification_report(y, preds, target_names=["bearish", "bullish"]))
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
    args = parser.parse_args()

    cutoff_dt = date.fromisoformat(args.cutoff) if args.cutoff else date.today()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else TRAINING_UNIVERSE
    )
    train(symbols, args.years, cutoff_dt, calibration_method=args.calibration)


if __name__ == "__main__":
    main()
