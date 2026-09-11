from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.restore_research_artifacts_from_r2 import _safe_relative, cirrus_bundle_prefixes, restore_prefix
from scripts.upload_research_artifacts_to_r2 import _archive_manifest, _upload_canonical, _upload_cirrus
from engine.orographic.evidence_store import build_canonical_evidence_bundle


class R2EvidenceScriptTests(unittest.TestCase):
    def test_archive_manifest_has_hashes_and_stable_object_keys(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir) / "research_datasets"
            root.mkdir()
            file_path = root / "rows.json"
            file_path.write_text('[{"id": 1}]', encoding="utf-8")

            manifest = _archive_manifest(
                "orographic/research-data/2026/08/15/120000",
                [(root, file_path)],
            )

        self.assertEqual(manifest["file_count"], 1)
        self.assertEqual(
            manifest["files"][0]["object_key"],
            "orographic/research-data/2026/08/15/120000/research_datasets/rows.json",
        )
        self.assertEqual(len(manifest["files"][0]["sha256"]), 64)

    def test_canonical_upload_publishes_manifest_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            ledger = root / "ledger.json"
            moonshot = root / "moonshot.json"
            ledger.write_text(json.dumps({"entries": []}), encoding="utf-8")
            moonshot.write_text(json.dumps({"entries": []}), encoding="utf-8")
            bundle = root / "canonical"
            build_canonical_evidence_bundle(
                source_roots=[],
                current_prospective_ledger=ledger,
                current_moonshot_ledger=moonshot,
                payoff_evidence=None,
                output_dir=bundle,
            )
            uploaded: list[str] = []
            with mock.patch(
                "scripts.upload_research_artifacts_to_r2._put_object",
                side_effect=lambda bucket, key, path: uploaded.append(key),
            ):
                _upload_canonical(
                    bucket="bucket",
                    prefix="orographic/evidence-canonical/current",
                    bundle=bundle,
                )

        self.assertGreater(len(uploaded), 1)
        self.assertTrue(uploaded[-1].endswith("/evidence_manifest.json"))

    def test_cirrus_prefixes_prefer_current_then_newest_dated_sibling(self) -> None:
        prefixes = cirrus_bundle_prefixes(
            [
                {
                    "key": "cirrus/options_research_bundle/2026-08-24/manifest.json",
                    "last_modified": "2026-08-24T12:00:00Z",
                },
                {
                    "key": "cirrus/options_research_bundle/current/manifest.json",
                    "last_modified": "2026-08-01T00:00:00Z",
                },
                {
                    "key": "orographic/research-data/manifest.json",
                    "last_modified": "2026-09-01T00:00:00Z",
                },
            ]
        )
        self.assertEqual(
            [row["prefix"] for row in prefixes],
            [
                "cirrus/options_research_bundle/current",
                "cirrus/options_research_bundle/2026-08-24",
            ],
        )
        self.assertTrue(prefixes[0]["is_current"])
        self.assertFalse(prefixes[1]["is_current"])

    def test_cirrus_prefixes_accept_capitalized_root(self) -> None:
        prefixes = cirrus_bundle_prefixes(
            [
                {
                    "key": "Cirrus/options_research_bundle/current/manifest.json",
                    "last_modified": "2026-09-11T00:00:00Z",
                },
            ]
        )
        self.assertEqual(prefixes[0]["prefix"], "Cirrus/options_research_bundle/current")

    def test_cirrus_prefixes_accept_misrooted_options_research_bundle(self) -> None:
        prefixes = cirrus_bundle_prefixes(
            [
                {
                    "key": "orographic/cirrus/options_research_bundle/current/manifest.json",
                    "last_modified": "2026-09-01T00:00:00Z",
                },
                {
                    "key": "options_research_bundle/manifest.json",
                    "last_modified": "2026-09-11T00:00:00Z",
                },
            ]
        )
        self.assertEqual(
            [row["prefix"] for row in prefixes],
            [
                "options_research_bundle",
                "orographic/cirrus/options_research_bundle/current",
            ],
        )

    def test_cirrus_upload_publishes_manifest_last(self) -> None:
        from engine.tests.test_shared_research_mart import _write_cirrus_export

        with tempfile.TemporaryDirectory() as tmpdir:
            bundle = Path(tmpdir) / "cirrus"
            bundle.mkdir()
            _write_cirrus_export(bundle)
            uploaded: list[str] = []
            with mock.patch(
                "scripts.upload_research_artifacts_to_r2._put_object",
                side_effect=lambda bucket, key, path: uploaded.append(key),
            ):
                _upload_cirrus(
                    bucket="bucket",
                    prefix="cirrus/options_research_bundle/current",
                    bundle=bundle,
                )

        self.assertGreater(len(uploaded), 1)
        self.assertTrue(uploaded[-1].endswith("/manifest.json"))
        self.assertTrue(uploaded[-1].startswith("cirrus/options_research_bundle/current/"))

    def test_restore_prefix_downloads_only_selected_suffixes(self) -> None:
        objects = [
            {"key": "orographic/research-data/a/chain.parquet"},
            {"key": "orographic/research-data/a/notes.txt"},
        ]
        with tempfile.TemporaryDirectory() as tmpdir:
            downloaded: list[tuple[str, Path]] = []
            with (
                mock.patch(
                    "scripts.restore_research_artifacts_from_r2._list_objects",
                    return_value=objects,
                ),
                mock.patch(
                    "scripts.restore_research_artifacts_from_r2._get_object",
                    side_effect=lambda bucket, key, destination: downloaded.append((key, destination)),
                ),
            ):
                count = restore_prefix(
                    bucket="bucket",
                    account_id="account",
                    api_token="token",
                    prefix="orographic/research-data",
                    output_dir=Path(tmpdir),
                    include_suffixes=("/chain.parquet",),
                )

        self.assertEqual(count, 1)
        self.assertEqual(downloaded[0][0], objects[0]["key"])
        self.assertEqual(downloaded[0][1], Path(tmpdir) / "a" / "chain.parquet")

    def test_safe_relative_rejects_parent_traversal(self) -> None:
        with self.assertRaises(ValueError):
            _safe_relative("orographic/research-data/../secret", "orographic/research-data/")


if __name__ == "__main__":
    unittest.main()
