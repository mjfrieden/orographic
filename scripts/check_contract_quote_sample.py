"""Read-only, bounded quote-quality check; prints aggregate coverage, never raw rows."""
import argparse
import hashlib
import json
from pathlib import Path

from engine.orographic.contract_quote_paths import ReplayPolicy, join_quote_paths, retained_json


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--input', type=Path, required=True)
    p.add_argument('--sha256', required=True)
    p.add_argument('--source-namespace', required=True)
    p.add_argument('--pointer', action='append', required=True)
    p.add_argument('--as-of', required=True)
    args = p.parse_args()
    with args.input.open('rb') as handle:
        raw = handle.read(2_000_001)
    if len(raw) > 2_000_000:
        p.error('sample exceeds 2 MB offline audit bound')
    sha = hashlib.sha256(raw).hexdigest()
    if sha != args.sha256:
        p.error('source byte hash mismatch')
    quotes = []
    for pointer in args.pointer:
        quotes.extend(retained_json(raw, source_namespace=args.source_namespace, pointer=pointer))
    policy = ReplayPolicy('quote-coverage-audit-only', '1', 60, 3600, 60, 30, 5)
    report = join_quote_paths([], quotes, policy=policy, as_of_utc=args.as_of, artifacts={sha: raw})
    print(json.dumps({'source_sha256': sha, 'source_namespace': args.source_namespace,
        'pointers': args.pointer, 'coverage': report['coverage'],
        'quote_rows_passing_input_checks': len(quotes) - len(report['quote_exclusions']),
        'recommendation_outcome_recovery_tested': False,
        'limitations': 'Quote eligibility only; no authentic recommendation/path join or profit claim.',
        'runtime_active': False}, sort_keys=True, indent=2))


if __name__ == '__main__':
    main()
