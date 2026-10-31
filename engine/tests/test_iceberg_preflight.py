from __future__ import annotations

import sys
import unittest
from types import SimpleNamespace
from unittest import mock

from engine.orographic.iceberg_mart import (
    IcebergPreflightError, preflight_iceberg_tables, publish_iceberg_mart,
)


PLAN = {
    "mart_id": "rebuilt", "catalog_name": "r2_mart", "namespace": "research_mart",
    "schema_version": "test", "tables": [{"name": "first"}, {"name": "broken"}],
}


class Connection:
    def __init__(self, fail=None, absent=False):
        self.calls = []
        self.fail = fail
        self.absent = absent
        self.batches = 0
        self.closed = False

    def execute(self, sql, params=None):
        self.calls.append(sql)
        self.sql = sql
        self.batches = 0
        return self

    def fetchone(self):
        return (0 if self.absent else 1,)

    def fetchmany(self, size):
        self.batches += 1
        # Late read failure proves the preflight drains more than one batch.
        if self.fail and self.fail in self.sql and self.batches == 2:
            raise OSError("missing deletes.parquet?secret=do-not-log")
        return [("value",)] if self.batches <= 2 else []

    def close(self):
        self.closed = True


class PreflightTests(unittest.TestCase):
    def test_catalog_setup_has_no_remote_table_mutations(self):
        from engine.orographic.iceberg_mart import _attach_iceberg_catalog
        connection = Connection()
        _attach_iceberg_catalog(connection, catalog="r2_mart", catalog_uri="https://example.test", warehouse="w", token="test-only")
        self.assertEqual(len(connection.calls), 6)
        self.assertTrue(all(sql.startswith(("INSTALL ", "LOAD ", "CREATE OR REPLACE SECRET ", "ATTACH ")) for sql in connection.calls))

    def test_stale_absence_does_not_claim_atomic_publication(self):
        plan = {**PLAN, "tables": [{"name": "first", "target": "r2_mart.research_mart.first", "path": "unused.parquet"}]}
        class StaleConnection(Connection):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                if sql.startswith("CREATE TABLE r2_mart"):
                    raise RuntimeError("table created concurrently; secret URL")
                return self
        connection = StaleConnection(absent=True)
        with (
            mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan", return_value=plan),
            mock.patch("engine.orographic.iceberg_mart.contracts_for_schema", return_value={}),
            mock.patch("engine.orographic.iceberg_mart._attach_iceberg_catalog"),
            mock.patch.dict(sys.modules, {"duckdb": mock.Mock(connect=mock.Mock(return_value=connection))}),
        ):
            with self.assertRaises(IcebergPreflightError) as raised:
                publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t", allow_full_scan=True)
        self.assertEqual(raised.exception.report["commit_outcome"], "uncertain")
        self.assertNotIn("secret", str(raised.exception))
        self.assertTrue(connection.closed)

    def test_default_rejects_full_scan_before_connecting(self):
        with mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan") as plan:
            with self.assertRaisesRegex(ValueError, "explicit allow_full_scan"):
                publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t")
            plan.assert_not_called()

    def test_stale_catalog_then_missing_delete_during_merge_is_uncertain(self):
        plan = {**PLAN, "tables": [
            {"name": name, "target": "r2_mart.research_mart." + name, "path": "unused.parquet"}
            for name in ("first", "broken")
        ]}
        class ChangedConnection(Connection):
            def execute(self, sql, params=None):
                super().execute(sql, params)
                if sql.startswith("MERGE INTO") and ".broken " in sql:
                    raise OSError("404 deletes.parquet?secret=do-not-log")
                return self
        connection = ChangedConnection()
        contract = SimpleNamespace(columns=("id",), primary_key=("id",))
        with (
            mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan", return_value=plan),
            mock.patch("engine.orographic.iceberg_mart.contracts_for_schema", return_value={"first": contract, "broken": contract}),
            mock.patch("engine.orographic.iceberg_mart._attach_iceberg_catalog"),
            mock.patch.dict(sys.modules, {"duckdb": mock.Mock(connect=mock.Mock(return_value=connection))}),
        ):
            with self.assertRaises(IcebergPreflightError) as raised:
                publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t", allow_full_scan=True)
        report = raised.exception.report
        self.assertEqual(report["status"], "publication_failed")
        self.assertEqual(report["commit_outcome"], "uncertain")
        self.assertTrue(report["remote_writes_started"])
        self.assertIsNone(report["published_mart_id"])
        self.assertNotIn("secret", str(raised.exception))
        self.assertEqual(sum(sql.startswith("MERGE INTO") for sql in connection.calls), 2)
        self.assertFalse(any(sql.startswith("INSERT INTO") for sql in connection.calls))
        self.assertTrue(connection.closed)

    def test_attach_failure_is_redacted_before_remote_writes(self):
        connection = Connection()
        with (
            mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan", return_value=PLAN),
            mock.patch("engine.orographic.iceberg_mart.contracts_for_schema", return_value={}),
            mock.patch("engine.orographic.iceberg_mart._attach_iceberg_catalog", side_effect=OSError("TOKEN secret")),
            mock.patch.dict(sys.modules, {"duckdb": mock.Mock(connect=mock.Mock(return_value=connection))}),
        ):
            with self.assertRaises(IcebergPreflightError) as raised:
                publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t", allow_full_scan=True)
        self.assertNotIn("secret", str(raised.exception))
        self.assertFalse(raised.exception.report["remote_writes_started"])
        self.assertEqual(connection.calls, [])
        self.assertTrue(connection.closed)

    def test_cli_plan_stays_offline_and_preflight_is_explicit(self):
        from scripts import publish_shared_research_mart as cli
        with (
            mock.patch.object(sys, "argv", ["publish"]),
            mock.patch.object(cli, "build_iceberg_publication_plan", return_value={}) as plan,
            mock.patch.object(cli, "publish_iceberg_mart") as publish,
            mock.patch("builtins.print"),
        ):
            self.assertEqual(cli.main(), 0)
            plan.assert_called_once()
            self.assertNotIn("preflight_only", plan.call_args.kwargs)
            publish.assert_not_called()
        with (
            mock.patch.object(sys, "argv", ["publish", "--preflight"]),
            mock.patch.object(cli, "publication_environment", return_value={"catalog_uri": "https://example.test", "warehouse": "w", "token": "t"}),
            mock.patch.object(cli, "publish_iceberg_mart", return_value={}) as publish,
            mock.patch("builtins.print"),
        ):
            self.assertEqual(cli.main(), 0)
            self.assertTrue(publish.call_args.kwargs["preflight_only"])

    def test_full_read_includes_publication_table_without_promoting_identity(self):
        connection = Connection()
        report = preflight_iceberg_tables(connection, plan=PLAN)
        self.assertEqual(report["status"], "preflight_passed")
        self.assertEqual([t["rows"] for t in report["tables"]], [2, 2, 2])
        self.assertEqual(report["rebuilt_mart_id"], "rebuilt")
        self.assertIsNone(report["published_mart_id"])
        self.assertIsNone(report["served_mart_id"])
        self.assertFalse(report["publication_verified"])
        self.assertTrue(all(sql.startswith("SELECT") for sql in connection.calls))
        self.assertEqual(len(connection.calls), 6)

    def test_missing_tables_are_reported_without_creation(self):
        connection = Connection(absent=True)
        report = preflight_iceberg_tables(connection, plan=PLAN)
        self.assertEqual([t["status"] for t in report["tables"]], ["absent"] * 3)
        self.assertTrue(all("information_schema" in sql for sql in connection.calls))

    def test_failure_redacts_engine_message_and_identifies_target(self):
        with self.assertRaises(IcebergPreflightError) as raised:
            preflight_iceberg_tables(Connection(fail=".broken"), plan=PLAN)
        report = raised.exception.report
        self.assertEqual(report["status"], "blocked_unreadable_destination")
        self.assertEqual(report["tables"][-1]["target"], "r2_mart.research_mart.broken")
        self.assertFalse(report["remote_writes_started"])
        self.assertNotIn("secret", str(raised.exception))

    def test_publish_failure_prevents_all_remote_writes_and_closes_connection(self):
        connection = Connection(fail=".broken")
        with (
            mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan", return_value=PLAN),
            mock.patch("engine.orographic.iceberg_mart.contracts_for_schema", return_value={}),
            mock.patch("engine.orographic.iceberg_mart._attach_iceberg_catalog"),
            mock.patch.dict(sys.modules, {"duckdb": mock.Mock(connect=mock.Mock(return_value=connection))}),
        ):
            with self.assertRaises(IcebergPreflightError):
                publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t", allow_full_scan=True)
        self.assertTrue(connection.closed)
        self.assertTrue(all(sql.startswith("SELECT") for sql in connection.calls))

    def test_preflight_only_success_does_not_write(self):
        connection = Connection()
        with (
            mock.patch("engine.orographic.iceberg_mart.build_iceberg_publication_plan", return_value=PLAN),
            mock.patch("engine.orographic.iceberg_mart.contracts_for_schema", return_value={}),
            mock.patch("engine.orographic.iceberg_mart._attach_iceberg_catalog"),
            mock.patch.dict(sys.modules, {"duckdb": mock.Mock(connect=mock.Mock(return_value=connection))}),
        ):
            result = publish_iceberg_mart(mart_dir="unused", catalog_uri="https://example.test", warehouse="w", token="t", preflight_only=True, allow_full_scan=True)
        self.assertEqual(result["status"], "preflight_passed")
        self.assertTrue(connection.closed)
        self.assertTrue(all(sql.startswith("SELECT") for sql in connection.calls))


if __name__ == "__main__":
    unittest.main()
