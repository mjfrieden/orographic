from __future__ import annotations

from pathlib import Path
import tempfile
import unittest
from unittest import mock

from engine.tests.test_shared_research_mart import _write_cirrus_export
from scripts.materialize_cirrus_bundle_from_git import materialize_cirrus_bundle


class MaterializeCirrusBundleTests(unittest.TestCase):
    def test_materializes_valid_bundle_and_replaces_stale_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            destination = root / "shared" / "cirrus_bundle"
            destination.mkdir(parents=True)
            (destination / "stale.txt").write_text("stale", encoding="utf-8")

            def fake_clone(**kwargs) -> None:
                checkout = Path(kwargs["destination"])
                checkout.mkdir(parents=True)
                _write_cirrus_export(checkout)
                (checkout / ".git").mkdir()

            with mock.patch(
                "scripts.materialize_cirrus_bundle_from_git._clone_repository",
                side_effect=fake_clone,
            ):
                result = materialize_cirrus_bundle(
                    repository="mjfrieden/Cirrus",
                    ref="data/options-research-bundle",
                    destination=destination,
                    token="secret-token",
                )

            self.assertEqual(result["status"], "materialized")
            self.assertEqual(result["bundle_id"], "cirrus-test-bundle")
            self.assertTrue((destination / "manifest.json").exists())
            self.assertFalse((destination / "stale.txt").exists())
            self.assertFalse((destination / ".git").exists())

    def test_invalid_bundle_does_not_replace_existing_destination(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            destination = root / "shared" / "cirrus_bundle"
            destination.mkdir(parents=True)
            sentinel = destination / "keep.txt"
            sentinel.write_text("keep", encoding="utf-8")

            def fake_clone(**kwargs) -> None:
                checkout = Path(kwargs["destination"])
                checkout.mkdir(parents=True)
                (checkout / "manifest.json").write_text(
                    '{"bundle_id":"broken","artifacts":{"missing":{"rows":1,"sha256":"bad"}}}',
                    encoding="utf-8",
                )

            with (
                mock.patch(
                    "scripts.materialize_cirrus_bundle_from_git._clone_repository",
                    side_effect=fake_clone,
                ),
                self.assertRaisesRegex(ValueError, "validation failed"),
            ):
                materialize_cirrus_bundle(
                    repository="mjfrieden/Cirrus",
                    ref="data/options-research-bundle",
                    destination=destination,
                    token="secret-token",
                )

            self.assertEqual(sentinel.read_text(encoding="utf-8"), "keep")

    def test_token_is_required(self) -> None:
        with self.assertRaisesRegex(ValueError, "TOKEN"):
            materialize_cirrus_bundle(
                repository="mjfrieden/Cirrus",
                ref="data/options-research-bundle",
                destination=Path("/tmp/cirrus-test-bundle"),
                token="",
            )


if __name__ == "__main__":
    unittest.main()
