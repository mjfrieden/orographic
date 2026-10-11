"""Pure offline SEC submissions + filing-scoped XBRL instance adapter.

No collectors, clocks, inferred publication, companyfacts/frames, or execution feed.
The source-use review gate in event_observations remains unchanged.
"""
from __future__ import annotations

import base64
import io
from datetime import date
from decimal import Decimal, InvalidOperation
import re
import xml.etree.ElementTree as ET

from . import event_observations as events

VERSION = 'sec-filing-offline-v1'
XBRLI = 'http://www.xbrl.org/2003/instance'
XSI = 'http://www.w3.org/2001/XMLSchema-instance'
XBRLDI = 'http://xbrl.org/2006/xbrldi'
DEI_PREFIX = 'http://xbrl.sec.gov/dei/'
CIK_SCHEME = 'http://www.sec.gov/CIK'
MAX_BYTES = 8 * 1024 * 1024


def _cik(value):
    if isinstance(value, bool) or not re.fullmatch(r'[0-9]{1,10}', str(value)):
        raise ValueError('invalid CIK')
    return str(int(value)).zfill(10)


def _accession(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9]{10}-[0-9]{2}-[0-9]{6}', value):
        raise ValueError('invalid accession')
    return value


def _raw(value):
    if not isinstance(value, bytes) or not value or len(value) > MAX_BYTES:
        raise ValueError('nonempty bounded raw bytes required')
    return value


def _text(value, field):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(field + ' required')
    return value


def _optional_time(value):
    # Missing or date-only witnesses stay missing; never attach midnight/timezone.
    if value in (None, ''):
        return None
    try:
        return events.timestamp(value)
    except (ValueError, TypeError):
        return None


def _day(value):
    if not isinstance(value, str) or date.fromisoformat(value).isoformat() != value:
        raise ValueError('XBRL period must have an exact ISO date')
    return value


def _xml(element):
    return ET.canonicalize(ET.tostring(element, encoding='unicode'))


def _filing(submissions, accession):
    cik = _cik(submissions.get('cik'))
    recent = submissions['filings']['recent']
    required = ('accessionNumber', 'form', 'filingDate', 'acceptanceDateTime', 'primaryDocument')
    arrays = [recent.get(k) for k in required]
    if any(not isinstance(a, list) for a in arrays) or len({len(a) for a in arrays}) != 1:
        raise ValueError('unaligned SEC submissions arrays')
    matches = [i for i, a in enumerate(recent['accessionNumber']) if a == accession]
    if len(matches) != 1:
        raise ValueError('accession must match exactly one submissions row')
    i = matches[0]
    row = {k: recent[k][i] for k in required}
    row['cik'] = cik
    row['pointer'] = '/filings/recent/accessionNumber/' + str(i)
    if row['form'] not in {'10-K', '10-K/A', '10-Q', '10-Q/A'}:
        raise ValueError('only 10-K/Q filing instances supported')
    _day(row['filingDate'])
    document = _text(row['primaryDocument'], 'primaryDocument')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+', document) or document in {'.', '..'}:
        raise ValueError('unsafe primaryDocument')
    row['filing_url'] = ('https://www.sec.gov/Archives/edgar/data/' + str(int(cik)) + '/'
                         + accession.replace('-', '') + '/' + document)
    return row


def _instance(raw, filing):
    # No DTD/entity processing, inline HTML, aggregated companyfacts, or frames.
    decoded = raw.decode('utf-8-sig')
    if '<!DOCTYPE' in decoded.upper() or '<!ENTITY' in decoded.upper():
        raise ValueError('DTD/entities are unsupported')
    root = ET.fromstring(raw)
    if root.tag != '{' + XBRLI + '}xbrl':
        raise ValueError('filing-scoped XBRL instance required')
    stack = [(root, 1)]
    nodes = 0
    while stack:
        node, depth = stack.pop()
        nodes += 1
        if depth > 64 or nodes > 100000:
            raise ValueError('XML depth/node limit exceeded')
        stack.extend((child, depth + 1) for child in node)
    mapping = {}
    for event, item in ET.iterparse(io.BytesIO(raw), events=('start-ns', 'start')):
        if event == 'start':
            break
        prefix, uri = item
        mapping[prefix] = uri
    # Deliberately narrow: all namespace declarations must be rooted. This avoids
    # resolving QName text against a declaration that exists only on a sibling.
    for _, (prefix, uri) in ET.iterparse(io.BytesIO(raw), events=('start-ns',)):
        if prefix not in mapping or mapping[prefix] != uri:
            raise ValueError('descendant-only or rebound namespace unsupported')
    namespace_bindings = sorted([prefix, uri] for prefix, uri in mapping.items())

    def qname_valid(qname):
        parts = (qname or '').strip().split(':')
        return (len(parts) in (1, 2) and all(re.fullmatch(r'[A-Za-z_][A-Za-z0-9_.-]*', p) for p in parts)
                and ((len(parts) == 2 and parts[0] in mapping) or (len(parts) == 1 and '' in mapping)))

    contexts, units, facts = {}, {}, []
    dei = {}
    for element in root:
        if element.tag == '{' + XBRLI + '}context':
            tags = [c.tag for c in element]
            required = ['{' + XBRLI + '}entity', '{' + XBRLI + '}period']
            if tags not in (required, required + ['{' + XBRLI + '}scenario']):
                raise ValueError('unsupported/ambiguous context structure')
            entity = element[0]
            entity_tags = [c.tag for c in entity]
            if entity_tags not in (['{' + XBRLI + '}identifier'],
                                    ['{' + XBRLI + '}identifier', '{' + XBRLI + '}segment']):
                raise ValueError('unsupported/ambiguous entity structure')
            for member in element.iter():
                if member.tag in {'{' + XBRLDI + '}explicitMember', '{' + XBRLDI + '}typedMember'}:
                    if not qname_valid(member.get('dimension')):
                        raise ValueError('unresolved dimension QName')
                    if member.tag == '{' + XBRLDI + '}explicitMember':
                        if list(member) or not qname_valid(member.text):
                            raise ValueError('unresolved explicit member QName')
                    elif len(list(member)) != 1:
                        raise ValueError('typed dimension requires one member')
            identifier = element.find('./{' + XBRLI + '}entity/{' + XBRLI + '}identifier')
            if len(element.findall('./{' + XBRLI + '}entity/{' + XBRLI + '}identifier')) != 1:
                raise ValueError('exactly one XBRL entity identifier required')
            if identifier is None or identifier.get('scheme') != CIK_SCHEME or _cik(identifier.text) != filing['cik']:
                raise ValueError('XBRL context issuer mismatch')
            key = _text(element.get('id'), 'context id')
            if key in contexts:
                raise ValueError('duplicate context id')
            period = element.find('{' + XBRLI + '}period')
            if period is None:
                raise ValueError('missing XBRL period')
            children = list(period)
            if [e.tag for e in children] == ['{' + XBRLI + '}instant']:
                periods = {'instant': _day(children[0].text)}
            elif [e.tag for e in children] == ['{' + XBRLI + '}startDate', '{' + XBRLI + '}endDate']:
                periods = {'start': _day(children[0].text), 'end': _day(children[1].text)}
                if periods['start'] > periods['end']:
                    raise ValueError('reversed XBRL period')
            else:
                raise ValueError('unsupported XBRL period')
            # Preserve dimensions and all entity/segment/scenario context verbatim.
            dimensions = [_xml(e) for e in element.iter()
                          if e.tag in {'{' + XBRLI + '}segment', '{' + XBRLI + '}scenario'}]
            contexts[key] = {'period': periods, 'dimensions_xml': dimensions,
                             'context_xml': _xml(element)}
        elif element.tag == '{' + XBRLI + '}unit':
            key = _text(element.get('id'), 'unit id')
            if key in units:
                raise ValueError('duplicate unit id')
            original = _xml(element)
            element.attrib.pop('id')
            units[key] = {'unit_xml': original, 'unit_scope_xml': _xml(element)}
            element.set('id', key)
        elif element.tag.startswith('{' + DEI_PREFIX):
            local = element.tag.split('}', 1)[1]
            if list(element) or element.get('{' + XSI + '}nil') not in (None, 'false', '0'):
                raise ValueError('unsupported DEI identity value')
            dei.setdefault(local, []).append(((element.text or '').strip(), element.get('contextRef')))
    for field, expected in [('EntityCentralIndexKey', filing['cik']),
                            ('DocumentType', filing['form']),
                            ('AmendmentFlag', 'true' if filing['form'].endswith('/A') else 'false')]:
        entries = dei.get(field, [])
        if any(context not in contexts for _, context in entries):
            raise ValueError('DEI identity requires a valid filing context')
        values = [value for value, _ in entries]
        normalized = {_cik(v) if field == 'EntityCentralIndexKey' else v for v in values}
        allowed = {expected, expected.removesuffix('/A')} if field == 'DocumentType' else {expected}
        if len(normalized) != 1 or not normalized <= allowed:
            raise ValueError('filing DEI identity mismatch: ' + field)
    for element in root.findall('{' + XBRLI + '}unit'):
        children = list(element)
        measure_tag = '{' + XBRLI + '}measure'
        if not children:
            raise ValueError('empty unit')
        if len(children) == 1 and children[0].tag == '{' + XBRLI + '}divide':
            parts = list(children[0])
            if [p.tag for p in parts] != ['{' + XBRLI + '}unitNumerator', '{' + XBRLI + '}unitDenominator']:
                raise ValueError('invalid divided unit')
            if any(not list(p) or any(m.tag != measure_tag for m in p) for p in parts):
                raise ValueError('invalid divided unit measures')
        elif any(c.tag != measure_tag for c in children):
            raise ValueError('unsupported unit shape')
        measures = list(element.iter('{' + XBRLI + '}measure'))
        if not measures:
            raise ValueError('unit measures required')
        for measure in measures:
            if list(measure) or not qname_valid(measure.text):
                raise ValueError('unresolved unit measure QName')
    for i, element in enumerate(root):
        context_id, unit_id = element.get('contextRef'), element.get('unitRef')
        if context_id is None or unit_id is None:
            continue
        if context_id not in contexts or unit_id not in units:
            raise ValueError('fact references missing context or unit')
        if any(element.get(k) is not None for k in ('scale', 'sign', 'format')):
            raise ValueError('inline fact transforms unsupported')
        if list(element):
            raise ValueError('nested numeric/fraction fact unsupported')
        raw_value = (element.text or '').strip()
        nil = element.get('{' + XSI + '}nil')
        if nil not in (None, 'false', '0', 'true', '1'):
            raise ValueError('invalid xsi:nil')
        decimals, precision = element.get('decimals'), element.get('precision')
        if decimals is not None and not re.fullmatch(r'INF|[+-]?[0-9]+', decimals):
            raise ValueError('invalid decimals')
        if precision is not None and not re.fullmatch(r'INF|[+]?[1-9][0-9]*', precision):
            raise ValueError('invalid precision')
        if decimals is not None and precision is not None:
            raise ValueError('decimals and precision are mutually exclusive')
        if nil in ('true', '1'):
            if raw_value:
                raise ValueError('nil fact must not contain a value')
            value = None
        else:
            if (decimals is None and precision is None) or len(raw_value) > 10000 or not re.fullmatch(r'[+-]?(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)', raw_value):
                raise ValueError('unsupported numeric lexical value or missing accuracy')
            try:
                value = Decimal(raw_value)
            except InvalidOperation as exc:
                raise ValueError('invalid numeric fact') from exc
            if not value.is_finite():
                raise ValueError('nonfinite numeric fact')
            value = str(value)
        facts.append({'concept': element.tag, 'context_id': context_id, 'unit_id': unit_id,
                      **contexts[context_id], **units[unit_id], 'namespace_bindings': namespace_bindings,
                      'value_decimal': value, 'raw_value': raw_value,
                      'decimals': element.get('decimals'), 'precision': element.get('precision'),
                      'xml_child_index': i, 'fact_xml': _xml(element)})
    return facts


def ingest_sec_filing(conn, submissions_raw, filing_raw, receipt_raw, *, accession,
                      source_id='sec_public_filing'):
    """Append a captured filing and facts; missing witnesses remain ineligible.

    receipt_raw is a caller-retained capture manifest, not proof invented by this
    adapter. It must bind exact raw hashes, CIK, form and accession. first_public_at
    additionally requires publication_reference. Receipt times require their own
    evidence references. A current receipt never backdates an old filing.
    synthetic_fixture is only for named pure-offline tests.
    """
    if source_id not in {'sec_public_filing', 'synthetic_fixture'}:
        raise ValueError('unsupported source')
    accession = _accession(accession)
    submissions_raw, filing_raw, receipt_raw = map(_raw, (submissions_raw, filing_raw, receipt_raw))
    submissions, receipt = events.strict_json(submissions_raw), events.strict_json(receipt_raw)
    filing = _filing(submissions, accession)
    if not isinstance(receipt, dict) or receipt.get('schema') != VERSION:
        raise ValueError('unsupported receipt manifest')
    for key, expected in [('submissions_sha256', events.sha256(submissions_raw)),
                          ('filing_sha256', events.sha256(filing_raw)),
                          ('cik', filing['cik']), ('accession', accession), ('form', filing['form'])]:
        if receipt.get(key) != expected:
            raise ValueError('receipt binding mismatch: ' + key)
    filing_source_url = receipt.get('filing_source_url')
    expected_prefix = ('https://www.sec.gov/Archives/edgar/data/' + str(int(filing['cik']))
                       + '/' + accession.replace('-', '') + '/')
    if (not isinstance(filing_source_url, str) or not filing_source_url.startswith(expected_prefix)
            or not re.fullmatch(r'[A-Za-z0-9_.-]+\.xml', filing_source_url[len(expected_prefix):])):
        raise ValueError('filing source URL must bind the accession archive')
    facts = _instance(filing_raw, filing)
    accepted = _optional_time(filing['acceptanceDateTime'])
    witnessed = {}
    for field in ('first_public_at', 'submissions_received_at', 'filing_received_at'):
        value = _optional_time(receipt.get(field))
        reference = receipt.get(field + '_reference')
        witnessed[field] = value if isinstance(reference, str) and reference.strip() else None
    public = witnessed['first_public_at']
    receipts = [witnessed[k] for k in ('submissions_received_at', 'filing_received_at')]
    seen = max(receipts) if all(receipts) else None
    available = max(accepted, public, seen) if all((accepted, public, seen)) else None
    bundle = {'schema': VERSION, 'submissions_base64': base64.b64encode(submissions_raw).decode(),
              'filing_base64': base64.b64encode(filing_raw).decode(),
              'receipt_base64': base64.b64encode(receipt_raw).decode()}
    raw = events.canonical(bundle).encode()
    record = {'kind': 'sec_filing_release', 'event_id': 'sec:' + filing['cik'] + ':' + accession,
              'issuer_id': 'CIK:' + filing['cik'], 'revision_id': accession,
              'released_at': accepted, 'first_public_at': public, 'first_seen_at': seen,
              'available_at': available, 'scheduled_at': None, 'event_date': None,
              'actual': None, 'expected': None, 'prior': None,
              'filing': filing, 'facts': facts, 'amendment': filing['form'].endswith('/A'),
              'supersedes_accession': None}
    evidence = {'released_at': 'submissions_sha256:' + events.sha256(submissions_raw) + filing['pointer'],
                'first_public_at': receipt.get('first_public_at_reference'),
                'first_seen_at': 'receipt_sha256:' + events.sha256(receipt_raw),
                'available_at': 'max(accepted, evidenced publication, both evidenced receipts)'}
    provenance = {'adapter_version': VERSION, 'field_evidence': evidence,
                  'submissions_sha256': events.sha256(submissions_raw),
                  'filing_sha256': events.sha256(filing_raw), 'receipt_sha256': events.sha256(receipt_raw),
                  'review_status': receipt.get('review_status'), 'review_reference': receipt.get('review_reference'),
                  'filing_url': filing['filing_url'], 'filing_source_url': filing_source_url,
                  'publication_is_not_acceptance': True,
                  'claim_authentication': 'not_performed_offline'}
    row = events.normalize(record, source_id=source_id, artifact_sha256=events.sha256(raw),
                           pointer='/filing_base64', provenance=provenance)
    return events._persist(conn, raw, source_id, [row])


def facts_as_of(conn, cutoff, *, issuer_id, allow_fixtures=False):
    """Select exact-context facts from eligible event vintages, never frames/latest.

    SEC remains source-use-gated, so production SEC rows currently yield no facts.
    No period aggregation, unit conversion, quarter/YTD conversion or surprise.
    Conflicting equal-acceptance facts are omitted. Amendments stay in the ledger.
    """
    groups = {}
    for row in events.as_of(conn, cutoff, issuer_id=issuer_id, allow_fixtures=allow_fixtures):
        if row['kind'] != 'sec_filing_release':
            continue
        payload = events.strict_json(row['payload_json'])
        for fact in payload['facts']:
            # Unit/context IDs can differ between files; conservative exact XML
            # scope retains namespace environment and avoids unsafe equivalence.
            key = events.canonical({k: fact[k] for k in ('concept', 'period', 'dimensions_xml',
                                                        'unit_scope_xml', 'namespace_bindings')})
            groups.setdefault(key, []).append({**fact, 'accession': payload['filing']['accessionNumber'],
                                               'released_at': row['released_at'],
                                               'available_at': row['available_at'],
                                               'observation_id': row['observation_id'],
                                               'artifact_sha256': row['artifact_sha256'],
                                               'execution_ready': False})
    result = []
    for group in groups.values():
        latest = max(f['released_at'] for f in group)
        choices = [f for f in group if f['released_at'] == latest]
        if len({(f['value_decimal'], f['decimals'], f['precision'], f['accession']) for f in choices}) == 1:
            result.append(sorted(choices, key=lambda f: f['observation_id'])[0])
    return sorted(result, key=events.canonical)
