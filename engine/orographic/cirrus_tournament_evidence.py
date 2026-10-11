"""Read-only Cirrus retained artifacts -> additive Atmosphere evidence tables.

Never populates execution_outcomes or eligibility tables. Does not import Cirrus,
load models, call providers, or infer missing point-in-time witnesses.
"""
import argparse
import gzip
import hashlib
import json
import math
import os
from datetime import datetime, timezone
from dataclasses import dataclass, fields
from pathlib import Path
import sqlite3

VERSION = 'cirrus-tournament-evidence-v1'
UNAVAILABLE = ['decision_cutoff_not_attested', 'candidate_completeness_not_attested',
               'fit_completion_not_attested', 'execution_fills_not_retained']
SCHEMA = '''
CREATE TABLE IF NOT EXISTS cirrus_tournament_bridge_versions(version TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS cirrus_tournament_artifacts(
 artifact_key TEXT PRIMARY KEY, sha256 TEXT NOT NULL, byte_count INTEGER NOT NULL,
 kind TEXT NOT NULL, decoding_status TEXT NOT NULL, unavailable_reason TEXT);
CREATE TABLE IF NOT EXISTS cirrus_tournament_sources(
 source_key TEXT PRIMARY KEY, source_namespace TEXT NOT NULL, relative_path TEXT NOT NULL,
 artifact_key TEXT NOT NULL REFERENCES cirrus_tournament_artifacts(artifact_key));
CREATE TABLE IF NOT EXISTS cirrus_tournament_observations(
 observation_key TEXT PRIMARY KEY, artifact_key TEXT NOT NULL REFERENCES cirrus_tournament_artifacts(artifact_key),
 json_pointer TEXT NOT NULL, kind TEXT NOT NULL, release_id TEXT, model_name TEXT,
 contract_symbol TEXT, observed_at TEXT, scan_date TEXT, payload_json TEXT NOT NULL,
 evidence_class TEXT NOT NULL CHECK(evidence_class='retained_observation_only'),
 eligible_for_backtest INTEGER NOT NULL CHECK(eligible_for_backtest=0),
 unavailable_reasons_json TEXT NOT NULL);
CREATE VIEW IF NOT EXISTS cirrus_tournament_release_collisions AS
 SELECT release_id, COUNT(DISTINCT artifact_key) AS manifest_versions
 FROM cirrus_tournament_observations WHERE kind='release' AND release_id IS NOT NULL
 GROUP BY release_id HAVING COUNT(DISTINCT artifact_key)>1;
'''


@dataclass(frozen=True)
class Limits:
    """Finite ingestion ceilings; lower values can be injected for constrained hosts."""
    encoded_file_bytes: int = 32 * 1024 * 1024
    decoded_file_bytes: int = 128 * 1024 * 1024
    encoded_batch_bytes: int = 128 * 1024 * 1024
    decoded_batch_bytes: int = 192 * 1024 * 1024
    retained_payload_bytes: int = 160 * 1024 * 1024
    source_files: int = 2048
    directory_entries: int = 10000
    observations: int = 250000

    def __post_init__(self):
        for field in fields(self):
            value = getattr(self, field.name)
            if isinstance(value, bool) or not isinstance(value, int) or value < 0:
                raise ValueError(f'{field.name}: nonnegative integer limit required')


def bounded_read(stream, limit, label):
    """Read at most limit+1 bytes, refusing excess before JSON materialization."""
    chunks = bytearray()
    while True:
        chunk = stream.read(min(64 * 1024, limit - len(chunks) + 1))
        if not chunk:
            return bytes(chunks)
        if len(chunks) + len(chunk) > limit:
            raise ValueError(f'{label} limit exceeded')
        chunks.extend(chunk)


def hash_stream(stream, limit):
    sha, count = hashlib.sha256(), 0
    while True:
        chunk = stream.read(min(64 * 1024, limit - count + 1))
        if not chunk:
            return sha.hexdigest(), count
        count += len(chunk)
        if count > limit:
            raise ValueError('encoded bytes limit exceeded')
        sha.update(chunk)


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def digest(value):
    return hashlib.sha256(value).hexdigest()


def timestamp(value, field):
    if value is None:
        return None
    if not isinstance(value, str):
        raise ValueError(f'{field}: timestamp must be a timezone-aware string')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ValueError(f'{field}: invalid timestamp') from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f'{field}: timezone required')
    return parsed.astimezone(timezone.utc).isoformat()


def strict_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f'duplicate JSON key: {key}')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f'non-finite JSON value: {value}')


def validate(value):
    """Reject malformed temporal/numeric evidence rather than silently coercing it."""
    if isinstance(value, dict):
        for key, item in value.items():
            if key in {'generated_at', 'recorded_at', 'entry_at', 'exit_at', 'exit_due',
                       'emailed_at', 'training_max_label_at', 'snapshot'}:
                timestamp(item, key)
            if key in {'scan_date', 'source_end', 'expiry'} and item is not None:
                if not isinstance(item, str):
                    raise ValueError(f'{key}: ISO date required')
                from datetime import date
                if date.fromisoformat(item).isoformat() != item:
                    raise ValueError(f'{key}: ISO date required')
            validate(item)
    elif isinstance(value, list):
        for item in value:
            validate(item)
    elif isinstance(value, float) and not math.isfinite(value):
        raise ValueError('non-finite number')


def classify(path):
    name = path.name
    if name.endswith('.parquet'):
        return 'parquet'
    if name.endswith('.joblib'):
        return 'model_binary'
    if name.endswith('-raw.json.gz'):
        return 'raw_quotes'
    if path.parent.name == 'reviews' and name.endswith('.json'):
        return 'release'
    if name == 'manifest.json' or (path.parent.name == 'releases' and name.endswith('.json')):
        return 'release'
    if name.endswith('-decision.json') or name == 'current_scan.json':
        return 'decision'
    if name in {'ledger.json', 'durable-ledger.json'}:
        return 'ledger'
    if name == 'delivery.json':
        return 'delivery'
    if name == 'scoreboard.json':
        return 'scoreboard'
    return None


def rows_for(kind, data):
    if kind == 'raw_quotes':
        if not isinstance(data, list):
            raise ValueError('raw_quotes must be a list')
        for index, row in enumerate(data):
            if not isinstance(row, dict) or not isinstance(row.get('quote'), dict):
                raise ValueError('raw quote row must contain quote object')
            yield f'/{index}', 'raw_quote', row, None
        return
    if not isinstance(data, dict):
        raise ValueError(f'{kind} must be an object')
    if kind == 'ledger':
        if not isinstance(data.get('positions'), list):
            raise ValueError('ledger.positions must be a list')
        for index, row in enumerate(data['positions']):
            if not isinstance(row, dict):
                raise ValueError('ledger position must be an object')
            yield f'/positions/{index}', 'quote_proxy_position', row, None
        return
    yield '', kind, data, None
    if kind == 'decision':
        selections = data.get('selections', {})
        if not isinstance(selections, dict):
            raise ValueError('decision.selections must be an object')
        for model, row in sorted(selections.items()):
            if not isinstance(row, dict):
                raise ValueError('selection must be an object')
            # Fields remain selection evidence, never a broker fill or full score matrix.
            payload = dict(row)
            payload['release_id'] = data.get('release_id')
            payload['scan_date'] = data.get('scan_date')
            escaped = model.replace('~', '~0').replace('/', '~1')
            yield f'/selections/{escaped}', 'selection', payload, model


def collect(root, namespace, limits=None):
    limits = limits or Limits()
    root = Path(root)
    if root.is_symlink():
        raise ValueError('symlink source root is unsupported')
    if not root.is_dir() or not namespace or not isinstance(namespace, str):
        raise ValueError('existing source directory and nonempty namespace required')
    artifacts, sources, observations = {}, {}, {}
    paths, pending = [], [root]
    while pending:
        with os.scandir(pending.pop()) as entries:
            for entry in entries:
                if len(paths) >= limits.directory_entries:
                    raise ValueError('directory entries limit exceeded')
                if entry.is_symlink():
                    raise ValueError('symlink source is unsupported')
                path = Path(entry.path)
                paths.append(path)
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
    encoded_total = decoded_total = payload_total = file_count = 0
    for path in sorted(paths):
        if path.is_symlink():
            raise ValueError('symlink source is unsupported')
        if not path.is_file():
            continue
        kind = classify(path)
        if kind is None:
            continue
        file_count += 1
        if file_count > limits.source_files:
            raise ValueError('source files limit exceeded')
        with path.open('rb') as stream:
            sha, byte_count = hash_stream(stream, min(limits.encoded_file_bytes, limits.encoded_batch_bytes - encoded_total))
            encoded_total += byte_count
            body = None
            if kind not in {'parquet', 'model_binary'}:
                stream.seek(0)
                ceiling = min(limits.decoded_file_bytes, limits.decoded_batch_bytes - decoded_total)
                if path.name.endswith('.gz'):
                    with gzip.GzipFile(fileobj=stream, mode='rb') as decoded:
                        body = bounded_read(decoded, ceiling, 'decoded bytes')
                else:
                    body = bounded_read(stream, ceiling, 'decoded bytes')
                decoded_total += len(body)
        artifact_key = f'cirrus|tournament|sha256|{sha}'
        status, reason = 'decoded', None
        if kind in {'parquet', 'model_binary'}:
            status = 'inventory_only'
            reason = ('parquet_decoder_not_enabled' if kind == 'parquet'
                      else 'model_deserialization_prohibited')
        artifact = (artifact_key, sha, byte_count, kind, status, reason)
        if artifact_key in artifacts and artifacts[artifact_key] != artifact:
            raise ValueError('same artifact bytes classified inconsistently')
        artifacts[artifact_key] = artifact
        relative = path.relative_to(root).as_posix()
        source_key = digest(canonical([namespace, relative, sha]).encode())
        sources[source_key] = (source_key, namespace, relative, artifact_key)
        if status != 'decoded':
            continue
        data = json.loads(body, object_pairs_hook=strict_object, parse_constant=reject_constant)
        del body
        validate(data)
        for pointer, row_kind, row, model in rows_for(kind, data):
            if len(observations) >= limits.observations and f'{artifact_key}|{pointer}' not in observations:
                raise ValueError('observations limit exceeded')
            release = row.get('release_id')
            contract = row.get('contract_symbol')
            observed = None
            if row_kind == 'raw_quote':
                contract = row['quote'].get('symbol')
                observed = timestamp(row.get('snapshot'), 'snapshot')
            elif row_kind in {'selection', 'quote_proxy_position'}:
                observed = timestamp(row.get('entry_at'), 'entry_at')
            elif row_kind == 'delivery':
                observed = timestamp(row.get('recorded_at'), 'recorded_at')
            else:
                observed = timestamp(row.get('generated_at'), 'generated_at')
            for label, value in [('release_id', release), ('contract_symbol', contract)]:
                if value is not None and (not isinstance(value, str) or not value):
                    raise ValueError(f'{label}: nonempty string required')
            if row_kind == 'quote_proxy_position':
                entry, exit_ = row.get('entry_at'), row.get('exit_at')
                if entry and exit_ and datetime.fromisoformat(timestamp(exit_, 'exit_at')) < datetime.fromisoformat(timestamp(entry, 'entry_at')):
                    raise ValueError('exit_at precedes entry_at')
            key = f'{artifact_key}|{pointer}'
            reasons = list(UNAVAILABLE)
            if row_kind == 'raw_quote':
                reasons.append('prediction_and_release_link_unavailable')
            payload = canonical(row)
            if key not in observations:
                payload_total += len(payload.encode('utf-8'))
                if payload_total > limits.retained_payload_bytes:
                    raise ValueError('retained payload bytes limit exceeded')
            observations[key] = (key, artifact_key, pointer, row_kind, release, model,
                                 contract, observed, row.get('scan_date'), payload,
                                 'retained_observation_only', 0, canonical(reasons))
    if not artifacts:
        raise ValueError('no supported retained artifacts')
    return artifacts, sources, observations


def import_artifacts(root, database, namespace, limits=None):
    """Validate the entire batch before opening DB; atomically append immutable evidence."""
    artifacts, sources, observations = collect(root, namespace, limits)
    connection = sqlite3.connect(database)
    try:
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('BEGIN IMMEDIATE')
        # executescript commits implicitly; execute each fixed statement instead.
        for statement in SCHEMA.split(';'):
            if statement.strip():
                connection.execute(statement)
        connection.execute('INSERT OR IGNORE INTO cirrus_tournament_bridge_versions VALUES (?)', (VERSION,))
        inserted = {}
        for table, records in [('cirrus_tournament_artifacts', artifacts),
                               ('cirrus_tournament_sources', sources),
                               ('cirrus_tournament_observations', observations)]:
            count = 0
            for key, record in sorted(records.items()):
                existing = connection.execute(f'SELECT * FROM {table} WHERE {"observation_key" if table.endswith("observations") else "source_key" if table.endswith("sources") else "artifact_key"}=?', (key,)).fetchone()
                if existing is not None:
                    if existing != record:
                        raise ValueError(f'immutable evidence collision in {table}')
                    continue
                connection.execute(f'INSERT INTO {table} VALUES ({",".join("?" for _ in record)})', record)
                count += 1
            inserted[table] = count
        connection.commit()
        return {'schema_version': VERSION, 'inserted': inserted,
                'batch_artifacts': len(artifacts), 'batch_observations': len(observations),
                'eligible_for_backtest': 0, 'unavailable_reasons': UNAVAILABLE}
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', required=True)
    parser.add_argument('--database', required=True, help='Local scratch mart SQLite path')
    parser.add_argument('--source-namespace', required=True, help='Stable archive/run identity')
    args = parser.parse_args()
    print(json.dumps(import_artifacts(args.source, args.database, args.source_namespace), indent=2))


if __name__ == '__main__':
    main()
