from __future__ import annotations

from collections.abc import Iterable
import json
import math
import re
from pathlib import Path
from typing import Any

import pandas as pd

from .event_features import GLOBAL_EVENT_SYMBOL, load_event_feature_frame, write_event_feature_frame

FNSPID_DATASET_TAG = "fnspid"
EDT_DATASET_TAG = "edt"
MIRAI_DATASET_TAG = "mirai"
STOCKEMOTIONS_DATASET_TAG = "stockemotions"
SEC_FILINGS_DATASET_TAG = "sec_filings"
CANDIDATE_HEADLINE_WORDS = (
    "acquisition",
    "acquire",
    "buyback",
    "clinical",
    "contract",
    "dividend",
    "downgrade",
    "earnings",
    "financing",
    "fraud",
    "guidance",
    "investigation",
    "lawsuit",
    "merger",
    "offering",
    "product",
    "rating",
    "repurchase",
    "split",
    "warning",
)
CANDIDATE_HEADLINE_PATTERN = re.compile(r"\b(?:" + "|".join(CANDIDATE_HEADLINE_WORDS) + r")\b", re.IGNORECASE)
EDT_EVENT_ALIASES = {
    "a": "acquisition",
    "acquisition": "acquisition",
    "ct": "clinical_trial",
    "clinical trial": "clinical_trial",
    "clinical_trial": "clinical_trial",
    "rd": "regular_dividend",
    "regular dividend": "regular_dividend",
    "regular_dividend": "regular_dividend",
    "dc": "dividend_cut",
    "dividend cut": "dividend_cut",
    "dividend_cut": "dividend_cut",
    "di": "dividend_increase",
    "dividend increase": "dividend_increase",
    "dividend_increase": "dividend_increase",
    "gi": "guidance_increase",
    "guidance increase": "guidance_increase",
    "guidance_increase": "guidance_increase",
    "nc": "new_contract",
    "new contract": "new_contract",
    "new_contract": "new_contract",
    "rss": "reverse_stock_split",
    "reverse stock split": "reverse_stock_split",
    "reverse_stock_split": "reverse_stock_split",
    "sd": "special_dividend",
    "special dividend": "special_dividend",
    "special_dividend": "special_dividend",
    "sr": "stock_repurchase",
    "stock repurchase": "stock_repurchase",
    "stock_repurchase": "stock_repurchase",
    "ss": "stock_split",
    "stock split": "stock_split",
    "stock_split": "stock_split",
    "o": "no_event",
    "none": "no_event",
    "no_event": "no_event",
}
EDT_CATEGORY_COLUMNS = {
    "acquisition": "edt_acquisition_score",
    "clinical_trial": "edt_clinical_trial_score",
    "regular_dividend": "edt_dividend_score",
    "dividend_cut": "edt_dividend_score",
    "dividend_increase": "edt_dividend_score",
    "special_dividend": "edt_dividend_score",
    "guidance_increase": "edt_guidance_score",
    "new_contract": "edt_new_contract_score",
    "stock_repurchase": "edt_repurchase_score",
    "reverse_stock_split": "edt_split_score",
    "stock_split": "edt_split_score",
}
MIRAI_RISK_OFF_PATTERN = re.compile(
    r"\b(?:war|attack|missile|conflict|invasion|sanction|military|terror|coup|protest|riot|embargo|hostage)\b",
    re.IGNORECASE,
)
MIRAI_RISK_ON_PATTERN = re.compile(
    r"\b(?:ceasefire|peace|truce|deal|agreement|talks|negotiation|diplomatic|normalization|cooperation)\b",
    re.IGNORECASE,
)
MIRAI_COMMODITY_PATTERN = re.compile(
    r"\b(?:oil|gas|lng|commodity|crude|pipeline|opec|energy|wheat|grain|shipping)\b",
    re.IGNORECASE,
)
MIRAI_GEO_PATTERN = re.compile(
    r"\b(?:country|government|president|ministry|border|province|state|army|foreign|diplomatic|military)\b",
    re.IGNORECASE,
)
STOCK_EMOTION_INTENSITY = {
    "ambiguous": 0.15,
    "amusement": 0.35,
    "anger": 0.85,
    "anxiety": 0.75,
    "belief": 0.45,
    "confusion": 0.4,
    "depression": 0.8,
    "disgust": 0.85,
    "excitement": 0.9,
    "optimism": 0.65,
    "panic": 1.0,
    "surprise": 0.55,
}
SEC_8K_FORMS = {"8-K"}
SEC_10Q_FORMS = {"10-Q", "10-QT", "6-K"}
SEC_10K_FORMS = {"10-K", "10-KT", "20-F", "40-F"}
SEC_SIGNAL_OFFERING_FORMS = {"S-1", "S-3", "F-1", "F-3", "424B3", "424B5", "424B7", "424B8"}
SEC_DEBT_HEAVY_FORMS = {"424B2"}
SEC_FWP_FORMS = {"FWP"}
SEC_CAPITAL_MARKETS_PREFIXES = ("S-1", "S-3", "F-1", "F-3", "424B", "424H", "FWP")
SEC_PROXY_FORMS = {"DEF 14A", "DEFA14A", "PRE 14A", "PRE14A", "PREC14A", "PRER14A"}
SEC_OWNERSHIP_FORMS = {
    "SC 13D",
    "SC 13D/A",
    "SC 13G",
    "SC 13G/A",
    "SCHEDULE 13D",
    "SCHEDULE 13D/A",
    "SCHEDULE 13G",
    "SCHEDULE 13G/A",
    "13D",
    "13D/A",
    "13G",
    "13G/A",
}
SEC_INSIDER_FORMS = {"3", "4", "5"}


def _first_present(columns: Iterable[str], candidates: Iterable[str]) -> str | None:
    normalized = {str(column).strip().lower(): str(column) for column in columns}
    for candidate in candidates:
        found = normalized.get(str(candidate).strip().lower())
        if found:
            return found
    return None


def _normalize_symbol_token(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        tokens = value
    else:
        text = str(value).strip()
        if not text:
            return []
        tokens = re.split(r"[,|;/\s]+", text.replace("[", " ").replace("]", " "))
    cleaned: list[str] = []
    for token in tokens:
        normalized = re.sub(r"[^A-Za-z0-9._-]", "", str(token).upper())
        if normalized and 1 <= len(normalized) <= 8 and normalized not in {"NYSE", "NASDAQ"}:
            cleaned.append(normalized)
    return list(dict.fromkeys(cleaned))


def _sentiment_to_float(value: object) -> float:
    if value is None:
        return 0.0
    if isinstance(value, (int, float)):
        result = float(value)
        if math.isfinite(result):
            return max(min(result, 1.0), -1.0)
        return 0.0
    text = str(value).strip().lower()
    if not text:
        return 0.0
    mapping = {
        "positive": 1.0,
        "bullish": 1.0,
        "up": 1.0,
        "negative": -1.0,
        "bearish": -1.0,
        "down": -1.0,
        "neutral": 0.0,
    }
    if text in mapping:
        return mapping[text]
    try:
        result = float(text)
        return max(min(result, 1.0), -1.0) if math.isfinite(result) else 0.0
    except ValueError:
        return 0.0


def _normalize_text(value: object) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    return text


def _clamp_01(value: float) -> float:
    return max(0.0, min(1.0, float(value)))


def _normalize_sec_form(value: object) -> str:
    text = str(value or "").strip().upper()
    if not text:
        return ""
    text = re.sub(r"\s+", " ", text)
    if text.startswith("SCHEDULE 13D"):
        return text.replace("SCHEDULE", "SC", 1)
    if text.startswith("SCHEDULE 13G"):
        return text.replace("SCHEDULE", "SC", 1)
    return text


def _read_table(path: Path, *, chunksize: int | None = None) -> Iterable[pd.DataFrame]:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        if chunksize:
            return pd.read_csv(path, chunksize=chunksize)
        return [pd.read_csv(path)]
    if suffix == ".json":
        return [pd.read_json(path)]
    if suffix == ".jsonl":
        return [pd.read_json(path, lines=True)]
    if suffix == ".parquet":
        return [pd.read_parquet(path)]
    raise ValueError(f"Unsupported source format: {path}")


def _empty_canonical_frame() -> pd.DataFrame:
    return load_event_feature_frame(None).iloc[0:0].copy()


def _collapse_dataset_tags(series: pd.Series) -> str:
    tags: list[str] = []
    for value in series.astype(str):
        for token in value.split(","):
            cleaned = token.strip()
            if cleaned and cleaned not in tags:
                tags.append(cleaned)
    return ",".join(tags)


def merge_canonical_event_frames(frames: list[pd.DataFrame]) -> pd.DataFrame:
    non_empty = [frame.copy() for frame in frames if frame is not None and not frame.empty]
    if not non_empty:
        return _empty_canonical_frame()
    combined = pd.concat(non_empty, ignore_index=True, sort=False)
    numeric_columns = [
        column
        for column in combined.columns
        if column not in {"symbol", "date", "dataset_tags"}
    ]
    for column in numeric_columns:
        combined[column] = pd.to_numeric(combined[column], errors="coerce").fillna(0.0)
    grouped = combined.groupby(["symbol", "date"], as_index=False).agg(
        {
            **{column: "sum" for column in numeric_columns},
            "dataset_tags": _collapse_dataset_tags,
        }
    )
    grouped = grouped.sort_values(["symbol", "date"]).reset_index(drop=True)
    return grouped


def _explode_symbols(frame: pd.DataFrame, symbol_column: str) -> pd.DataFrame:
    expanded = frame.copy()
    expanded["symbol"] = expanded[symbol_column].map(_normalize_symbol_token)
    expanded = expanded.explode("symbol")
    expanded = expanded.dropna(subset=["symbol"])
    expanded["symbol"] = expanded["symbol"].astype(str)
    return expanded.loc[expanded["symbol"] != ""].copy()


def _filename_symbol_fallback(source_path: Path) -> str | None:
    tokens = _normalize_symbol_token(source_path.stem)
    return tokens[0] if tokens else None


def build_fnspid_daily_features(source_path: Path, *, chunksize: int = 250_000) -> pd.DataFrame:
    counts: list[pd.DataFrame] = []
    headlines: list[pd.DataFrame] = []
    filename_symbol = _filename_symbol_fallback(source_path)
    for chunk in _read_table(source_path, chunksize=chunksize):
        if chunk.empty:
            continue
        datetime_column = _first_present(
            chunk.columns,
            ("date", "datetime", "time", "timestamp", "publish_time", "published_at", "pub_time", "created_at"),
        )
        symbol_column = _first_present(
            chunk.columns,
            ("ticker", "symbol", "stock", "mentioned_companies", "company_symbol"),
        )
        headline_column = _first_present(
            chunk.columns,
            ("title", "headline", "news", "text", "summary"),
        )
        sentiment_column = _first_present(
            chunk.columns,
            ("sentiment", "sentiment_score", "score", "label"),
        )
        if datetime_column is None or headline_column is None:
            continue
        working_columns = [column for column in [datetime_column, symbol_column, headline_column, sentiment_column] if column]
        working = chunk[working_columns].copy()
        if symbol_column is not None:
            working = _explode_symbols(working, symbol_column)
        elif filename_symbol:
            working["symbol"] = filename_symbol
        else:
            continue
        working["date"] = pd.to_datetime(working[datetime_column], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        working = working.dropna(subset=["date"])
        working["headline_norm"] = working[headline_column].map(_normalize_text)
        working["sentiment_value"] = working[sentiment_column].map(_sentiment_to_float) if sentiment_column else 0.0
        working["catalyst_hit"] = working[headline_column].astype(str).str.contains(CANDIDATE_HEADLINE_PATTERN, regex=True)
        working["sentiment_value_sq"] = working["sentiment_value"] ** 2

        counts.append(
            working.groupby(["symbol", "date"], as_index=False).agg(
                fnspid_news_volume_1d=("headline_norm", "size"),
                sentiment_sum=("sentiment_value", "sum"),
                sentiment_sumsq=("sentiment_value_sq", "sum"),
                catalyst_hits=("catalyst_hit", "sum"),
            )
        )
        headlines.append(working[["symbol", "date", "headline_norm"]].drop_duplicates())

    if not counts:
        return _empty_canonical_frame()

    count_frame = pd.concat(counts, ignore_index=True)
    base = count_frame.groupby(["symbol", "date"], as_index=False).agg(
        fnspid_news_volume_1d=("fnspid_news_volume_1d", "sum"),
        sentiment_sum=("sentiment_sum", "sum"),
        sentiment_sumsq=("sentiment_sumsq", "sum"),
        catalyst_hits=("catalyst_hits", "sum"),
    )
    base["fnspid_sentiment_mean"] = base["sentiment_sum"] / base["fnspid_news_volume_1d"].replace(0, 1)
    variance = (
        base["sentiment_sumsq"] / base["fnspid_news_volume_1d"].replace(0, 1)
    ) - (base["fnspid_sentiment_mean"] ** 2)
    base["fnspid_sentiment_std"] = variance.clip(lower=0.0).pow(0.5)

    unique_headlines = pd.concat(headlines, ignore_index=True)
    unique_counts = unique_headlines.groupby(["symbol", "date"], as_index=False).agg(
        unique_headlines=("headline_norm", "size")
    )
    base = base.merge(unique_counts, on=["symbol", "date"], how="left")
    base["unique_headlines"] = base["unique_headlines"].fillna(0.0)
    base["fnspid_novelty_score"] = (
        base["unique_headlines"] / base["fnspid_news_volume_1d"].replace(0, 1)
    ).clip(0.0, 1.0)
    base["fnspid_catalyst_density"] = (
        base["catalyst_hits"] / base["fnspid_news_volume_1d"].replace(0, 1)
    ).clip(0.0, 1.0)
    base = base.sort_values(["symbol", "date"]).reset_index(drop=True)
    base["fnspid_news_volume_3d"] = (
        base.groupby("symbol")["fnspid_news_volume_1d"].rolling(3, min_periods=1).sum().reset_index(level=0, drop=True)
    )
    base["dataset_tags"] = FNSPID_DATASET_TAG
    return base[
        [
            "symbol",
            "date",
            "fnspid_news_volume_1d",
            "fnspid_news_volume_3d",
            "fnspid_sentiment_mean",
            "fnspid_sentiment_std",
            "fnspid_novelty_score",
            "fnspid_catalyst_density",
            "dataset_tags",
        ]
    ]


def _extract_edt_label_value(record: dict[str, Any], candidate_columns: list[str]) -> str:
    for column in candidate_columns:
        if column in record and record[column] not in (None, ""):
            return str(record[column])
    labels = record.get("labels")
    if isinstance(labels, dict):
        for column in ("event_type", "event", "label", "category"):
            if column in labels and labels[column] not in (None, ""):
                return str(labels[column])
    return ""


def _extract_edt_symbol_value(record: dict[str, Any], candidate_columns: list[str]) -> str:
    for column in candidate_columns:
        if column in record and record[column] not in (None, ""):
            tokens = _normalize_symbol_token(record[column])
            if tokens:
                return tokens[0]
    labels = record.get("labels")
    if isinstance(labels, dict):
        tokens = _normalize_symbol_token(labels.get("ticker"))
        if tokens:
            return tokens[0]
    return ""


def _extract_edt_date_value(record: dict[str, Any], candidate_columns: list[str]) -> pd.Timestamp | None:
    for column in candidate_columns:
        if column in record and record[column] not in (None, ""):
            ts = pd.to_datetime(record[column], errors="coerce")
            if not pd.isna(ts):
                if getattr(ts, "tzinfo", None) is not None:
                    ts = ts.tz_convert(None)
                return ts.normalize()
    labels = record.get("labels")
    if isinstance(labels, dict):
        for column in ("pub_time", "publish_time", "date", "start_time"):
            if column in labels and labels[column] not in (None, ""):
                ts = pd.to_datetime(labels[column], errors="coerce")
                if not pd.isna(ts):
                    if getattr(ts, "tzinfo", None) is not None:
                        ts = ts.tz_convert(None)
                    return ts.normalize()
    return None


def _normalize_edt_event(value: object) -> str:
    key = str(value or "").strip().lower()
    return EDT_EVENT_ALIASES.get(key, key.replace(" ", "_"))


def build_edt_daily_features(source_path: Path) -> pd.DataFrame:
    records: list[dict[str, Any]] = []
    for table in _read_table(source_path):
        if table.empty:
            continue
        if isinstance(table, pd.DataFrame):
            raw_records = table.to_dict(orient="records")
        else:
            raw_records = list(table)
        records.extend(raw_records)
    if not records:
        return _empty_canonical_frame()

    rows: list[dict[str, Any]] = []
    for record in records:
        published = _extract_edt_date_value(
            record,
            ["pub_time", "publish_time", "date", "datetime", "timestamp", "created_at"],
        )
        symbol = _extract_edt_symbol_value(record, ["ticker", "symbol", "stock"])
        if published is None or not symbol:
            continue
        event_name = _normalize_edt_event(
            _extract_edt_label_value(record, ["event_type", "event", "label", "category"])
        )
        rows.append(
            {
                "symbol": symbol,
                "date": published,
                "event_name": event_name,
            }
        )
    if not rows:
        return _empty_canonical_frame()

    frame = pd.DataFrame(rows)
    base = frame.groupby(["symbol", "date"], as_index=False).agg(
        edt_event_intensity=("event_name", lambda values: float(sum(1 for value in values if value != "no_event"))),
        total_rows=("event_name", "size"),
    )
    category_to_events: dict[str, set[str]] = {}
    for event_name, column in EDT_CATEGORY_COLUMNS.items():
        category_to_events.setdefault(column, set()).add(event_name)
    for column, event_names in category_to_events.items():
        mask = frame["event_name"].isin(event_names)
        grouped = frame.loc[mask].groupby(["symbol", "date"], as_index=False).agg(value=("event_name", "size"))
        base = base.merge(grouped.rename(columns={"value": column}), on=["symbol", "date"], how="left")

    for column in category_to_events:
        if column not in base.columns:
            base[column] = 0.0
        base[column] = pd.to_numeric(base[column], errors="coerce").fillna(0.0)

    # Keep the broader placeholder buckets available even though official EDT does not natively label them.
    for column in ("edt_financing_score", "edt_violation_score", "edt_risk_warning_score", "edt_rating_action_score"):
        base[column] = 0.0
    base["edt_dividend_score"] = pd.to_numeric(base.get("edt_dividend_score", 0.0), errors="coerce").fillna(0.0)
    base["dataset_tags"] = EDT_DATASET_TAG
    return base[
        [
            "symbol",
            "date",
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
            "dataset_tags",
        ]
    ]


def build_mirai_daily_features(source_path: Path, *, chunksize: int = 250_000) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for chunk in _read_table(source_path, chunksize=chunksize):
        if chunk.empty:
            continue
        datetime_column = _first_present(
            chunk.columns,
            ("date", "datetime", "timestamp", "query_date", "event_date", "day", "created_at"),
        )
        text_columns = [
            column
            for column in (
                _first_present(chunk.columns, ("news", "text", "headline", "title", "summary")),
                _first_present(chunk.columns, ("relation", "relation_text", "event_type", "event_name", "query")),
                _first_present(chunk.columns, ("subject", "subject_name", "head_entity")),
                _first_present(chunk.columns, ("object", "object_name", "tail_entity")),
            )
            if column
        ]
        if datetime_column is None or not text_columns:
            continue
        working = chunk[[datetime_column, *list(dict.fromkeys(text_columns))]].copy()
        working["date"] = pd.to_datetime(working[datetime_column], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        working = working.dropna(subset=["date"])
        text_series = pd.Series("", index=working.index, dtype=object)
        for column in text_columns:
            text_series = text_series + " " + working[column].astype(str)
        working["mirai_text"] = text_series.map(_normalize_text)
        if working.empty:
            continue
        working["mirai_risk_off_score"] = working["mirai_text"].str.contains(MIRAI_RISK_OFF_PATTERN, regex=True).astype(float)
        working["mirai_risk_on_score"] = working["mirai_text"].str.contains(MIRAI_RISK_ON_PATTERN, regex=True).astype(float)
        working["mirai_commodity_risk_score"] = working["mirai_text"].str.contains(MIRAI_COMMODITY_PATTERN, regex=True).astype(float)
        geo_flag = working["mirai_text"].str.contains(MIRAI_GEO_PATTERN, regex=True)
        risk_off_flag = working["mirai_risk_off_score"] > 0
        working["mirai_geopolitical_risk_score"] = (geo_flag | risk_off_flag).astype(float)
        working["mirai_macro_shock_score"] = (
            0.6 * working["mirai_risk_off_score"]
            + 0.35 * working["mirai_commodity_risk_score"]
            + 0.25 * working["mirai_geopolitical_risk_score"]
            + 0.25 * working["mirai_risk_on_score"]
        )
        rows.append(
            working.groupby("date", as_index=False).agg(
                mirai_macro_shock_score=("mirai_macro_shock_score", "mean"),
                mirai_geopolitical_risk_score=("mirai_geopolitical_risk_score", "mean"),
                mirai_commodity_risk_score=("mirai_commodity_risk_score", "mean"),
                mirai_risk_on_score=("mirai_risk_on_score", "mean"),
                mirai_risk_off_score=("mirai_risk_off_score", "mean"),
            )
        )
    if not rows:
        return _empty_canonical_frame()
    base = pd.concat(rows, ignore_index=True).groupby("date", as_index=False).mean(numeric_only=True)
    for column in (
        "mirai_macro_shock_score",
        "mirai_geopolitical_risk_score",
        "mirai_commodity_risk_score",
        "mirai_risk_on_score",
        "mirai_risk_off_score",
    ):
        base[column] = base[column].map(_clamp_01)
    base["symbol"] = GLOBAL_EVENT_SYMBOL
    base["dataset_tags"] = MIRAI_DATASET_TAG
    return base[
        [
            "symbol",
            "date",
            "mirai_macro_shock_score",
            "mirai_geopolitical_risk_score",
            "mirai_commodity_risk_score",
            "mirai_risk_on_score",
            "mirai_risk_off_score",
            "dataset_tags",
        ]
    ]


def build_stockemotions_daily_features(source_path: Path, *, chunksize: int = 250_000) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for chunk in _read_table(source_path, chunksize=chunksize):
        if chunk.empty:
            continue
        datetime_column = _first_present(
            chunk.columns,
            ("date", "datetime", "timestamp", "created_at", "time"),
        )
        symbol_column = _first_present(
            chunk.columns,
            ("ticker", "symbol", "stock", "cashtag"),
        )
        sentiment_column = _first_present(
            chunk.columns,
            ("senti_label", "sentiment", "label"),
        )
        emotion_column = _first_present(
            chunk.columns,
            ("emo_label", "emotion", "emotion_label"),
        )
        if datetime_column is None or symbol_column is None:
            continue
        working = chunk[[column for column in [datetime_column, symbol_column, sentiment_column, emotion_column] if column]].copy()
        working = _explode_symbols(working, symbol_column)
        working["date"] = pd.to_datetime(working[datetime_column], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        working = working.dropna(subset=["date"])
        working["sentiment_norm"] = working[sentiment_column].map(_normalize_text) if sentiment_column else ""
        working["emotion_norm"] = working[emotion_column].map(_normalize_text) if emotion_column else ""
        working["bullish_flag"] = working["sentiment_norm"].isin({"bullish", "positive", "up"}).astype(float)
        working["bearish_flag"] = working["sentiment_norm"].isin({"bearish", "negative", "down"}).astype(float)
        working["emotion_intensity"] = working["emotion_norm"].map(STOCK_EMOTION_INTENSITY).fillna(0.0)
        rows.append(
            working.groupby(["symbol", "date"], as_index=False).agg(
                stocktwits_message_count=("symbol", "size"),
                stocktwits_bullish_ratio=("bullish_flag", "mean"),
                stocktwits_bearish_ratio=("bearish_flag", "mean"),
                stocktwits_emotion_intensity=("emotion_intensity", "mean"),
            )
        )
    if not rows:
        return _empty_canonical_frame()
    base = pd.concat(rows, ignore_index=True).groupby(["symbol", "date"], as_index=False).agg(
        stocktwits_message_count=("stocktwits_message_count", "sum"),
        stocktwits_bullish_ratio=("stocktwits_bullish_ratio", "mean"),
        stocktwits_bearish_ratio=("stocktwits_bearish_ratio", "mean"),
        stocktwits_emotion_intensity=("stocktwits_emotion_intensity", "mean"),
    )
    for column in ("stocktwits_bullish_ratio", "stocktwits_bearish_ratio", "stocktwits_emotion_intensity"):
        base[column] = base[column].map(_clamp_01)
    base["dataset_tags"] = STOCKEMOTIONS_DATASET_TAG
    return base[
        [
            "symbol",
            "date",
            "stocktwits_message_count",
            "stocktwits_bullish_ratio",
            "stocktwits_bearish_ratio",
            "stocktwits_emotion_intensity",
            "dataset_tags",
        ]
    ]


def build_sec_filing_daily_features(
    source_path: Path,
    *,
    chunksize: int = 250_000,
    sec_8k_weight: float = 1.0,
) -> pd.DataFrame:
    rows: list[pd.DataFrame] = []
    for chunk in _read_table(source_path, chunksize=chunksize):
        if chunk.empty:
            continue
        datetime_column = _first_present(
            chunk.columns,
            ("acceptance_datetime", "accepted_at", "accepted", "filing_date", "date", "filed_at"),
        )
        symbol_column = _first_present(
            chunk.columns,
            ("symbol", "ticker"),
        )
        form_column = _first_present(
            chunk.columns,
            ("form", "form_type", "filing_form"),
        )
        if datetime_column is None or symbol_column is None or form_column is None:
            continue
        working = chunk[[datetime_column, symbol_column, form_column]].copy()
        working = _explode_symbols(working, symbol_column)
        working["date"] = pd.to_datetime(working[datetime_column], errors="coerce", utc=True).dt.tz_convert(None).dt.normalize()
        working = working.dropna(subset=["date"])
        working["form_norm"] = working[form_column].map(_normalize_sec_form)
        working["base_form_norm"] = working["form_norm"].str.replace(r"/A$", "", regex=True)
        working["sec_filing_count_1d"] = 1.0
        working["sec_amendment_count"] = working["form_norm"].str.endswith("/A").astype(float)
        working["sec_8k_count"] = working["base_form_norm"].isin(SEC_8K_FORMS).astype(float)
        working["sec_10q_count"] = working["base_form_norm"].isin(SEC_10Q_FORMS).astype(float)
        working["sec_10k_count"] = working["base_form_norm"].isin(SEC_10K_FORMS).astype(float)
        working["sec_offering_count"] = working["base_form_norm"].isin(SEC_SIGNAL_OFFERING_FORMS).astype(float)
        working["sec_capital_markets_count"] = working["base_form_norm"].str.startswith(SEC_CAPITAL_MARKETS_PREFIXES).astype(float)
        working["sec_debt_markets_count"] = working["base_form_norm"].isin(SEC_DEBT_HEAVY_FORMS).astype(float)
        working["sec_fwp_count"] = working["base_form_norm"].isin(SEC_FWP_FORMS).astype(float)
        working["sec_proxy_count"] = working["form_norm"].isin(SEC_PROXY_FORMS).astype(float)
        working["sec_ownership_count"] = working["form_norm"].isin(SEC_OWNERSHIP_FORMS).astype(float)
        working["sec_insider_count"] = working["base_form_norm"].isin(SEC_INSIDER_FORMS).astype(float)
        rows.append(
            working.groupby(["symbol", "date"], as_index=False).agg(
                sec_filing_count_1d=("sec_filing_count_1d", "sum"),
                sec_8k_count=("sec_8k_count", "sum"),
                sec_10q_count=("sec_10q_count", "sum"),
                sec_10k_count=("sec_10k_count", "sum"),
                sec_offering_count=("sec_offering_count", "sum"),
                sec_capital_markets_count=("sec_capital_markets_count", "sum"),
                sec_debt_markets_count=("sec_debt_markets_count", "sum"),
                sec_fwp_count=("sec_fwp_count", "sum"),
                sec_proxy_count=("sec_proxy_count", "sum"),
                sec_ownership_count=("sec_ownership_count", "sum"),
                sec_insider_count=("sec_insider_count", "sum"),
                sec_amendment_count=("sec_amendment_count", "sum"),
            )
        )
    if not rows:
        return _empty_canonical_frame()
    base = pd.concat(rows, ignore_index=True).groupby(["symbol", "date"], as_index=False).sum(numeric_only=True)
    base = base.sort_values(["symbol", "date"]).reset_index(drop=True)
    for source_column, flag_column in (
        ("sec_8k_count", "sec_8k_flag"),
        ("sec_10q_count", "sec_10q_flag"),
        ("sec_10k_count", "sec_10k_flag"),
        ("sec_offering_count", "sec_offering_flag"),
        ("sec_proxy_count", "sec_proxy_flag"),
    ):
        base[flag_column] = (base[source_column] > 0).astype(float)
    base["sec_signal_count_1d"] = (
        base["sec_8k_flag"]
        + base["sec_10q_flag"]
        + base["sec_10k_flag"]
        + base["sec_offering_flag"]
        + base["sec_proxy_flag"]
    )
    base["sec_material_event_score"] = (
        base["sec_8k_flag"] * float(sec_8k_weight)
        + base["sec_10q_flag"] * 1.25
        + base["sec_10k_flag"] * 1.5
        + base["sec_offering_flag"] * 1.1
        + base["sec_proxy_flag"] * 0.9
    )
    base["sec_capital_markets_noise_count"] = (
        base["sec_capital_markets_count"] - base["sec_offering_count"]
    ).clip(lower=0.0)
    base["sec_noise_count_1d"] = (
        base["sec_ownership_count"]
        + base["sec_insider_count"]
        + base["sec_amendment_count"]
    )
    signal_denominator = (base["sec_signal_count_1d"] + base["sec_noise_count_1d"]).replace(0.0, 1.0)
    base["sec_signal_ratio"] = (base["sec_signal_count_1d"] / signal_denominator).clip(0.0, 1.0)
    base["sec_filing_count_5d"] = (
        base.groupby("symbol")["sec_filing_count_1d"].rolling(5, min_periods=1).sum().reset_index(level=0, drop=True)
    )
    base["sec_signal_count_5d"] = (
        base.groupby("symbol")["sec_signal_count_1d"].rolling(5, min_periods=1).sum().reset_index(level=0, drop=True)
    )
    base["sec_material_event_score_5d"] = (
        base.groupby("symbol")["sec_material_event_score"].rolling(5, min_periods=1).sum().reset_index(level=0, drop=True)
    )
    base["dataset_tags"] = SEC_FILINGS_DATASET_TAG
    return base[
        [
            "symbol",
            "date",
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
            "dataset_tags",
        ]
    ]


def build_event_feature_store(
    *,
    fnspid_inputs: list[Path] | None = None,
    edt_inputs: list[Path] | None = None,
    mirai_inputs: list[Path] | None = None,
    sec_inputs: list[Path] | None = None,
    sec_8k_weight: float = 1.0,
    stockemotions_inputs: list[Path] | None = None,
    merge_existing_path: Path | None = None,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    if merge_existing_path and merge_existing_path.exists():
        frames.append(load_event_feature_frame(merge_existing_path))
    for path in fnspid_inputs or []:
        frames.append(build_fnspid_daily_features(path))
    for path in edt_inputs or []:
        frames.append(build_edt_daily_features(path))
    for path in mirai_inputs or []:
        frames.append(build_mirai_daily_features(path))
    for path in sec_inputs or []:
        frames.append(build_sec_filing_daily_features(path, sec_8k_weight=sec_8k_weight))
    for path in stockemotions_inputs or []:
        frames.append(build_stockemotions_daily_features(path))
    return merge_canonical_event_frames(frames)


def save_event_feature_store(frame: pd.DataFrame, output_path: Path) -> None:
    write_event_feature_frame(frame.to_dict(orient="records"), output_path)
