from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from scripts.sync_operational_ledgers_r2 import pull_ledgers, push_ledgers


class OperationalLedgersR2Tests(unittest.TestCase):
    def test_content_addressed_round_trip_publishes_manifest_last(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            prospective = root / "prospective_pick_ledger.json"
            moonshot = root / "moonshot_prospective_ledger.json"
            prospective.write_text(json.dumps({"artifact": "prospective", "entries": [{"id": 1}]}), encoding="utf-8")
            moonshot.write_text(json.dumps({"artifact": "moonshot", "entries": []}), encoding="utf-8")
            paths = (prospective, moonshot)
            objects: dict[str, bytes] = {}
            calls: list[tuple[str, str]] = []

            def fake_wrangler(action: str, *, bucket: str, key: str, file_path: Path) -> None:
                self.assertEqual(bucket, "research")
                calls.append((action, key))
                if action == "put":
                    objects[key] = file_path.read_bytes()
                else:
                    file_path.parent.mkdir(parents=True, exist_ok=True)
                    file_path.write_bytes(objects[key])

            with patch("scripts.sync_operational_ledgers_r2._wrangler_object", side_effect=fake_wrangler):
                manifest = push_ledgers(bucket="research", prefix="operational/v1", paths=paths)
                expected_prospective = prospective.read_bytes()
                prospective.unlink()
                moonshot.unlink()
                restored = pull_ledgers(bucket="research", prefix="operational/v1", paths=paths)

            self.assertEqual(restored, 2)
            self.assertEqual(prospective.read_bytes(), expected_prospective)
            self.assertTrue(all("/objects/" in row["object_key"] for row in manifest["ledgers"]))
            put_calls = [key for action, key in calls if action == "put"]
            self.assertEqual(put_calls[-1], "operational/v1/manifest.json")

    def test_missing_manifest_can_fall_back_to_repository_seed(self) -> None:
        def missing(*args, **kwargs) -> None:
            from subprocess import CalledProcessError

            raise CalledProcessError(1, ["wrangler"])

        with patch("scripts.sync_operational_ledgers_r2._wrangler_object", side_effect=missing):
            restored = pull_ledgers(bucket="research", prefix="operational/v1", allow_missing=True)
        self.assertEqual(restored, 0)


if __name__ == "__main__":
    unittest.main()
