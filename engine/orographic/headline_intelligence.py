from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any

import pandas as pd


CLASSIFIER_VERSION = "headline_rules_v1"
EVENT_RULES: tuple[tuple[str, re.Pattern[str], float], ...] = (
    ("guidance", re.compile(r"\b(?:raises?|cuts?|lowers?|withdraws?|reaffirms?)\s+(?:its\s+)?guidance\b", re.I), 0.90),
    ("earnings", re.compile(r"\b(?:earnings|revenue|profit|eps|results)\b", re.I), 0.78),
    ("merger_acquisition", re.compile(r"\b(?:acquire[sd]?|acquisition|merger|takeover|buyout)\b", re.I), 0.88),
    ("financing", re.compile(r"\b(?:offering|raises? capital|debt|notes|share sale|dilution)\b", re.I), 0.82),
    ("regulatory_legal", re.compile(r"\b(?:fda|approval|investigation|lawsuit|antitrust|regulator)\b", re.I), 0.80),
    ("product_contract", re.compile(r"\b(?:launch(?:es|ed)?|product|contract|partnership|award)\b", re.I), 0.68),
    ("analyst_rating", re.compile(r"\b(?:upgrade[sd]?|downgrade[sd]?|price target|rating)\b", re.I), 0.78),
    ("capital_return", re.compile(r"\b(?:buyback|repurchase|dividend)\b", re.I), 0.80),
)
POSITIVE_PATTERN = re.compile(r"\b(?:beat|raises?|raised|approval|upgrade[sd]?|record|surge[sd]?|growth|wins?|award)\b", re.I)
NEGATIVE_PATTERN = re.compile(r"\b(?:miss|cuts?|cut|lowers?|lowered|withdraws?|downgrade[sd]?|lawsuit|investigation|offering|dilution|warning)\b", re.I)


def canonical_headline(headline: object) -> str:
    text = re.sub(r"[^a-z0-9]+", " ", str(headline or "").lower()).strip()
    return re.sub(r"\s+", " ", text)


def headline_cluster_id(headline: object) -> str:
    return hashlib.sha256(canonical_headline(headline).encode("utf-8")).hexdigest()[:20]


def classify_headline(headline: object) -> dict[str, Any]:
    text = str(headline or "").strip()
    matches = [(event_type, confidence) for event_type, pattern, confidence in EVENT_RULES if pattern.search(text)]
    specific_matches = [match for match in matches if match[0] != "earnings"]
    selected_matches = specific_matches or matches
    event_type, confidence = selected_matches[0] if selected_matches else ("unclassified", 0.25)
    positive = len(POSITIVE_PATTERN.findall(text))
    negative = len(NEGATIVE_PATTERN.findall(text))
    if positive > negative:
        direction, sentiment = "bullish", 1.0
    elif negative > positive:
        direction, sentiment = "bearish", -1.0
    else:
        direction, sentiment = "neutral", 0.0
    requires_review = event_type == "unclassified" or len(selected_matches) > 1
    return {
        "event_type": event_type,
        "direction": direction,
        "sentiment": sentiment,
        "confidence": confidence if not requires_review else min(confidence, 0.5),
        "requires_llm_review": requires_review,
    }


def normalize_headlines(
    frame: pd.DataFrame,
    *,
    source: str,
    default_source_quality: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add deterministic, replayable headline intelligence and a bounded review queue."""
    if frame.empty:
        return frame.copy(), pd.DataFrame(columns=["headline_cluster_id", "headline", "reason"])
    result = frame.copy()
    if "headline" not in result.columns:
        raise ValueError("Headline intelligence input requires a headline column")
    classified = result["headline"].map(classify_headline).apply(pd.Series)
    for column in classified.columns:
        result[column] = classified[column]
    result["headline_cluster_id"] = result["headline"].map(headline_cluster_id)
    cluster_counts = Counter(result["headline_cluster_id"])
    result["duplicate_cluster_size"] = result["headline_cluster_id"].map(cluster_counts).astype(int)
    result["novelty"] = result["duplicate_cluster_size"].map(lambda size: 1.0 if size == 1 else round(1.0 / size, 4))
    if "source_quality" not in result.columns:
        result["source_quality"] = float(default_source_quality)
    else:
        result["source_quality"] = pd.to_numeric(result["source_quality"], errors="coerce").fillna(default_source_quality)
    result["headline_classifier_version"] = CLASSIFIER_VERSION
    result["headline_classifier_source"] = str(source).strip().lower()
    review = result.loc[result["requires_llm_review"]].copy()
    if not review.empty:
        review["reason"] = review["event_type"].map(
            lambda event_type: "unclassified_headline" if event_type == "unclassified" else "ambiguous_event_type"
        )
    return result, review
