"""Offline reviewed extraction, not an official-site parser or receipt authenticator.

Every imported observation is reconstructed history, with null as-of clocks.
No network, clock, collector, model input, schedule, or execution path is added.
"""
from __future__ import annotations

import base64
from datetime import date
from decimal import Decimal
import re
import unicodedata

from . import event_observations as events

VERSION = 'official-macro-reviewed-extraction-v1'
MAX_BYTES = 1024 * 1024
MAX_SIDECAR_BYTES = 16 * 1024
MONTHS = {name: i for i, name in enumerate(('January February March April May June July August September October November December').split(), 1)}
METRICS = {
    'cpi_all_items_sa_mom': ('cpi', 'percent', 'pct_mom', 'Consumer Price Index for All Urban Consumers', 'seasonally adjusted'),
    'nonfarm_payroll_sa_change': ('empsit', 'jobs', 'persons_change', 'Total nonfarm payroll employment', 'seasonally adjusted'),
}


def _text(value, name, maximum=300):
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ValueError(name + ': bounded nonempty text required')
    return value


def source(url):
    """Exact archive path families only; never dereferences a URL."""
    _text(url, 'source_url')
    bls = re.fullmatch(r'https://www\.bls\.gov/news\.release/archives/(cpi|empsit)_([0-9]{2})([0-9]{2})([0-9]{4})\.htm', url)
    fed = re.fullmatch(r'https://www\.federalreserve\.gov/newsevents/pressreleases/monetary([0-9]{4})([0-9]{2})([0-9]{2})a\.htm', url)
    if bls:
        day = date(int(bls[4]), int(bls[2]), int(bls[3]))
        return 'bls_public_archive', bls[1], day.isoformat()
    if fed:
        day = date(int(fed[1]), int(fed[2]), int(fed[3]))
        return 'fed_public_statement', 'fomc_statement', day.isoformat()
    raise ValueError('source outside bounded official archive allowlist')


def _span(raw, item, name):
    if not isinstance(item, dict) or set(item) != {'start', 'end', 'text'}:
        raise ValueError(name + ': exact byte span required')
    start, end = item['start'], item['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(raw):
        raise ValueError(name + ': invalid byte offsets')
    text = _text(item['text'], name, 1000)
    if raw[start:end] != text.encode('utf-8'):
        raise ValueError(name + ': byte span does not match source')
    return text


def _receipt(receipt, url, digest):
    # These clocks remain claims in provenance; this function cannot authenticate them.
    if receipt is None:
        return None
    fields = {'source_url', 'final_url', 'artifact_sha256', 'request_at', 'received_at', 'status', 'complete'}
    if not isinstance(receipt, dict) or set(receipt) != fields:
        raise ValueError('unsupported receipt claim')
    if receipt['source_url'] != url or receipt['final_url'] != url or receipt['artifact_sha256'] != digest:
        raise ValueError('receipt claim is not bound to this source and artifact')
    if type(receipt['status']) is not int or receipt['status'] != 200 or receipt['complete'] is not True:
        raise ValueError('receipt claim is incomplete or unsuccessful')
    begin, end = events.timestamp(receipt['request_at']), events.timestamp(receipt['received_at'])
    if begin is None or end is None or begin > end:
        raise ValueError('invalid receipt claim clocks')
    return dict(receipt, request_at=begin, received_at=end)


def prepare(raw, sidecar_raw):
    """Bind a reviewed local JSON extraction to exact retained UTF-8 archive bytes.

    Supported formats: BLS monthly metric extraction and FOMC statement metadata.
    This is our sidecar format, not a claim about an official JSON/HTML schema.
    Values and review assertions are retained as reconstructed evidence only.
    """
    if not isinstance(raw, bytes) or not 0 < len(raw) <= MAX_BYTES:
        raise ValueError('raw source must be 1..1048576 bytes')
    raw.decode('utf-8', errors='strict')
    if not isinstance(sidecar_raw, bytes) or not 0 < len(sidecar_raw) <= MAX_SIDECAR_BYTES:
        raise ValueError('sidecar must be 1..16384 bytes')
    sidecar = events.strict_json(sidecar_raw)
    required = {'schema_version', 'source_url', 'artifact_sha256', 'review_reference', 'rights_review_reference',
                'vintage_class', 'title', 'metric', 'publication_claim', 'receipt_claim'}
    if not isinstance(sidecar, dict) or set(sidecar) != required or sidecar['schema_version'] != VERSION:
        raise ValueError('unsupported extraction sidecar')
    digest = events.sha256(raw)
    if sidecar['artifact_sha256'] != digest:
        raise ValueError('source hash mismatch')
    review = _text(sidecar['review_reference'], 'review_reference')
    _text(sidecar['rights_review_reference'], 'rights_review_reference')
    if sidecar['vintage_class'] not in ('first_release_claim', 'revision_claim', 'unknown'):
        raise ValueError('unrecognized vintage class')
    source_id, family, document_date = source(sidecar['source_url'])
    title = _span(raw, sidecar['title'], 'title')
    expected_title = {'cpi': 'CONSUMER PRICE INDEX', 'empsit': 'THE EMPLOYMENT SITUATION',
                      'fomc_statement': 'Federal Reserve issues FOMC statement'}[family]
    if title != expected_title:
        raise ValueError('title does not match source family')
    publication_claim = sidecar['publication_claim']
    if publication_claim is not None:
        # A publication/embargo line is retained, never translated into release or availability time.
        _span(raw, publication_claim, 'publication_claim')
    receipt = _receipt(sidecar['receipt_claim'], sidecar['source_url'], digest)
    record = dict(kind='macro_release', status='released', event_id=None,
                  scheduled_at=None, event_date=None, released_at=None,
                  first_public_at=None, first_seen_at=None, available_at=None,
                  revision_id=digest, actual=None, expected=None, prior=None)
    metric = sidecar['metric']
    if family == 'fomc_statement':
        if metric is not None:
            raise ValueError('FOMC adapter accepts metadata only, no inferred rate or consensus')
        record['event_id'] = 'fomc_statement_document:' + document_date
    else:
        fields = {'name', 'period', 'context', 'adjustment', 'unit', 'actual'}
        if not isinstance(metric, dict) or set(metric) != fields or not isinstance(metric['name'], str) or metric['name'] not in METRICS:
            raise ValueError('supported explicit BLS metric required')
        metric_family, unit_text, unit, context, adjustment = METRICS[metric['name']]
        if metric_family != family:
            raise ValueError('metric and archive source disagree')
        for key, expected in (('context', context), ('adjustment', adjustment), ('unit', unit_text)):
            if _span(raw, metric[key], key) != expected:
                raise ValueError(key + ': unsupported or ambiguous interpretation')
        period = _span(raw, metric['period'], 'period')
        match = re.fullmatch(r'([A-Za-z]+) ([0-9]{4})', period)
        if not match or match[1] not in MONTHS:
            raise ValueError('reference month must be explicit English month and year')
        reference_month = date(int(match[2]), MONTHS[match[1]], 1).isoformat()[:7]
        if reference_month >= document_date[:7]:
            raise ValueError('reference month must precede release archive month')
        number = _span(raw, metric['actual'], 'actual')
        start, end = metric['actual']['start'], metric['actual']['end']
        # Decode around byte offsets: an individual UTF-8 byte is not a character.
        before, after = raw[:start].decode('utf-8'), raw[end:].decode('utf-8')
        if ((before and not (before[-1].isspace() or before[-1] == '>')) or
                (after and not (after[0].isspace() or after[0] == '<'))):
            raise ValueError('numeric span cuts through a larger token or ambiguous delimiter')
        # Also reject separated sign/accounting markers. We cannot infer a sign
        # or rewrite Unicode typography into an ASCII signed quantity.
        def ambiguous_marker(char):
            return bool(char) and char not in '<>' and unicodedata.category(char) in {'Pd', 'Sm', 'Ps', 'Pe'}
        if ambiguous_marker(before.rstrip()[-1:]) or ambiguous_marker(after.lstrip()[:1]):
            raise ValueError('numeric span omits a sign or accounting delimiter')
        if not re.fullmatch(r'-?(?:0|[1-9][0-9]{0,11}|[1-9][0-9]{0,2}(?:,[0-9]{3}){1,3})(?:\.[0-9]{1,6})?', number):
            raise ValueError('ambiguous numeric lexeme')
        value = Decimal(number.replace(',', ''))
        if family == 'empsit' and value != value.to_integral_value():
            raise ValueError('payroll change in persons must be integral')
        record['actual'] = {'value': int(value) if family == 'empsit' else float(value), 'unit': unit}
        record['event_id'] = 'bls:' + metric['name'] + ':' + reference_month
        record['reference_period'] = reference_month
    # Bind the original sidecar bytes too; reconstruction/review claims cannot grant eligibility.
    record.update(history_class='reconstructed', source_document_date=document_date,
                  vintage_class=sidecar['vintage_class'], source_url=sidecar['source_url'])
    envelope = {'schema_version': VERSION, 'source_sha256': digest, 'raw_base64': base64.b64encode(raw).decode('ascii'),
                'sidecar_sha256': events.sha256(sidecar_raw), 'sidecar_base64': base64.b64encode(sidecar_raw).decode('ascii'),
                'event': record}
    provenance = {'adapter': VERSION, 'review_status': 'unverified_external_assertion', 'review_reference': review,
                  'rights_review_reference': sidecar['rights_review_reference'], 'field_evidence': {},
                  'receipt_claim': receipt, 'history_class': 'reconstructed',
                  'limitation': 'Byte spans and reviewer text do not authenticate source, publication, or capture time.'}
    return source_id, events.canonical(envelope).encode('utf-8'), record, provenance


def ingest(conn, raw, sidecar_raw):
    """Append one reconstructed observation. Repeats are idempotent; revisions never overwrite.

    The scoped triggers protect this adapter's persisted rows, not unrelated imports.
    File/DB owners can remove triggers; hashes are integrity checks, not signatures.
    """
    source_id, envelope, record, provenance = prepare(raw, sidecar_raw)
    events.initialize(conn)
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS official_macro_imports(
      observation_id TEXT PRIMARY KEY, artifact_sha256 TEXT NOT NULL);
    CREATE TRIGGER IF NOT EXISTS official_macro_no_update BEFORE UPDATE ON official_macro_imports
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_no_delete BEFORE DELETE ON official_macro_imports
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_observation_no_update BEFORE UPDATE ON event_observation_vintages
      WHEN OLD.observation_id IN (SELECT observation_id FROM official_macro_imports)
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_observation_no_delete BEFORE DELETE ON event_observation_vintages
      WHEN OLD.observation_id IN (SELECT observation_id FROM official_macro_imports)
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_artifact_no_update BEFORE UPDATE ON event_source_artifacts
      WHEN OLD.sha256 IN (SELECT artifact_sha256 FROM official_macro_imports)
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_artifact_no_delete BEFORE DELETE ON event_source_artifacts
      WHEN OLD.sha256 IN (SELECT artifact_sha256 FROM official_macro_imports)
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    ''')
    # Normalize with the same existing event contract; no alternative readiness selector.
    row = events.normalize(record, source_id=source_id, artifact_sha256=events.sha256(envelope),
                           pointer='/event', provenance=provenance)
    # REPLACE may delete without firing DELETE triggers when recursive_triggers
    # is off (SQLite's default). Protect primary-key collisions before insertion.
    comparisons = ' OR '.join('saved.' + key + ' IS NOT NEW.' + key for key in row)
    conn.executescript('''
    CREATE TRIGGER IF NOT EXISTS official_macro_registration_no_replace BEFORE INSERT ON official_macro_imports
      WHEN EXISTS (SELECT 1 FROM official_macro_imports AS saved
        WHERE saved.observation_id=NEW.observation_id AND saved.artifact_sha256 IS NOT NEW.artifact_sha256)
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_artifact_no_replace BEFORE INSERT ON event_source_artifacts
      WHEN NEW.sha256 IN (SELECT artifact_sha256 FROM official_macro_imports)
        AND EXISTS (SELECT 1 FROM event_source_artifacts AS saved WHERE saved.sha256=NEW.sha256
          AND (saved.source_id IS NOT NEW.source_id OR saved.raw_bytes IS NOT NEW.raw_bytes))
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    CREATE TRIGGER IF NOT EXISTS official_macro_observation_no_replace BEFORE INSERT ON event_observation_vintages
      WHEN NEW.observation_id IN (SELECT observation_id FROM official_macro_imports)
        AND EXISTS (SELECT 1 FROM event_observation_vintages AS saved
          WHERE saved.observation_id=NEW.observation_id AND (''' + comparisons + '''))
      BEGIN SELECT RAISE(ABORT, 'append only'); END;
    ''')
    with conn:
        conn.execute('INSERT OR IGNORE INTO event_source_artifacts VALUES(?,?,?)', (events.sha256(envelope), source_id, envelope))
        existing = conn.execute('SELECT source_id FROM event_source_artifacts WHERE sha256=?', (events.sha256(envelope),)).fetchone()
        if existing[0] != source_id:
            raise ValueError('artifact source attribution conflict')
        keys = tuple(row)
        conn.execute('INSERT OR IGNORE INTO event_observation_vintages (' + ','.join(keys) + ') VALUES (' + ','.join('?' for _ in keys) + ')', tuple(row[k] for k in keys))
        conn.execute('INSERT OR IGNORE INTO official_macro_imports VALUES(?,?)', (row['observation_id'], events.sha256(envelope)))
    return row['observation_id']
