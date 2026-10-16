import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readinessModel, snapshotAge, mountReadiness } from '../../web/atmosphere/readiness.js';
const fixture = name => JSON.parse(readFileSync(new URL(`../../web/data/diagnostics/shared_mart_${name}_latest.json`, import.meta.url)));
const sync = fixture('sync'), shadow = fixture('shadow_evidence');
const now = Date.parse('2026-10-11T22:00:00Z');
const fixtureNow = Date.parse('2026-10-16T22:00:00Z');
test('pinned aggregate values are read dynamically with distinct scopes', () => {
 const result = readinessModel(sync, shadow, 'all', fixtureNow);
 assert.equal(result.matched, true); assert.match(result.age,/Stale/);
 assert.deepEqual(result.metrics.map(x=>x.value), [547,5105,5105,6138668,151730,3640,3527,1578,113,152,0,2]);
 assert.equal(result.metrics[5].scope, 'Stored table rows'); assert.equal(result.metrics[6].scope,'Generic training funnel');
 const changed = structuredClone(shadow); changed.training_funnel.dropped_missing_executable_outcome=100;
 assert.equal(readinessModel(sync,changed).metrics[7].value,100);
});
test('source scopes preserve live/shadow populations and unavailable training denominator', () => {
 const oro = readinessModel(sync,shadow,'orographic'); const cirrus = readinessModel(sync,shadow,'cirrus');
 assert.deepEqual(oro.metrics.map(x=>x.value),[3453,46,20]); assert.deepEqual(cirrus.metrics.map(x=>x.value),[26,52,15,12]);
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
 assert.equal(readinessModel(sync,shadow).gates[0].status,'Not met');
});
test('freshness is clock-based, invalid and future dates are unknown', () => {
 for(const value of [null,undefined,'oops','2099-01-01']) assert.equal(snapshotAge(value,now),'Freshness unknown');
 assert.match(snapshotAge('2026-10-11T20:00:00Z',now),/Recent snapshot · 2h/);
 assert.match(snapshotAge('2026-10-08 22:20:06+00',now),/Stale snapshot/);
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
 const root=new Node('section'); const update=mountReadiness(root,{doc:{createElement:tag=>new Node(tag)},now:()=>fixtureNow});
 update({sync,shadow}); assert.match(root.textContent,/6,138,668/);assert.match(root.textContent,/Cirrus export marked current: Yes/);assert.match(root.textContent,/Current export: No/);
 assert.match(root.textContent,/2026-10-14/);assert.match(root.textContent,/2026-10-13/);
 const select=flatten(root).find(n=>n.tag==='select');select.value='cirrus';select.events.change();
 assert.match(root.textContent,/Cirrus SHADOW/);assert.doesNotMatch(root.textContent,/6,138,668/);
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
