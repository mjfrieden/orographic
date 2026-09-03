"""Compact shadow evidence derived from versioned shared-mart consumer views."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .shared_mart_consumers import validate_shared_mart_consumer_bundle


def _path(root: Path, manifest: dict[str, Any], view: str) -> str:
    path = root / manifest["views"][view]["path"]
    return str(path.resolve()).replace("'", "''")


def _record(connection: Any, sql: str) -> dict[str, Any]:
    cursor = connection.execute(sql)
    columns = [row[0] for row in cursor.description]
    values = cursor.fetchone()
    return dict(zip(columns, values, strict=True)) if values is not None else {}


def build_shared_mart_shadow_evidence(consumer_dir: str | Path) -> dict[str, Any]:
    root = Path(consumer_dir)
    manifest = validate_shared_mart_consumer_bundle(root)
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise RuntimeError("DuckDB is required; install engine/requirements-mart.txt") from exc

    connection = duckdb.connect()
    try:
        execution = _path(root, manifest, "orographic_execution_quality_v1")
        exits = _path(root, manifest, "orographic_exit_replay_v1")
        disagreements = _path(root, manifest, "cirrus_orographic_disagreement_v1")
        training = _path(root, manifest, "orographic_training_v1")
        monitoring = _path(root, manifest, "orographic_model_monitoring_v1")
        data_quality = _path(root, manifest, "mart_data_quality_v1")
        execution_summary = _record(connection, f"""
            SELECT
                count(*) AS recommendations,
                count(DISTINCT source_system) AS source_systems,
                count(*) FILTER (WHERE executable_outcome_rows > 0) AS executable_recommendations,
                count(*) FILTER (WHERE feature_snapshot_rows > 0) AS recommendations_with_features,
                avg(entry_spread_pct) AS avg_entry_spread_pct,
                avg(avg_executable_return) FILTER (WHERE executable_outcome_rows > 0) AS avg_executable_return,
                avg(executable_win_rate) FILTER (WHERE executable_outcome_rows > 0) AS executable_win_rate
            FROM read_parquet('{execution}')
        """)
        exit_summary = _record(connection, f"""
            WITH per_recommendation AS (
                SELECT recommendation_key, source_system,
                       max(executable_path_return) AS max_path_return,
                       min(executable_path_return) AS min_path_return,
                       arg_max(executable_path_return, observed_at_utc) AS final_path_return,
                       count(*) AS quote_observations
                FROM read_parquet('{exits}')
                GROUP BY recommendation_key, source_system
            )
            SELECT
                count(*) AS recommendations,
                sum(quote_observations) AS quote_observations,
                avg(CASE WHEN max_path_return >= 0.25 THEN 1.0 ELSE 0.0 END) AS touched_25_rate,
                avg(CASE WHEN max_path_return >= 0.40 THEN 1.0 ELSE 0.0 END) AS touched_40_rate,
                avg(CASE WHEN min_path_return <= -0.15 THEN 1.0 ELSE 0.0 END) AS touched_negative_15_rate,
                avg(final_path_return) AS avg_final_path_return
            FROM per_recommendation
        """)
        disagreement_summary = _record(connection, f"""
            SELECT
                count(*) AS daily_symbol_comparisons,
                count(*) FILTER (WHERE orographic_recommendation_key IS NOT NULL
                                  AND cirrus_recommendation_key IS NOT NULL) AS paired_comparisons,
                count(DISTINCT market_date) FILTER (
                    WHERE orographic_recommendation_key IS NOT NULL
                      AND cirrus_recommendation_key IS NOT NULL
                ) AS paired_market_dates,
                count(*) FILTER (WHERE comparison_cohort = 'same_contract') AS same_contract,
                count(*) FILTER (WHERE comparison_cohort = 'same_side_different_contract') AS same_side_different_contract,
                count(*) FILTER (WHERE comparison_cohort = 'directional_disagreement') AS directional_disagreements,
                count(*) FILTER (WHERE comparison_cohort = 'orographic_only') AS orographic_only,
                count(*) FILTER (WHERE comparison_cohort = 'cirrus_only') AS cirrus_only,
                count(*) FILTER (WHERE orographic_executable_return IS NOT NULL
                                  AND cirrus_executable_return IS NOT NULL) AS paired_executable_outcomes,
                avg(orographic_executable_return - cirrus_executable_return)
                    FILTER (WHERE orographic_executable_return IS NOT NULL
                            AND cirrus_executable_return IS NOT NULL) AS avg_orographic_minus_cirrus_return
            FROM read_parquet('{disagreements}')
        """)
        training_summary = _record(connection, f"""
            SELECT count(*) AS training_rows,
                   count(DISTINCT recommendation_key) AS recommendations,
                   count(DISTINCT CAST(decision_at_utc AS DATE)) AS market_dates,
                   count(*) FILTER (WHERE features_json IS NOT NULL) AS rows_with_features,
                   count(DISTINCT option_type) AS option_sides,
                   count(DISTINCT model_version) AS model_versions
            FROM read_parquet('{training}')
        """)
        monitoring_summary = _record(connection, f"""
            SELECT count(*) AS monitoring_cohorts,
                   sum(recommendations) AS recommendations,
                   sum(recommendations_with_executable_outcomes) AS recommendations_with_executable_outcomes,
                   sum(recommendations_with_features) AS recommendations_with_features
            FROM read_parquet('{monitoring}')
        """)
        data_quality_summary = _record(connection, f"""
            SELECT count(*) AS cohorts,
                   sum(recommendations) AS recommendations,
                   max(critical_null_rate) AS worst_critical_null_rate,
                   max(integrity_anomaly_rate) AS worst_integrity_anomaly_rate,
                   max(wide_entry_spread_rate) AS worst_wide_entry_spread_rate,
                   min(feature_coverage_rate) AS min_feature_coverage_rate,
                   min(path_quote_coverage_rate) AS min_path_quote_coverage_rate,
                   min(executable_outcome_coverage_rate) AS min_executable_outcome_coverage_rate,
                   avg(avg_entry_spread_pct) AS avg_entry_spread_pct,
                   count(*) FILTER (WHERE integrity_anomaly_rate > 0) AS cohorts_with_integrity_anomalies,
                   count(*) FILTER (WHERE critical_null_rate > 0) AS cohorts_with_null_gaps
            FROM read_parquet('{data_quality}')
        """)
    finally:
        connection.close()

    worst_null = float(data_quality_summary.get("worst_critical_null_rate") or 0.0)
    worst_integrity = float(data_quality_summary.get("worst_integrity_anomaly_rate") or 0.0)
    data_quality_clean = worst_integrity == 0.0 and worst_null == 0.0

    paired_outcomes = int(disagreement_summary.get("paired_executable_outcomes") or 0)
    paired_market_dates = int(disagreement_summary.get("paired_market_dates") or 0)
    shadow_ready = paired_outcomes >= 30 and paired_market_dates >= 30
    consumer_views = {
        name: {
            "rows": int(view.get("rows") or 0),
            "primary_key": list(view.get("primary_key") or []),
            "sha256": view.get("sha256"),
        }
        for name, view in sorted(manifest["views"].items())
    }
    return {
        "artifact": "orographic_shared_mart_shadow_evidence",
        "schema_version": 2,
        "generated_at_utc": datetime.now(UTC).replace(microsecond=0).isoformat(),
        "mart_id": manifest["mart_id"],
        "consumer_schema_version": manifest["schema_version"],
        "status": "shadow_evidence_ready" if shadow_ready else "collecting_shadow_evidence",
        "production_authority": manifest["production_authority"],
        "production_changes_allowed": False,
        "consumer_bundle": {
            "status": manifest["status"],
            "generated_at_utc": manifest["generated_at_utc"],
            "source_systems": manifest["source_systems"],
            "views": consumer_views,
        },
        "execution_quality": execution_summary,
        "exit_replay": exit_summary,
        "cross_system_comparison": disagreement_summary,
        "training_evidence": training_summary,
        "model_monitoring": monitoring_summary,
        "data_quality": {
            **data_quality_summary,
            "integrity_clean": data_quality_clean,
        },
        "shadow_entry_gates": {
            "paired_executable_outcomes": {
                "passed": paired_outcomes >= 30, "actual": paired_outcomes, "required": 30,
            },
            "paired_market_dates": {
                "passed": paired_market_dates >= 30, "actual": paired_market_dates, "required": 30,
            },
        },
        "next_action": (
            "Evaluate one pre-registered liquidity veto in shadow; do not change live routing."
            if shadow_ready
            else "Collect paired executable outcomes and independent paired market dates."
        ),
    }
