import test from 'node:test';
import assert from 'node:assert/strict';
import { freshness, curatedFreshness, formatMetric, validateCards, renderCards, MODEL_SOURCE } from '../../web/atmosphere/model-cards.js';
import { readFileSync } from 'node:fs';
const now = Date.parse('2026-10-09T22:00:00Z');
test('freshness uses runtime date, never generation date', () => {
  assert.match(freshness({ runtime_at_utc: '2026-10-01', generated_at_utc: '2026-10-09' }, now), /Stale/);
  assert.match(freshness({ runtime_at_utc: '2026-10-09T20:29:04Z' }, now), /Recent/);
  for (const runtime_at_utc of [null, '', 'invalid', '2099-01-01']) assert.match(freshness({ runtime_at_utc }, now), /invalid/);
});
test('missing metrics remain missing', () => {
  for (const value of [null, undefined, '', '0.5', NaN, Infinity]) assert.equal(formatMetric(value), 'Not reported');
  assert.equal(formatMetric(0), '0');
});
test('schema is bounded and fails closed', () => {
  for (const value of [{}, { schema_version: 2, cards: [] }, { schema_version: 1, cards: Array(9).fill({}) }, { schema_version: 1, cards: [{}] }]) assert.throws(() => validateCards(value));
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  assert.equal(validateCards(fixture), fixture);
  assert.equal(fixture.cards.at(-1).system, 'Cirrus');
  assert.deepEqual(fixture.cards.at(-1).metrics, []);
});
test('first section after hero contains cards; no trading controls changed', () => {
  const html = readFileSync(new URL('../../web/atmosphere/index.html', import.meta.url), 'utf8');
  assert.ok(html.indexOf('id="current-model-cards"') < html.indexOf('class="toolbar"'));
  assert.equal(MODEL_SOURCE, '/data/diagnostics/current_model_cards_latest.json');
});

class Node {
  constructor() { this.children = []; this.text = ''; this.attributes = {}; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
}
test('refresh races, auth, failure and back-forward restoration', async () => {
  const old = { document: globalThis.document, window: globalThis.window };
  const events = {}, requests = [], root = new Node(), button = new Node();
  globalThis.document = { createElement: () => new Node() };
  globalThis.window = { addEventListener: (name, fn) => { events[name] = fn; } };
  const tick = () => new Promise(resolve => setImmediate(resolve));
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  const response = value => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
  try {
    const { mountModelCards } = await import('../../web/atmosphere/model-cards.js');
    const refresh = mountModelCards(root, button, { fetcher: (url, options) => new Promise(resolve => requests.push({ url, options, resolve })) });
    const newer = refresh(); assert.equal(requests[0].options.signal.aborted, true);
    requests[1].resolve(response(fixture)); await newer; assert.match(root.textContent, /Directional Scout/);
    requests[0].resolve(new Response('{}', { status: 404 })); await tick(); await tick(); assert.match(root.textContent, /Directional Scout/);
    const failure = refresh(); assert.doesNotMatch(root.textContent, /Directional Scout/); requests[2].resolve(new Response('{}', { status: 404 })); await failure; assert.match(root.textContent, /unavailable/);
    const auth = refresh(); requests[3].resolve(new Response('{}', { status: 401 })); await auth; assert.match(root.textContent, /Sign in again/);
    events.pagehide(); events.pageshow({ persisted: true }); requests[4].resolve(response(fixture)); await tick(); await tick(); assert.match(root.textContent, /Directional Scout/); assert.equal(root.attributes['aria-busy'], 'false');
    assert.ok(requests.every(r => r.url === MODEL_SOURCE && r.options.credentials === 'same-origin' && r.options.redirect === 'error'));
  } finally { Object.assign(globalThis, old); }
});
test('matched cards reject missing identity, invalid metrics and invalid sample sizes', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  for (const change of [card => { delete card.model_sha256; }, card => { delete card.scaler_sha256; }, card => { card.metrics[0].value = 3; }, card => { card.metrics[0].value = -1; }, card => { card.sample_size = -1; }, card => { card.sample_size = 1.5; }, card => { card.population = ''; }]) {
    const malformed = structuredClone(fixture); change(malformed.cards[0]); assert.throws(() => validateCards(malformed));
  }
});
test('boolean, fractional and negative nested counts are rejected', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  for (const value of [true, false, -1, 1.5, '9']) {
    const classes = structuredClone(fixture); classes.cards[1].training_class_counts.put_edge = value; assert.throws(() => validateCards(classes));
    const replay = structuredClone(fixture); replay.cards[2].replay.sample_size = value; assert.throws(() => validateCards(replay));
  }
});

test('curated source freshness never follows new scan generation', () => {
  const card = { verified_at_utc: '2026-10-01T00:00:00Z', generated_at_utc: '2026-10-09T21:00:00Z' };
  assert.match(curatedFreshness(card, now), /Stale/);
  assert.match(curatedFreshness({ verified_at_utc: '2026-10-09T00:00:00Z' }, now), /Recent/);
  for (const value of [null, 'bad', '2099-01-01']) assert.match(curatedFreshness({ verified_at_utc: value }, now), /invalid/);
});
test('curated snapshots reject live metrics and missing provenance', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  for (const change of [c => { c.metrics = [{ label: 'AUC', value: .99 }]; }, c => { c.source_sha256 = 'bad'; }, c => { c.verified_at_utc = 'bad'; }, c => { c.source_revision = ''; }, c => { c.evaluation_summary = Array(9).fill('x'); }, c => { c.evidence_kind = 'live'; }]) {
    const malformed = structuredClone(fixture); change(malformed.cards[3]); assert.throws(() => validateCards(malformed));
  }
  const malformed = structuredClone(fixture); malformed.cards[7].evaluation_completed_at = null; assert.throws(() => validateCards(malformed));
});
test('cards show research separately and architecture after all cards', () => {
  const previous = globalThis.document; globalThis.document = { createElement: () => new Node() };
  try {
    const root = new Node(); const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
    renderCards(root, validateCards(fixture));
    assert.match(root.textContent, /current served identity and live performance unverified/);
    assert.match(root.textContent, /Research only · rejected and inactive/);
    assert.match(root.textContent, /Historical discovery only/);
    assert.match(root.textContent, /do not fetch Cirrus private sources/);
    assert.ok(root.textContent.indexOf('Noon three-session tail ensemble') < root.textContent.indexOf('How the systems fit together'));
  } finally { globalThis.document = previous; }
});
