from __future__ import annotations

from datetime import timedelta
import re
from typing import Any

import pandas as pd

from .event_features import GLOBAL_EVENT_SYMBOL


def _narrative_at_entry(matched: pd.DataFrame, entry_at: pd.Timestamp) -> dict[str, float]:
    narrative = matched.loc[
        matched["source_kind"].isin({"news", "social"})
        & matched["headline"].astype(str).str.strip().ne("")
    ].copy()
    if narrative.empty:
        return {
            "narrative_attention_1d_at_entry": 0.0,
            "narrative_attention_3d_at_entry": 0.0,
            "narrative_attention_acceleration_3d_at_entry": 0.0,
            "narrative_source_diversity_1d_at_entry": 0.0,
            "narrative_duplicate_ratio_1d_at_entry": 0.0,
            "narrative_novelty_mean_1d_at_entry": 0.0,
            "narrative_directional_intensity_1d_at_entry": 0.0,
            "narrative_confirmation_score_1d_at_entry": 0.0,
            "narrative_hype_pressure_at_entry": 0.0,
        }
    current = narrative.loc[narrative["effective_at"].ge(entry_at - timedelta(days=1))].copy()
    three_day = narrative.loc[narrative["effective_at"].ge(entry_at - timedelta(days=3))]
    prior = narrative.loc[
        narrative["effective_at"].lt(entry_at - timedelta(days=1))
        & narrative["effective_at"].ge(entry_at - timedelta(days=4))
    ]
    attention_1d = float(len(current))
    prior_daily_mean = float(len(prior)) / 3.0
    acceleration = max(-1.0, min(5.0, (attention_1d - prior_daily_mean) / (prior_daily_mean + 1.0)))
    if current.empty:
        source_diversity = duplicate_ratio = novelty = intensity = confirmation = hype = 0.0
    else:
        headline_keys = current["headline"].astype(str).map(
            lambda value: re.sub(r"[^a-z0-9]+", " ", value.lower()).strip()
        )
        current["headline_key"] = headline_keys
        unique_headlines = max(int(current["headline_key"].nunique()), 1)
        duplicate_ratio = max(0.0, min(1.0, 1.0 - unique_headlines / len(current)))
        source_diversity = max(0.0, min(1.0, (current["source"].nunique() - 1.0) / 2.0))
        story_sources = current.groupby("headline_key")["source"].nunique()
        confirmation = float((story_sources >= 2).sum()) / unique_headlines
        novelty = max(0.0, min(1.0, float(current["novelty"].mean())))
        intensity = max(0.0, min(1.0, float(current["sentiment"].abs().mean())))
        hype = max(0.0, min(1.0,
            0.30 * max(acceleration, 0.0) / 5.0
            + 0.20 * duplicate_ratio
            + 0.15 * intensity
            + 0.15 * (1.0 - novelty)
            + 0.10 * (1.0 - source_diversity)
            + 0.10 * (1.0 - confirmation)
        ))
    return {
        "narrative_attention_1d_at_entry": attention_1d,
        "narrative_attention_3d_at_entry": float(len(three_day)),
        "narrative_attention_acceleration_3d_at_entry": round(acceleration, 6),
        "narrative_source_diversity_1d_at_entry": round(source_diversity, 6),
        "narrative_duplicate_ratio_1d_at_entry": round(duplicate_ratio, 6),
        "narrative_novelty_mean_1d_at_entry": round(novelty, 6),
        "narrative_directional_intensity_1d_at_entry": round(intensity, 6),
        "narrative_confirmation_score_1d_at_entry": round(confirmation, 6),
        "narrative_hype_pressure_at_entry": round(hype, 6),
    }


def enrich_outcomes_with_events(
    outcomes: pd.DataFrame,
    observations: pd.DataFrame,
    *,
    lookback_days: int = 5,
) -> tuple[pd.DataFrame, dict[str, Any]]:
    """Attach only events observable at or before each recommendation timestamp."""
    enriched = outcomes.copy()
    if "run_generated_at_utc" not in enriched.columns or "symbol" not in enriched.columns:
        raise ValueError("Outcome data requires run_generated_at_utc and symbol columns")
    enriched["run_generated_at_utc"] = pd.to_datetime(
        enriched["run_generated_at_utc"], errors="coerce", utc=True
    )
    enriched["symbol"] = enriched["symbol"].astype(str).str.upper().str.strip()
    events = observations.copy()
    if not events.empty:
        events["effective_at"] = pd.to_datetime(events["effective_at"], errors="coerce", utc=True)
        events = events.dropna(subset=["effective_at"]).copy()
        events["symbol"] = events["symbol"].astype(str).str.upper().str.strip()

    summaries: list[dict[str, Any]] = []
    lookback = timedelta(days=max(int(lookback_days), 0))
    for _, outcome in enriched.iterrows():
        entry_at = outcome["run_generated_at_utc"]
        symbol = str(outcome["symbol"])
        if pd.isna(entry_at) or events.empty:
            matched = events.iloc[0:0]
        else:
            matched = events.loc[
                events["symbol"].isin({symbol, GLOBAL_EVENT_SYMBOL})
                & events["effective_at"].le(entry_at)
                & events["effective_at"].ge(entry_at - lookback)
            ].copy()
        one_day_start = entry_at - timedelta(days=1) if not pd.isna(entry_at) else None
        one_day_count = int(matched["effective_at"].ge(one_day_start).sum()) if one_day_start is not None else 0
        narrative_summary = _narrative_at_entry(matched, entry_at) if not pd.isna(entry_at) else _narrative_at_entry(matched, pd.Timestamp.now(tz="UTC"))
        summaries.append(
            {
                "event_observation_count_1d": one_day_count,
                "event_observation_count_lookback": int(len(matched)),
                "event_symbol_specific_count": int(matched["symbol"].eq(symbol).sum()),
                "event_global_count": int(matched["symbol"].eq(GLOBAL_EVENT_SYMBOL).sum()),
                "event_source_count": int(matched["source"].nunique()) if not matched.empty else 0,
                "event_source_kind_count": int(matched["source_kind"].nunique()) if not matched.empty else 0,
                "event_sentiment_mean": round(float(matched["sentiment"].mean()), 6) if not matched.empty else 0.0,
                "event_novelty_mean": round(float(matched["novelty"].mean()), 6) if not matched.empty else 0.0,
                "event_confidence_max": round(float(matched["confidence"].max()), 6) if not matched.empty else 0.0,
                "event_sources": ",".join(sorted(set(matched["source"].astype(str)))) if not matched.empty else "",
                "event_source_kinds": ",".join(sorted(set(matched["source_kind"].astype(str)))) if not matched.empty else "",
                "event_types": ",".join(sorted({value for value in matched["event_type"].astype(str) if value})) if not matched.empty else "",
                "event_ids": ",".join(matched["event_id"].astype(str).tolist()) if not matched.empty else "",
                **narrative_summary,
            }
        )
    summary_frame = pd.DataFrame(summaries, index=enriched.index)
    enriched = pd.concat([enriched, summary_frame], axis=1)

    complete_mask = (
        enriched["outcome_status"].astype(str).str.lower().eq("complete")
        if "outcome_status" in enriched.columns
        else pd.Series(False, index=enriched.index)
    )
    covered_mask = enriched["event_observation_count_lookback"].gt(0)
    complete_rows = int(complete_mask.sum())
    complete_covered = int((complete_mask & covered_mask).sum())
    report = {
        "artifact": "event_outcome_coverage",
        "schema_version": 1,
        "lookback_days": max(int(lookback_days), 0),
        "summary": {
            "recommendation_rows": int(len(enriched)),
            "complete_outcome_rows": complete_rows,
            "rows_with_prior_events": int(covered_mask.sum()),
            "complete_rows_with_prior_events": complete_covered,
            "recommendation_event_coverage_pct": round(float(covered_mask.mean()), 4) if len(enriched) else 0.0,
            "complete_outcome_event_coverage_pct": round(complete_covered / complete_rows, 4) if complete_rows else 0.0,
            "linked_event_observations": int(enriched["event_observation_count_lookback"].sum()),
            "symbols_with_prior_events": int(enriched.loc[covered_mask, "symbol"].nunique()),
        },
        "source_kind_activation": {
            source_kind: int(enriched["event_source_kinds"].str.split(",").map(lambda values: source_kind in values).sum())
            for source_kind in sorted(set(events["source_kind"].astype(str)))
        } if not events.empty else {},
    }
    return enriched, report
