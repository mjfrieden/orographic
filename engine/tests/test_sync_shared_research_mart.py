from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from scripts.sync_shared_research_mart import (
    _missing_cirrus_next_action,
    _restore_cirrus_from_r2,
)


class SharedMartCirrusRestoreTests(unittest.TestCase):
    def test_missing_action_names_publish_command_when_bucket_is_empty(self) -> None:
        text = _missing_cirrus_next_action({"listed_prefixes": [], "listed_objects": 0})
        self.assertIn("--mode cirrus", text)
        self.assertIn("cirrus/options_research_bundle/current", text)

    def test_missing_action_lists_invalid_sibling_prefixes(self) -> None:
        text = _missing_cirrus_next_action({
            "listed_prefixes": [
                {"prefix": "cirrus/options_research_bundle/2026-08-24", "is_current": False},
            ]
        })
        self.assertIn("cirrus/options_research_bundle/2026-08-24", text)
        self.assertIn("Promote a validated prefix", text)

    def test_restore_uses_dated_fallback_when_current_is_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            dest = Path(tmpdir) / "cirrus"
            objects = [
                {
                    "key": "cirrus/options_research_bundle/2026-08-24/manifest.json",
                    "last_modified": "2026-08-24T12:00:00Z",
                },
            ]

            def fake_restore(*, prefix, output_dir, **_kwargs):
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "manifest.json").write_text("{}", encoding="utf-8")
                return 4 if str(prefix).endswith("2026-08-24") else 0

            with (
                mock.patch.dict(
                    os.environ,
                    {
                        "OROGRAPHIC_RESEARCH_R2_BUCKET": "orographic-research-data",
                        "CLOUDFLARE_ACCOUNT_ID": "account",
                        "CLOUDFLARE_R2_API_TOKEN": "token",
                    },
                    clear=False,
                ),
                mock.patch(
                    "scripts.sync_shared_research_mart._list_delimited_prefixes",
                    return_value=[],
                ),
                mock.patch(
                    "scripts.sync_shared_research_mart._list_objects",
                    return_value=objects,
                ),
                mock.patch(
                    "scripts.sync_shared_research_mart.restore_prefix",
                    side_effect=fake_restore,
                ),
                mock.patch(
                    "scripts.sync_shared_research_mart._valid_cirrus_dir",
                    return_value=dest,
                ),
            ):
                info = _restore_cirrus_from_r2(dest, allow_missing=True)

        self.assertEqual(info["status"], "restored")
        self.assertEqual(info["cirrus_pin"], "fallback")
        self.assertEqual(info["prefix"], "cirrus/options_research_bundle/2026-08-24")
        self.assertEqual(info["listed_objects"], 1)
        self.assertEqual(info["objects"], 4)
        self.assertEqual(info["bucket_roots"], [])

    def test_discover_adds_case_variant_cirrus_root(self) -> None:
        from scripts.sync_shared_research_mart import _discover_cirrus_search_prefixes

        with mock.patch(
            "scripts.sync_shared_research_mart._list_delimited_prefixes",
            side_effect=[["Cirrus/", "orographic/"], ["orographic/cirrus/", "orographic/research-data/"]],
        ):
            search, discovery = _discover_cirrus_search_prefixes(
                account_id="account",
                api_token="token",
                bucket="bucket",
            )
        self.assertIn("Cirrus/", search)
        self.assertIn("orographic/cirrus/", search)
        self.assertNotIn("orographic/research-data/", search)
        self.assertEqual(discovery["bucket_roots"], ["Cirrus/", "orographic/"])

    def test_discover_lists_shared_mart_archive_children(self) -> None:
        from scripts.sync_shared_research_mart import _discover_cirrus_search_prefixes

        def fake_delimited(**kwargs):
            prefix = str(kwargs.get("prefix") or "")
            if prefix == "":
                return ["orographic/", "shared-research-mart/"]
            if prefix == "orographic/":
                return ["orographic/evidence-canonical/"]
            return []

        with (
            mock.patch(
                "scripts.sync_shared_research_mart._list_delimited_prefixes",
                side_effect=lambda **kwargs: fake_delimited(**kwargs),
            ),
            mock.patch(
                "scripts.sync_shared_research_mart._list_delimited_index",
                return_value={
                    "delimited": [
                        "shared-research-mart/2026-08-24/",
                        "shared-research-mart/cirrus/",
                    ],
                    "objects": [{"key": "shared-research-mart/latest.tar.gz", "size": 12}],
                },
            ),
        ):
            search, discovery = _discover_cirrus_search_prefixes(
                account_id="account",
                api_token="token",
                bucket="bucket",
            )
        self.assertIn("shared-research-mart/cirrus/", search)
        self.assertEqual(
            discovery["shared_mart_roots"],
            ["shared-research-mart/2026-08-24/", "shared-research-mart/cirrus/"],
        )
        self.assertEqual(discovery["shared_mart_objects"], ["shared-research-mart/latest.tar.gz"])

    def test_research_data_prefix_is_never_cirrus_like(self) -> None:
        from scripts.sync_shared_research_mart import _cirrus_like_search_prefix

        self.assertFalse(_cirrus_like_search_prefix("orographic/research-data/"))
        self.assertTrue(_cirrus_like_search_prefix("Cirrus/"))
        self.assertTrue(_cirrus_like_search_prefix("orographic/cirrus/"))


if __name__ == "__main__":
    unittest.main()
