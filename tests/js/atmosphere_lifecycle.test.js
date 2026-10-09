import test from 'node:test';
import assert from 'node:assert/strict';
class Node {
  constructor(tag = '') { this.tag = tag; this.children = []; this.text = ''; this.value = ''; this.attributes = {}; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); if (this.tag === 'select' && !this.value && children[0]) this.value = children[0].value; }
  replaceChildren(...children) { this.text = ''; this.children = []; this.append(...children); }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
}
const response = data => new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } });
const sync = id => ({ schema_version: 1, mart_id: id, generated_at_utc: '2026-10-08T22:20:49Z', rows: { option_quotes: 5925875, recommendations: 4988 }, source_systems: ['cirrus','orographic'] });
const shadow = id => ({ schema_version: 4, mart_id: id, generated_at_utc: '2026-10-08T22:20:49Z', consumer_bundle: { views: { example: { rows: 1 } } }, data_quality: { integrity_clean: false, cohorts_with_integrity_anomalies: 1, worst_integrity_anomaly_rate: 0.0004679457182966776 }, cross_system_comparison: { direct_return_comparable_pairs: 0 } });
const tick = () => new Promise(resolve => setImmediate(resolve));
test('page lifecycle: superseded requests, partial data, ID mismatch, auth and back-forward restore', async () => {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  const nodes = new Map(); const request = []; const events = {};
  globalThis.document = { getElementById: id => { if (!nodes.has(id)) nodes.set(id, new Node(['topic','catalog','sort'].includes(id) ? 'select' : 'div')); return nodes.get(id); }, createElement: tag => new Node(tag), createTextNode: text => { const node = new Node(); node.textContent = text; return node; } };
  globalThis.window = { addEventListener: (name, fn) => events[name] = fn };
  globalThis.fetch = (url, options) => new Promise((resolve, reject) => request.push({ url, options, resolve, reject }));
  const node = id => document.getElementById(id); node('catalog').value = 'tables'; node('sort').value = 'name';
  try {
    await import('../../web/atmosphere/atmosphere.js'); assert.equal(request.length, 2);
    const fresh = node('refresh').events.click(); assert.equal(request.length, 4); assert.equal(request[0].options.signal.aborted, true);
    request[2].resolve(response(sync('new'))); request[3].resolve(response(shadow('new'))); await fresh;
    assert.match(node('context').textContent, /Mart new/); assert.match(node('summary').textContent, /0.0468%/);
    request[0].resolve(response(sync('old'))); request[1].resolve(response(shadow('old'))); await tick(); await tick(); assert.doesNotMatch(node('context').textContent, /Mart old/);
    const partial = node('refresh').events.click(); assert.doesNotMatch(node('overview').textContent, /5,925,875/); request[4].resolve(response(sync('new'))); request[5].resolve(new Response('{}', { status: 404 })); await partial; assert.match(node('status').textContent, /Partial/); assert.match(node('summary').textContent, /not available/);
    const mismatch = node('refresh').events.click(); request[6].resolve(response(sync('a'))); request[7].resolve(response(shadow('b'))); await mismatch; assert.match(node('notices').textContent, /do not match/); assert.match(node('summary').textContent, /not available/);
    const auth = node('refresh').events.click(); request[8].resolve(new Response('{}', { status: 401 })); request[9].resolve(new Response('{}', { status: 401 })); await auth; assert.match(node('notices').textContent, /Sign in again/); assert.equal(node('overview').attributes['aria-busy'], 'false');
    events.pagehide(); events.pageshow({ persisted: true }); assert.equal(request.length, 12); request[10].resolve(response(sync('restored'))); request[11].resolve(response(shadow('restored'))); await tick(); await tick(); assert.match(node('context').textContent, /Mart restored/);
    assert.equal(request.every(r => r.url.startsWith('/data/diagnostics/shared_mart_')), true);
  } finally { Object.assign(globalThis, previous); }
});
