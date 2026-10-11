import test from 'node:test';
import assert from 'node:assert/strict';
import { validGate, TOPICS, count } from '../../web/atmosphere/data.js';
import { readinessModel } from '../../web/atmosphere/readiness.js';

const keys = TOPICS.shadow_entry_gates[2];
const valid = [
  { actual: 0, required: 30, passed: false },
  { actual: 29, required: 30, passed: false },
  { actual: 30, required: 30, passed: true },
  { actual: 31, required: 30, passed: true },
  { actual: Number.MAX_SAFE_INTEGER, required: 1000, passed: true },
];
const missing = [undefined, null, {}, { actual: 0, required: 30 }, { required: 30, passed: false }, { actual: 0, passed: false }];
const malformed = [false, 0, 'gate', [],
  ...[null, '0', true, -1, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1].map(actual => ({ actual, required: 30, passed: false })),
  ...[null, '30', true, -1, 0, 0.5, NaN, Infinity, Number.MAX_SAFE_INTEGER + 1].map(required => ({ actual: 0, required, passed: false })),
  ...[null, 'false', 0, 1].map(passed => ({ actual: 0, required: 30, passed })),
];
const contradictory = [
  { actual: 0, required: 30, passed: true },
  { actual: 29, required: 30, passed: true },
  { actual: 30, required: 30, passed: false },
  { actual: 31, required: 30, passed: false },
];
for (const [name, cases, available] of [['valid', valid, true], ['missing', missing, false], ['malformed', malformed, false], ['contradictory', contradictory, false]]) {
  test(`shared gate validation and readiness handle ${name} evidence consistently`, () => {
    for (const gate of cases) {
      assert.equal(validGate(gate), available);
      const shadow = { shadow_entry_gates: Object.fromEntries(keys.map(key => [key, gate])) };
      const expected = available ? (gate.passed ? 'Met' : 'Not met') : 'Not reported';
      assert.deepEqual(readinessModel(null, shadow).gates.map(item => item.status), keys.map(() => expected));
    }
  });
}

// In-memory DOM and injected fetch only; no browser, server or external requests.
class Node {
  constructor(tag = '') { this.tag = tag; this.children = []; this.text = ''; this.value = ''; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); if (this.tag === 'select' && !this.value && children[0]) this.value = children[0].value; }
  replaceChildren(...children) { this.text = ''; this.children = []; this.append(...children); }
  setAttribute() {}
  addEventListener(name, fn) { this.events[name] = fn; }
}
const flatten = node => [node, ...node.children.flatMap(flatten)];
test('summary renders only consistent gates and clears stale pass results after refresh', async () => {
  const previous = { document: globalThis.document, window: globalThis.window, fetch: globalThis.fetch };
  const nodes = new Map(); const requests = [];
  const node = id => { if (!nodes.has(id)) nodes.set(id, new Node(['topic', 'catalog', 'sort'].includes(id) ? 'select' : 'div')); return nodes.get(id); };
  globalThis.document = { getElementById: node, createElement: tag => new Node(tag), createTextNode: text => { const result = new Node(); result.textContent = text; return result; } };
  globalThis.window = { addEventListener() {} };
  let current;
  globalThis.fetch = async url => {
    requests.push(url);
    assert.ok(url === '/data/diagnostics/shared_mart_sync_latest.json' || url === '/data/diagnostics/shared_mart_shadow_evidence_latest.json');
    const data = url.endsWith('_sync_latest.json') ? { schema_version: 1, mart_id: 'test' } : { schema_version: 4, mart_id: 'test', shadow_entry_gates: Object.fromEntries(keys.map(key => [key, current])) };
    return new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' } });
  };
  node('catalog').value = 'tables'; node('sort').value = 'name';
  try {
    await import('../../web/atmosphere/atmosphere.js');
    // Drain the module's initial mocked refresh before driving sequential cases.
    await new Promise(resolve => setImmediate(resolve));
    node('topic').value = 'shadow_entry_gates';
    for (const gate of [...valid, ...contradictory, ...missing, ...malformed]) {
      current = gate;
      await node('refresh').events.click();
      // Non-finite values become null over JSON, and must still fail closed.
      const expected = validGate(gate) ? `${gate.passed ? 'Pass' : 'Not met'} · ${count(gate.actual)} / ${count(gate.required)}` : 'Not reported';
      const values = flatten(node('summary')).filter(item => item.tag === 'dd').map(item => item.textContent);
      assert.deepEqual(values, keys.map(() => expected));
      const readiness = node('evidence-readiness').textContent;
      for (const key of keys) assert.ok(readiness.includes(`${key.replaceAll('_', ' ')}: ${validGate(gate) ? (gate.passed ? 'Met' : 'Not met') : 'Not reported'}`));
    }
    assert.equal(requests.length, 2 * (1 + valid.length + missing.length + malformed.length + contradictory.length));
  } finally { Object.assign(globalThis, previous); }
});
