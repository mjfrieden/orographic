from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
from typing import Any, Iterable

import pandas as pd

from .event_features import GLOBAL_EVENT_SYMBOL


DEFAULT_EVENT_OBSERVATORY_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "event_features" / "event_observatory.parquet"
)
OBSERVATORY_COLUMNS = [
    "event_id",
    "source",
    "source_kind",
    "source_event_id",
    "symbol",
    "published_at",
    "first_seen_at",
    "effective_at",
    "event_type",
    "headline",
    "direction",
    "sentiment",
    "confidence",
    "novelty",
    "source_quality",
    "time_horizon_hours",
    "decay_half_life_hours",
    "url",
    "raw_payload_json",
    "raw_payload_hash",
    "ingested_at",
]
SOURCE_KINDS = {"news", "structured_event", "macro", "sec", "social"}
# These are computed after collection. They may evolve with a classifier version and
# must never redefine immutable source evidence or its replay hash.
DERIVED_ENRICHMENT_FIELDS = {
    "event_type",
    "direction",
    "sentiment",
    "confidence",
    "novelty",
    "source_quality",
    "requires_llm_review",
    "headline_cluster_id",
    "duplicate_cluster_size",
    "headline_classifier_version",
    "headline_classifier_source",
}
_COLUMN_ALIASES = {
    "source_event_id": ("source_event_id", "id", "event_id", "accession_number", "accession"),
    "symbol": ("symbol", "ticker", "stock", "cashtag", "company_symbol"),
    "published_at": (
        "published_at",
        "acceptance_datetime",
        "accepted_at",
        "pub_time",
        "publish_time",
        "datetime",
        "timestamp",
        "created_at",
        "date",
    ),
    "first_seen_at": ("first_seen_at", "observed_at", "collected_at", "fetched_at", "ingested_at"),
    "event_type": ("event_type", "event", "category", "label", "form", "form_type"),
    "headline": ("headline", "title", "news", "text", "summary", "description"),
    "direction": ("direction", "expected_direction", "side"),
    "sentiment": ("sentiment", "sentiment_score", "score", "senti_label"),
    "confidence": ("confidence", "event_confidence", "probability"),
    "novelty": ("novelty", "novelty_score"),
    "source_quality": ("source_quality", "quality", "reliability"),
    "time_horizon_hours": ("time_horizon_hours", "horizon_hours"),
    "decay_half_life_hours": ("decay_half_life_hours", "half_life_hours"),
    "url": ("url", "link", "source_url", "filing_url"),
}


class ObservatoryConflictError(ValueError):
    """Raised when a stable event identifier is reused for different raw content."""


@dataclass(frozen=True)
class ObservatoryQualityReport:
    status: str
    warnings: list[str]
    rows: int
    symbols: int
    sources: int
    duplicate_rows_removed: int
    invalid_rows_removed: int
    delayed_rows: int
    missing_headline_pct: float
    missing_url_pct: float
    mean_delay_minutes: float
    p95_delay_minutes: float
    source_breakdown: dict[str, dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "warnings": self.warnings,
            "rows": self.rows,
            "symbols": self.symbols,
            "sources": self.sources,
            "duplicate_rows_removed": self.duplicate_rows_removed,
            "invalid_rows_removed": self.invalid_rows_removed,
            "delayed_rows": self.delayed_rows,
            "missing_headline_pct": self.missing_headline_pct,
            "missing_url_pct": self.missing_url_pct,
            "mean_delay_minutes": self.mean_delay_minutes,
            "p95_delay_minutes": self.p95_delay_minutes,
            "source_breakdown": self.source_breakdown,
        }


def _first_column(columns: Iterable[str], aliases: Iterable[str]) -> str | None:
    lookup = {str(column).strip().lower(): str(column) for column in columns}
    return next((lookup[alias] for alias in aliases if alias in lookup), None)


def _clean_text(value: object) -> str:
    if value is None or pd.isna(value):
        return ""
    return re.sub(r"\s+", " ", str(value).strip())


def _normalize_symbol(value: object, *, source_kind: str) -> list[str]:
    text = _clean_text(value).upper()
    tokens = [re.sub(r"[^A-Z0-9._-]", "", token) for token in re.split(r"[,|;/\s]+", text)]
    symbols = [token for token in tokens if token and 1 <= len(token) <= 12]
    if symbols:
        return list(dict.fromkeys(symbols))
    return [GLOBAL_EVENT_SYMBOL] if source_kind == "macro" else []


def _score(value: object, default: float = 0.0) -> float:
    labels = {
        "positive": 1.0,
        "bullish": 1.0,
        "negative": -1.0,
        "bearish": -1.0,
        "neutral": 0.0,
    }
    text = _clean_text(value).lower()
    if text in labels:
        return labels[text]
    try:
        result = float(text)
    except (TypeError, ValueError):
        return default
    return result if pd.notna(result) else default


def _timestamp(value: object) -> pd.Timestamp | None:
    parsed = pd.to_datetime(value, errors="coerce", utc=True)
    return None if pd.isna(parsed) else parsed


def _hash_payload(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _source_payload(payload: dict[str, Any]) -> dict[str, Any]:
    derived_fields = DERIVED_ENRICHMENT_FIELDS if "headline_classifier_version" in payload else set()
    collector_fields = {
        alias
        for alias in _COLUMN_ALIASES["first_seen_at"]
    } | {"ingested_at", "effective_at"} | derived_fields
    return {
        str(key): value
        for key, value in payload.items()
        if str(key).strip().lower() not in collector_fields
    }


def _event_fingerprint(row: dict[str, Any]) -> str:
    stable_source_id = _clean_text(row.get("source_event_id"))
    if stable_source_id:
        # Publishers can revise an item in-place while retaining its permalink.  The
        # source ID locates the item, while the immutable raw-payload hash identifies
        # its specific version.  Keeping both lets the observatory preserve the
        # original evidence and ingest a revision without treating it as corruption.
        identity = f"{row['source']}|{stable_source_id}|{row['symbol']}|{row['raw_payload_hash']}"
    else:
        headline = re.sub(r"[^a-z0-9]+", " ", row["headline"].lower()).strip()
        identity = "|".join(
            [
                row["source"],
                row["symbol"],
                row["published_at"].isoformat(),
                row["event_type"].lower(),
                headline,
                row["url"].lower(),
            ]
        )
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _read_frame(path: Path) -> pd.DataFrame:
    suffix = path.suffix.lower()
    if suffix == ".csv":
        return pd.read_csv(path)
    if suffix == ".parquet":
        return pd.read_parquet(path)
    if suffix == ".json":
        return pd.read_json(path)
    if suffix == ".jsonl":
        return pd.read_json(path, lines=True)
    raise ValueError(f"Unsupported observatory source format: {path}")


def normalize_observations(
    frame: pd.DataFrame,
    *,
    source: str,
    source_kind: str,
    observed_at: datetime | pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, int]:
    if source_kind not in SOURCE_KINDS:
        raise ValueError(f"Unsupported source kind: {source_kind}")
    if frame.empty:
        return pd.DataFrame(columns=OBSERVATORY_COLUMNS), 0

    selected = {
        field: _first_column(frame.columns, aliases)
        for field, aliases in _COLUMN_ALIASES.items()
    }
    if selected["published_at"] is None:
        raise ValueError("Observation input requires a publication/acceptance timestamp column")
    if selected["symbol"] is None and source_kind != "macro":
        raise ValueError("Non-macro observation input requires a symbol column")

    fallback_seen = _timestamp(observed_at) or pd.Timestamp.now(tz="UTC")
    ingested_at = pd.Timestamp.now(tz="UTC")
    rows: list[dict[str, Any]] = []
    invalid_rows = 0
    for payload in frame.to_dict(orient="records"):
        published_at = _timestamp(payload.get(selected["published_at"]))
        first_seen_at = _timestamp(payload.get(selected["first_seen_at"])) if selected["first_seen_at"] else None
        first_seen_at = first_seen_at or fallback_seen
        symbols = _normalize_symbol(payload.get(selected["symbol"]) if selected["symbol"] else None, source_kind=source_kind)
        if published_at is None or not symbols:
            invalid_rows += 1
            continue
        effective_at = max(published_at, first_seen_at)
        source_payload = _source_payload(payload)
        raw_payload_hash = _hash_payload(source_payload)
        raw_payload_json = json.dumps(source_payload, sort_keys=True, default=str, separators=(",", ":"))
        for symbol in symbols:
            row = {
                "source": _clean_text(source).lower(),
                "source_kind": source_kind,
                "source_event_id": _clean_text(payload.get(selected["source_event_id"])) if selected["source_event_id"] else "",
                "symbol": symbol,
                "published_at": published_at,
                "first_seen_at": first_seen_at,
                "effective_at": effective_at,
                "event_type": _clean_text(payload.get(selected["event_type"])).lower() if selected["event_type"] else "",
                "headline": _clean_text(payload.get(selected["headline"])) if selected["headline"] else "",
                "direction": _clean_text(payload.get(selected["direction"])).lower() if selected["direction"] else "",
                "sentiment": _score(payload.get(selected["sentiment"])) if selected["sentiment"] else 0.0,
                "confidence": _score(payload.get(selected["confidence"]), 1.0) if selected["confidence"] else 1.0,
                "novelty": _score(payload.get(selected["novelty"]), 1.0) if selected["novelty"] else 1.0,
                "source_quality": _score(payload.get(selected["source_quality"]), 0.5) if selected["source_quality"] else 0.5,
                "time_horizon_hours": max(_score(payload.get(selected["time_horizon_hours"])), 0.0) if selected["time_horizon_hours"] else 0.0,
                "decay_half_life_hours": max(_score(payload.get(selected["decay_half_life_hours"])), 0.0) if selected["decay_half_life_hours"] else 0.0,
                "url": _clean_text(payload.get(selected["url"])) if selected["url"] else "",
                "raw_payload_json": raw_payload_json,
                "raw_payload_hash": raw_payload_hash,
                "ingested_at": ingested_at,
            }
            row["event_id"] = _event_fingerprint(row)
            rows.append(row)
    return _normalize_observatory_frame(pd.DataFrame(rows)), invalid_rows


def _normalize_observatory_frame(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return pd.DataFrame(columns=OBSERVATORY_COLUMNS)
    result = frame.copy()
    for column in OBSERVATORY_COLUMNS:
        if column not in result.columns:
            result[column] = "" if column not in {
                "sentiment", "confidence", "novelty", "source_quality", "time_horizon_hours", "decay_half_life_hours"
            } else 0.0
    for column in ("published_at", "first_seen_at", "effective_at", "ingested_at"):
        result[column] = pd.to_datetime(result[column], errors="coerce", utc=True)
    for column in ("sentiment", "confidence", "novelty", "source_quality", "time_horizon_hours", "decay_half_life_hours"):
        result[column] = pd.to_numeric(result[column], errors="coerce").fillna(0.0)
    result["symbol"] = result["symbol"].astype(str).str.upper().str.strip()
    return result[OBSERVATORY_COLUMNS].sort_values(["effective_at", "source", "event_id"]).reset_index(drop=True)


def load_observatory(path: Path) -> pd.DataFrame:
    return _normalize_observatory_frame(_read_frame(path))


def merge_observations(existing: pd.DataFrame, incoming: pd.DataFrame) -> tuple[pd.DataFrame, int]:
    existing = _normalize_observatory_frame(existing)
    incoming = _normalize_observatory_frame(incoming)
    if not incoming.empty:
        conflicting_ids = incoming.groupby("event_id")["raw_payload_hash"].nunique()
        conflicting_ids = conflicting_ids.loc[conflicting_ids > 1]
        if not conflicting_ids.empty:
            raise ObservatoryConflictError(
                f"Immutable event payload changed within input for {conflicting_ids.index[0]}"
            )
    if existing.empty:
        combined = incoming
    elif incoming.empty:
        combined = existing
    else:
        prior_hashes = existing.set_index("event_id")["raw_payload_hash"].to_dict()
        conflicts = incoming.loc[
            incoming.apply(
                lambda row: row["event_id"] in prior_hashes
                and prior_hashes[row["event_id"]] != row["raw_payload_hash"],
                axis=1,
            )
        ]
        if not conflicts.empty:
            raise ObservatoryConflictError(
                f"Immutable event payload changed for {conflicts.iloc[0]['event_id']}"
            )
        combined = pd.concat([existing, incoming], ignore_index=True)
    before = len(combined)
    combined = combined.drop_duplicates(subset=["event_id"], keep="first")
    return _normalize_observatory_frame(combined), before - len(combined)


def build_observatory(
    inputs: list[tuple[str, str, Path]],
    *,
    existing_path: Path | None = None,
    observed_at: datetime | pd.Timestamp | str | None = None,
) -> tuple[pd.DataFrame, ObservatoryQualityReport]:
    store = load_observatory(existing_path) if existing_path and existing_path.exists() else pd.DataFrame(columns=OBSERVATORY_COLUMNS)
    duplicates = 0
    invalid = 0
    for source, source_kind, path in inputs:
        normalized, rejected = normalize_observations(
            _read_frame(path), source=source, source_kind=source_kind, observed_at=observed_at
        )
        store, removed = merge_observations(store, normalized)
        duplicates += removed
        invalid += rejected
    return store, assess_observatory_quality(store, duplicate_rows_removed=duplicates, invalid_rows_removed=invalid)


def assess_observatory_quality(
    frame: pd.DataFrame,
    *,
    duplicate_rows_removed: int = 0,
    invalid_rows_removed: int = 0,
) -> ObservatoryQualityReport:
    frame = _normalize_observatory_frame(frame)
    if frame.empty:
        return ObservatoryQualityReport(
            "empty", ["No valid event observations were produced."], 0, 0, 0,
            duplicate_rows_removed, invalid_rows_removed, 0, 0.0, 0.0, 0.0, 0.0, {}
        )
    delay = (frame["first_seen_at"] - frame["published_at"]).dt.total_seconds().div(60).clip(lower=0.0)
    breakdown: dict[str, dict[str, Any]] = {}
    for source, source_frame in frame.groupby("source"):
        source_delay = (source_frame["first_seen_at"] - source_frame["published_at"]).dt.total_seconds().div(60).clip(lower=0.0)
        breakdown[str(source)] = {
            "rows": int(len(source_frame)),
            "symbols": int(source_frame["symbol"].nunique()),
            "min_effective_at": source_frame["effective_at"].min().isoformat(),
            "max_effective_at": source_frame["effective_at"].max().isoformat(),
            "mean_delay_minutes": round(float(source_delay.mean()), 3),
            "missing_headline_pct": round(float(source_frame["headline"].eq("").mean()), 4),
            "missing_url_pct": round(float(source_frame["url"].eq("").mean()), 4),
        }
    missing_headline_pct = round(float(frame["headline"].eq("").mean()), 4)
    missing_url_pct = round(float(frame["url"].eq("").mean()), 4)
    p95_delay_minutes = round(float(delay.quantile(0.95)), 3)
    warnings: list[str] = []
    if missing_headline_pct > 0.25:
        warnings.append("More than 25% of observations lack headline/text evidence.")
    if missing_url_pct > 0.5:
        warnings.append("More than 50% of observations lack a source URL.")
    if p95_delay_minutes > 1440:
        warnings.append("The 95th-percentile collection delay exceeds one day.")
    if invalid_rows_removed:
        warnings.append(f"Rejected {invalid_rows_removed} invalid input rows.")
    return ObservatoryQualityReport(
        status="warning" if warnings else "passed",
        warnings=warnings,
        rows=len(frame),
        symbols=int(frame["symbol"].nunique()),
        sources=int(frame["source"].nunique()),
        duplicate_rows_removed=duplicate_rows_removed,
        invalid_rows_removed=invalid_rows_removed,
        delayed_rows=int((delay > 0).sum()),
        missing_headline_pct=missing_headline_pct,
        missing_url_pct=missing_url_pct,
        mean_delay_minutes=round(float(delay.mean()), 3),
        p95_delay_minutes=p95_delay_minutes,
        source_breakdown=breakdown,
    )


def write_observatory(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame = _normalize_observatory_frame(frame)
    if path.suffix.lower() == ".parquet":
        frame.to_parquet(path, index=False)
    elif path.suffix.lower() == ".csv":
        frame.to_csv(path, index=False)
    elif path.suffix.lower() == ".json":
        path.write_text(frame.to_json(orient="records", date_format="iso"), encoding="utf-8")
    elif path.suffix.lower() == ".jsonl":
        path.write_text(frame.to_json(orient="records", lines=True, date_format="iso") + ("\n" if len(frame) else ""), encoding="utf-8")
    else:
        raise ValueError(f"Unsupported observatory output format: {path}")


def write_quality_report(report: ObservatoryQualityReport, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
