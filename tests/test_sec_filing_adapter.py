import base64
import json
import sqlite3
import unittest

from engine.orographic import event_observations as events
from engine.orographic import sec_filing_adapter as sec

ACC = '0000123456-26-000001'
AMEND = '0000123456-26-000002'


def fixture(*, accession=ACC, form='10-Q', accepted='2026-08-01T16:00:00Z',
            public='2026-08-01T16:01:00Z', seen='2026-08-01T16:02:00Z', value='123.4500'):
    submissions = {'cik': 123456, 'filings': {'recent': {
        'accessionNumber': [accession], 'form': [form], 'filingDate': ['2026-08-01'],
        'acceptanceDateTime': [accepted], 'primaryDocument': ['report.htm']}}}
    xml = f'''<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance"
 xmlns:dei="http://xbrl.sec.gov/dei/2026" xmlns:gaap="http://fasb.org/us-gaap/2026"
 xmlns:iso4217="http://www.xbrl.org/2003/iso4217" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">
 <xbrli:context id="Q"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000123456</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:context id="YTD"><xbrli:entity><xbrli:identifier scheme="http://www.sec.gov/CIK">0000123456</xbrli:identifier></xbrli:entity><xbrli:period><xbrli:startDate>2026-01-01</xbrli:startDate><xbrli:endDate>2026-06-30</xbrli:endDate></xbrli:period></xbrli:context>
 <xbrli:unit id="USD"><xbrli:measure>iso4217:USD</xbrli:measure></xbrli:unit>
 <dei:EntityCentralIndexKey contextRef="Q">123456</dei:EntityCentralIndexKey>
 <dei:DocumentType contextRef="Q">{form.removesuffix('/A')}</dei:DocumentType>
 <dei:AmendmentFlag contextRef="Q">{'true' if form.endswith('/A') else 'false'}</dei:AmendmentFlag>
 <gaap:Revenue contextRef="Q" unitRef="USD" decimals="-3">{value}</gaap:Revenue>
 <gaap:Revenue contextRef="YTD" unitRef="USD" decimals="-3">456.78</gaap:Revenue>
</xbrli:xbrl>'''.encode()
    raw = events.canonical(submissions).encode()
    receipt = {'schema': sec.VERSION, 'submissions_sha256': events.sha256(raw),
               'filing_sha256': events.sha256(xml), 'cik': '0000123456', 'accession': accession,
               'form': form, 'first_public_at': public, 'submissions_received_at': seen,
               'filing_received_at': seen, 'first_public_at_reference': 'synthetic publication witness',
               'submissions_received_at_reference': 'synthetic receipt A',
               'filing_received_at_reference': 'synthetic receipt B',
               'filing_source_url': 'https://www.sec.gov/Archives/edgar/data/123456/' + accession.replace('-', '') + '/report.xml',
               'review_status': 'reviewed', 'review_reference': 'synthetic test-only review'}
    return raw, xml, receipt


class SecFilingTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:')

    def ingest(self, data=None, *, accession=ACC, source='synthetic_fixture'):
        raw, xml, receipt = data or fixture()
        return sec.ingest_sec_filing(self.conn, raw, xml, events.canonical(receipt).encode(),
                                     accession=accession, source_id=source)

    def facts(self, cutoff='2026-08-02T00:00:00Z'):
        return sec.facts_as_of(self.conn, cutoff, issuer_id='CIK:0000123456', allow_fixtures=True)

    def test_exact_decimal_contexts_and_raw_bytes_retained(self):
        raw, xml, receipt = fixture()
        self.ingest((raw, xml, receipt))
        facts = self.facts()
        self.assertEqual({f['value_decimal'] for f in facts}, {'123.4500', '456.78'})
        self.assertEqual(len(facts), 2)
        self.assertTrue(all(f['execution_ready'] is False for f in facts))
        stored = json.loads(self.conn.execute('SELECT raw_bytes FROM event_source_artifacts').fetchone()[0])
        self.assertEqual(base64.b64decode(stored['filing_base64']), xml)
        self.assertEqual(base64.b64decode(stored['submissions_base64']), raw)
        self.assertEqual(json.loads(base64.b64decode(stored['receipt_base64'])), receipt)

    def test_production_source_remains_ineligible(self):
        self.ingest(source='sec_public_filing')
        self.assertEqual(self.facts(), [])
        reasons = json.loads(self.conn.execute('SELECT unavailable_reasons_json FROM event_observation_vintages').fetchone()[0])
        self.assertIn('source_use_not_reviewed', reasons)

    def test_publication_and_both_receipts_bound_cutoff(self):
        self.ingest()
        self.assertEqual(self.facts('2026-08-01T16:01:59Z'), [])
        self.assertEqual(len(self.facts('2026-08-01T16:02:00Z')), 2)

    def test_late_download_never_backdates(self):
        self.ingest(fixture(seen='2026-10-01T00:00:00Z'))
        self.assertEqual(self.facts(), [])

    def test_missing_or_imprecise_witnesses_fail_closed(self):
        for field in ('first_public_at', 'submissions_received_at', 'filing_received_at'):
            for value in (None, '2026-08-01', '2026-08-01T16:00:00'):
                with self.subTest(field=field, value=value):
                    self.conn = sqlite3.connect(':memory:')
                    raw, xml, r = fixture()
                    r[field] = value
                    self.ingest((raw, xml, r))
                    self.assertEqual(self.facts(), [])

    def test_missing_acceptance_is_not_filing_or_period_date(self):
        self.ingest(fixture(accepted=''))
        self.assertEqual(self.facts(), [])
        row = self.conn.execute('SELECT released_at,event_date,available_at FROM event_observation_vintages').fetchone()
        self.assertEqual(row, (None, None, None))

    def test_witness_reference_required(self):
        for field in ('first_public_at', 'submissions_received_at', 'filing_received_at'):
            self.conn = sqlite3.connect(':memory:')
            raw, xml, r = fixture()
            r.pop(field + '_reference')
            self.ingest((raw, xml, r))
            self.assertEqual(self.facts(), [])

    def test_amendment_preserves_original_and_changes_only_after_available(self):
        self.ingest()
        self.ingest(fixture(accession=AMEND, form='10-Q/A', accepted='2026-08-10T16:00:00Z',
                            public='2026-08-10T16:01:00Z', seen='2026-08-10T16:02:00Z', value='120'), accession=AMEND)
        self.assertEqual({f['accession'] for f in self.facts()}, {ACC})
        self.assertEqual({f['accession'] for f in self.facts('2026-08-11T00:00:00Z')}, {AMEND})
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM event_observation_vintages').fetchone()[0], 2)

    def test_no_consensus_surprise_schedule_or_aggregate(self):
        self.ingest()
        payload = json.loads(self.conn.execute('SELECT payload_json FROM event_observation_vintages').fetchone()[0])
        for key in ('actual', 'expected', 'prior', 'scheduled_at', 'event_date'):
            self.assertIsNone(payload[key])
        self.assertEqual(len(payload['facts']), 2)

    def test_manifest_identity_and_raw_hashes_match(self):
        for field in ('submissions_sha256', 'filing_sha256', 'cik', 'form', 'accession', 'filing_source_url'):
            with self.subTest(field=field):
                raw, xml, r = fixture()
                r[field] = 'wrong'
                with self.assertRaises(ValueError):
                    self.ingest((raw, xml, r))

    def mutate_xml(self, before, after):
        raw, xml, receipt = fixture()
        xml = xml.replace(before.encode(), after.encode())
        receipt['filing_sha256'] = events.sha256(xml)
        return raw, xml, receipt

    def test_context_and_dei_issuer_form_amendment_match(self):
        cases = [('>0000123456<', '>0000654321<'), ('>123456<', '>654321<'),
                 ('>10-Q<', '>10-K<'), ('>false<', '>true<')]
        for before, after in cases:
            with self.subTest(before=before):
                with self.assertRaises(ValueError):
                    self.ingest(self.mutate_xml(before, after))

    def test_context_and_unit_references_required(self):
        for before, after in [('contextRef="Q" unitRef', 'contextRef="missing" unitRef'),
                              ('unitRef="USD"', 'unitRef="missing"')]:
            with self.assertRaises(ValueError):
                self.ingest(self.mutate_xml(before, after))

    def test_duplicate_contexts_rejected(self):
        with self.assertRaises(ValueError):
            self.ingest(self.mutate_xml('id="YTD"', 'id="Q"'))

    def test_nonfinite_and_inline_transforms_rejected(self):
        for before, after in [('123.4500', 'NaN'), ('123.4500', 'Infinity'),
                              ('decimals="-3"', 'decimals="-3" scale="3"')]:
            with self.assertRaises(ValueError):
                self.ingest(self.mutate_xml(before, after))

    def test_companyfacts_and_inline_html_not_accepted(self):
        for xml in (b'{"facts": {}}', b'<html/>'):
            raw, _, r = fixture()
            r['filing_sha256'] = events.sha256(xml)
            with self.assertRaises((ValueError, sec.ET.ParseError)):
                self.ingest((raw, xml, r))

    def test_dtd_rejected(self):
        with self.assertRaises(ValueError):
            self.ingest(self.mutate_xml('<xbrli:xbrl', '<!DOCTYPE xbrl><xbrli:xbrl'))

    def test_conflicting_same_acceptance_fact_omitted(self):
        raw, xml, r = fixture()
        xml = xml.replace(b'</xbrli:xbrl>', b'<gaap:Revenue contextRef="Q" unitRef="USD" decimals="-3">999</gaap:Revenue></xbrli:xbrl>')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r))
        self.assertEqual([f['value_decimal'] for f in self.facts()], ['456.78'])

    def test_fixture_requires_explicit_selection_opt_in(self):
        self.ingest()
        self.assertEqual(sec.facts_as_of(self.conn, '2026-08-02T00:00:00Z', issuer_id='CIK:0000123456'), [])

    def test_submissions_arrays_and_accession_are_strict(self):
        raw, xml, r = fixture()
        doc = json.loads(raw)
        doc['filings']['recent']['form'] = []
        raw = events.canonical(doc).encode()
        r['submissions_sha256'] = events.sha256(raw)
        with self.assertRaises(ValueError):
            self.ingest((raw, xml, r))
        with self.assertRaises(ValueError):
            self.ingest(accession=AMEND)

    def test_amendment_document_type_may_include_suffix(self):
        raw, xml, r = fixture(accession=AMEND, form='10-Q/A')
        xml = xml.replace(b'>10-Q<', b'>10-Q/A<')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r), accession=AMEND)
        self.assertEqual(len(self.facts()), 2)

    def test_unit_identifier_does_not_split_same_unit_scope(self):
        self.ingest()
        raw, xml, r = fixture(accession=AMEND, form='10-Q/A', accepted='2026-08-10T16:00:00Z',
                              public='2026-08-10T16:01:00Z', seen='2026-08-10T16:02:00Z')
        xml = xml.replace(b'id="USD"', b'id="dollars"').replace(b'unitRef="USD"', b'unitRef="dollars"')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r), accession=AMEND)
        facts = self.facts('2026-08-11T00:00:00Z')
        self.assertEqual(len(facts), 2)
        self.assertEqual({f['accession'] for f in facts}, {AMEND})

    def test_distinct_currency_stays_separate(self):
        raw, xml, r = fixture()
        xml = xml.replace(b'</xbrli:xbrl>', b'<xbrli:unit id="EUR"><xbrli:measure>iso4217:EUR</xbrli:measure></xbrli:unit><gaap:Revenue contextRef="Q" unitRef="EUR" decimals="0">100</gaap:Revenue></xbrli:xbrl>')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r))
        self.assertEqual(len(self.facts()), 3)

    def test_undeclared_unit_prefix_and_rebinding_rejected(self):
        for before, after in [('>iso4217:USD<', '>unknown:USD<'),
                              ('<gaap:Revenue ', '<gaap:Revenue xmlns:iso4217="urn:other" ')]:
            with self.assertRaises(ValueError):
                self.ingest(self.mutate_xml(before, after))

    def test_wrong_issuer_returns_no_facts(self):
        self.ingest()
        self.assertEqual(sec.facts_as_of(self.conn, '2026-08-02T00:00:00Z',
                                         issuer_id='CIK:0000654321', allow_fixtures=True), [])

    def test_dimensioned_context_stays_separate(self):
        raw, xml, r = fixture()
        marker = b'<xbrli:context id="Q">'
        start = xml.index(marker)
        end = xml.index(b'</xbrli:context>', start) + len(b'</xbrli:context>')
        context = xml[start:end].replace(b'id="Q"', b'id="segment"').replace(
            b'</xbrli:entity>', b'<xbrli:segment><gaap:BusinessUnit>Retail</gaap:BusinessUnit></xbrli:segment></xbrli:entity>')
        xml = xml.replace(b'</xbrli:xbrl>', context + b'<gaap:Revenue contextRef="segment" unitRef="USD" decimals="0">50</gaap:Revenue></xbrli:xbrl>')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r))
        facts = self.facts()
        self.assertEqual(len(facts), 3)
        self.assertEqual(sum(bool(f['dimensions_xml']) for f in facts), 1)

    def test_nil_amendment_does_not_fall_back_to_prior_value(self):
        self.ingest()
        raw, xml, r = fixture(accession=AMEND, form='10-Q/A', accepted='2026-08-10T16:00:00Z',
                              public='2026-08-10T16:01:00Z', seen='2026-08-10T16:02:00Z')
        xml = xml.replace(b'decimals="-3">123.4500', b'decimals="-3" xsi:nil="true">')
        r['filing_sha256'] = events.sha256(xml)
        self.ingest((raw, xml, r), accession=AMEND)
        facts = self.facts('2026-08-11T00:00:00Z')
        self.assertEqual(next(f['value_decimal'] for f in facts if f['period'].get('start') == '2026-04-01'), None)

    def test_future_acceptance_not_available_early(self):
        self.ingest(fixture(accepted='2026-08-03T00:00:00Z'))
        self.assertEqual(self.facts(), [])

    def test_invalid_numeric_lexical_accuracy_and_nil_rejected(self):
        cases = [('123.4500', '1_000'), ('123.4500', '1e3'),
                 ('decimals="-3"', 'decimals="garbage"'),
                 ('decimals="-3"', 'decimals="-3" precision="3"'),
                 ('decimals="-3"', 'precision="0"'),
                 ('decimals="-3"', ''),
                 ('decimals="-3"', 'decimals="-3" xsi:nil="true"')]
        for before, after in cases:
            with self.subTest(after=after), self.assertRaises(ValueError):
                self.ingest(self.mutate_xml(before, after))

    def test_ambiguous_context_and_dei_context_rejected(self):
        cases = [('<xbrli:period>', '<xbrli:period/><xbrli:period>'),
                 ('<xbrli:entity>', '<xbrli:entity/><xbrli:entity>'),
                 ('<dei:DocumentType contextRef="Q">', '<dei:DocumentType contextRef="missing">'),
                 ('<dei:DocumentType contextRef="Q">', '<dei:DocumentType>')]
        for before, after in cases:
            with self.subTest(after=after), self.assertRaises(ValueError):
                self.ingest(self.mutate_xml(before, after))

    def test_unresolved_dimensions_and_deep_tree_rejected(self):
        malformed = '<xbrli:segment><d:explicitMember xmlns:d="http://xbrl.org/2006/xbrldi" dimension="ghost:Axis">ghost:Member</d:explicitMember></xbrli:segment>'
        deep = '<xbrli:segment>' + '<node>' * 1500 + '</node>' * 1500 + '</xbrli:segment>'
        for segment in (malformed, deep):
            with self.assertRaises(ValueError):
                self.ingest(self.mutate_xml('</xbrli:entity>', segment + '</xbrli:entity>'))

    def test_malformed_unit_rejected(self):
        with self.assertRaises(ValueError):
            self.ingest(self.mutate_xml('</xbrli:measure>', '</xbrli:measure><xbrli:divide/>'))

    def test_sibling_namespace_cannot_resolve_unit_qname(self):
        raw, xml, r = fixture()
        xml = xml.replace(b' xmlns:iso4217="http://www.xbrl.org/2003/iso4217"', b'')
        xml = xml.replace(b'<gaap:Revenue contextRef="YTD"',
                          b'<gaap:Revenue xmlns:iso4217="http://www.xbrl.org/2003/iso4217" contextRef="YTD"')
        r['filing_sha256'] = events.sha256(xml)
        with self.assertRaises(ValueError):
            self.ingest((raw, xml, r))

    def test_idempotent_reingestion(self):
        self.assertEqual(self.ingest(), self.ingest())
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM event_observation_vintages').fetchone()[0], 1)


if __name__ == '__main__':
    unittest.main()
