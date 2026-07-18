from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from functools import lru_cache
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_EVENT_FEATURES_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "event_features" / "daily_event_features.parquet"
)
EVENT_FEATURES_PATH_ENV = "OROGRAPHIC_EVENT_FEATURES_PATH"
GLOBAL_EVENT_SYMBOL = "__GLOBAL__"
EVENT_FEATURE_COLUMNS = [
    "fnspid_news_volume_1d",
    "fnspid_news_volume_3d",
    "fnspid_sentiment_mean",
    "fnspid_sentiment_std",
    "fnspid_novelty_score",
    "fnspid_catalyst_density",
    "edt_event_intensity",
    "edt_acquisition_score",
    "edt_clinical_trial_score",
    "edt_dividend_score",
    "edt_guidance_score",
    "edt_new_contract_score",
    "edt_repurchase_score",
    "edt_split_score",
    "edt_financing_score",
    "edt_violation_score",
    "edt_risk_warning_score",
    "edt_rating_action_score",
    "mirai_macro_shock_score",
    "mirai_geopolitical_risk_score",
    "mirai_commodity_risk_score",
    "mirai_risk_on_score",
    "mirai_risk_off_score",
    "sec_filing_count_1d",
    "sec_filing_count_5d",
    "sec_8k_count",
    "sec_10q_count",
    "sec_10k_count",
    "sec_offering_count",
    "sec_capital_markets_count",
    "sec_debt_markets_count",
    "sec_fwp_count",
    "sec_capital_markets_noise_count",
    "sec_proxy_count",
    "sec_ownership_count",
    "sec_insider_count",
    "sec_amendment_count",
    "sec_8k_flag",
    "sec_10q_flag",
    "sec_10k_flag",
    "sec_offering_flag",
    "sec_proxy_flag",
    "sec_signal_count_1d",
    "sec_signal_count_5d",
    "sec_material_event_score",
    "sec_material_event_score_5d",
    "sec_noise_count_1d",
    "sec_signal_ratio",
    "stocktwits_message_count",
    "stocktwits_bullish_ratio",
    "stocktwits_bearish_ratio",
    "stocktwits_emotion_intensity",
    "narrative_attention_1d",
    "narrative_attention_3d",
    "narrative_attention_acceleration_3d",
    "narrative_source_diversity_1d",
    "narrative_duplicate_ratio_1d",
    "narrative_novelty_mean_1d",
    "narrative_directional_intensity_1d",
    "narrative_confirmation_score_1d",
    "narrative_hype_pressure",
]
NON_NUMERIC_EVENT_COLUMNS = ["dataset_tags"]
STANDARD_EVENT_COLUMNS = ["symbol", "date", *EVENT_FEATURE_COLUMNS, *NON_NUMERIC_EVENT_COLUMNS]


@dataclass(frozen=True)
class EventFeatureSnapshot:
    symbol: str
    as_of: date | None
    values: dict[str, float]
    dataset_tags: str = ""

    def to_feature_dict(self) -> dict[str, float]:
        return {column: float(self.values.get(column, 0.0)) for column in EVENT_FEATURE_COLUMNS}

    def to_context_dict(self) -> dict[str, Any]:
        return {
            "symbol": self.symbol,
            "date": self.as_of.isoformat() if self.as_of else None,
            "dataset_tags": self.dataset_tags,
            **self.to_feature_dict(),
        }


def _empty_event_feature_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=STANDARD_EVENT_COLUMNS)


def _collapse_dataset_tags(values: list[str]) -> str:
    tags: list[str] = []
    for value in values:
        for token in str(value or "").split(","):
            cleaned = token.strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
    return ",".join(tags)


def _normalize_event_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_event_feature_frame()

    normalized = frame.copy()
    normalized.columns = [str(column).strip() for column in normalized.columns]
    if "symbol" not in normalized.columns or "date" not in normalized.columns:
        return _empty_event_feature_frame()

    normalized["symbol"] = normalized["symbol"].astype(str).str.upper().str.strip()
    date_series = pd.to_datetime(normalized["date"], errors="coerce")
    if getattr(date_series.dt, "tz", None) is not None:
        date_series = date_series.dt.tz_convert(None)
    normalized["date"] = date_series.dt.normalize()
    normalized = normalized.dropna(subset=["symbol", "date"]).copy()

    for column in EVENT_FEATURE_COLUMNS:
        if column not in normalized.columns:
            normalized[column] = 0.0
        normalized[column] = pd.to_numeric(normalized[column], errors="coerce").fillna(0.0)

    dataset_tags = normalized["dataset_tags"] if "dataset_tags" in normalized.columns else ""
    normalized["dataset_tags"] = dataset_tags.astype(str).fillna("")

    normalized = normalized[STANDARD_EVENT_COLUMNS].sort_values(["symbol", "date"]).drop_duplicates(
        subset=["symbol", "date"],
        keep="last",
    )
    normalized.reset_index(drop=True, inplace=True)
    return normalized


def _resolve_event_features_path(path: Path | str | None = None) -> Path | None:
    explicit = path or os.getenv(EVENT_FEATURES_PATH_ENV)
    if not explicit:
        return DEFAULT_EVENT_FEATURES_PATH if DEFAULT_EVENT_FEATURES_PATH.exists() else None
    candidate = Path(explicit)
    if candidate.is_dir():
        for name in (
            "daily_event_features.parquet",
            "daily_event_features.csv",
            "daily_event_features.json",
            "daily_event_features.jsonl",
        ):
            resolved = candidate / name
            if resolved.exists():
                return resolved
        return None
    return candidate if candidate.exists() else None


@lru_cache(maxsize=4)
def _load_event_feature_frame_cached(path_key: str) -> pd.DataFrame:
    path = Path(path_key)
    suffix = path.suffix.lower()
    if suffix == ".parquet":
        frame = pd.read_parquet(path)
    elif suffix == ".csv":
        frame = pd.read_csv(path)
    elif suffix == ".json":
        frame = pd.read_json(path)
    elif suffix == ".jsonl":
        frame = pd.read_json(path, lines=True)
    else:
        raise ValueError(f"Unsupported event feature file format: {path}")
    return _normalize_event_frame(frame)


def load_event_feature_frame(path: Path | str | None = None) -> pd.DataFrame:
    resolved = _resolve_event_features_path(path)
    if resolved is None:
        return _empty_event_feature_frame()
    return _load_event_feature_frame_cached(str(resolved))


def build_event_feature_history(
    symbol: str,
    index: pd.Index,
    event_feature_frame: pd.DataFrame | None,
) -> pd.DataFrame:
    aligned_index = pd.to_datetime(index)
    if getattr(aligned_index, "tz", None) is not None:
        aligned_index = aligned_index.tz_convert(None)
    aligned_dates = aligned_index.normalize()
    history = pd.DataFrame(index=index)
    for column in EVENT_FEATURE_COLUMNS:
        history[column] = 0.0
    history["dataset_tags"] = ""
    if event_feature_frame is None or event_feature_frame.empty:
        return history

    frames: list[pd.DataFrame] = []
    global_frame = event_feature_frame.loc[event_feature_frame["symbol"] == GLOBAL_EVENT_SYMBOL].copy()
    if not global_frame.empty:
        frames.append(global_frame)
    symbol_frame = event_feature_frame.loc[event_feature_frame["symbol"] == symbol.upper()].copy()
    if not symbol_frame.empty:
        frames.append(symbol_frame)
    if not frames:
        return history
    combined = pd.concat(frames, ignore_index=True).sort_values(["date", "symbol"])
    lookup = pd.Series(aligned_dates, index=index)
    merged = pd.DataFrame(index=index)
    for column in EVENT_FEATURE_COLUMNS:
        grouped = combined.groupby("date")[column].sum() if column in combined.columns else pd.Series(dtype=float)
        merged[column] = lookup.map(grouped).fillna(0.0).astype(float).to_numpy()
    tag_grouped = combined.groupby("date")["dataset_tags"].apply(lambda values: _collapse_dataset_tags(list(values)))
    merged["dataset_tags"] = lookup.map(tag_grouped).fillna("").astype(str).to_numpy()
    return merged


def latest_event_feature_snapshot(
    symbol: str,
    event_feature_frame: pd.DataFrame | None,
    *,
    as_of: date | datetime | pd.Timestamp | None = None,
) -> EventFeatureSnapshot | None:
    if event_feature_frame is None or event_feature_frame.empty:
        return None

    lookup_date = pd.to_datetime(as_of, errors="coerce")
    if pd.isna(lookup_date):
        lookup_date = None
    elif getattr(lookup_date, "tzinfo", None) is not None:
        lookup_date = lookup_date.tz_convert(None)
    frames: list[pd.DataFrame] = []
    global_frame = event_feature_frame.loc[event_feature_frame["symbol"] == GLOBAL_EVENT_SYMBOL].copy()
    if not global_frame.empty:
        frames.append(global_frame)
    symbol_frame = event_feature_frame.loc[event_feature_frame["symbol"] == symbol.upper()].copy()
    if not symbol_frame.empty:
        frames.append(symbol_frame)
    if not frames:
        return None
    combined = pd.concat(frames, ignore_index=True)
    if lookup_date is not None:
        combined = combined.loc[combined["date"] <= lookup_date.normalize()]
        if combined.empty:
            return None
    latest_date = combined["date"].max()
    same_day = combined.loc[combined["date"] == latest_date].copy()
    snapshot_date = pd.to_datetime(latest_date, errors="coerce")
    normalized_date = snapshot_date.date() if not pd.isna(snapshot_date) else None
    values: dict[str, float] = {}
    for column in EVENT_FEATURE_COLUMNS:
        series = same_day[column] if column in same_day.columns else pd.Series([0.0], dtype=float)
        values[column] = float(pd.to_numeric(series, errors="coerce").fillna(0.0).sum())
    return EventFeatureSnapshot(
        symbol=symbol.upper(),
        as_of=normalized_date,
        values=values,
        dataset_tags=_collapse_dataset_tags(same_day.get("dataset_tags", pd.Series(dtype=str)).astype(str).tolist()),
    )


def write_event_feature_frame(rows: list[dict[str, Any]], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = _normalize_event_frame(pd.DataFrame(rows))
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
        return
    if path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
        return
    if path.suffix.lower() == ".json":
        path.write_text(frame.to_json(orient="records"), encoding="utf-8")
        return
    if path.suffix.lower() == ".jsonl":
        payload = "\n".join(json.dumps(row, sort_keys=True) for row in frame.to_dict(orient="records"))
        path.write_text(payload + ("\n" if payload else ""), encoding="utf-8")
        return
    raise ValueError(f"Unsupported event feature file format: {path}")
