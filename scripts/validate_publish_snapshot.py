"""Reject a Pages bundle older than the durable production run ledger."""
from datetime import datetime
import json
from pathlib import Path


def validate_snapshot(snapshot: dict, ledger: dict) -> None:
    snapshot_time = datetime.fromisoformat(snapshot['generated_at_utc'].replace('Z', '+00:00'))
    for entry in ledger['entries']:
        run_time = datetime.fromisoformat(entry['run_generated_at_utc'].replace('Z', '+00:00'))
        if run_time > snapshot_time:
            raise ValueError(f'Refusing stale Pages snapshot {snapshot_time}; durable scan {run_time} is newer')


if __name__ == '__main__':
    validate_snapshot(
        json.loads(Path('web/data/latest_run.json').read_text()),
        json.loads(Path('web/data/diagnostics/research_run_ledger.json').read_text()),
    )
