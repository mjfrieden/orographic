import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readinessModel, snapshotAge, mountReadiness } from '../../web/atmosphere/readiness.js';
// Synthetic contract fixtures: never refresh these from mutable production snapshots.
// Distinct counts expose accidental mixing of table, training and replay populations.
const now = Date.parse('2026-10-11T22:00:00Z');
const sync = {
 schema_version: 1, mart_id: 'synthetic-readiness-mart',
 generated_at_utc: '2026-10-11T20:00:00Z', cirrus_export_is_current: true,
 iceberg: { latest_publication_at_utc: '2026-10-08T22:00:00Z', current_export: false },
 rows: { model_runs: 3, recommendations: 17, feature_snapshots: 16,
  option_quotes: 1234, quote_provenance: 19, execution_outcomes: 11 }
};
const shadow = {
 schema_version: 4, mart_id: 'synthetic-readiness-mart', generated_at_utc: '2026-10-09T20:00:00Z',
 training_funnel: { with_valid_label_outcome: 7, dropped_missing_executable_outcome: 10 },
 joint_learning: { legacy_feature_rows: 4, lane_only_model_rows: 5,
  orographic_source_specific_training_rows: 6, cirrus_source_specific_training_rows: 2 },
 cross_system_comparison: { direct_return_comparable_pairs: 0 },
 common_replay: { label_contract_id: 'synthetic-fixed-24h-contract',
  fixed_24h_exploratory_shadow_pairs: 1, orographic_live_recommendations: 9,
  orographic_live_replays: 8, cirrus_shadow_recommendations: 12,
  cirrus_shadow_replays: 3, cirrus_shadow_vertical_replays: 2 },
 shadow_entry_gates: {
  paired_executable_outcomes: { actual: 1, required: 30, passed: false },
  paired_market_dates: { actual: 2, required: 2, passed: true },
  direct_return_comparable_pairs: { actual: 0, required: 30, passed: false },
  direct_return_comparable_market_dates: { actual: 0, required: 30, passed: false },
  live_direct_return_comparable_pairs: { actual: 0, required: 30, passed: false },
  live_direct_return_comparable_market_dates: { actual: 0, required: 30, passed: false }
 },
 data_quality: { integrity_clean: false, cohorts_with_integrity_anomalies: 1 }
};
test('pinned aggregate values are read dynamically with distinct scopes', () => {
 const result = readinessModel(sync, shadow, 'all', now);
 assert.equal(result.matched, true); assert.match(result.age,/Stale/);
 assert.deepEqual(result.metrics.map(x=>x.value), [3,17,16,1234,19,11,7,10,4,5,0,1]);
 assert.equal(result.metrics[5].scope, 'Stored table rows'); assert.equal(result.metrics[6].scope,'Generic training funnel');
 const changed = structuredClone(shadow); changed.training_funnel.dropped_missing_executable_outcome=100;
 assert.equal(readinessModel(sync,changed).metrics[7].value,100);
});
test('source scopes preserve live/shadow populations and unavailable training denominator', () => {
 const oro = readinessModel(sync,shadow,'orographic'); const cirrus = readinessModel(sync,shadow,'cirrus');
 assert.deepEqual(oro.metrics.map(x=>x.value),[6,9,8]); assert.deepEqual(cirrus.metrics.map(x=>x.value),[2,12,3,2]);
 assert.match(oro.metrics[0].note,/denominator is not reported/); assert.match(oro.metrics[1].scope,/LIVE/); assert.match(cirrus.metrics[1].scope,/SHADOW/);
});
test('mismatch, missing and invalid gates fail closed', () => {
 assert.equal(readinessModel({...sync,mart_id:'other'},shadow).shadow,null);
 assert.equal(readinessModel(null,shadow).matched,false);
 assert.ok(readinessModel(null,null).metrics.every(x=>x.value===undefined));
 for (const gate of [{actual:0,required:30,passed:true},{actual:null,required:30,passed:false},{actual:30,required:0,passed:true},{actual:'30',required:30,passed:true}]) {
 const altered=structuredClone(shadow);altered.shadow_entry_gates.paired_executable_outcomes=gate;
 assert.equal(readinessModel(sync,altered).gates[0].status,'Not reported');
 }
 assert.deepEqual(readinessModel(sync,shadow).gates.map(g=>g.status), ['Not met','Met','Not met','Not met','Not met','Not met']);
});
test('freshness is clock-based, invalid and future dates are unknown', () => {
 for(const value of [null,undefined,'oops','2099-01-01']) assert.equal(snapshotAge(value,now),'Freshness unknown');
 assert.match(snapshotAge('2026-10-11T20:00:00Z',now),/Recent snapshot · 2h/);
 assert.match(snapshotAge('2026-10-08 22:20:06+00',now),/Stale snapshot/);
 assert.match(snapshotAge('2026-10-09T22:00:01Z',now),/Recent snapshot · 47h/);
 assert.match(snapshotAge('2026-10-09T22:00:00Z',now),/Stale snapshot · 48h/);
});
class Node {
 constructor(tag){this.tag=tag;this.children=[];this.text='';this.events={};this.value='';this.attributes={};}
 set textContent(v){this.text=String(v);this.children=[];} get textContent(){return this.text+this.children.map(c=>c.textContent).join(' ');}
 append(...children){this.children.push(...children);if(this.tag==='select'&&!this.value)this.value=children[0].value;}
 replaceChildren(...children){this.text='';this.children=children;}
 setAttribute(k,v){this.attributes[k]=v;} addEventListener(k,v){this.events[k]=v;}
}
const flatten = node => [node,...node.children.flatMap(flatten)];
test('native source control, disclosure provenance, freshness separation and refresh states', () => {
 const root=new Node('section'); const update=mountReadiness(root,{doc:{createElement:tag=>new Node(tag)},now:()=>now});
 update({sync,shadow}); assert.match(root.textContent,/1,234/);assert.match(root.textContent,/Cirrus export marked current: Yes/);assert.match(root.textContent,/Current export: No/);
 const cards=flatten(root).filter(n=>n.tag==='article');
 const connection=title=>cards.find(n=>n.children[0]?.textContent===title).textContent;
 assert.match(connection('Current export'),/2026-10-11 20:00:00 UTC · Recent snapshot · 2h/);
 assert.match(connection('Observed Iceberg publication'),/2026-10-08 22:00:00 UTC · Stale snapshot · 72h/);
 assert.match(connection('Evidence contract'),/2026-10-09 20:00:00 UTC · Stale snapshot · 50h/);
 assert.match(connection('Evidence contract'),/synthetic-readiness-mart/);
 assert.match(connection('Evidence contract'),/synthetic-fixed-24h-contract/);
 const select=flatten(root).find(n=>n.tag==='select');select.value='cirrus';select.events.change();
 assert.match(root.textContent,/Cirrus SHADOW/);assert.doesNotMatch(root.textContent,/1,234/);
 assert.ok(flatten(root).some(n=>n.tag==='summary'&&n.textContent==='Scope & provenance'));
 assert.ok(flatten(root).filter(n=>n.tag==='a').every(n=>n.href.startsWith('/data/diagnostics/shared_mart_')));
 update({sync:null,shadow:null});assert.match(root.textContent,/Evidence unavailable/);assert.doesNotMatch(root.textContent,/Current export: No/);
 update({sync,shadow});assert.equal(select.value,'cirrus');assert.match(root.textContent,/Cirrus SHADOW/);
});
test('rendering treats hostile text as text and never issues network requests',()=>{
 const root=new Node('section');const altered=structuredClone(shadow);altered.common_replay.label_contract_id='<img src=x onerror=alert(1)>';
 mountReadiness(root,{doc:{createElement:tag=>new Node(tag)}})({sync,shadow:altered});
 assert.ok(flatten(root).every(n=>n.tag!=='img'));assert.match(root.textContent,/<img src=x/);
 const source=readFileSync(new URL('../../web/atmosphere/readiness.js',import.meta.url),'utf8');assert.doesNotMatch(source,/\bfetch\s*\(|innerHTML|XMLHttpRequest/);
});
test('model cards stay first and readiness shares existing snapshot lifecycle',()=>{
 const html=readFileSync(new URL('../../web/atmosphere/index.html',import.meta.url),'utf8');
 assert.ok(html.indexOf('id="current-model-cards"')<html.indexOf('id="evidence-readiness"'));
 const source=readFileSync(new URL('../../web/atmosphere/atmosphere.js',import.meta.url),'utf8');assert.match(source,/renderReadiness\(state\)/);
});
