import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { reviewedRelease, renderCards, validateCards } from '../../web/atmosphere/model-cards.js';
const fixture = JSON.parse(readFileSync(new URL('../fixtures/cirrus_reviewed_release.synthetic.json', import.meta.url)));
const card = () => ({ id: 'cirrus-hybrid', system: 'Cirrus', title: 'Synthetic registry model', identity_status: 'curated_snapshot', evidence_kind: 'registry_role', role: 'Synthetic role', version: null, trained_at: null, runtime_mode: 'not verified', metrics: [], limitations: ['Synthetic limitation'], evaluation_summary: ['Synthetic evaluation context'], evaluation_completed_at: null, evaluation_window: 'Synthetic historical window', population: 'Synthetic population', how_it_works: 'Synthetic design', verified_at_utc: '2020-02-07T00:00:00Z', source_sha256: 'b'.repeat(64), source_revision: 'c'.repeat(40), source_path: 'web/data/model-evidence/cirrus_public_summary.json', source_description: 'Synthetic reviewed source', refresh_policy: 'Manual review only', reviewed_release_evidence: structuredClone(fixture) });
class Node {
  constructor(tag = 'div') { this.tag = tag; this.children = []; this.text = ''; this.attributes = {}; }
  set textContent(value) { this.text = String(value); this.children = []; }
  get textContent() { return this.text + this.children.map(child => child.textContent).join(' '); }
  append(...children) { this.children.push(...children); }
  replaceChildren(...children) { this.text = ''; this.children = children; }
  setAttribute(key, value) { this.attributes[key] = value; }
  addEventListener() {}
}
const walk = node => [node, ...node.children.flatMap(walk)];
function render(value) {
  const previous = globalThis.document; globalThis.document = { createElement: tag => new Node(tag) };
  const root = new Node();
  try { renderCards(root, validateCards({ schema_version: 1, runtime_at_utc: '2020-02-08T00:00:00Z', cards: [value] })); return root; }
  finally { globalThis.document = previous; }
}
test('release metadata keeps fitted and candidate counts and all dates separate', () => {
  assert.deepEqual(reviewedRelease(card()), fixture);
  const root = render(card());
  for (const text of ['Fitted input count: 9', 'Candidate-pool count (separate from fitted inputs): 22', 'Source-data end: 2020-02-03', 'Training-label cutoff: 2020-01-31', 'Model version: Not reported', 'Model trained: Not reported', 'Artifact built (not training time)', 'Historical artifact observation', 'Release evidence reviewed', 'Current serving, live AUC/F1, profitability and promotion remain unverified']) assert.ok(root.textContent.includes(text), text);
  const summary = walk(root).find(node => node.tag === 'summary' && node.text === 'Reviewed Cirrus release evidence');
  assert.equal(summary.attributes['aria-label'], 'Reviewed Cirrus release evidence for Synthetic registry model');
  assert.ok(walk(root).some(node => node.tag === 'section' && node.attributes['aria-label'] === 'Model version history'));
});
test('malformed optional evidence fails closed while original cards still render', () => {
  const mutations = [v => { v.fitted_feature_count = 22; }, v => { v.fitted_feature_count = true; }, v => { v.candidate_pool_count = 8; }, v => { v.fitted_feature_names = ['bad']; }, v => { v.model_sha256 = 'bad'; }, v => { v.source_data_end = '2020-02-30'; }, v => { v.built_at_utc = '2020-02-30T00:00:00Z'; }, v => { v.reviewed_at_utc = '2099-01-01T00:00:00Z'; }, v => { v.release_id = '<script>'; }, v => { v.training_label_cutoff = '2020-02-04'; }, v => { v.historical_observation_at_utc = '2020-02-07T00:00:00Z'; }];
  for (const mutate of mutations) { const value = card(); mutate(value.reviewed_release_evidence); assert.equal(reviewedRelease(value), null); const root = render(value); assert.match(root.textContent, /Synthetic registry model/); assert.match(root.textContent, /release evidence unavailable/); assert.doesNotMatch(root.textContent, /Fitted input count:/); }
  for (const value of [null, [], true, 'invalid', {}]) { const c = card(); c.reviewed_release_evidence = value; assert.equal(reviewedRelease(c), null); assert.match(render(c).textContent, /Synthetic registry model/); }
});
test('only three registry models accept evidence and review does not refresh release', () => {
  for (const id of ['cirrus-hybrid', 'cirrus-boosting', 'cirrus-tail-ensemble']) { const c = card(); c.id = id; assert.deepEqual(reviewedRelease(c), fixture); }
  for (const id of ['cirrus-calibration-study', 'cirrus-three-session-study', 'scout-direction', 'unknown']) { const c = card(); c.id = id; assert.equal(reviewedRelease(c), null); }
  const c = card(); c.evidence_kind = 'discovery_study'; c.evaluation_completed_at = '2020-01-01T00:00:00Z'; assert.equal(reviewedRelease(c), null); assert.doesNotMatch(render(c).textContent, /Fitted input count|release evidence unavailable/);
  const review = card(); review.verified_at_utc = '2020-03-01T00:00:00Z'; assert.deepEqual(reviewedRelease(review), fixture);
  review.reviewed_release_evidence.reviewed_at_utc = '2020-03-02T00:00:00Z'; assert.equal(reviewedRelease(review).built_at_utc, fixture.built_at_utc);
  delete review.reviewed_release_evidence.candidate_pool_count; assert.ok(reviewedRelease(review)); assert.match(render(review).textContent, /Candidate-pool count \(separate from fitted inputs\): Not reported/);
});
