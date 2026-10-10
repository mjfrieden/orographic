import assert from 'node:assert/strict';
import test from 'node:test';
import { finite, productionRows, compareScans, quoteSummary, brokerMetrics, modelHtml, changesHtml, accountHtml, quotesHtml, researchHtml } from '../../web/evidence-workbench.js';
const pick = (contract, value = null, lane = 'live') => ({ contract_symbol: contract, lane, outcomes: { fixed_exit_marks: { one_hour: { pnl_pct_from_emission: value } } }, emission_quote: { ask: 2 } });
const entry = (date, picks) => ({ run_generated_at_utc: date, picks });
const ledger = { updated_at_utc: '2026-10-09T14:00:00Z', entries: [entry('2026-10-08T14:00:00Z', [pick('OLD', 0), pick('KEEP', .2)]), entry('2026-10-09T14:00:00Z', [pick('NEW', -.1), pick('KEEP'), pick('SHADOW', 99, 'shadow')])], outcome_policy: { required_fixed_exits: ['one_hour'] } };
for (const value of [null, undefined, '', '  ', false, true, NaN, Infinity, {}, []]) test(`finite preserves missing/non-numeric ${String(value)}`, () => assert.equal(finite(value), null));
test('real zero stays zero, numeric strings supported', () => { assert.equal(finite(0), 0); assert.equal(finite('0'), 0); assert.equal(finite('-2.5'), -2.5); });
test('only explicit production lane appears in quoted observations', () => assert.equal(productionRows(ledger).length, 4));
test('compare scans is date ordered and identifies added/removed/retained', () => {
 const diff = compareScans({ ...ledger, entries: [...ledger.entries].reverse() });
 assert.equal(diff.current, '2026-10-09T14:00:00Z');
 assert.deepEqual(Object.fromEntries(diff.rows.map(x => [x.contract, x.status])), { NEW: 'added', KEEP: 'retained', OLD: 'removed' });
});
test('duplicate timestamps do not fabricate two scans', () => assert.equal(compareScans({ entries: [ledger.entries[0], ledger.entries[0]] }).available, false));
test('invalid scan dates are excluded, empty valid scans are retained', () => {
 const result = compareScans({ entries: [entry('bad', [pick('X')]), entry('2026-10-08', []), entry('2026-10-09', [])] });
 assert.equal(result.available, true); assert.equal(result.rows.length, 0); assert.equal(result.scans, 2);
});
test('missing quote marks are excluded from mean and count, zero included', () => {
 const summary = quoteSummary(ledger, 'one_hour');
 assert.equal(summary.total, 4); assert.equal(summary.observed, 3); assert.equal(summary.missing, 1); assert.ok(Math.abs(summary.mean - 1/30) < 1e-12);
 assert.equal(quoteSummary(ledger, 'missing_horizon').mean, null);
});
test('broker P&L is never fallback between closed and open', () => {
 assert.deepEqual(brokerMetrics({ configured: true, balances: { close_pl: null, open_pl: 3, total_equity: 0, total_cash: '0' } }), { available: true, closed: null, open: 3, equity: 0, cash: 0 });
});
for (const extra of [{ lastError: 'Unavailable' }, { loading: true }, { configured: false }]) test(`broker snapshot withheld on ${JSON.stringify(extra)}`, () => {
 const actual = brokerMetrics({ configured: true, balances: { close_pl: 100, open_pl: 50, total_equity: 1000 }, ...extra });
 assert.equal(actual.available, false); assert.equal(actual.closed, null); assert.equal(actual.open, null); assert.equal(actual.equity, null);
});
test('missing model source never produces a protected or green default', () => {
 const html = modelHtml({}); assert.match(html, /Model evidence unavailable/); assert.doesNotMatch(html, /Protected|is-pass|Production v2/);
});
test('stale governance, missing artifacts and overlapping blockers are explicit', () => {
 const html = modelHtml({ snapshot: { generated_at_utc: '2020-01-01', council: { live_board: [], abstain: true, summary: { abstain_audit: { fill_quality_fail_count: 2 } } }, model_artifacts: { ranker: { required: true, present: false } }, model_modes: { payoff_ranker: 'active' } }, governance: { generated_at_utc: '2020-01-01' } });
 assert.match(html, /Older than 4 hours/); assert.match(html, /1 missing/); assert.match(html, /Counts can overlap/); assert.match(html, /Authority policy unavailable/);
});
test('all published prose is escaped', () => {
 const html = modelHtml({ snapshot: { council: { summary: { abstain_audit: { primary_reason_label: '<img onerror=alert(1)>' } } } } });
 assert.doesNotMatch(html, /<img/); assert.match(html, /&lt;img/);
 const changes = changesHtml({ ledger: { entries: [entry('2026-10-08', []), entry('2026-10-09', [pick('<script>')])] } });
 assert.doesNotMatch(changes, /<script>/); assert.match(changes, /&lt;script&gt;/);
});
test('one-scan, missing, and no live picks comparison states are distinct', () => {
 assert.match(changesHtml({}), /No dated recommendation scans/);
 assert.match(changesHtml({ ledger: { entries: [ledger.entries[0]] } }), /Only one dated scan/);
 assert.match(changesHtml({ ledger: { entries: [entry('2026-10-08', []), entry('2026-10-09', [pick('X', .2, 'shadow')])] } }), /Both scans have no production recommendations/);
});
test('quote view labels generic quoted marks and bounded coverage', () => {
 const html = quotesHtml({ ledger }, 'one_hour');
 assert.match(html, /Quoted mark change/); assert.match(html, /not realized or after-cost returns/); assert.match(html, /not independent trades/); assert.match(html, /1 without a usable mark/); assert.doesNotMatch(html, /99\.0%/);
});
test('account panel explicitly lacks historical returns rather than inventing chart', () => {
 const html = accountHtml({ broker: { configured: true, balances: { close_pl: null, open_pl: 0 }, lastLoadedAt: '2026-10-09' } });
 assert.match(html, /Closed P&amp;L · current session/); assert.match(html, /Open-position P&amp;L/); assert.match(html, /Unavailable/); assert.match(html, /\$0.00/); assert.match(html, /Period returns are not available/);
});
test('research retains study identity and declines current deployment inference', () => {
 const html = researchHtml({ backtest: { total_trades: 3, variant_label: 'Old Council', generated_at_utc: '2026-04-21' } });
 assert.match(html, /Old Council/); assert.match(html, /archived model study/); assert.match(html, /not account P&amp;L/); assert.doesNotMatch(html, /net_pnl|sharpe_ratio/);
});
test('partial sources do not imply zero contracts or a hold decision', () => {
 const html = modelHtml({ snapshot: { model_artifacts: { bad: null }, council: {} } });
 assert.match(html, /Council decision unavailable/); assert.doesNotMatch(html, /Why is the board on hold/); assert.match(html, /No decision can be established/);
});
test('date-only archived publication dates do not shift across timezones', () => {
 const html=researchHtml({backtest:{generated_at_utc:'2026-04-21',total_trades:34}});
 assert.match(html,/<dt>Study generated<\/dt><dd>2026-04-21<\/dd>/);
});
