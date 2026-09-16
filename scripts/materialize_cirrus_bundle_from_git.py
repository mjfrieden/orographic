#!/usr/bin/env python3
"""Materialize and validate Cirrus's git-published research bundle."""

from __future__ import annotations

import argparse
import base64
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import uuid

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from engine.orographic.shared_research_mart import validate_cirrus_export


DEFAULT_REPOSITORY = "mjfrieden/Cirrus"
DEFAULT_REF = "data/options-research-bundle"
DEFAULT_DESTINATION = Path("../Cirrus/analysis/output/options_research_bundle")
REPOSITORY_PATTERN = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")


def _clone_repository(*, repository: str, ref: str, token: str, destination: Path) -> None:
    if not REPOSITORY_PATTERN.fullmatch(repository):
        raise ValueError(f"Invalid GitHub repository name: {repository!r}")
    if not ref.strip() or ref.startswith("-"):
        raise ValueError(f"Invalid git ref: {ref!r}")
    basic = base64.b64encode(f"x-access-token:{token}".encode()).decode()
    env = os.environ.copy()
    env.update(
        {
            "GIT_CONFIG_COUNT": "1",
            "GIT_CONFIG_KEY_0": "http.extraHeader",
            "GIT_CONFIG_VALUE_0": f"AUTHORIZATION: basic {basic}",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    subprocess.run(
        [
            "git",
            "clone",
            "--quiet",
            "--depth",
            "1",
            "--single-branch",
            "--branch",
            ref,
            f"https://github.com/{repository}.git",
            str(destination),
        ],
        check=True,
        env=env,
        capture_output=True,
        text=True,
    )


def _safe_destination(destination: Path) -> Path:
    resolved = destination.expanduser().resolve()
    if resolved in {Path("/"), Path.home().resolve()} or len(resolved.parts) < 4:
        raise ValueError(f"Refusing unsafe destination: {resolved}")
    return resolved


def _atomic_replace_directory(source: Path, destination: Path) -> None:
    destination = _safe_destination(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    ready = Path(
        tempfile.mkdtemp(
            prefix=f".{destination.name}.ready-",
            dir=destination.parent,
        )
    )
    backup = destination.parent / f".{destination.name}.backup-{uuid.uuid4().hex}"
    moved_existing = False
    try:
        shutil.copytree(
            source,
            ready,
            dirs_exist_ok=True,
            ignore=shutil.ignore_patterns(".git"),
        )
        validate_cirrus_export(ready)
        if destination.exists():
            os.replace(destination, backup)
            moved_existing = True
        os.replace(ready, destination)
    except Exception:
        if moved_existing and backup.exists() and not destination.exists():
            os.replace(backup, destination)
        raise
    finally:
        if ready.exists():
            shutil.rmtree(ready)
        if backup.exists():
            shutil.rmtree(backup)


def materialize_cirrus_bundle(
    *,
    repository: str,
    ref: str,
    destination: Path,
    token: str,
) -> dict[str, object]:
    if not token.strip():
        raise ValueError(
            "CIRRUS_BUNDLE_TOKEN or OROGRAPHIC_CRON_GITHUB_TOKEN is required"
        )
    with tempfile.TemporaryDirectory(prefix="cirrus-bundle-materialize-") as tmpdir:
        checkout = Path(tmpdir) / "checkout"
        _clone_repository(
            repository=repository,
            ref=ref,
            token=token,
            destination=checkout,
        )
        manifest = validate_cirrus_export(checkout)
        _atomic_replace_directory(checkout, destination)
    return {
        "status": "materialized",
        "repository": repository,
        "ref": ref,
        "destination": str(_safe_destination(destination)),
        "bundle_id": manifest.get("bundle_id"),
        "artifacts": len(dict(manifest.get("artifacts") or {})),
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Clone and validate Cirrus's options research bundle data branch."
    )
    parser.add_argument(
        "--repository",
        default=os.getenv("CIRRUS_REPO", DEFAULT_REPOSITORY),
    )
    parser.add_argument(
        "--ref",
        default=os.getenv("CIRRUS_BUNDLE_REF", DEFAULT_REF),
    )
    parser.add_argument(
        "--destination",
        type=Path,
        default=Path(os.getenv("CIRRUS_BUNDLE_DEST", str(DEFAULT_DESTINATION))),
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    token = (
        os.getenv("CIRRUS_BUNDLE_TOKEN")
        or os.getenv("OROGRAPHIC_CRON_GITHUB_TOKEN")
        or ""
    )
    result = materialize_cirrus_bundle(
        repository=args.repository,
        ref=args.ref,
        destination=args.destination,
        token=token,
    )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
