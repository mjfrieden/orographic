from __future__ import annotations

import argparse
from pathlib import Path, PurePosixPath
import shutil


DEFAULT_SOURCE = Path("web")
DEFAULT_OUTPUT = Path("output/pages_bundle")


def _patterns(source: Path) -> tuple[str, ...]:
    ignore_file = source / ".assetsignore"
    if not ignore_file.is_file():
        return ()
    return tuple(
        line.strip()
        for line in ignore_file.read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    )


def build_pages_bundle(source: Path, output: Path) -> list[Path]:
    source = source.resolve()
    output = output.resolve()
    patterns = _patterns(source)
    if output == source or source in output.parents:
        raise ValueError("Pages bundle output must be outside the source directory.")
    if output.exists():
        shutil.rmtree(output)
    output.mkdir(parents=True)
    copied: list[Path] = []
    for path in sorted(source.rglob("*")):
        if not path.is_file():
            continue
        relative = path.relative_to(source)
        rendered = relative.as_posix()
        if rendered == ".assetsignore" or any(PurePosixPath(rendered).match(pattern) for pattern in patterns):
            continue
        destination = output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, destination)
        copied.append(relative)
    return copied


def main() -> int:
    parser = argparse.ArgumentParser(description="Stage the Cloudflare Pages bundle without research-only assets.")
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--max-file-mib", type=float, default=25.0)
    args = parser.parse_args()
    copied = build_pages_bundle(args.source, args.output)
    limit = int(args.max_file_mib * 1024 * 1024)
    oversized = [path for path in copied if (args.output / path).stat().st_size > limit]
    if oversized:
        rendered = ", ".join(str(path) for path in oversized)
        raise SystemExit(f"Pages bundle contains files larger than {args.max_file_mib:g} MiB: {rendered}")
    print(f"Staged {len(copied)} Pages assets in {args.output}; research-only ledgers were excluded.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
