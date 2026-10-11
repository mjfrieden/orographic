/** Pure, offline calculations for explicitly supplied valuation receipts. */
export const MAX_RETURN_BYTES = 1024 * 1024;
export const MAX_RETURN_POINTS = 1000;
const escape = (v) => String(v ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const money = (v) => v.toLocaleString('en-US', { style: 'currency', currency: 'USD' });
const pct = (v) => `${(v * 100).toFixed(2)}%`;
const validNumber = (v) => typeof v === 'number' && Number.isFinite(v) && Math.abs(v) <= 1e12;
// Reject sub-cent valuations: an account receipt is not an arbitrary-scale numeric series.
const MIN_EQUITY = 0.01;
const fail = (message) => { throw new Error(message); };

export function calculateAccountReturns(receipt, account, now = Date.now()) {
  if (!account?.accountId || !['live', 'sandbox'].includes(account.mode)) fail('Load a known broker account and environment before importing.');
  if (receipt?.schema !== 'orographic.account-return-receipt.v1' || receipt.currency !== 'USD') fail('Expected a v1 USD account-return receipt.');
  if (receipt.account_id !== account.accountId || receipt.environment !== account.mode) fail('Receipt account or environment does not match the current account.');
  if (receipt.coverage?.all_external_flows_included !== true || receipt.coverage?.valued_at_every_external_flow !== true) fail('Complete external-flow coverage and valuations at every flow are required.');
  if (typeof receipt.source !== 'string' || !receipt.source.trim() || receipt.source.length > 200) fail('A source description is required (up to 200 characters).');
  const points = receipt.valuations;
  if (!Array.isArray(points) || points.length < 2 || points.length > MAX_RETURN_POINTS) fail('Supply 2–1,000 valuation observations.');
  let previousTime = -Infinity;
  let index = 100;
  let netFlows = 0;
  const rows = points.map((point, i) => {
    if (!point || typeof point.at !== 'string' || !/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d{3}Z$/.test(point.at)) fail('Each observation requires a canonical UTC ISO timestamp.');
    const time = Date.parse(point.at);
    if (!Number.isFinite(time) || new Date(time).toISOString() !== point.at || time <= previousTime || time > now) fail('Observation times must be valid, strictly increasing and not in the future.');
    previousTime = time;
    const before = point.equity_before_flow;
    const after = point.equity_after_flow;
    const flow = point.external_flow;
    if (![before, after, flow].every(validNumber) || before < MIN_EQUITY || after < MIN_EQUITY) fail('Valuations must be finite, at least one cent before and after flow.');
    if (Math.abs(after - before - flow) > 0.010001) fail('Before/after valuations must reconcile to the external flow within one cent.');
    if (i === 0 && flow !== 0) fail('The opening observation must have zero flow; begin after opening funding.');
    const growthFactor = i === 0 ? 1 : before / points[i - 1].equity_after_flow;
    const periodReturn = i === 0 ? null : growthFactor - 1;
    if (i > 0) { index *= growthFactor; netFlows += flow; }
    if (!Number.isFinite(index) || index < 1e-12 || index > 1e15 || !Number.isFinite(netFlows)) fail('Calculated results exceed supported numeric bounds.');
    return { at: point.at, equity: after, flow, periodReturn, index, totalReturn: index / 100 - 1 };
  });
  return { rows, source: receipt.source, environment: receipt.environment, start: rows[0].at, end: rows.at(-1).at,
    return: index / 100 - 1, netFlows, pnl: rows.at(-1).equity - rows[0].equity - netFlows };
}

export function parseReturnReceipt(text, account, now) {
  if (typeof text !== 'string' || new TextEncoder().encode(text).byteLength > MAX_RETURN_BYTES) fail('Receipt exceeds the 1 MiB limit.');
  let receipt;
  try { receipt = JSON.parse(text); } catch { fail('Receipt is not valid JSON.'); }
  return calculateAccountReturns(receipt, account, now);
}

function chart(rows, key, label) {
  const values = rows.map((row) => row[key]);
  const min = Math.min(...values), max = Math.max(...values), range = max - min || 1;
  const start = Date.parse(rows[0].at), span = Date.parse(rows.at(-1).at) - start;
  const coordinates = rows.map((row) => `${20 + 600 * (Date.parse(row.at) - start) / span},${110 - 90 * (row[key] - min) / range}`).join(' ');
  return `<figure><figcaption>${escape(label)} · ${escape(key === 'equity' ? `${money(min)} to ${money(max)}` : `${min.toFixed(2)} to ${max.toFixed(2)}`)}</figcaption><svg viewBox="0 0 640 130" role="img" aria-label="${escape(label)}; exact observations in the table below" style="width:100%;max-height:180px"><polyline points="${coordinates}" fill="none" stroke="currentColor" stroke-width="2"/></svg></figure>`;
}

export function accountReturnsHtml(state = {}, session = {}, broker = {}) {
  const allowed = session.authenticated === true && session.session?.role === 'admin' && broker.accountId && ['live', 'sandbox'].includes(broker.mode);
  const result = allowed ? state.result : null;
  return `<section class="ew-history" aria-labelledby="ew-returns-title"><h3 id="ew-returns-title">Cash-flow-adjusted period returns</h3>
    <p>Broker balance history is not retained here. Current balances and closed trades cannot establish an account return or equity curve.</p>
    ${allowed ? `<label for="ew-returns-import">Open local valuation receipt (JSON, at most 1 MiB / 1,000 observations)</label><input id="ew-returns-import" type="file" accept=".json,application/json"><p>Read locally into this tab only. No upload or storage. Receipt assertions are user-supplied, not independently verified against the broker. <a href="/account-return-receipts.html" target="_blank" rel="noopener">Receipt format and limitations</a></p>` : '<p>An authenticated administrator with a known account is required to inspect a local receipt.</p>'}
    ${state.error && allowed ? `<p role="alert">${escape(state.error)}</p>` : ''}
    ${state.loading && allowed ? '<p role="status">Reading local receipt…</p><button id="ew-returns-clear" type="button" class="quiet-button">Cancel local receipt</button>' : ''}
    ${result ? `<p><strong>User-supplied ${escape(result.environment)} receipt · not broker-verified.</strong> Source: ${escape(result.source)}</p><p>Observed period: ${escape(result.start)} to ${escape(result.end)}. Historical through the last observation; not a current account valuation.</p>
    <dl class="ew-metrics"><div class="ew-metric"><dt>Time-weighted period return</dt><dd>${pct(result.return)}</dd></div><div class="ew-metric"><dt>External net funding</dt><dd>${money(result.netFlows)}</dd></div><div class="ew-metric"><dt>Cash-flow-adjusted dollar change</dt><dd>${money(result.pnl)}</dd></div></dl>
    <p>Return chains valuation-to-valuation growth, splitting at every external cash flow. Deposits are positive, withdrawals negative. Dividends, interest, trading costs and fees remain in account performance. Dollar change is not a percentage return.</p>
    ${chart(result.rows, 'equity', 'Observed account equity (includes funding)')}${chart(result.rows, 'index', 'Cash-flow-adjusted growth index (opening value 100)')}
    <details><summary>Inspect all ${result.rows.length} observations</summary><div style="overflow:auto"><table><caption>Receipt observations; lines connect supplied observations only</caption><thead><tr><th scope="col">UTC time</th><th scope="col">Equity after flow</th><th scope="col">External flow</th><th scope="col">Subperiod return</th><th scope="col">Growth index</th></tr></thead><tbody>${result.rows.map((row) => `<tr><td>${escape(row.at)}</td><td>${money(row.equity)}</td><td>${money(row.flow)}</td><td>${row.periodReturn === null ? 'Opening observation' : pct(row.periodReturn)}</td><td>${row.index.toFixed(2)}</td></tr>`).join('')}</tbody></table></div></details><button id="ew-returns-clear" type="button" class="quiet-button">Clear local receipt</button>` : '<p>Period return: unavailable. Equity curve: unavailable. Supply dated valuations and complete external-flow coverage to calculate them; missing history is never zero.</p>'}</section>`;
}

/** Invalidates in-flight file reads on replacement, clear, account change or logout. */
export function createReturnReceiptReader(onChange) {
  let generation = 0;
  return {
    clear() { generation++; onChange({ result: null, error: null, loading: false }); },
    async load(file, account) {
      const request = ++generation;
      onChange({ result: null, error: null, loading: true });
      try {
        if (!file || file.size > MAX_RETURN_BYTES) fail('Choose a JSON receipt no larger than 1 MiB.');
        const text = await file.text();
        if (request !== generation) return;
        const result = parseReturnReceipt(text, account);
        onChange({ result, error: null, loading: false });
      } catch (error) {
        if (request === generation) onChange({ result: null, error: String(error?.message || error), loading: false });
      }
    },
  };
}
