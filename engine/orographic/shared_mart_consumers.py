"""Versioned, observation-only consumers of the shared Cirrus/Orographic mart."""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from pathlib import Path
import shutil
from typing import Any
import uuid

from .shared_research_mart import TABLE_CONTRACTS, validate_shared_research_mart


CONSUMER_SCHEMA_VERSION = "orographic_shared_mart_consumers_v1"
PRODUCTION_AUTHORITY = "observation_only_never_used_for_routing"


VIEW_SQL: dict[str, str] = {
    "orographic_training_v1": """
        WITH latest_features AS (
            SELECT *, row_number() OVER (
                PARTITION BY recommendation_key
                ORDER BY available_at_utc DESC NULLS LAST, feature_key
            ) AS feature_rank
            FROM feature_snapshots
        )
        SELECT
            o.outcome_key AS training_row_key,
            r.recommendation_key,
            r.run_key,
            r.source_system,
            r.cohort,
            r.lane,
            r.model_version,
            r.decision_at_utc,
            r.underlying_symbol,
            r.contract_symbol,
            r.option_type,
            r.expiry_date,
            r.strike,
            date_diff('day', CAST(r.decision_at_utc AS DATE), CAST(r.expiry_date AS DATE)) AS dte,
            r.entry_bid,
            r.entry_ask,
            r.entry_mid,
            r.score,
            o.exit_policy,
            o.entry_at_utc,
            o.exit_at_utc,
            o.label_available_at_utc,
            o.entry_price,
            o.exit_price,
            o.executable_return,
            o.exit_reason,
            o.label_contract_id,
            o.label_contract_version,
            f.feature_schema_version,
            f.available_at_utc AS feature_available_at_utc,
            f.features_sha256,
            f.features_json,
            r.source_bundle_id
        FROM recommendations r
        JOIN execution_outcomes o USING (recommendation_key)
        JOIN latest_features f
          ON f.recommendation_key = r.recommendation_key
         AND f.feature_rank = 1
         AND CAST(f.available_at_utc AS TIMESTAMPTZ) <= CAST(r.decision_at_utc AS TIMESTAMPTZ)
        WHERE r.source_system = 'orographic'
          AND o.is_executable
          AND NOT o.is_excluded
          AND CAST(o.label_available_at_utc AS TIMESTAMPTZ) >= CAST(r.decision_at_utc AS TIMESTAMPTZ)
    """,
    "orographic_execution_quality_v1": """
        WITH quote_quality AS (
            SELECT
                recommendation_key,
                count(*) AS quote_observations,
                min(observed_at_utc) AS first_quote_at_utc,
                max(observed_at_utc) AS last_quote_at_utc,
                avg(CASE WHEN bid > 0 AND ask >= bid AND mid > 0 THEN (ask - bid) / mid END) AS avg_spread_pct,
                max(open_interest) AS max_open_interest,
                max(volume) AS max_volume,
                count(*) FILTER (WHERE bid > 0 AND ask >= bid) AS two_sided_quote_observations
            FROM option_quotes
            WHERE recommendation_key IS NOT NULL
            GROUP BY recommendation_key
        ), outcome_quality AS (
            SELECT
                recommendation_key,
                count(*) AS outcome_rows,
                count(*) FILTER (WHERE is_executable AND NOT is_excluded) AS executable_outcome_rows,
                avg(executable_return) FILTER (WHERE is_executable AND NOT is_excluded) AS avg_executable_return,
                avg(CASE WHEN executable_return > 0 THEN 1.0 ELSE 0.0 END)
                    FILTER (WHERE is_executable AND NOT is_excluded) AS executable_win_rate
            FROM execution_outcomes
            GROUP BY recommendation_key
        ), feature_quality AS (
            SELECT recommendation_key, count(*) AS feature_snapshot_rows
            FROM feature_snapshots
            GROUP BY recommendation_key
        )
        SELECT
            r.recommendation_key,
            r.run_key,
            r.source_system,
            r.cohort,
            r.lane,
            r.model_version,
            r.decision_at_utc,
            r.underlying_symbol,
            r.contract_symbol,
            r.option_type,
            r.expiry_date,
            date_diff('day', CAST(r.decision_at_utc AS DATE), CAST(r.expiry_date AS DATE)) AS dte,
            r.entry_bid,
            r.entry_ask,
            r.entry_mid,
            CASE WHEN r.entry_bid > 0 AND r.entry_ask >= r.entry_bid AND r.entry_mid > 0
                 THEN (r.entry_ask - r.entry_bid) / r.entry_mid END AS entry_spread_pct,
            r.score,
            coalesce(q.quote_observations, 0) AS quote_observations,
            q.first_quote_at_utc,
            q.last_quote_at_utc,
            q.avg_spread_pct,
            q.max_open_interest,
            q.max_volume,
            coalesce(q.two_sided_quote_observations, 0) AS two_sided_quote_observations,
            coalesce(o.outcome_rows, 0) AS outcome_rows,
            coalesce(o.executable_outcome_rows, 0) AS executable_outcome_rows,
            o.avg_executable_return,
            o.executable_win_rate,
            coalesce(f.feature_snapshot_rows, 0) AS feature_snapshot_rows,
            r.source_bundle_id
        FROM recommendations r
        LEFT JOIN quote_quality q USING (recommendation_key)
        LEFT JOIN outcome_quality o USING (recommendation_key)
        LEFT JOIN feature_quality f USING (recommendation_key)
    """,
    "orographic_exit_replay_v1": """
        SELECT
            r.recommendation_key,
            r.source_system,
            r.cohort,
            r.lane,
            r.model_version,
            r.decision_at_utc,
            r.underlying_symbol,
            r.contract_symbol,
            r.option_type,
            r.expiry_date,
            q.quote_key,
            q.observed_at_utc,
            date_diff('minute', CAST(r.decision_at_utc AS TIMESTAMP), CAST(q.observed_at_utc AS TIMESTAMP)) AS elapsed_minutes,
            coalesce(r.entry_ask, r.entry_mid) AS executable_entry,
            coalesce(q.executable_exit, q.bid) AS executable_exit,
            (coalesce(q.executable_exit, q.bid) / nullif(coalesce(r.entry_ask, r.entry_mid), 0)) - 1.0 AS executable_path_return,
            q.bid,
            q.ask,
            q.mid,
            CASE WHEN q.bid > 0 AND q.ask >= q.bid AND q.mid > 0 THEN (q.ask - q.bid) / q.mid END AS spread_pct,
            q.open_interest,
            q.volume,
            q.quote_source,
            q.source_bundle_id
        FROM recommendations r
        JOIN option_quotes q USING (recommendation_key)
        WHERE CAST(q.observed_at_utc AS TIMESTAMPTZ) >= CAST(r.decision_at_utc AS TIMESTAMPTZ)
          AND coalesce(r.entry_ask, r.entry_mid) > 0
          AND coalesce(q.executable_exit, q.bid) IS NOT NULL
    """,
    "cirrus_orographic_disagreement_v1": """
        WITH outcomes AS (
            SELECT recommendation_key,
                   avg(executable_return) FILTER (WHERE is_executable AND NOT is_excluded) AS executable_return
            FROM execution_outcomes
            GROUP BY recommendation_key
        ), ranked AS (
            SELECT
                r.*,
                o.executable_return,
                CAST(r.decision_at_utc AS DATE) AS market_date,
                row_number() OVER (
                    PARTITION BY CAST(r.decision_at_utc AS DATE), r.underlying_symbol, r.source_system
                    ORDER BY r.score DESC NULLS LAST, r.recommendation_key
                ) AS daily_rank
            FROM recommendations r
            LEFT JOIN outcomes o USING (recommendation_key)
            WHERE r.source_system IN ('cirrus', 'orographic')
        ), oro AS (
            SELECT * FROM ranked WHERE source_system = 'orographic' AND daily_rank = 1
        ), cirrus AS (
            SELECT * FROM ranked WHERE source_system = 'cirrus' AND daily_rank = 1
        )
        SELECT
            coalesce(o.market_date, c.market_date) AS market_date,
            coalesce(o.underlying_symbol, c.underlying_symbol) AS underlying_symbol,
            o.recommendation_key AS orographic_recommendation_key,
            c.recommendation_key AS cirrus_recommendation_key,
            o.cohort AS orographic_cohort,
            c.cohort AS cirrus_cohort,
            o.model_version AS orographic_model_version,
            c.model_version AS cirrus_model_version,
            o.option_type AS orographic_option_type,
            c.option_type AS cirrus_option_type,
            o.contract_symbol AS orographic_contract_symbol,
            c.contract_symbol AS cirrus_contract_symbol,
            o.score AS orographic_score,
            c.score AS cirrus_score,
            o.executable_return AS orographic_executable_return,
            c.executable_return AS cirrus_executable_return,
            CASE
                WHEN o.recommendation_key IS NULL THEN 'cirrus_only'
                WHEN c.recommendation_key IS NULL THEN 'orographic_only'
                WHEN o.option_type = c.option_type AND o.contract_symbol = c.contract_symbol THEN 'same_contract'
                WHEN o.option_type = c.option_type THEN 'same_side_different_contract'
                ELSE 'directional_disagreement'
            END AS comparison_cohort,
            CASE WHEN o.option_type = c.option_type THEN true
                 WHEN o.recommendation_key IS NULL OR c.recommendation_key IS NULL THEN NULL
                 ELSE false END AS same_side,
            CASE WHEN o.contract_symbol = c.contract_symbol THEN true
                 WHEN o.recommendation_key IS NULL OR c.recommendation_key IS NULL THEN NULL
                 ELSE false END AS same_contract
        FROM oro o
        FULL OUTER JOIN cirrus c
          ON o.market_date = c.market_date
         AND o.underlying_symbol = c.underlying_symbol
    """,
    "orographic_model_monitoring_v1": """
        SELECT
            source_system,
            cohort,
            model_version,
            option_type,
            count(*) AS recommendations,
            count(*) FILTER (WHERE executable_outcome_rows > 0) AS recommendations_with_executable_outcomes,
            count(*) FILTER (WHERE feature_snapshot_rows > 0) AS recommendations_with_features,
            avg(entry_spread_pct) AS avg_entry_spread_pct,
            avg(avg_spread_pct) AS avg_path_spread_pct,
            avg(avg_executable_return) FILTER (WHERE executable_outcome_rows > 0) AS avg_executable_return,
            avg(executable_win_rate) FILTER (WHERE executable_outcome_rows > 0) AS executable_win_rate,
            min(decision_at_utc) AS first_decision_at_utc,
            max(decision_at_utc) AS last_decision_at_utc
        FROM orographic_execution_quality_v1
        GROUP BY source_system, cohort, model_version, option_type
    """,
    "mart_data_quality_v1": """
        WITH path_quotes AS (
            SELECT
                recommendation_key,
                count(*) AS quote_observations,
                count(*) FILTER (
                    WHERE bid IS NOT NULL AND ask IS NOT NULL AND ask < bid
                ) AS crossed_quotes,
                count(*) FILTER (WHERE implied_volatility IS NULL) AS null_iv_quotes,
                count(*) FILTER (WHERE delta IS NULL) AS null_delta_quotes
            FROM option_quotes
            WHERE recommendation_key IS NOT NULL
            GROUP BY recommendation_key
        ), outcome_cov AS (
            SELECT
                recommendation_key,
                count(*) FILTER (WHERE is_executable AND NOT is_excluded) AS executable_rows
            FROM execution_outcomes
            GROUP BY recommendation_key
        ), feature_cov AS (
            SELECT recommendation_key, count(*) AS feature_rows
            FROM feature_snapshots
            GROUP BY recommendation_key
        ), per_cohort AS (
            SELECT
                r.source_system,
                r.cohort,
                count(*) AS recommendations,
                min(r.decision_at_utc) AS first_decision_at_utc,
                max(r.decision_at_utc) AS last_decision_at_utc,
                avg(CASE WHEN f.feature_rows > 0 THEN 1.0 ELSE 0.0 END) AS feature_coverage_rate,
                avg(CASE WHEN q.quote_observations > 0 THEN 1.0 ELSE 0.0 END) AS path_quote_coverage_rate,
                avg(CASE WHEN o.executable_rows > 0 THEN 1.0 ELSE 0.0 END) AS executable_outcome_coverage_rate,
                avg(CASE WHEN r.entry_mid IS NULL THEN 1.0 ELSE 0.0 END) AS entry_mid_null_rate,
                avg(CASE WHEN r.score IS NULL THEN 1.0 ELSE 0.0 END) AS score_null_rate,
                avg(CASE WHEN r.strike IS NULL THEN 1.0 ELSE 0.0 END) AS strike_null_rate,
                avg(CASE WHEN r.expiry_date IS NULL THEN 1.0 ELSE 0.0 END) AS expiry_null_rate,
                avg(CASE WHEN r.contract_symbol IS NULL THEN 1.0 ELSE 0.0 END) AS contract_symbol_null_rate,
                avg(CASE WHEN r.underlying_symbol IS NULL THEN 1.0 ELSE 0.0 END) AS underlying_null_rate,
                avg(CASE
                        WHEN r.entry_bid IS NOT NULL AND r.entry_ask IS NOT NULL AND r.entry_ask < r.entry_bid
                        THEN 1.0 ELSE 0.0 END) AS crossed_entry_rate,
                avg(CASE
                        WHEN r.entry_mid IS NOT NULL AND r.entry_mid <= 0
                        THEN 1.0 ELSE 0.0 END) AS nonpositive_entry_mid_rate,
                avg(CASE
                        WHEN r.entry_bid > 0 AND r.entry_ask >= r.entry_bid AND r.entry_mid > 0
                        THEN (r.entry_ask - r.entry_bid) / r.entry_mid END) AS avg_entry_spread_pct,
                quantile_cont(
                    CASE
                        WHEN r.entry_bid > 0 AND r.entry_ask >= r.entry_bid AND r.entry_mid > 0
                        THEN (r.entry_ask - r.entry_bid) / r.entry_mid END, 0.5) AS median_entry_spread_pct,
                avg(CASE
                        WHEN r.entry_bid > 0 AND r.entry_ask >= r.entry_bid AND r.entry_mid > 0
                             AND (r.entry_ask - r.entry_bid) / r.entry_mid > 0.5
                        THEN 1.0 ELSE 0.0 END) AS wide_entry_spread_rate,
                coalesce(sum(q.quote_observations), 0) AS path_quote_observations,
                CASE WHEN sum(q.quote_observations) > 0
                     THEN sum(q.crossed_quotes) * 1.0 / sum(q.quote_observations) END AS path_crossed_quote_rate,
                CASE WHEN sum(q.quote_observations) > 0
                     THEN sum(q.null_iv_quotes) * 1.0 / sum(q.quote_observations) END AS path_null_iv_rate,
                CASE WHEN sum(q.quote_observations) > 0
                     THEN sum(q.null_delta_quotes) * 1.0 / sum(q.quote_observations) END AS path_null_delta_rate
            FROM recommendations r
            LEFT JOIN path_quotes q USING (recommendation_key)
            LEFT JOIN outcome_cov o USING (recommendation_key)
            LEFT JOIN feature_cov f USING (recommendation_key)
            GROUP BY r.source_system, r.cohort
        )
        SELECT
            *,
            greatest(
                entry_mid_null_rate, score_null_rate, strike_null_rate,
                expiry_null_rate, contract_symbol_null_rate, underlying_null_rate
            ) AS critical_null_rate,
            greatest(
                crossed_entry_rate,
                nonpositive_entry_mid_rate,
                coalesce(path_crossed_quote_rate, 0.0)
            ) AS integrity_anomaly_rate
        FROM per_cohort
    """,
    "orographic_training_funnel_v1": """
        WITH feat AS (
            SELECT
                f.recommendation_key,
                count(*) AS feature_rows,
                count(*) FILTER (
                    WHERE CAST(f.available_at_utc AS TIMESTAMPTZ)
                          <= CAST(r.decision_at_utc AS TIMESTAMPTZ)
                ) AS pit_feature_rows
            FROM feature_snapshots f
            JOIN recommendations r USING (recommendation_key)
            GROUP BY f.recommendation_key
        ), outc AS (
            SELECT
                o.recommendation_key,
                count(*) FILTER (WHERE o.is_executable AND NOT o.is_excluded) AS executable_rows,
                count(*) FILTER (
                    WHERE o.is_executable AND NOT o.is_excluded
                      AND CAST(o.label_available_at_utc AS TIMESTAMPTZ)
                          >= CAST(r.decision_at_utc AS TIMESTAMPTZ)
                ) AS valid_label_rows
            FROM execution_outcomes o
            JOIN recommendations r USING (recommendation_key)
            GROUP BY o.recommendation_key
        ), per_rec AS (
            SELECT
                r.source_system,
                r.cohort,
                coalesce(f.feature_rows, 0) AS feature_rows,
                coalesce(f.pit_feature_rows, 0) AS pit_feature_rows,
                coalesce(o.executable_rows, 0) AS executable_rows,
                coalesce(o.valid_label_rows, 0) AS valid_label_rows
            FROM recommendations r
            LEFT JOIN feat f USING (recommendation_key)
            LEFT JOIN outc o USING (recommendation_key)
        )
        SELECT
            source_system,
            cohort,
            count(*) AS recommendations,
            count(*) FILTER (WHERE feature_rows > 0) AS with_any_feature,
            count(*) FILTER (WHERE pit_feature_rows > 0) AS with_point_in_time_feature,
            count(*) FILTER (WHERE executable_rows > 0) AS with_executable_outcome,
            count(*) FILTER (WHERE valid_label_rows > 0) AS with_valid_label_outcome,
            count(*) FILTER (WHERE pit_feature_rows > 0 AND valid_label_rows > 0)
                AS training_eligible_recommendations,
            coalesce(sum(valid_label_rows) FILTER (WHERE pit_feature_rows > 0), 0) AS training_rows,
            -- Mutually exclusive drop-off reasons, evaluated in funnel order so
            -- each lost recommendation is attributed to exactly one stage.
            count(*) FILTER (
                WHERE valid_label_rows > 0 AND feature_rows = 0
            ) AS dropped_missing_feature,
            count(*) FILTER (
                WHERE valid_label_rows > 0 AND feature_rows > 0 AND pit_feature_rows = 0
            ) AS dropped_feature_not_point_in_time,
            count(*) FILTER (
                WHERE pit_feature_rows > 0 AND executable_rows = 0
            ) AS dropped_missing_executable_outcome,
            count(*) FILTER (
                WHERE pit_feature_rows > 0 AND executable_rows > 0 AND valid_label_rows = 0
            ) AS dropped_label_before_decision,
            avg(CASE WHEN pit_feature_rows > 0 THEN 1.0 ELSE 0.0 END)
                AS point_in_time_feature_coverage_rate,
            avg(CASE WHEN valid_label_rows > 0 THEN 1.0 ELSE 0.0 END)
                AS valid_label_coverage_rate,
            avg(CASE WHEN pit_feature_rows > 0 AND valid_label_rows > 0 THEN 1.0 ELSE 0.0 END)
                AS training_eligibility_rate
        FROM per_rec
        GROUP BY source_system, cohort
    """,
}


VIEW_KEYS: dict[str, tuple[str, ...]] = {
    "orographic_training_v1": ("training_row_key",),
    "orographic_execution_quality_v1": ("recommendation_key",),
    "orographic_exit_replay_v1": ("recommendation_key", "quote_key"),
    "cirrus_orographic_disagreement_v1": ("market_date", "underlying_symbol"),
    "orographic_model_monitoring_v1": ("source_system", "cohort", "model_version", "option_type"),
    "mart_data_quality_v1": ("source_system", "cohort"),
    "orographic_training_funnel_v1": ("source_system", "cohort"),
}


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def build_shared_mart_consumer_bundle(
    mart_dir: str | Path,
    output_dir: str | Path,
) -> dict[str, Any]:
    """Materialize pinned, observation-only research views from one validated mart."""
    mart_root = Path(mart_dir)
    mart = validate_shared_research_mart(mart_root)
    source_systems = {str(row.get("source_system")) for row in mart.get("sources", [])}
    if source_systems != {"cirrus", "orographic"}:
        raise ValueError("Consumer bundle requires a complete Cirrus and Orographic mart")
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - optional runtime
        raise RuntimeError("DuckDB is required; install engine/requirements-mart.txt") from exc

    output = Path(output_dir)
    staging = output.parent / f".{output.name}.{uuid.uuid4().hex}.tmp"
    staging.mkdir(parents=True, exist_ok=False)
    connection = duckdb.connect()
    try:
        for table in TABLE_CONTRACTS:
            path = mart_root / mart["artifacts"][table]["path"]
            connection.execute(
                f"CREATE VIEW {table} AS SELECT * FROM read_parquet('{_sql_path(path)}')"
            )
        artifacts: dict[str, Any] = {}
        for name, sql in VIEW_SQL.items():
            connection.execute(f"CREATE VIEW {name} AS {sql}")
            duplicate_predicate = " AND ".join(f"a.{key} IS NOT DISTINCT FROM b.{key}" for key in VIEW_KEYS[name])
            duplicate_count = int(connection.execute(
                f"SELECT count(*) FROM (SELECT {', '.join(VIEW_KEYS[name])}, count(*) n "
                f"FROM {name} GROUP BY {', '.join(VIEW_KEYS[name])} HAVING count(*) > 1)"
            ).fetchone()[0])
            if duplicate_count:
                raise ValueError(f"{name} has {duplicate_count} duplicate key groups: {duplicate_predicate}")
            path = staging / f"{name}.parquet"
            connection.execute(f"COPY (SELECT * FROM {name}) TO '{_sql_path(path)}' (FORMAT PARQUET)")
            description = connection.execute(f"DESCRIBE SELECT * FROM {name}").fetchall()
            artifacts[name] = {
                "path": path.name,
                "rows": int(connection.execute(f"SELECT count(*) FROM {name}").fetchone()[0]),
                "sha256": _sha256(path),
                "primary_key": list(VIEW_KEYS[name]),
                "columns": [row[0] for row in description],
            }
        generated_at = datetime.now(UTC).replace(microsecond=0).isoformat()
        manifest = {
            "artifact": "orographic_shared_mart_consumer_bundle",
            "schema_version": CONSUMER_SCHEMA_VERSION,
            "generated_at_utc": generated_at,
            "status": "ready",
            "production_authority": PRODUCTION_AUTHORITY,
            "mart_id": mart["mart_id"],
            "mart_schema_version": mart["schema_version"],
            "required_source_systems": ["cirrus", "orographic"],
            "source_systems": sorted(source_systems),
            "source_table_contracts": sorted(TABLE_CONTRACTS),
            "views": artifacts,
            "promotion_contract": {
                "may_change_scoring": False,
                "may_change_council": False,
                "may_change_sizing": False,
                "may_route_orders": False,
                "required_gate_artifact": "orographic_rebuild_readiness",
            },
        }
        (staging / "consumer_manifest.json").write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        backup = output.parent / f".{output.name}.{uuid.uuid4().hex}.bak"
        if output.exists():
            output.rename(backup)
        try:
            staging.rename(output)
        except Exception:
            if backup.exists() and not output.exists():
                backup.rename(output)
            raise
        if backup.exists():
            shutil.rmtree(backup)
        return manifest
    finally:
        connection.close()
        if staging.exists():
            shutil.rmtree(staging)


def validate_shared_mart_consumer_bundle(directory: str | Path) -> dict[str, Any]:
    root = Path(directory)
    manifest = json.loads((root / "consumer_manifest.json").read_text(encoding="utf-8"))
    failures: list[str] = []
    if manifest.get("artifact") != "orographic_shared_mart_consumer_bundle":
        failures.append("artifact")
    if manifest.get("schema_version") != CONSUMER_SCHEMA_VERSION:
        failures.append("schema_version")
    if manifest.get("status") != "ready":
        failures.append("status")
    if manifest.get("production_authority") != PRODUCTION_AUTHORITY:
        failures.append("production_authority")
    if set(manifest.get("source_systems") or []) != {"cirrus", "orographic"}:
        failures.append("source_systems")
    views = manifest.get("views") if isinstance(manifest.get("views"), dict) else {}
    for name in VIEW_SQL:
        artifact = views.get(name) if isinstance(views.get(name), dict) else {}
        path = root / str(artifact.get("path") or f"{name}.parquet")
        if not path.exists():
            failures.append(f"missing:{name}")
        elif artifact.get("sha256") != _sha256(path):
            failures.append(f"hash:{name}")
        if list(artifact.get("primary_key") or []) != list(VIEW_KEYS[name]):
            failures.append(f"primary_key:{name}")
    if failures:
        raise ValueError("Shared mart consumer validation failed: " + ", ".join(failures))
    return manifest
