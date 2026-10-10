import { fetchSnapshot, compatible, count, timestamp, safeText, TOPICS, catalogRows, filterRows, metricValue, LIMITS } from './data.js';
const $ = id => document.getElementById(id);
const el = (tag, text, className) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (className) node.className = className; return node; };
const label = key => key.replaceAll('_', ' ').replace(/\b(orographic|cirrus)\b/g, word => word[0].toUpperCase() + word.slice(1));
let state = { sync: null, shadow: null }; let active; let generation = 0;
function notice(text) { $('notices').append(el('p', text, 'notice')); }
function contextLine(name, data, path) {
  const row = el('p'); row.append(el('strong', `${name}: `));
  row.append(document.createTextNode(data ? `generated ${timestamp(data.generated_at_utc)} · Mart ${safeText(data.mart_id) || 'not reported'}` : 'Unavailable'));
  if (data) { row.append(el('br')); const link = el('a', 'View source snapshot'); link.href = path; row.append(link); } return row;
}
function render() {
  $('overview').replaceChildren(); $('context').replaceChildren();
  const { sync, shadow } = state;
  const stats = [['Quote observations', sync?.rows?.option_quotes, 'Sync snapshot · stored rows'], ['Recommendations', sync?.rows?.recommendations, 'Sync snapshot · stored rows'], ['Consumer views', shadow?.consumer_bundle?.views && typeof shadow.consumer_bundle.views === 'object' && !Array.isArray(shadow.consumer_bundle.views) ? Object.keys(shadow.consumer_bundle.views).length : undefined, 'Evidence snapshot · catalog'], ['Comparable return pairs', shadow?.cross_system_comparison?.direct_return_comparable_pairs, 'Evidence snapshot · direct comparison']];
  for (const [name, value, context] of stats) { const card = el('article', undefined, 'stat'); card.append(el('p', name), el('strong', count(value)), el('small', context)); $('overview').append(card); }
  $('context').append(contextLine('Sync', sync, '/data/diagnostics/shared_mart_sync_latest.json'), contextLine('Evidence', shadow, '/data/diagnostics/shared_mart_shadow_evidence_latest.json'));
  if (sync) {
    $('context').append(el('p', `Source systems: ${Array.isArray(sync.source_systems) ? sync.source_systems.slice(0, 8).map(safeText).join(', ') : 'Not reported'}`));
    $('context').append(el('p', `Last observed Iceberg publication: ${timestamp(sync.iceberg?.latest_publication_at_utc)}. This is a pre-publication probe, not a verdict on current storage freshness.`, 'muted'));
  }
  if (shadow?.data_quality?.integrity_clean === false) notice(`Integrity anomalies reported in ${count(shadow.data_quality.cohorts_with_integrity_anomalies)} cohort(s). Inspect Data quality before interpreting coverage.`);
  renderCatalog(); renderSummary();
}
function renderCatalog() {
  const kind = $('catalog').value; const data = kind === 'tables' ? state.sync : state.shadow;
  const catalog = catalogRows(data, kind); const rows = filterRows(catalog.rows, $('search').value, $('sort').value);
  $('catalog-body').replaceChildren();
  $('catalog-status').textContent = !data ? 'This catalog snapshot is unavailable.' : `${rows.length} shown · generated ${timestamp(data.generated_at_utc)}${catalog.truncated ? ` · limited to first ${LIMITS.rows} entries` : ''}`;
  for (const item of rows) { const row = el('tr'); const context = el('td'); context.append(document.createTextNode(kind === 'tables' ? 'Stored table rows; no raw records loaded.' : `Primary key: ${item.primaryKey || 'Not reported'}`)); if (item.hash) { const details = el('details'); details.append(el('summary', 'Snapshot hash'), el('span', item.hash)); context.append(details); } row.append(el('td', item.name), el('td', count(item.rows)), context); $('catalog-body').append(row); }
  if (!rows.length) { const row = el('tr'); const cell = el('td', data ? 'No matching catalog entries.' : 'Refresh to retry this snapshot.'); cell.colSpan = 3; row.append(cell); $('catalog-body').append(row); }
}
function renderSummary() {
  const key = $('topic').value; const [title, description, metrics] = TOPICS[key]; const data = state.shadow?.[key];
  $('summary').replaceChildren(el('h3', title), el('p', description, 'muted'));
  if (!data || typeof data !== 'object') { $('summary').append(el('p', 'This summary is not available in the evidence snapshot.')); return; }
  $('summary').append(el('p', `Evidence generated ${timestamp(state.shadow.generated_at_utc)} · ${metrics.length} selected aggregate metrics`, 'muted'));
  const list = el('dl', undefined, 'metrics');
  for (const metric of metrics) { const card = el('div', undefined, 'metric'); let value = metricValue(metric, data[metric]); if (key === 'shadow_entry_gates') { const gate = data[metric]; value = !gate || typeof gate.passed !== 'boolean' ? 'Not reported' : `${gate.passed ? 'Pass' : 'Not met'} · ${count(gate.actual)} / ${count(gate.required)}`; } card.append(el('dt', label(metric)), el('dd', value)); list.append(card); }
  $('summary').append(list);
}
async function refresh() {
  active?.abort(); const controller = new AbortController(); active = controller; const current = ++generation;
  const timer = setTimeout(() => controller.abort(), LIMITS.timeout); $('refresh').textContent = 'Restart refresh'; $('overview').setAttribute('aria-busy', 'true'); $('status').textContent = 'Loading two bounded aggregate snapshots…';
  // Clear old data immediately: never present a prior snapshot as the new request's result.
  state = { sync: null, shadow: null }; $('notices').replaceChildren(); render();
  const results = await Promise.allSettled(['sync', 'shadow'].map(kind => fetchSnapshot(kind, { signal: controller.signal }))); clearTimeout(timer);
  if (current !== generation) return;
  let loaded = 0; let auth = false; $('notices').replaceChildren();
  results.forEach((result, index) => { const kind = index ? 'shadow' : 'sync'; if (result.status === 'fulfilled') { state[kind] = result.value; loaded++; } else { auth ||= result.reason?.code === 'auth'; notice(`${index ? 'Evidence' : 'Sync'} snapshot: ${controller.signal.aborted ? 'Request timed out or was interrupted. Refresh to retry.' : safeText(result.reason?.message) || 'Unavailable. Refresh to retry.'}`); } });
  if (state.sync && state.shadow && !compatible(state.sync, state.shadow)) { notice('Snapshot mart IDs do not match. Evidence analytics are withheld to avoid combining different snapshots. Refresh to retry.'); state.shadow = null; loaded--; }
  if (auth) { const link = el('a', 'Sign in again'); link.href = '/login/?next=%2Fatmosphere%2F'; $('notices').append(link); }
  render(); $('overview').setAttribute('aria-busy', 'false'); $('refresh').textContent = 'Refresh snapshots'; $('status').textContent = loaded === 2 ? 'Two snapshots loaded. Saved research data; refresh manually to check for updates.' : loaded === 1 ? 'Partial data: one snapshot is available. See its source and date below.' : 'Snapshots unavailable. No saved values are being shown.';
}
for (const [key, [title]] of Object.entries(TOPICS)) { const option = el('option', title); option.value = key; $('topic').append(option); }
$('refresh').addEventListener('click', refresh); for (const id of ['catalog', 'sort']) $(id).addEventListener('change', renderCatalog); $('search').addEventListener('input', renderCatalog); $('topic').addEventListener('change', renderSummary);
window.addEventListener('pagehide', () => { generation++; active?.abort(); });
window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
refresh();
