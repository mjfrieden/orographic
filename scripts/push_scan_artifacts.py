"""Push committed scan output without rebasing the dirty research workspace."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import subprocess
import tempfile
import time


def push_artifacts(branch: str, *, repo: Path = Path('.'), attempts: int = 4, delay: float = 15) -> None:
    def git(*args: str, cwd: Path = repo, check: bool = True):
        return subprocess.run(['git', *args], cwd=cwd, check=check)

    # A separate checkout excludes mutable ledgers and generated research files.
    # Resolve generated output conflicts only when this scan is newer; code
    # conflicts and stale snapshots must still stop publication.
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
                result = git('rebase', 'FETCH_HEAD', cwd=checkout, check=False)
                if result.returncode:
                    conflicts = subprocess.check_output(
                        ['git', 'diff', '--name-only', '--diff-filter=U'],
                        cwd=checkout, text=True,
                    ).splitlines()
                    git('rebase', '--abort', cwd=checkout)
                    if not conflicts or any(not path.startswith('web/data/') for path in conflicts):
                        raise RuntimeError('Artifact publication encountered a non-generated conflict')
                    def snapshot_time(ref):
                        raw = subprocess.check_output(
                            ['git', 'show', f'{ref}:web/data/latest_run.json'],
                            cwd=checkout, text=True,
                        )
                        return datetime.fromisoformat(json.loads(raw)['generated_at_utc'].replace('Z', '+00:00'))
                    if snapshot_time('HEAD') < snapshot_time('FETCH_HEAD'):
                        raise RuntimeError('Refusing to publish a scan older than the remote snapshot')
                    # During rebase, theirs is the scan being replayed. Only
                    # generated JSON conflicts qualify for this resolution.
                    git('rebase', '-X', 'theirs', 'FETCH_HEAD', cwd=checkout)
                time.sleep(delay * (attempt + 1))
        finally:
            git('worktree', 'remove', '--force', str(checkout))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--branch', required=True)
    push_artifacts(parser.parse_args().branch)
