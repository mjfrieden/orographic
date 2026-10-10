import subprocess

import pytest

from scripts.push_scan_artifacts import push_artifacts
from scripts.validate_publish_snapshot import validate_snapshot


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], text=True).strip()


def test_push_retries_without_touching_dirty_research_files(tmp_path):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True)
    scan = tmp_path / 'scan'
    subprocess.run(['git', 'clone', str(remote), str(scan)], check=True)
    git(scan, 'config', 'user.name', 'Test')
    git(scan, 'config', 'user.email', 'test@example.com')
    git(scan, 'checkout', '-b', 'main')
    (scan / 'ledger.json').write_text('seed')
    git(scan, 'add', '.')
    git(scan, 'commit', '-m', 'seed')
    git(scan, 'push', 'origin', 'main')
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)], check=True)
    git(other, 'config', 'user.name', 'Test')
    git(other, 'config', 'user.email', 'test@example.com')
    (other / 'code.py').write_text('new code')
    git(other, 'add', '.')
    git(other, 'commit', '-m', 'concurrent code')
    git(other, 'push', 'origin', 'main')
    (scan / 'snapshot.json').write_text('fresh snapshot')
    git(scan, 'add', 'snapshot.json')
    git(scan, 'commit', '-m', 'scan')
    original_head = git(scan, 'rev-parse', 'HEAD')
    (scan / 'ledger.json').write_text('uncommitted evidence')
    (scan / 'research.tmp').write_text('untracked evidence')
    push_artifacts('main', repo=scan, delay=0)
    assert git(scan, 'rev-parse', 'HEAD') == original_head
    assert (scan / 'ledger.json').read_text() == 'uncommitted evidence'
    assert (scan / 'research.tmp').read_text() == 'untracked evidence'
    assert git(remote, 'show', 'main:snapshot.json') == 'fresh snapshot'
    assert git(remote, 'show', 'main:code.py') == 'new code'
    assert git(remote, 'show', 'main:ledger.json') == 'seed'
    # A second publication from the original workspace must retain both commits.
    (scan / 'health.json').write_text('healthy')
    git(scan, 'add', 'health.json')
    git(scan, 'commit', '-m', 'health')
    push_artifacts('main', repo=scan, delay=0)
    assert git(remote, 'show', 'main:health.json') == 'healthy'
    assert git(remote, 'show', 'main:code.py') == 'new code'


def test_stale_snapshot_is_blocked_even_for_abstaining_scan():
    ledger = {'entries': [{'run_generated_at_utc': '2026-09-18T14:11:50+00:00', 'picks': []}]}
    with pytest.raises(ValueError, match='Refusing stale'):
        validate_snapshot({'generated_at_utc': '2026-09-17T20:11:22+00:00'}, ledger)
    validate_snapshot({'generated_at_utc': '2026-09-18T14:11:50Z'}, ledger)
    validate_snapshot({'generated_at_utc': '2026-09-18T17:11:50Z'}, ledger)


@pytest.mark.parametrize("remote_time,should_publish", [
    ("2026-10-07T14:29:15Z", True),
    ("2026-10-07T16:00:00Z", False),
])
def test_conflicting_snapshot_keeps_newest_scan(tmp_path, remote_time, should_publish):
    remote = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(remote)], check=True)
    scan = tmp_path / 'scan'
    subprocess.run(['git', 'clone', str(remote), str(scan)], check=True)
    git(scan, 'config', 'user.name', 'Test')
    git(scan, 'config', 'user.email', 'test@example.com')
    git(scan, 'checkout', '-b', 'main')
    snapshot = scan / 'web/data/latest_run.json'
    snapshot.parent.mkdir(parents=True)
    snapshot.write_text('{"generated_at_utc":"2026-10-07T13:00:00Z"}')
    git(scan, 'add', '.')
    git(scan, 'commit', '-m', 'seed')
    git(scan, 'push', 'origin', 'main')
    other = tmp_path / 'other'
    subprocess.run(['git', 'clone', '-b', 'main', str(remote), str(other)], check=True)
    git(other, 'config', 'user.name', 'Test')
    git(other, 'config', 'user.email', 'test@example.com')
    (other / 'web/data/latest_run.json').write_text('{"generated_at_utc":"' + remote_time + '"}')
    (other / 'code.py').write_text('preserved code')
    git(other, 'add', '.')
    git(other, 'commit', '-m', 'other publisher')
    git(other, 'push', 'origin', 'main')
    snapshot.write_text('{"generated_at_utc":"2026-10-07T14:41:13Z"}')
    git(scan, 'add', '.')
    git(scan, 'commit', '-m', 'scan')
    if should_publish:
        push_artifacts('main', repo=scan, delay=0)
        assert '14:41:13' in git(remote, 'show', 'main:web/data/latest_run.json')
    else:
        with pytest.raises(RuntimeError, match='older than'):
            push_artifacts('main', repo=scan, delay=0)
        assert remote_time in git(remote, 'show', 'main:web/data/latest_run.json')
    assert git(remote, 'show', 'main:code.py') == 'preserved code'
