"""Push committed scan output without rebasing the dirty research workspace."""
from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import tempfile
import time


def push_artifacts(branch: str, *, repo: Path = Path('.'), attempts: int = 4, delay: float = 15) -> None:
    def git(*args: str, cwd: Path = repo, check: bool = True):
        return subprocess.run(['git', *args], cwd=cwd, check=check)

    # A separate checkout excludes mutable ledgers and generated research files.
    # Never resolve conflicts by silently dropping either publisher's changes.
    with tempfile.TemporaryDirectory(prefix='orographic-publish-') as tmp:
        checkout = Path(tmp) / 'checkout'
        git('worktree', 'add', '--detach', str(checkout), 'HEAD')
        try:
            for attempt in range(attempts):
                if git('push', 'origin', f'HEAD:{branch}', cwd=checkout, check=False).returncode == 0:
                    return
                if attempt + 1 == attempts:
                    raise RuntimeError('Artifact push exhausted retries')
                git('fetch', 'origin', branch, cwd=checkout)
                git('rebase', 'FETCH_HEAD', cwd=checkout)
                time.sleep(delay * (attempt + 1))
        finally:
            git('worktree', 'remove', '--force', str(checkout))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', required=True)
    push_artifacts(parser.parse_args().branch)
