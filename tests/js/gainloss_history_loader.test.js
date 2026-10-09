import assert from 'node:assert/strict';
import test from 'node:test';
import { createGainLossLoader } from '../../web/gainloss-history.js';
import { historyHtml, historyRows } from '../../web/evidence-workbench.js';
const payload = (page = 1, rows = [{ symbol: 'TEST', gain_loss: 0 }]) => ({ ok: true, source: 'tradier_gainloss', environment: 'live', fetched_at_utc: '2026-10-09T15:00:00Z', rows, pagination: { page, next_page: rows.length ? page + 1 : null, completeness: 'unknown' } });
const response = (body, status = 200) => ({ ok: status < 400, status, json: async () => body });
function loader(fetch) { const updates = []; return { updates, loader: createGainLossLoader(state => updates.push(state), { fetch, readJson: response => response.json() }) }; }
test('history loader makes no request until explicitly called', () => { let calls = 0; loader(() => calls++); assert.equal(calls, 0); });
test('one request per click; concurrent clicks coalesce', async () => {
 let calls = 0, release; const gate = new Promise(resolve => release = resolve);
 const h = loader(async () => { calls++; await gate; return response(payload()); });
 const request = h.loader.load(); await h.loader.load(); assert.equal(calls, 1); release(); await request;
 assert.equal(h.loader.state().loaded, true); assert.equal(h.loader.state().loading, false);
});
test('pages replace by page identity, never symbol/date dedupe', async () => {
 const h = loader(async url => response(payload(Number(new URL(url, 'http://local').searchParams.get('page')))));
 await h.loader.load(1); await h.loader.load(2); await h.loader.load(2);
 assert.equal(h.loader.state().pages.length, 2); assert.equal(historyRows(h.loader.state()).length, 2);
 await h.loader.load(1, true); assert.equal(h.loader.state().pages.length, 1);
});
test('refresh failure preserves old pages visibly stale; no retries', async () => {
 let calls = 0; const h = loader(async () => ++calls === 1 ? response(payload()) : response({ ok: false, error: 'Rate limit reached' }, 429));
 await h.loader.load(); await h.loader.load(1, true); assert.equal(calls, 2); assert.equal(h.loader.state().pages.length, 1);
 assert.match(historyHtml(h.loader.state()), /Previously loaded records may be stale/); assert.match(h.loader.state().error, /Rate limit/);
});
for (const status of [401, 403, 502]) test(`HTTP ${status} is access/error, not empty history`, async () => {
 const h = loader(async () => response({ ok: false, error: status === 403 ? 'Admin session required.' : 'Unavailable' }, status));
 await h.loader.load(); assert.equal(h.loader.state().loaded, false); assert.equal(h.loader.state().pages.length, 0); assert.ok(h.loader.state().error);
});
test('malformed page does not replace successful state', async () => {
 const h = loader(async () => response({ ...payload(2), rows: null })); await h.loader.load(1);
 assert.equal(h.loader.state().loaded, false); assert.match(h.loader.state().error, /incomplete/);
});
test('history sums loaded finite gain_loss only and preserves missing zero', () => {
 const state = { loaded: true, pages: [payload(1, [{ symbol: 'A', gain_loss: 0, close_date_calendar: '2026-10-08' }, { symbol: 'B', gain_loss: null, close_date_calendar: '2026-10-08' }, { symbol: 'A', gain_loss: 10, close_date_calendar: '2026-10-09' }])] };
 const html = historyHtml(state); assert.match(html, /\$10.00/); assert.match(html, /2 matching rows with gain\/loss; 1 unavailable/); assert.match(html, /not lifetime P&L or a selected-period total/);
 assert.equal(historyRows(state, { result: 'flat' }).length, 1); assert.equal(historyRows(state, { result: 'missing' }).length, 1);
 assert.equal(historyRows(state, { symbol: 'a', from: '2026-10-09' }).length, 1);
});
test('viewer cannot see history load controls', () => {
 const html = historyHtml({}, {}, { authenticated: true, session: { role: 'viewer' } });
 assert.match(html, /requires an admin session/); assert.doesNotMatch(html, /ew-history-load/);
});

test('reset aborts and invalidates late old-account response', async () => {
 let release, signal; const gate = new Promise(resolve => release = resolve);
 const h = loader(async (_url, options) => { signal = options.signal; await gate; return response(payload()); });
 const old = h.loader.load(); h.loader.reset(); assert.equal(signal.aborted, true); release(); await old;
 assert.equal(h.loader.state().loaded, false); assert.deepEqual(h.loader.state().pages, []);
});
test('hostile broker symbols and errors render as text', () => {
 const html = historyHtml({ loaded: true, pages: [payload(1, [{ symbol: '<img src=x onerror=alert(1)>', gain_loss: 0 }])], error: '<script>bad()</script>' });
 assert.doesNotMatch(html, /<img|<script>/); assert.match(html, /&lt;img/); assert.match(html, /&lt;script&gt;/);
});
test('history is bounded to ten pages and rejects oversized response pages', async () => {
 let calls = 0; const h = loader(async url => { calls++; return response(payload(Number(new URL(url,'http://local').searchParams.get('page')))); });
 for (let page=1; page<=10; page++) await h.loader.load(page);
 await h.loader.load(11); assert.equal(calls, 10); assert.equal(h.loader.state().pages.length, 10); assert.match(h.loader.state().error, /limit reached/);
 assert.doesNotMatch(historyHtml(h.loader.state()), /id="ew-history-older"/);
 await h.loader.load(1, true); assert.equal(h.loader.state().pages.length, 1);
 const large = loader(async()=>response(payload(1, Array.from({length:101},()=>({gain_loss:1})))));
 await large.loader.load(); assert.equal(large.loader.state().loaded, false);
});
test('history displays at most one hundred rows while subtotal covers all loaded matching rows', () => {
 const state={loaded:true,pages:[payload(1,Array.from({length:100},()=>({gain_loss:1}))),payload(2,Array.from({length:100},()=>({gain_loss:1})))]};
 const html=historyHtml(state); assert.equal((html.match(/data-ew-key="history-/g)||[]).length,100); assert.match(html,/\$200.00/); assert.match(html,/Showing 100 of 200/);
});
