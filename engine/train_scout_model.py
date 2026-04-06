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
import logging
import sys
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import joblib
import yfinance as yf
from sklearn.model_selection import TimeSeriesSplit
from sklearn.metrics import roc_auc_score, classification_report
from sklearn.preprocessing import RobustScaler
from sklearn.pipeline import Pipeline

try:
    import lightgbm as lgb
except ImportError:
    print("ERROR: lightgbm not installed. Run: pip install lightgbm")
    sys.exit(1)

logging.basicConfig(level=logging.INFO, format="%(asctime)s  %(levelname)-7s  %(message)s")
log = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "orographic" / "models"
MODEL_PATH = MODEL_DIR / "scout_model.pkl"
SCALER_PATH = MODEL_DIR / "scout_scaler.pkl"

TRAINING_UNIVERSE = [
    "SPY", "QQQ", "IWM", "AAPL", "MSFT", "NVDA", "GOOGL", "AMZN",
    "META", "TSLA", "JPM", "BAC", "GS", "V", "MA",
    "AVGO", "AMD", "INTC", "QCOM", "TXN",
    "LLY", "UNH", "JNJ", "ABBV", "PFE",
    "XOM", "CVX", "COP",
    "COST", "HD", "WMT", "MCD", "NKE",
    "CRM", "ORCL", "ADBE", "CSCO", "IBM",
    "BRK-B", "PG", "KO", "PEP",
    "NFLX", "DIS", "BA", "GE",
]


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

def train(symbols: list[str], years: int) -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

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
    log.info("Combined dataset: %d rows across %d symbols", len(combined), len(all_features))

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
        auc = roc_auc_score(y_val, probs)
        # IC = Pearson correlation between predicted proba and realized fwd return
        fwd_returns = combined["fwd_5d_return"].values[val_idx]
        ic = float(np.corrcoef(probs, fwd_returns)[0, 1]) if len(probs) > 1 else 0.0

        auc_scores.append(auc)
        ic_scores.append(ic)
        log.info("  Fold %d — AUC: %.4f  IC: %.4f", fold + 1, auc, ic)

    log.info("Mean AUC: %.4f  |  Mean IC: %.4f", np.mean(auc_scores), np.mean(ic_scores))

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

    # Save artifacts
    joblib.dump(final_model, MODEL_PATH)
    joblib.dump({"scaler": final_scaler, "feature_cols": available}, SCALER_PATH)
    log.info("✅  Model saved  → %s", MODEL_PATH)
    log.info("✅  Scaler saved → %s", SCALER_PATH)

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
    print()
    print(classification_report(y, preds, target_names=["bearish", "bullish"]))
    print(f"\n  Feature importances (top 10):")
    importances = sorted(
        zip(available, final_model.feature_importances_),
        key=lambda x: x[1], reverse=True
    )
    for feat, imp in importances[:10]:
        print(f"    {feat:<25s}  {imp:>6.0f}")
    print("═" * 50 + "\n")


# ── Entry point ──────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="Train the Orographic Scout ML model")
    parser.add_argument("--years",   type=int, default=2, help="Years of training history (default: 2)")
    parser.add_argument("--symbols", type=str, default=None,
                        help="Comma-separated symbols to train on (default: full universe)")
    args = parser.parse_args()

    symbols = (
        [s.strip().upper() for s in args.symbols.split(",") if s.strip()]
        if args.symbols
        else TRAINING_UNIVERSE
    )
    train(symbols, args.years)


if __name__ == "__main__":
    main()
