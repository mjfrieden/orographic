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

test('empty snapshot gives an explicit empty state without unsupported architecture claims', () => {
  const previous = globalThis.document; globalThis.document = { createElement: () => new Node() };
  try {
    const root = new Node(); renderCards(root, validateCards({ schema_version: 1, cards: [] }));
    assert.match(root.textContent, /No model cards are present/);
    assert.match(root.textContent, /No model identity or performance can be assessed/);
    assert.match(root.textContent, /Refresh snapshots/);
    assert.doesNotMatch(root.textContent, /How the systems fit together|studies above|Validation sample/);
    assert.equal(root.children.at(-1).href, MODEL_SOURCE);
  } finally { globalThis.document = previous; }
});
test('replay returns reject malformed and overflowing values but retain losses and missing evidence', () => {
  const fixture = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
  for (const key of ['mean_return', 'median_return']) {
    for (const value of [true, false, '0.5', {}, [], undefined, NaN, Infinity, -Infinity, 1e308]) {
      const data = structuredClone(fixture); data.cards[2].replay[key] = value;
      assert.throws(() => validateCards(data), /Invalid replay evidence/);
    }
    for (const value of [null, 0, -.4432, 1.5]) {
      const data = structuredClone(fixture); data.cards[2].replay[key] = value; assert.equal(validateCards(data), data);
    }
  }
});
test('missing replay percentages never acquire a percent sign', async () => {
  const { formatReturn } = await import('../../web/atmosphere/model-cards.js');
  for (const value of [null, undefined, '', '0.5', false, NaN, Infinity, 1e308]) assert.equal(formatReturn(value), 'Not reported');
  assert.equal(formatReturn(0), '0%'); assert.equal(formatReturn(-.4432), '-44.32%'); assert.equal(formatReturn(1.5), '150%');
  const previous = globalThis.document; globalThis.document = { createElement: () => new Node() };
  try {
    const data = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
    data.cards[2].replay.mean_return = null; data.cards[2].replay.median_return = null;
    const root = new Node(); renderCards(root, validateCards(data));
    assert.match(root.textContent, /mean return Not reported; median Not reported\. Not broker P&L/);
    assert.doesNotMatch(root.textContent, /Not reported%/);
  } finally { globalThis.document = previous; }
});
test('provenance disclosures have distinct accessible names for each card', () => {
  const previous = globalThis.document; globalThis.document = { createElement: () => new Node() };
  try {
    const data = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
    const root = new Node(); renderCards(root, validateCards(data));
    const walk = node => [node, ...node.children.flatMap(walk)];
    const names = walk(root).map(n => n.attributes['aria-label']).filter(n => n && /provenance for|identity for/.test(n));
    assert.equal(names.length, 8); assert.equal(new Set(names).size, 8);
    for (const card of data.cards) assert.ok(names.some(name => name.endsWith(card.title)));
  } finally { globalThis.document = previous; }
});
test('disclosures have source-defined keyboard focus and minimum touch target styles', () => {
  const css = readFileSync(new URL('../../web/atmosphere/model-cards.css', import.meta.url), 'utf8');
  assert.match(css, /\.current-model-card summary\{[^}]*min-height:44px/);
  assert.match(css, /\.current-model-card summary:focus-visible\{[^}]*outline:3px/);
  assert.match(css, /@media\(max-width:760px\)\{\.model-card-grid\{grid-template-columns:1fr\}/);
});
test('loading, empty, stale and malformed responses clear busy state and recover on refresh', async () => {
  const old = { document: globalThis.document, window: globalThis.window };
  globalThis.document = { createElement: () => new Node() }; globalThis.window = { addEventListener() {} };
  const requests = [], root = new Node(); const tick = () => new Promise(resolve => setImmediate(resolve));
  const response = value => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json' } });
  try {
    const { mountModelCards } = await import('../../web/atmosphere/model-cards.js');
    const refresh = mountModelCards(root, new Node(), { fetcher: () => new Promise(resolve => requests.push(resolve)) });
    assert.equal(root.attributes['aria-busy'], 'true'); assert.match(root.textContent, /Loading model identity/);
    requests[0](response({ schema_version: 1, cards: [] })); await tick(); await tick();
    assert.equal(root.attributes['aria-busy'], 'false'); assert.match(root.textContent, /No model cards are present/);
    const stale = JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
    stale.runtime_at_utc = '2000-01-01T00:00:00Z'; stale.cards[3].verified_at_utc = '2000-01-01T00:00:00Z';
    const updating = refresh(); requests[1](response(stale)); await updating;
    assert.match(root.textContent, /Stale runtime snapshot/); assert.match(root.textContent, /Stale curated source review/);
    const malformed = refresh(); requests[2](response({schema_version:2,cards:[]})); await malformed;
    assert.match(root.textContent, /Model cards unavailable/); assert.doesNotMatch(root.textContent, /Directional Scout/); assert.equal(root.attributes['aria-busy'], 'false');
    const recovered = refresh(); requests[3](response(stale)); await recovered;
    assert.match(root.textContent, /Directional Scout/); assert.equal(root.attributes['aria-busy'], 'false');
  } finally { Object.assign(globalThis, old); }
});
test('request timeout clears loading and permits a later refresh', async t => {
  const old = { document: globalThis.document, window: globalThis.window };
  globalThis.document = { createElement: () => new Node() }; globalThis.window = { addEventListener() {} };
  t.mock.timers.enable({ apis: ['setTimeout'] });
  const root = new Node(); let timeoutSignal;
  try {
    const { mountModelCards } = await import('../../web/atmosphere/model-cards.js');
    const refresh = mountModelCards(root, new Node(), { fetcher: (url, { signal }) => new Promise((resolve, reject) => {
      timeoutSignal = signal; signal.addEventListener('abort', () => reject(new Error('aborted')));
    }) });
    t.mock.timers.tick(15000); await Promise.resolve(); await Promise.resolve();
    assert.equal(timeoutSignal.aborted, true); assert.match(root.textContent, /Model cards unavailable/); assert.equal(root.attributes['aria-busy'], 'false');
    const again = refresh(); assert.equal(timeoutSignal.aborted, false); assert.equal(root.attributes['aria-busy'], 'true');
    t.mock.timers.tick(15000); await again; assert.equal(root.attributes['aria-busy'], 'false');
  } finally { t.mock.timers.reset(); Object.assign(globalThis, old); }
});
