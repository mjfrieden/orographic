"""Private local observation Parquet bundles beside the unchanged execution mart.

SQLite is only the reviewed adapters' staging input. Consumers read verified
Parquet. This module never publishes, activates a consumer, or grants eligibility.
Inputs and output must be private, quiescent, local paths (not hostile storage).
"""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile

import pyarrow as pa
import pyarrow.parquet as pq

from . import cirrus_tournament_evidence as tournament
from . import event_observations as events
from .shared_research_mart import TABLE_CONTRACTS, validate_shared_research_mart

VERSION = 'atmosphere_observation_parquet_v1'
NAMESPACE = 'observation_bundles/v1'
MANIFEST = 'observation_manifest.json'
MAX_ROWS = 250000
MAX_BYTES = 256 * 1024 * 1024
MAX_DATABASE_BYTES = 512 * 1024 * 1024
MAX_MANIFEST_BYTES = 65536
GROUPS = {
    'tournament': ('cirrus_tournament_bridge_versions', 'cirrus_tournament_artifacts',
                   'cirrus_tournament_sources', 'cirrus_tournament_observations'),
    'event': ('event_observation_versions', 'event_source_registry',
              'event_source_artifacts', 'event_observation_vintages'),
}


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def contracts():
    with sqlite3.connect(':memory:') as conn:
        conn.executescript(tournament.SCHEMA)
        conn.executescript(events.SCHEMA)
        return {name: list(conn.execute('PRAGMA table_info(' + name + ')'))
                for names in GROUPS.values() for name in names}


CONTRACTS = contracts()
SCHEMAS = {name: pa.schema([(r[1], {'TEXT': pa.string(), 'INTEGER': pa.int64(),
                                    'BLOB': pa.binary()}[r[2]]) for r in rows])
           for name, rows in CONTRACTS.items()}


def _cell_bytes(values):
    """Identical writer/reader budget: UTF-8 text, binary length, eight otherwise."""
    return sum(len(v.encode()) if isinstance(v, str) else len(v) if isinstance(v, bytes) else 8
               for v in values)


def _manifest_contract(manifest):
    root_keys = {'bundle_id', 'schema_version', 'native_mart_id', 'native_manifest_sha256',
                 'artifacts', 'source_versions', 'summary', 'classification'}
    if not isinstance(manifest, dict) or set(manifest) != root_keys:
        raise ValueError('manifest field allowlist mismatch')
    for field in ('bundle_id', 'native_mart_id', 'native_manifest_sha256'):
        value = manifest[field]
        if not isinstance(value, str) or len(value) != 64 or any(c not in '0123456789abcdef' for c in value):
            raise ValueError('manifest hash format mismatch')
    if manifest['schema_version'] != VERSION or manifest['classification'] != 'private_observation_only':
        raise ValueError('manifest version or classification mismatch')
    if manifest['source_versions'] != {'tournament': tournament.VERSION, 'event': events.VERSION}:
        raise ValueError('source version mismatch')
    summary = manifest['summary']
    if not isinstance(summary, dict) or set(summary) != {'rows', 'rows_with_unavailable_reasons', 'eligible_for_backtest', 'execution_ready'}:
        raise ValueError('summary field allowlist mismatch')
    for field in ('rows', 'rows_with_unavailable_reasons', 'eligible_for_backtest'):
        if type(summary[field]) is not int or not 0 <= summary[field] <= MAX_ROWS:
            raise ValueError('summary integer mismatch')
    if summary['eligible_for_backtest'] != 0 or summary['execution_ready'] is not False:
        raise ValueError('observation guard mismatch')
    if not isinstance(manifest['artifacts'], dict) or set(manifest['artifacts']) != set(CONTRACTS):
        raise ValueError('observation table set mismatch')
    for name, artifact in manifest['artifacts'].items():
        if not isinstance(artifact, dict) or set(artifact) != {'path', 'sha256', 'rows', 'columns', 'primary_key'}:
            raise ValueError('artifact field allowlist mismatch')
        digest = artifact['sha256']
        if not isinstance(digest, str) or len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
            raise ValueError('artifact hash format mismatch')
        if type(artifact['rows']) is not int or not 0 <= artifact['rows'] <= MAX_ROWS:
            raise ValueError('artifact row integer mismatch')
        if artifact['path'] != name + '-' + digest + '.parquet':
            raise ValueError('artifact path mismatch')
        if artifact['columns'] != SCHEMAS[name].names or artifact['primary_key'] != [r[1] for r in CONTRACTS[name] if r[5]]:
            raise ValueError('artifact contract mismatch')


def _private_directory(root):
    # Reject symlinks in the full existing path, including ancestors.
    for path in [root, *root.parents]:
        if path.is_symlink():
            raise ValueError('symlink path unsupported')
    root.mkdir(parents=True, exist_ok=True, mode=0o700)


def _open_database(stack, path, group):
    path = Path(path).absolute()
    if not path.is_file() or any(p.is_symlink() for p in [path, *path.parents]):
        raise ValueError('regular local database required')
    if path.stat().st_size > MAX_DATABASE_BYTES:
        raise ValueError('database byte limit exceeded')
    conn = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
    stack.callback(conn.close)
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    conn.execute('BEGIN')
    for name in GROUPS[group]:
        obj = conn.execute('SELECT type FROM sqlite_master WHERE name=?', (name,)).fetchone()
        if obj != ('table',) or list(conn.execute('PRAGMA table_info(' + name + ')')) != CONTRACTS[name]:
            raise ValueError('input schema mismatch: ' + name)
    if conn.execute('PRAGMA foreign_key_check').fetchone():
        raise ValueError('input foreign key failure')
    version_table = GROUPS[group][0]
    version = tournament.VERSION if group == 'tournament' else events.VERSION
    if conn.execute('SELECT version FROM ' + version_table).fetchall() != [(version,)]:
        raise ValueError('input version mismatch')
    if group == 'event':
        expected = sorted((k, *v) for k, v in events.REGISTRY.items())
        if conn.execute('SELECT * FROM event_source_registry ORDER BY source_id').fetchall() != expected:
            raise ValueError('source-use registry mismatch')
    return conn


def _check_row(name, row):
    if name == 'cirrus_tournament_observations':
        if row['eligible_for_backtest'] != 0 or row['evidence_class'] != 'retained_observation_only':
            raise ValueError('tournament eligibility escalation')
        if row['observation_key'] != row['artifact_key'] + '|' + row['json_pointer']:
            raise ValueError('tournament identity mismatch')
        if not set(tournament.UNAVAILABLE).issubset(json.loads(row['unavailable_reasons_json'])):
            raise ValueError('missing tournament witness reasons')
    for field in ('sha256', 'artifact_sha256'):
        if field in row and (not isinstance(row[field], str) or len(row[field]) != 64 or any(c not in '0123456789abcdef' for c in row[field])):
            raise ValueError('invalid source hash')
    for column in CONTRACTS[name]:
        if (column[3] or column[5]) and row[column[1]] is None:
            raise ValueError('missing required observation field')
    if name == 'cirrus_tournament_artifacts':
        if row['artifact_key'] != 'cirrus|tournament|sha256|' + row['sha256']:
            raise ValueError('artifact identity mismatch')
    if name == 'event_source_artifacts':
        if hashlib.sha256(row['raw_bytes']).hexdigest() != row['sha256']:
            raise ValueError('event source hash mismatch')
        if row['source_id'] not in events.REGISTRY or events.REGISTRY[row['source_id']][0].startswith('blocked'):
            raise ValueError('blocked event source')
    if name == 'event_observation_vintages':
        if row['execution_ready'] != 0 or row['evidence_class'] != 'event_observation_only':
            raise ValueError('event eligibility escalation')
        identity = {k: v for k, v in row.items() if k != 'observation_id'}
        if events.sha256(events.canonical(identity).encode()) != row['observation_id']:
            raise ValueError('event observation identity mismatch')
    if name.endswith(('observations', 'vintages')):
        reasons = json.loads(row['unavailable_reasons_json'])
        if not isinstance(reasons, list) or any(not isinstance(r, str) for r in reasons):
            raise ValueError('invalid unavailable reasons')


def _fsync_dir(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def write_bundle(mart_dir, *, tournament_database, event_database):
    """Export both reviewed staging snapshots into native, typed Parquet.

    Content-addressed files are installed before one atomic manifest replace.
    Failure leaves the previous manifest valid; orphan files are harmless.
    Only this fixed child namespace is written. No execution mart writes.
    """
    mart = Path(mart_dir).absolute()
    _private_directory(mart)
    native = validate_shared_research_mart(mart)
    before = sha(mart / 'mart_manifest.json')
    output = mart / NAMESPACE
    _private_directory(output)
    artifacts = {}
    total_rows = total_bytes = missing_rows = 0
    with ExitStack() as stack, tempfile.TemporaryDirectory(prefix='.staging-', dir=output) as staging_dir:
        staging = Path(staging_dir)
        for group, db in [('tournament', tournament_database), ('event', event_database)]:
            conn = _open_database(stack, db, group)
            for name in GROUPS[group]:
                if name in TABLE_CONTRACTS:
                    raise ValueError('execution table collision')
                fields = [r[1] for r in CONTRACTS[name]]
                key = [r[1] for r in CONTRACTS[name] if r[5]]
                cursor = conn.execute('SELECT * FROM ' + name + ' ORDER BY ' + ','.join(key))
                path = staging / (name + '.parquet')
                count = 0
                with pq.ParquetWriter(path, SCHEMAS[name], compression='zstd', version='2.6') as writer:
                    while batch := cursor.fetchmany(1024):
                        records = []
                        for values in batch:
                            total_rows += 1
                            total_bytes += _cell_bytes(values)
                            if total_rows > MAX_ROWS or total_bytes > MAX_BYTES:
                                raise ValueError('observation bundle limits exceeded')
                            row = dict(zip(fields, values))
                            _check_row(name, row)
                            if 'unavailable_reasons_json' in row and json.loads(row['unavailable_reasons_json']):
                                missing_rows += 1
                            records.append(row)
                        writer.write_table(pa.Table.from_pylist(records, schema=SCHEMAS[name]))
                        count += len(records)
                os.chmod(path, 0o600)
                with path.open('rb') as f:
                    os.fsync(f.fileno())
                digest = sha(path)
                filename = name + '-' + digest + '.parquet'
                destination = output / filename
                if destination.exists():
                    if destination.is_symlink() or sha(destination) != digest:
                        raise ValueError('existing content-addressed file corrupted')
                else:
                    os.replace(path, destination)
                artifacts[name] = {'path': filename, 'sha256': digest, 'rows': count,
                                   'columns': fields, 'primary_key': key}
        identity = {'schema_version': VERSION, 'native_mart_id': native['mart_id'],
                    'native_manifest_sha256': before, 'artifacts': artifacts,
                    'source_versions': {'tournament': tournament.VERSION, 'event': events.VERSION},
                    'summary': {'rows': total_rows, 'rows_with_unavailable_reasons': missing_rows,
                                'eligible_for_backtest': 0, 'execution_ready': False},
                    'classification': 'private_observation_only'}
        manifest = {'bundle_id': hashlib.sha256(canonical(identity).encode()).hexdigest(), **identity}
        encoded = (canonical(manifest) + '\n').encode()
        if len(encoded) > MAX_MANIFEST_BYTES:
            raise ValueError('manifest size limit')
        # Validate native mart again, detecting concurrent changes before commit.
        validate_shared_research_mart(mart)
        if sha(mart / 'mart_manifest.json') != before:
            raise ValueError('native mart changed during export')
        manifest_path = staging / MANIFEST
        with manifest_path.open('wb') as f:
            os.chmod(manifest_path, 0o600)
            f.write(encoded)
            f.flush()
            os.fsync(f.fileno())
        # The exact consumer validator must accept every staged bundle before
        # its manifest can replace the previous valid commit.
        _read_verified(mart, native, output, manifest_path)
        _fsync_dir(output)
        os.replace(manifest_path, output / MANIFEST)
        _fsync_dir(output)
    return manifest


def read_bundle(mart_dir):
    """Verify native coexistence, manifest, hashes, schemas, rows, and guard fields.

    Returns typed Arrow tables. No implicit execution or as-of eligibility view.
    """
    mart = Path(mart_dir).absolute()
    native = validate_shared_research_mart(mart)
    output = mart / NAMESPACE
    return _read_verified(mart, native, output, output / MANIFEST)


def _read_verified(mart, native, output, path):
    if any(p.is_symlink() for p in [path, *path.parents]) or path.stat().st_size > MAX_MANIFEST_BYTES:
        raise ValueError('unsafe manifest')
    manifest = events.strict_json(path.read_bytes())
    _manifest_contract(manifest)
    identity = {k: v for k, v in manifest.items() if k != 'bundle_id'}
    if manifest.get('schema_version') != VERSION or manifest.get('bundle_id') != hashlib.sha256(canonical(identity).encode()).hexdigest():
        raise ValueError('invalid observation manifest')
    if manifest.get('native_mart_id') != native['mart_id'] or manifest.get('native_manifest_sha256') != sha(mart / 'mart_manifest.json'):
        raise ValueError('observation bundle belongs to a different native snapshot')
    if set(manifest['artifacts']) != set(CONTRACTS):
        raise ValueError('observation table set mismatch')
    if manifest.get('classification') != 'private_observation_only' or manifest['summary']['eligible_for_backtest'] != 0 or manifest['summary']['execution_ready'] is not False:
        raise ValueError('observation guard mismatch')
    tables = {}
    total_rows = total_bytes = missing = 0
    for name in CONTRACTS:
        artifact = manifest['artifacts'][name]
        expected = name + '-' + artifact['sha256'] + '.parquet'
        if artifact['path'] != expected or Path(expected).name != expected:
            raise ValueError('unsafe artifact path')
        path = output / expected
        if path.is_symlink() or path.stat().st_size > MAX_DATABASE_BYTES or sha(path) != artifact['sha256']:
            raise ValueError('artifact hash mismatch')
        parquet = pq.ParquetFile(path)
        if parquet.schema_arrow != SCHEMAS[name] or parquet.metadata.num_rows != artifact['rows']:
            raise ValueError('artifact schema or row mismatch')
        key = [r[1] for r in CONTRACTS[name] if r[5]]
        if artifact['columns'] != SCHEMAS[name].names or artifact['primary_key'] != key:
            raise ValueError('artifact contract mismatch')
        batches = []
        previous = None
        for batch in parquet.iter_batches(batch_size=1024):
            total_rows += batch.num_rows
            if total_rows > MAX_ROWS:
                raise ValueError('observation bundle limits exceeded')
            for row in batch.to_pylist():
                total_bytes += _cell_bytes(row.values())
                if total_bytes > MAX_BYTES:
                    raise ValueError('observation bundle limits exceeded')
                _check_row(name, row)
                current = tuple(row[k] for k in key)
                if any(v is None for v in current) or previous is not None and current <= previous:
                    raise ValueError('nonunique or unordered observation key')
                previous = current
                missing += bool(json.loads(row.get('unavailable_reasons_json', '[]')))
            batches.append(batch)
        tables[name] = pa.Table.from_batches(batches, schema=SCHEMAS[name])
    if manifest['summary']['rows'] != total_rows or manifest['summary']['rows_with_unavailable_reasons'] != missing:
        raise ValueError('summary mismatch')
    if manifest['source_versions'] != {'tournament': tournament.VERSION, 'event': events.VERSION}:
        raise ValueError('source version mismatch')
    if set(manifest['summary']) != {'rows', 'rows_with_unavailable_reasons', 'eligible_for_backtest', 'execution_ready'}:
        raise ValueError('unbounded summary fields')
    for group, module in [('tournament', tournament), ('event', events)]:
        if tables[GROUPS[group][0]].to_pylist() != [{'version': module.VERSION}]:
            raise ValueError('table version mismatch')
    registry = tables['event_source_registry'].to_pylist()
    if registry != [{'source_id': k, 'permitted_use_status': v[0], 'scope': v[1]} for k, v in sorted(events.REGISTRY.items())]:
        raise ValueError('source-use registry mismatch')
    tournament_hashes = set(tables['cirrus_tournament_artifacts']['artifact_key'].to_pylist())
    event_sources = {r['sha256']: r['source_id'] for r in tables['event_source_artifacts'].to_pylist()}
    for name in ('cirrus_tournament_sources', 'cirrus_tournament_observations'):
        if not set(tables[name]['artifact_key'].to_pylist()).issubset(tournament_hashes):
            raise ValueError('orphan tournament artifact')
    for row in tables['event_observation_vintages'].to_pylist():
        if event_sources.get(row['artifact_sha256']) != row['source_id']:
            raise ValueError('event source attribution mismatch')
    return manifest, tables
