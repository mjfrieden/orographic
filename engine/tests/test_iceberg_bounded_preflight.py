from dataclasses import replace
import io
import json
import unittest
from unittest import mock

from fastavro import writer

from engine.orographic.iceberg_preflight import (
    Blocked, Budget, Limits, R2Reader, Transport, inspect_tables,
)


PLAN = {"mart_id": "rebuilt", "catalog_name": "r2_mart", "namespace": "research_mart", "tables": [{"name": "recommendations"}]}
ROOT = "s3://bucket/table/"


def avro(schema, records):
    stream = io.BytesIO()
    writer(stream, schema, records)
    return stream.getvalue()


def fixture(content=1):
    entry_schema = {"type": "record", "name": "manifest_entry", "fields": [
        {"name": "status", "type": "int"},
        {"name": "data_file", "type": {"type": "record", "name": "data_file", "fields": [
            {"name": "content", "type": "int"}, {"name": "file_path", "type": "string"},
            {"name": "file_size_in_bytes", "type": "long"},
        ]}},
    ]}
    entries = [{"status": 1, "data_file": {"content": content, "file_path": ROOT + "delete.parquet", "file_size_in_bytes": 123}},
               {"status": 2, "data_file": {"content": content, "file_path": ROOT + "removed.parquet", "file_size_in_bytes": 50}}]
    manifest = avro(entry_schema, entries)
    lists = avro({"type": "record", "name": "manifest_file", "fields": [
        {"name": "manifest_path", "type": "string"}, {"name": "manifest_length", "type": "long"},
        {"name": "content", "type": "int"},
    ]}, [{"manifest_path": ROOT + "manifest.avro", "manifest_length": len(manifest), "content": int(content != 0)}])
    meta = {"format-version": 2, "table-uuid": "uuid", "current-snapshot-id": 7,
            "snapshots": [{"snapshot-id": 7, "manifest-list": ROOT + "list.avro"}]}
    table = {"metadata-location": ROOT + "metadata.json", "metadata": meta}
    blobs = {ROOT + "metadata.json": json.dumps(meta).encode(), ROOT + "list.avro": lists,
             ROOT + "manifest.avro": manifest}
    return table, blobs


class Reader:
    def __init__(self, budget, content=1, missing=False, changed=False):
        self.table, self.blobs = fixture(content)
        self.budget, self.missing, self.changed = budget, missing, changed
        self.calls, self.loads = [], 0

    def load(self, namespace, name):
        self.budget.take("requests")
        self.loads += 1
        if name == "mart_publications":
            return None
        if self.changed and self.loads > 2:
            return {**self.table, "metadata-location": ROOT + "new.json"}
        return self.table

    def object(self, path, table, *, metadata=False):
        self.budget.take("requests")
        self.calls.append(("GET" if metadata else "HEAD", path))
        if metadata:
            data = self.blobs[path]
            self.budget.take("metadata_bytes", len(data))
            return data
        if self.missing:
            raise Blocked("object_http_404")
        return 123


class BoundedTests(unittest.TestCase):
    def test_real_avro_data_position_and_equality_delete_objects_are_headed(self):
        for content in (0, 1, 2):
            with self.subTest(content=content):
                budget = Budget()
                reader = Reader(budget, content)
                result = inspect_tables(PLAN, reader, budget)
                self.assertEqual(result["status"], "preflight_passed")
                self.assertEqual(budget.used["requests"], 8)
                self.assertEqual(budget.used["manifests"], 1)
                self.assertEqual(budget.used["objects"], 1)
                self.assertEqual(budget.used["entries"], 2)
                self.assertEqual(reader.calls[-1], ("HEAD", ROOT + "delete.parquet"))
                self.assertFalse(any("removed.parquet" in p for _, p in reader.calls))
                self.assertFalse(any(m == "GET" and p.endswith(".parquet") for m, p in reader.calls))
                self.assertIsNone(result["published_mart_id"])

    def test_missing_delete_blocks_and_preserves_redacted_lineage(self):
        budget = Budget()
        result = inspect_tables(PLAN, Reader(budget, missing=True), budget)
        self.assertEqual(result["reason"], "object_http_404")
        self.assertEqual(result["tables"][0]["checking_content"], 1)
        self.assertEqual(len(result["tables"][0]["checking_object_sha256"]), 64)
        self.assertFalse(result["remote_writes_started"])
        self.assertNotIn("s3://", json.dumps(result))

    def test_all_budgets_fail_closed(self):
        for key in ("manifests", "objects", "requests", "entries", "metadata_bytes"):
            with self.subTest(key=key):
                budget = Budget(replace(Limits(), **{key: 0}))
                result = inspect_tables(PLAN, Reader(budget), budget)
                self.assertEqual(result["status"], "blocked")
                self.assertEqual(result["reason"], "limit_" + key)
                self.assertEqual(budget.used[key], 0)

    def test_time_budget(self):
        budget = Budget(clock=mock.Mock(side_effect=[0, 121]))
        with self.assertRaisesRegex(Blocked, "limit_seconds"):
            budget.take("requests")

    def test_change_during_check_blocks(self):
        budget = Budget()
        result = inspect_tables(PLAN, Reader(budget, changed=True), budget)
        self.assertEqual(result["reason"], "catalog_changed")

    def test_corruption_and_metadata_mismatch_fail_without_raw_errors(self):
        budget = Budget()
        reader = Reader(budget)
        reader.blobs[ROOT + "metadata.json"] = b'{"secret": "do-not-log"}'
        result = inspect_tables(PLAN, reader, budget)
        self.assertEqual(result["reason"], "metadata_mismatch")
        self.assertNotIn("do-not-log", json.dumps(result))
        reader.blobs[ROOT + "metadata.json"] = b"invalid secret"
        self.assertEqual(inspect_tables(PLAN, reader, budget)["reason"], "invalid_or_unreadable_metadata")

    def test_real_transport_caps_unknown_length_body_and_forbids_writes(self):
        class Response(io.BytesIO):
            status = 200
            headers = {}
        budget = Budget(replace(Limits(), document_bytes=8))
        transport = Transport(budget)
        response = Response(b"x" * 100)
        transport.opener = mock.Mock()
        transport.opener.open.return_value = response
        with self.assertRaisesRegex(Blocked, "limit_document_bytes"):
            transport.request("GET", "https://example.test", {})
        self.assertEqual(budget.used["metadata_bytes"], 8)
        with self.assertRaisesRegex(Blocked, "write_not_allowed"):
            transport.request("POST", "https://example.test", {})
        self.assertEqual(budget.used["requests"], 1)

    def test_rest_config_prefix_exact_absence_and_signed_head(self):
        account = "a" * 32
        class FakeTransport:
            def __init__(self):
                self.calls = []
            def request(self, method, url, headers):
                self.calls.append((method, url, headers))
                if "/v1/config?" in url:
                    return 200, {}, b'{"overrides": {"prefix": "warehouse"}}'
                if "/tables/" in url:
                    return 404, {}, b'{"error": {"type": "NoSuchTableException"}}'
                return 200, {"Content-Length": "123"}, b""
        transport = FakeTransport()
        reader = R2Reader("https://catalog.cloudflarestorage.com/" + account + "/bucket", "warehouse", "secret", Budget(), transport)
        self.assertIsNone(reader.load("research_mart", "missing"))
        self.assertIn("/v1/warehouse/namespaces/research_mart/tables/missing", transport.calls[-1][1])
        table = {"storage-credentials": [{"prefix": "s3://bucket/", "config": {
            "s3.access-key-id": "key", "s3.secret-access-key": "secret", "s3.session-token": "temporary",
        }}]}
        self.assertEqual(reader.object(ROOT + "delete.parquet", table), 123)
        self.assertEqual(transport.calls[-1][0], "HEAD")
        self.assertIn("Authorization", transport.calls[-1][2])
        self.assertNotIn("Bearer", transport.calls[-1][2]["Authorization"])
        with self.assertRaisesRegex(Blocked, "unsupported_object_location"):
            reader.object("s3://other/file", table)


if __name__ == "__main__":
    unittest.main()
