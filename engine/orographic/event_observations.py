"""Offline, append-only event-vintage observations; never execution evidence.

No network, current clock, ticker inference, imputation, or production imports.
The ten-table execution mart is intentionally not modified.
"""
from __future__ import annotations

import hashlib
import json
import math
import sqlite3
from datetime import date, datetime, timezone

VERSION = 'atmosphere-event-observations-v1'
# These are bounded capabilities, not a claim that every document on a site is licensed.
REGISTRY = {
    'cirrus_retained_earnings': ('review_required', 'Existing provider-derived earnings evidence; historical and redistribution rights unverified.'),
    'cirrus_tier1_calendar': ('review_required', 'Existing curated calendar; URL attribution is not vintage or licensing proof.'),
    'bls_public_archive': ('public_domain_reviewed', 'BLS-authored public-domain archive only; no API ingestion; publication time must be evidenced.'),
    'sec_public_filing': ('review_required', 'Public accessibility and no API key do not prove all reuse rights or event timing.'),
    'fred_alfred': ('blocked_permission_required', 'Do not ingest FRED or ALFRED without a separately authorized permission review.'),
    'synthetic_fixture': ('fixture_only', 'Synthetic unit-test evidence only; never production/backtest readiness.'),
}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS event_observation_versions(version TEXT PRIMARY KEY);
CREATE TABLE IF NOT EXISTS event_source_registry(
 source_id TEXT PRIMARY KEY, permitted_use_status TEXT NOT NULL, scope TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS event_source_artifacts(
 sha256 TEXT PRIMARY KEY, source_id TEXT NOT NULL, raw_bytes BLOB NOT NULL,
 FOREIGN KEY(source_id) REFERENCES event_source_registry(source_id));
CREATE TABLE IF NOT EXISTS event_observation_vintages(
 observation_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL, json_pointer TEXT NOT NULL,
 source_id TEXT NOT NULL, event_id TEXT, issuer_id TEXT, ticker TEXT, kind TEXT NOT NULL,
 scheduled_at TEXT, event_date TEXT, released_at TEXT,
 first_public_at TEXT, first_seen_at TEXT, available_at TEXT,
 revision_id TEXT, values_json TEXT NOT NULL, payload_json TEXT NOT NULL,
 provenance_json TEXT NOT NULL, unavailable_reasons_json TEXT NOT NULL,
 evidence_class TEXT NOT NULL CHECK(evidence_class='event_observation_only'),
 execution_ready INTEGER NOT NULL CHECK(execution_ready=0),
 FOREIGN KEY(artifact_sha256) REFERENCES event_source_artifacts(sha256));
CREATE INDEX IF NOT EXISTS event_observation_lookup
 ON event_observation_vintages(source_id,event_id,available_at);
'''
TIME_FIELDS = ('scheduled_at', 'released_at', 'first_public_at', 'first_seen_at', 'available_at')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def sha256(raw):
    return hashlib.sha256(raw).hexdigest()


def timestamp(value):
    if value is None:
        return None
    if not isinstance(value, str) or 'T' not in value:
        raise ValueError('timestamp must include time and timezone; date-only is unavailable')
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError('timezone required')
    return parsed.astimezone(timezone.utc).isoformat(timespec='microseconds')


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError('duplicate JSON key: ' + key)
            result[key] = value
        return result
    def constant(value):
        raise ValueError('nonfinite JSON number: ' + value)
    result = json.loads(raw, object_pairs_hook=pairs, parse_constant=constant)
    canonical(result)  # Also catches float overflow (1e999).
    return result


def initialize(conn):
    conn.executescript(SCHEMA)
    versions = {r[0] for r in conn.execute('SELECT version FROM event_observation_versions')}
    if versions and versions != {VERSION}:
        raise ValueError('unsupported observation extension version')
    conn.execute('INSERT OR IGNORE INTO event_observation_versions VALUES(?)', (VERSION,))
    for key, (status, scope) in REGISTRY.items():
        existing = conn.execute('SELECT permitted_use_status,scope FROM event_source_registry WHERE source_id=?', (key,)).fetchone()
        if existing is not None and tuple(existing) != (status, scope):
            raise ValueError('source registry differs from reviewed policy')
        conn.execute('INSERT OR IGNORE INTO event_source_registry VALUES(?,?,?)', (key, status, scope))


def _text(value, field):
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ': nonempty string required')
    return value


def _quantity(value, field):
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {'value', 'unit'}:
        raise ValueError(field + ': requires explicit value and unit')
    number = value['value']
    if isinstance(number, bool) or not isinstance(number, (int, float)) or not math.isfinite(number):
        raise ValueError(field + ': finite numeric value required')
    _text(value['unit'], field + '.unit')
    if value['unit'] is None:
        raise ValueError(field + ': unit unavailable')
    return value


def normalize(record, *, source_id, artifact_sha256, pointer, provenance=None):
    """Normalize an explicit observation, preserving missingness and original payload.

    Provenance is reviewed per-record evidence, never generated/file-modification time.
    It must include field_evidence references for any supplied historical timestamps.
    A reference is retained, not automatically verified by this offline module.
    """
    if not isinstance(record, dict):
        raise ValueError('event must be an object')
    if source_id not in REGISTRY or REGISTRY[source_id][0].startswith('blocked'):
        raise ValueError('unknown or blocked source')
    provenance = provenance or {}
    canonical(provenance)
    evidence = provenance.get('field_evidence', {})
    if not isinstance(evidence, dict):
        raise ValueError('field_evidence must be an object')
    row = {key: _text(record.get(key), key) for key in ('event_id', 'issuer_id', 'ticker', 'revision_id')}
    row['kind'] = record.get('kind')
    if row['kind'] not in {'earnings_schedule', 'macro_schedule', 'macro_release', 'earnings_release'}:
        raise ValueError('unsupported event kind')
    reasons = []
    if record.get('status') not in (None, 'scheduled', 'released', 'confirmed') or record.get('error'):
        reasons.append('source_error_or_nonactive_status')
    if source_id != 'synthetic_fixture' and (provenance.get('review_status') != 'reviewed' or
            not isinstance(provenance.get('review_reference'), str) or not provenance['review_reference'].strip()):
        reasons.append('provenance_review_unavailable')
    for field in TIME_FIELDS:
        value = record.get(field)
        row[field] = timestamp(value)
        if value is not None and not isinstance(evidence.get(field), str):
            reasons.append(field + '_evidence_unavailable')
        elif value is not None and not evidence[field].strip():
            reasons.append(field + '_evidence_unavailable')
    event_date = record.get('event_date')
    if event_date is not None and (not isinstance(event_date, str) or date.fromisoformat(event_date).isoformat() != event_date):
        raise ValueError('event_date must be ISO date')
    row['event_date'] = event_date
    for field in ('first_public_at', 'first_seen_at', 'available_at'):
        if row[field] is None:
            reasons.append(field + '_unavailable')
    if row['scheduled_at'] is None and row['kind'].endswith('schedule'):
        reasons.append('precise_schedule_unavailable')
    if row['event_id'] is None:
        reasons.append('stable_event_id_unavailable')
    if row['kind'].startswith('earnings') and row['issuer_id'] is None:
        reasons.append('stable_issuer_id_unavailable')
    if row['available_at'] is not None:
        for field in ('first_public_at', 'first_seen_at'):
            if row[field] is not None and row[field] > row['available_at']:
                reasons.append('available_before_' + field)
    values = {field: _quantity(record.get(field), field) for field in ('actual', 'expected', 'prior')}
    if any(v is not None for v in values.values()) and row['kind'].endswith('schedule'):
        reasons.append('release_values_in_schedule')
    if row['kind'].endswith('release') and row['released_at'] is None:
        reasons.append('release_time_unavailable')
    if row['released_at'] and row['available_at'] and row['released_at'] > row['available_at'] and values['actual'] is not None:
        reasons.append('actual_available_before_release')
    status = REGISTRY[source_id][0]
    if status not in {'public_domain_reviewed', 'fixture_only'}:
        reasons.append('source_use_not_reviewed')
    row.update(source_id=source_id, artifact_sha256=artifact_sha256, json_pointer=pointer,
               values_json=canonical(values), payload_json=canonical(record), provenance_json=canonical(provenance),
               unavailable_reasons_json=canonical(sorted(set(reasons))),
               evidence_class='event_observation_only', execution_ready=0)
    row['observation_id'] = sha256(canonical(row).encode())
    return row


def ingest(conn, raw, *, source_id, records, provenance_by_pointer=None):
    """Append exact artifact bytes plus (JSON pointer, record) observations atomically.

    Pointer resolution binds the normalized record to retained source bytes. This API
    takes canonical event records. Use adapters below for existing source shapes.
    """
    if source_id not in REGISTRY or REGISTRY[source_id][0].startswith('blocked'):
        raise ValueError('unknown or blocked source')
    document = strict_json(raw)
    digest = sha256(raw)
    normalized = []
    for pointer, record in records:
        original = resolve_pointer(document, pointer)
        if canonical(original) != canonical(record):
            raise ValueError('record does not match retained source pointer')
        normalized.append(normalize(record, source_id=source_id, artifact_sha256=digest, pointer=pointer,
                                    provenance=(provenance_by_pointer or {}).get(pointer)))
    return _persist(conn, raw, source_id, normalized)


def resolve_pointer(document, pointer):
    if pointer == '':
        return document
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ValueError('invalid JSON pointer')
    node = document
    for segment in pointer[1:].split('/'):
        segment = segment.replace('~1', '/').replace('~0', '~')
        if isinstance(node, list):
            if not segment.isdecimal() or (len(segment) > 1 and segment.startswith('0')):
                raise ValueError('invalid array pointer')
            node = node[int(segment)]
        else:
            node = node[segment]
    return node


def _persist(conn, raw, source_id, rows):
    initialize(conn)
    with conn:
        conn.execute('INSERT OR IGNORE INTO event_source_artifacts VALUES(?,?,?)', (sha256(raw), source_id, raw))
        # Same bytes attributed to two sources are not silently relabeled.
        stored = conn.execute('SELECT source_id FROM event_source_artifacts WHERE sha256=?', (sha256(raw),)).fetchone()[0]
        if stored != source_id:
            raise ValueError('artifact source attribution conflict')
        for row in rows:
            fields = tuple(row)
            conn.execute('INSERT OR IGNORE INTO event_observation_vintages (' + ','.join(fields) + ') VALUES (' + ','.join('?' for _ in fields) + ')', tuple(row[k] for k in fields))
    return [row['observation_id'] for row in rows]


def ingest_cirrus(conn, raw, *, kind, candidate_pointer=''):
    """Retain existing candidate earnings_date or Tier-1 events[].release_at.

    Those fields are schedules, NOT public/seen/available timestamps. Never infer
    stable issuer IDs from tickers or vintage times from generated_at/scan dates.
    """
    document = strict_json(raw)
    if kind == 'tier1':
        source_id = 'cirrus_tier1_calendar'
        originals = document.get('events') if isinstance(document, dict) else None
        if not isinstance(originals, list):
            raise ValueError('Tier-1 events must be a list')
        pairs = [('/events/' + str(i), item) for i, item in enumerate(originals)]
    elif kind == 'earnings':
        source_id = 'cirrus_retained_earnings'
        candidates = resolve_pointer(document, candidate_pointer)
        if not isinstance(candidates, list):
            raise ValueError('earnings adapter expects a retained candidate array at candidate_pointer')
        pairs = [(candidate_pointer + '/' + str(i), item) for i, item in enumerate(candidates)]
    else:
        raise ValueError('unsupported Cirrus adapter')
    rows = []
    for pointer, original in pairs:
        if not isinstance(original, dict):
            raise ValueError('source event must be an object')
        if kind == 'earnings' and original.get('earnings_date') is None:
            continue
        record = {'kind': 'macro_schedule' if kind == 'tier1' else 'earnings_schedule',
                  'status': original.get('status'), 'error': original.get('error')}
        if isinstance(document, dict) and document.get('error'):
            record['error'] = document['error']
        if kind == 'earnings':
            record.update(event_date=original['earnings_date'], ticker=original.get('ticker') or original.get('symbol'))
        else:
            record['event_id'] = original.get('event_id')
            record['revision_id'] = original.get('revision_id')
            raw_time = original.get('release_at')
            if isinstance(raw_time, str) and len(raw_time) == 10:
                record['event_date'] = raw_time
            else:
                record['scheduled_at'] = raw_time
        row = normalize(record, source_id=source_id, artifact_sha256=sha256(raw), pointer=pointer)
        row['payload_json'] = canonical(original)
        row['observation_id'] = sha256(canonical({k: v for k, v in row.items() if k != 'observation_id'}).encode())
        rows.append(row)
    return _persist(conn, raw, source_id, rows)


def as_of(conn, cutoff, *, issuer_id=None, allow_fixtures=False):
    """Return per-source stable-event vintages known at or before an exact cutoff.

    Schedule times may be future: they are prospective announcements. Release
    actuals cannot be used before released_at. No cross-provider precedence is
    invented. Conflicting same-availability vintages are excluded as a group.
    Returned rows are research event inputs only, never execution eligibility.
    """
    cutoff = timestamp(cutoff)
    if cutoff is None:
        raise ValueError('cutoff required')
    cursor = conn.execute('SELECT * FROM event_observation_vintages')
    cursor.row_factory = sqlite3.Row
    groups = {}
    for fetched in cursor:
        row = dict(fetched)
        if issuer_id is not None and row['issuer_id'] != issuer_id:
            continue
        if not row['available_at'] or row['available_at'] > cutoff:
            continue
        key = (row['source_id'], row['event_id'], row['issuer_id'])
        groups.setdefault(key, []).append(row)
    selected = []
    for rows in groups.values():
        latest = max(row['available_at'] for row in rows)
        candidates = [row for row in rows if row['available_at'] == latest]
        # Payload differs even if two vintages claim an identical revision ID.
        semantics = {(r['payload_json'], r['provenance_json']) for r in candidates}
        if len(semantics) != 1:
            continue
        row = sorted(candidates, key=lambda r: r['observation_id'])[0]
        if json.loads(row['unavailable_reasons_json']):
            continue
        if row['source_id'] == 'synthetic_fixture' and not allow_fixtures:
            continue
        if any(row[field] is None or row[field] > cutoff for field in ('first_public_at', 'first_seen_at')):
            continue
        if row['kind'].endswith('release') and (row['released_at'] is None or row['released_at'] > cutoff):
            continue
        selected.append(row)
    return sorted(selected, key=lambda r: (r['source_id'], r['event_id'], r['issuer_id'] or ''))


def readiness(conn):
    count = conn.execute('SELECT COUNT(*) FROM event_observation_vintages').fetchone()[0]
    return {'schema_version': VERSION, 'observation_count': count, 'execution_ready': False,
            'backtest_ready': False, 'unavailable_reasons': ['event_inputs_are_not_execution_evidence',
            'coverage_and_missingness_not_attested', 'corporate_action_and_symbol_history_not_implemented']}
