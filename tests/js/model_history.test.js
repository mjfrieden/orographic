import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { MODEL_HISTORY } from '../../web/atmosphere/model-history-catalog.js';
import { validateHistory, historyRows, observationMatches, renderModelHistory } from '../../web/atmosphere/model-history.js';
const fixture = () => JSON.parse(readFileSync(new URL('../../web/data/diagnostics/current_model_cards_latest.json', import.meta.url)));
class Node {
  constructor(tag) { this.tag = tag; this.children = []; this.text = ''; this.attributes = {}; this.events = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener(key, fn) { this.events[key] = fn; }
}
const walk = node => [node, ...node.children.flatMap(walk)];
function dom(fn) { const previous = globalThis.document; globalThis.document = { createElement: tag => new Node(tag) }; try { fn(); } finally { globalThis.document = previous; } }
test('reviewed catalog pins eight manifest revisions independently of scan time', () => {
  assert.equal(validateHistory(MODEL_HISTORY), MODEL_HISTORY);
  assert.equal(MODEL_HISTORY.reviewed_revision, 'f9c6cc593ab7987774d8c249d37aab43d0e74a0d');
  assert.equal(MODEL_HISTORY.entries.length, 8);
  assert.equal(MODEL_HISTORY.entries[0].committed_at, '2026-08-30T19:26:10Z');
  assert.equal(MODEL_HISTORY.entries[0].artifacts.production_payoff_ranker.card_version, 2);
  assert.equal(MODEL_HISTORY.entries[1].artifacts.production_payoff_ranker.card_version, 1);
  assert.equal(MODEL_HISTORY.entries[1].artifacts.production_payoff_ranker.trained_at, null);
  assert.match(MODEL_HISTORY.coverage, /manually reviewed/);
});
test('actual artifact change differs from metadata-only revision and retained baseline', () => {
  const rows = historyRows(MODEL_HISTORY);
  assert.deepEqual(rows[0].changes.map(row => [row.key, row.change]), [['production_payoff_ranker', 'Artifact hash changed']]);
  const august = rows[2]; assert.equal(august.changes.find(row => row.key === 'scout_model').change, 'Role or card metadata changed');
  assert.equal(rows.at(-1).baseline, true);
  assert.ok(rows.at(-1).changes.every(row => row.change === 'Retained baseline'));
  assert.ok(!historyRows(MODEL_HISTORY, { kind: 'artifacts' }).some(row => row.baseline));
});
test('same card version with changed artifact hash is a replacement', () => {
  const rows = historyRows(MODEL_HISTORY, { artifact: 'scout_model', kind: 'artifacts' });
  assert.ok(rows.length >= 3);
  for (const row of rows) { const change = row.changes[0]; assert.equal(change.change, 'Artifact hash changed'); assert.equal(change.before.card_version, 3); assert.equal(change.after.card_version, 3); assert.notEqual(change.before.sha256, change.after.sha256); }
});
test('removed artifact does not become a continuing model or a verified stopping date', () => {
  const rows = historyRows(MODEL_HISTORY, { artifact: 'payoff_model', kind: 'artifacts' });
  assert.equal(rows[0].changes[0].change, 'Removed from manifest'); assert.equal(rows[0].changes[0].after, null);
});
test('current runtime comparison requires both model and source-card identity, including Scout scaler', () => {
  assert.ok(observationMatches(MODEL_HISTORY, fixture()).every(row => row.matched));
  for (const key of ['model_sha256', 'source_sha256', 'source_path', 'scaler_sha256', 'identity_status']) {
    const data = fixture(); data.cards[0][key] = 'wrong'; assert.equal(observationMatches(MODEL_HISTORY, data)[0].matched, false);
  }
  const scan = fixture(); scan.runtime_at_utc = '2099-01-01T00:00:00Z';
  assert.equal(historyRows(MODEL_HISTORY).length, 8); // Runtime timestamps never generate events.
  assert.deepEqual(observationMatches(MODEL_HISTORY, null), []);
});
test('malformed catalog cannot inject links or bypass bounded identities', () => {
  for (const change of [c => c.schema_version = 2, c => c.repository = 'evil/repo', c => c.reviewed_revision = '../evil', c => c.entries.push(c.entries[0]), c => c.entries[0].committed_at = '2099-01-01T00:00:00Z', c => c.entries[0].manifest_sha256 = '', c => c.entries[0].artifacts.scout_model.path = 'javascript:alert(1)', c => c.entries[0].artifacts.scout_model.card_path = '../../secret', c => c.entries[0].artifacts.scout_model.required = 'true']) { const catalog = structuredClone(MODEL_HISTORY); change(catalog); assert.throws(() => validateHistory(catalog)); }
});
test('interactive filters, empty result, repeated changes and native disclosures are usable offline', () => dom(() => {
  const root = new Node('root'); renderModelHistory(root, fixture());
  assert.match(root.textContent, /not verified served-release times/); assert.match(root.textContent, /not a release event/); assert.match(root.textContent, /Cirrus:.*no served-release receipt/);
  const controls = walk(root).filter(n => n.tag === 'select'); assert.equal(controls.length, 2);
  controls[0].value = 'production_payoff_ranker'; controls[0].events.change();
  assert.match(root.textContent, /2 of 8 retained/); assert.equal(walk(root).filter(n => n.tag === 'details').length, 2);
  controls[0].value = 'does_not_exist'; controls[0].events.change(); assert.match(root.textContent, /No retained revisions match/);
  controls[0].value = ''; controls[1].value = 'all'; controls[1].events.change(); assert.equal(walk(root).filter(n => n.tag === 'details').length, 8);
  controls[1].events.change(); assert.equal(walk(root).filter(n => n.tag === 'details').length, 8);
  assert.ok(walk(root).filter(n => n.tag === 'summary').every(n => n.attributes['aria-label']));
  assert.ok(walk(root).filter(n => n.tag === 'a').every(n => /^https:\/\/github.com\/mjfrieden\/orographic\/(blob|commit)\/[a-f0-9]{40}(\/|$)/.test(n.href)));
}));
test('history remains available without runtime and malformed history fails visibly', () => dom(() => {
  const root = new Node(); renderModelHistory(root, null); assert.match(root.textContent, /Runtime comparison unavailable/); assert.equal(walk(root).filter(n => n.tag === 'details').length, 8);
  const broken = new Node(); renderModelHistory(broken, fixture(), {}); assert.match(broken.textContent, /source catalog is invalid/); assert.equal(walk(broken).filter(n => n.tag === 'details').length, 0);
  const empty = structuredClone(MODEL_HISTORY); empty.entries = []; const blank = new Node(); renderModelHistory(blank, null, empty); assert.match(blank.textContent, /No retained revisions match/);
}));
test('artifact labels and event titles are rendered as text, not HTML', () => dom(() => {
  const catalog = structuredClone(MODEL_HISTORY); catalog.entries[0].title = '<img src=x onerror=evil()>';
  const root = new Node(); renderModelHistory(root, null, catalog);
  assert.ok(walk(root).some(node => node.tag === 'summary' && node.textContent.includes('<img')));
  assert.equal(walk(root).filter(node => node.tag === 'img').length, 0);
}));
test('history adds no fetch, broker action, persistence, or remote catalog dependency', () => {
  const source = readFileSync(new URL('../../web/atmosphere/model-history.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /\b(fetch|localStorage|sessionStorage|XMLHttpRequest|WebSocket)\b/);
  const css = readFileSync(new URL('../../web/atmosphere/model-cards.css', import.meta.url), 'utf8');
  assert.match(css, /\.model-history select\{[^}]*min-height:44px/);
  assert.match(css, /\.model-history select:focus-visible\{[^}]*outline:3px/);
});

test('policy and decision fields reject oversized, nested, unknown and wrong-type evidence', () => {
  for (const change of [
    c => c.entries[0].policy.artifact_change_rule = 'x'.repeat(1000000),
    c => c.entries[0].policy.profile = { nested: 'object' },
    c => c.entries[0].policy.extra = 'unknown',
    c => c.entries[0].policy.lane_count = true,
    c => c.entries[0].policy.legacy_runtime_authority = 'false',
    c => c.entries[0].policy.challenger_policy = Array(9).fill('requirement'),
    c => c.entries[0].policy.challenger_policy = [['nested']],
    c => c.entries[0].policy.challenger_policy = ['x'.repeat(501)],
    c => c.entries[2].decision.effective_date = ['2026-08-12'],
    c => c.entries[2].decision.effective_date = '2026-02-30',
    c => c.entries[2].decision.effective_date = '2026-08-12T00:00:00Z',
    c => c.entries[2].decision.evidence_note_path = 'docs/../../private.md',
    c => c.entries[2].decision.production_default_variant = 'x'.repeat(501),
    c => c.entries[2].decision.extra = 'unknown',
    c => delete c.entries[2].decision.effective_date
  ]) { const catalog = structuredClone(MODEL_HISTORY); change(catalog); assert.throws(() => validateHistory(catalog)); }
});
test('policy changes present actual bounded source values and ignore object key reordering', async () => {
  const { policyChanges } = await import('../../web/atmosphere/model-history.js');
  const rows = historyRows(MODEL_HISTORY);
  const active = rows.find(row => row.event.commit.startsWith('6403797'));
  assert.deepEqual(active.policyDiff.find(row => row.key === 'side_aware_scout_default'), { group: 'policy', key: 'side_aware_scout_default', before: 'shadow', after: 'active' });
  const same = structuredClone(MODEL_HISTORY.entries[0]); same.policy = Object.fromEntries(Object.entries(same.policy).reverse());
  assert.deepEqual(policyChanges(same, MODEL_HISTORY.entries[0]), []);
  dom(() => {
    const root = new Node(); renderModelHistory(root, null);
    const controls = walk(root).filter(node => node.tag === 'select'); controls[1].value = 'policy'; controls[1].events.change();
    assert.match(root.textContent, /Policy · side aware scout default Before: shadow After: active/);
    assert.match(root.textContent, /Decision · effective date Before: 2026-05-06 After: 2026-08-12/);
    assert.match(root.textContent, /Before: Not declared in this revision/);
    assert.match(root.textContent, /do not grant or prove runtime authority/);
    assert.doesNotMatch(root.textContent, /\[object Object\]/);
  });
});
