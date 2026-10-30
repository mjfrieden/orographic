import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {createHash} from 'node:crypto';
import {execFileSync, spawnSync} from 'node:child_process';
import {age,validateSnapshot,buildEvidence,filterEvidence} from '../../web/atmosphere/evidence-data.js';
import {isPublicPath} from '../../functions/_lib/public_paths.js';
const data=JSON.parse(await readFile(new URL('../../web/atmosphere/evidence-snapshot.json',import.meta.url),'utf8'));
const copy=()=>structuredClone(data);
test('pinned payload identity and protected delivery path',()=>{
  const {snapshot_id,...payload}=data;
  assert.equal(createHash('sha256').update(JSON.stringify(payload)).digest('hex'),snapshot_id);
  assert.equal(isPublicPath('/atmosphere/evidence-snapshot.json'),false);
});
test('all six source artifacts match exact pinned Git bytes',t=>{
  // Actions checks out one commit. If historical objects are absent, unchanged
  // HEAD blobs can still prove exact bytes. Newer different blobs cannot prove
  // an old snapshot; explicitly skip that historical audit, never bless them.
  const hasSource=spawnSync('git',['cat-file','-e',`${data.source_commit}^{commit}`]).status===0;
  const ref=hasSource?data.source_commit:'HEAD';
  const matches=[];
  for(const source of Object.values(data.sources)){
    const bytes=execFileSync('git',['show',`${ref}:web${source.path}`],{maxBuffer:4*1024*1024});
    matches.push(createHash('sha256').update(bytes).digest('hex')===source.sha256);
  }
  if(!hasSource && matches.some(match=>!match)) return t.skip('Pinned historical Git objects absent; current artifacts differ. Fetch source_commit for exact historical audit.');
  assert.ok(matches.every(Boolean));
});
test('clock policy handles timezone boundaries, invalid, missing and future clocks',()=>{
  const now=Date.parse('2026-10-30T12:00:00Z');
  assert.equal(age('2026-10-28T08:00:00-04:00',now).state,'available');
  assert.equal(age('2026-10-28T07:59:00-04:00',now).state,'stale');
  assert.equal(age('2026-10-28T07:00:00-04:00',now).state,'stale');
  for(const value of [null,'bad','2026-10-30','2026-10-31T00:00:00Z'])assert.equal(age(value,now).state,'unknown');
});
test('schema, mismatched mart, duplicate rows and malformed outcomes fail closed',()=>{
  assert.equal(validateSnapshot(data),data);
  for(const mutate of [d=>d.schema_version=2,d=>d.shadow.mart_id='other',d=>d.decisions.push(d.decisions[0]),d=>d.decisions[0].friday='0',d=>d.sources.runtime.sha256=null,d=>d.health.gates=null]){
    const d=copy();mutate(d);assert.throws(()=>validateSnapshot(d));
  }
});
test('displayed gate, family denominator, source age and zero outcome retain source semantics',()=>{
  const graph=buildEvidence(data,Date.parse('2026-10-30T12:00:00Z'));
  assert.equal(graph.nodes.find(n=>n.id==='gate').value,'0 / 30 dates');
  assert.equal(graph.nodes.find(n=>n.id==='quotes').value,'6,138,686 rows');
  assert.equal(graph.matrix[0].cells[3].value,'35.13%');
  assert.equal(graph.matrix[5].cells[3].value,'57.68%');
  assert.equal(graph.matrix[5].cells[3].facts.find(([k])=>k==='Denominator')[1],'5,742');
  assert.equal(graph.matrix[1].cells[4].state,'not-evaluated');
  assert.equal(data.decisions.find(r=>r.contract_symbol==='BAC261030C00051000').friday,0);
  assert.ok(data.decisions.some(r=>r.friday===null));
  const d=copy();d.shadow.shadow_entry_gates.direct_return_comparable_market_dates.passed=true;
  assert.equal(buildEvidence(d).nodes.find(n=>n.id==='gate').state,'unknown');
});
test('cohort, model and UTC artifact filters do not invent historical model joins',()=>{
  const {nodes}=buildEvidence(data);
  assert.equal(filterEvidence(nodes,{scope:'shared'}).length,7);
  assert.ok(filterEvidence(nodes,{scope:'runtime'}).every(n=>n.scope==='runtime'));
  assert.equal(filterEvidence(nodes,{model:'scout-side'}).filter(n=>n.model).length,1);
  assert.equal(filterEvidence(nodes,{from:'2030-01-01'}).length,0);
  assert.equal(filterEvidence(nodes,{from:'2026-10-29',to:'2026-10-29'}).length,8);
});
test('empty collections and hostile strings preserve text and unknown feature evidence',()=>{
  const d=copy();d.models=[];d.decisions=[];d.runtime.reason='<img src=x onerror=alert(1)>';
  validateSnapshot(d);assert.ok(buildEvidence(d).nodes.find(n=>n.id==='council').facts.some(([,v])=>v===d.runtime.reason));
  assert.equal(buildEvidence(d).matrix.length,6);
});
test('bounded representative records and missing aggregate counts stay explicit',()=>{
  const d=copy();d.decisions=Array.from({length:24},(_,i)=>({...d.decisions[0],id:`test-${i}`}));
  validateSnapshot(d);d.decisions.push({...d.decisions[0],id:'overflow'});assert.throws(()=>validateSnapshot(d));
  const missing=copy();missing.sync.rows={};missing.shadow.training_funnel={};missing.health.gates=[];missing.health.status=null;
  const graph=buildEvidence(missing);
  for(const id of ['features','recommendations','outcomes','research-gate'])assert.equal(graph.nodes.find(n=>n.id===id).state,'unknown');
});
test('rendering uses text nodes, bounded local request, native controls and reduced-motion override',async()=>{
  const js=await readFile(new URL('../../web/atmosphere/evidence-map.js',import.meta.url),'utf8');
  const css=await readFile(new URL('../../web/atmosphere/evidence-map.css',import.meta.url),'utf8');
  assert.doesNotMatch(js,/innerHTML|insertAdjacentHTML|setInterval|\/api\/tradier/);
  assert.match(js,/textContent=text/);assert.match(js,/tr.append\(td\)/);
  assert.match(js,/facts.tabIndex=0/);assert.match(js,/aria-pressed/);
  assert.match(css,/@media\(prefers-reduced-motion:reduce\)/);assert.match(css,/animation:none!important;transition:none!important/);
});
