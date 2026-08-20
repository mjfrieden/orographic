from __future__ import annotations

from pathlib import Path
import tempfile
import unittest

from scripts.build_pages_bundle import build_pages_bundle


class BuildPagesBundleTests(unittest.TestCase):
    def test_excludes_research_assets_before_pages_deploy(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            source = root / "web"
            output = root / "bundle"
            (source / "data" / "diagnostics").mkdir(parents=True)
            (source / ".assetsignore").write_text("data/diagnostics/raw.json\n", encoding="utf-8")
            (source / "index.html").write_text("ready", encoding="utf-8")
            (source / "data" / "diagnostics" / "raw.json").write_text("research", encoding="utf-8")
            (source / "data" / "diagnostics" / "summary.json").write_text("{}", encoding="utf-8")

            copied = build_pages_bundle(source, output)

            self.assertIn(Path("index.html"), copied)
            self.assertTrue((output / "data" / "diagnostics" / "summary.json").is_file())
            self.assertFalse((output / "data" / "diagnostics" / "raw.json").exists())
            self.assertFalse((output / ".assetsignore").exists())


if __name__ == "__main__":
    unittest.main()
