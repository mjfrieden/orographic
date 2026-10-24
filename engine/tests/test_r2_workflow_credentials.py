"""Keep optional S3 credentials confined to existing R2 uploader steps."""
from pathlib import Path

import pytest


UPLOAD_STEPS = {
    "orographic_scan.yml": "Upload research data to R2",
    "recover_canonical_evidence.yml": "Validate and publish recovered canonical bundle",
    "publish_cirrus_export.yml": "Publish to current Cirrus export prefix",
    "sync_shared_research_mart.yml": "Persist a local Cirrus export to the current R2 prefix",
}
SECRET_NAMES = ("R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY")


@pytest.mark.parametrize("filename,title", UPLOAD_STEPS.items())
def test_s3_credentials_are_only_mapped_on_the_existing_upload_step(filename, title):
    workflow = Path(".github/workflows", filename).read_text()
    header = workflow.split("    steps:", 1)[0]
    assert all(name not in header for name in SECRET_NAMES)
    blocks = workflow.split("\n      - ")
    selected = [block for block in blocks if any(name in block for name in SECRET_NAMES)]
    assert len(selected) == 1
    upload = selected[0]
    assert upload.startswith("name: " + title + "\n")
    assert "python scripts/upload_research_artifacts_to_r2.py" in upload
    assert "        env:\n" in upload
    for name in SECRET_NAMES:
        assert f"          {name}: ${{{{ secrets.{name} }}}}" in upload
        assert workflow.count("secrets." + name) == 1


def test_no_s3_credentials_in_ci_or_other_workflows():
    for path in Path(".github/workflows").glob("*.yml"):
        if path.name not in UPLOAD_STEPS:
            assert all(name not in path.read_text() for name in SECRET_NAMES)
    mart = Path(".github/workflows/sync_shared_research_mart.yml").read_text()
    offline = mart.split("  offline-pr-check:", 1)[1].split("  sync:", 1)[0]
    assert all(name not in offline for name in SECRET_NAMES)
