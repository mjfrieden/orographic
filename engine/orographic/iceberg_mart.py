from __future__ import annotations

import json
import os
from pathlib import Path
import re
from typing import Any

from .shared_research_mart import TABLE_CONTRACTS, validate_shared_research_mart


IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier(value: str, *, label: str) -> str:
    if not IDENTIFIER.fullmatch(value):
        raise ValueError(f"Invalid {label}: {value!r}")
    return value


def build_iceberg_publication_plan(
    *,
    mart_dir: str | Path,
    catalog_name: str = "r2_mart",
    namespace: str = "research_mart",
    require_sources: tuple[str, ...] = ("cirrus", "orographic"),
) -> dict[str, Any]:
    manifest = validate_shared_research_mart(mart_dir)
    catalog = _identifier(catalog_name, label="catalog name")
    schema = _identifier(namespace, label="namespace")
    present_sources = {
        str(source.get("source_system")) for source in manifest.get("sources", [])
    }
    missing_sources = sorted(set(require_sources) - present_sources)
    if missing_sources:
        raise ValueError(
            "Shared mart publication requires all sources: " + ", ".join(missing_sources)
        )
    tables = []
    for name, contract in TABLE_CONTRACTS.items():
        artifact = manifest["artifacts"][name]
        tables.append({
            "name": name,
            "target": f"{catalog}.{schema}.{name}",
            "path": str(Path(mart_dir) / artifact["path"]),
            "rows": int(artifact["rows"]),
            "sha256": artifact["sha256"],
            "primary_key": list(contract.primary_key),
            "mode": "merge",
        })
    return {
        "status": "ready",
        "mode": "iceberg_rest_catalog",
        "mart_id": manifest["mart_id"],
        "schema_version": manifest["schema_version"],
        "catalog_name": catalog,
        "namespace": schema,
        "sources": sorted(present_sources),
        "tables": tables,
        "commit_order": [*[table["name"] for table in tables], "mart_publications"],
        "publication_rule": "The mart_publications record is committed last.",
    }


def _literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def publish_iceberg_mart(
    *,
    mart_dir: str | Path,
    catalog_uri: str,
    warehouse: str,
    token: str,
    catalog_name: str = "r2_mart",
    namespace: str = "research_mart",
) -> dict[str, Any]:
    if not catalog_uri.startswith("https://"):
        raise ValueError("R2 catalog URI must use HTTPS")
    if not warehouse.strip() or not token.strip():
        raise ValueError("R2 warehouse and token are required")
    plan = build_iceberg_publication_plan(
        mart_dir=mart_dir, catalog_name=catalog_name, namespace=namespace
    )
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError(
            "DuckDB is required for Iceberg publication; install engine/requirements-mart.txt"
        ) from exc

    catalog = plan["catalog_name"]
    schema = plan["namespace"]
    connection = duckdb.connect()
    try:
        connection.execute("INSTALL iceberg")
        connection.execute("LOAD iceberg")
        connection.execute("INSTALL httpfs")
        connection.execute("LOAD httpfs")
        connection.execute(
            "CREATE OR REPLACE SECRET r2_mart_secret (TYPE ICEBERG, TOKEN "
            + _literal(token)
            + ")"
        )
        connection.execute(
            f"ATTACH {_literal(warehouse)} AS {catalog} "
            f"(TYPE ICEBERG, ENDPOINT {_literal(catalog_uri)})"
        )
        connection.execute(f"CREATE SCHEMA IF NOT EXISTS {catalog}.{schema}")
        for table in plan["tables"]:
            name = table["name"]
            target = table["target"]
            view = f"staged_{name}"
            connection.execute(
                f"CREATE OR REPLACE TEMP VIEW {view} AS "
                f"SELECT * FROM read_parquet({_literal(table['path'])})"
            )
            exists = connection.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_catalog = ? AND table_schema = ? AND table_name = ?",
                [catalog, schema, name],
            ).fetchone()[0]
            if not exists:
                connection.execute(f"CREATE TABLE {target} AS SELECT * FROM {view}")
                continue
            columns = list(TABLE_CONTRACTS[name].columns)
            keys = list(TABLE_CONTRACTS[name].primary_key)
            join = " AND ".join(f"target.{key} = source.{key}" for key in keys)
            update = ", ".join(f"{column} = source.{column}" for column in columns)
            insert_columns = ", ".join(columns)
            insert_values = ", ".join(f"source.{column}" for column in columns)
            connection.execute(
                f"MERGE INTO {target} AS target USING {view} AS source ON {join} "
                f"WHEN MATCHED THEN UPDATE SET {update} "
                f"WHEN NOT MATCHED THEN INSERT ({insert_columns}) VALUES ({insert_values})"
            )

        publication_target = f"{catalog}.{schema}.mart_publications"
        connection.execute(
            f"CREATE TABLE IF NOT EXISTS {publication_target} ("
            "mart_id VARCHAR, generated_at_utc TIMESTAMPTZ, schema_version VARCHAR, "
            "sources_json VARCHAR, tables_json VARCHAR, status VARCHAR)"
        )
        manifest = validate_shared_research_mart(mart_dir)
        connection.execute(
            f"INSERT INTO {publication_target} VALUES (?, ?, ?, ?, ?, ?)",
            [
                manifest["mart_id"], manifest["generated_at_utc"],
                manifest["schema_version"], json.dumps(manifest["sources"], sort_keys=True),
                json.dumps(manifest["artifacts"], sort_keys=True), "published",
            ],
        )
    finally:
        connection.close()
    return {**plan, "status": "published"}


def publication_environment() -> dict[str, str]:
    return {
        "catalog_uri": os.getenv("OROGRAPHIC_R2_DATA_CATALOG_URI", ""),
        "warehouse": os.getenv("OROGRAPHIC_R2_DATA_CATALOG_WAREHOUSE", ""),
        "token": os.getenv("OROGRAPHIC_R2_DATA_CATALOG_TOKEN", ""),
    }


def verify_iceberg_mart(
    *,
    manifest: dict[str, Any],
    catalog_name: str = "r2_mart",
    namespace: str = "research_mart",
) -> dict[str, Any]:
    env = publication_environment()
    missing = [name for name, value in env.items() if not value]
    if missing:
        raise ValueError("Missing Iceberg publication configuration: " + ", ".join(missing))
    catalog = _identifier(catalog_name, label="catalog name")
    schema = _identifier(namespace, label="namespace")
    try:
        import duckdb
    except ImportError as exc:  # pragma: no cover - depends on optional runtime
        raise RuntimeError("DuckDB is required for Iceberg verification") from exc

    connection = duckdb.connect()
    try:
        connection.execute("INSTALL iceberg")
        connection.execute("LOAD iceberg")
        connection.execute(
            "CREATE OR REPLACE SECRET r2_mart_secret (TYPE ICEBERG, TOKEN "
            + _literal(env["token"])
            + ")"
        )
        connection.execute(
            f"ATTACH {_literal(env['warehouse'])} AS {catalog} "
            f"(TYPE ICEBERG, ENDPOINT {_literal(env['catalog_uri'])})"
        )
        actual_rows = {
            name: int(connection.execute(f"SELECT COUNT(*) FROM {catalog}.{schema}.{name}").fetchone()[0])
            for name in TABLE_CONTRACTS
        }
        expected_rows = {
            name: int(manifest["artifacts"][name]["rows"])
            for name in TABLE_CONTRACTS
        }
        publication_rows = int(
            connection.execute(
                f"SELECT COUNT(*) FROM {catalog}.{schema}.mart_publications WHERE mart_id = ? AND status = 'published'",
                [manifest["mart_id"]],
            ).fetchone()[0]
        )
    finally:
        connection.close()
    mismatches = {
        name: {"expected": expected_rows[name], "actual": actual_rows[name]}
        for name in TABLE_CONTRACTS
        if actual_rows[name] != expected_rows[name]
    }
    if mismatches or publication_rows < 1:
        raise ValueError(
            "Published mart verification failed: "
            + json.dumps({"row_mismatches": mismatches, "publication_rows": publication_rows}, sort_keys=True)
        )
    return {
        "status": "verified",
        "mart_id": manifest["mart_id"],
        "row_counts": actual_rows,
        "publication_rows": publication_rows,
    }
