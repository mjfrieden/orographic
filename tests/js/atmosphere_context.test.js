import test from 'node:test';
import assert from 'node:assert/strict';
import { readFile } from 'node:fs/promises';
import { compareScans } from '../../web/evidence-workbench.js';
import { loadContextArtifact, compactContext, CONTEXT_SOURCES } from '../../web/atmosphere/context-loader.js';
const response = (value, headers = {}) => new Response(JSON.stringify(value), { headers: { 'content-type': 'application/json', ...headers } });
test('context loader permits only fixed read-only sources and does not read broker endpoints', async () => {
 assert.ok(Object.values(CONTEXT_SOURCES).every(({path})=>!path.includes('tradier')));
 await assert.rejects(loadContextArtifact('orders'), /Unsupported/);
});
test('context rejects oversized declared and streamed bodies', async () => {
 await assert.rejects(loadContextArtifact('governance', {fetch:async()=>response({}, {'content-length':70000})}), /safety limit/);
 await assert.rejects(loadContextArtifact('governance', {fetch:async()=>response({text:'x'.repeat(70000)})}), /safety limit/);
});
test('context rejects HTML and HTTP failures without inventing data', async () => {
 await assert.rejects(loadContextArtifact('governance', {fetch:async()=>new Response('login',{headers:{'content-type':'text/html'}})}), /not JSON/);
 await assert.rejects(loadContextArtifact('governance', {fetch:async()=>new Response('{}',{status:503})}), /503/);
});
test('compact snapshot preserves unknown boards and removes bulky detail arrays', () => {
 const value=compactContext('snapshot',{diagnostics:{scout:{symbols_requested:100,side_aware_scores:[1,2,3]}},council:{summary:{abstain_audit:{primary_reason_label:'Hold',best_rejected_candidates:{a:[]}}}}});
 assert.equal(value.council.live_board,undefined);assert.equal(value.diagnostics.scout.symbols_requested,100);assert.equal(value.diagnostics.scout.side_aware_scores,undefined);assert.equal(value.council.summary.abstain_audit.best_rejected_candidates,undefined);
});
test('context changes retains bounded scans and production recommendations only', () => {
 const value=compactContext('ledger',{entries:Array.from({length:50},(_,i)=>({run_generated_at_utc:new Date(Date.UTC(2026,0,i+1)).toISOString(),picks:[{lane:'live',contract_symbol:'X'},{lane:'shadow',contract_symbol:'Y'}]}))});
 assert.equal(value.entries.length,24);assert.equal(value.entries[0].picks.length,1);
});
test('home navigation and workbench keep research away from trading', async () => {
 const html=await readFile(new URL('../../web/index.html',import.meta.url),'utf8');
 assert.match(html,/href="\/atmosphere\/"/);assert.match(html,/data-ew-mode="trading"/);assert.doesNotMatch(html,/id="ew-panel-model"|id="ew-panel-changes"/);
});
test('home boot does not download optional governance or archived backtest artifacts', async () => {
 const app=await readFile(new URL('../../web/app.js',import.meta.url),'utf8');const boot=app.slice(app.indexOf('async function main()'));
 assert.doesNotMatch(boot,/loadResearchWorkbench\(|loadBacktest\(/);
});
test('caps admit current bounded artifacts verified from d6cc986 tree', () => {
 const observed={snapshot:595594,governance:1834,ledger:1328295,backtest:79484};
 for(const [key,bytes] of Object.entries(observed))assert.ok(bytes<CONTEXT_SOURCES[key].limit,`${key} exceeds its cap`);
});
test('archived context retains dated window, quote sources and zero slippage assumption', () => {
 const result=compactContext('backtest',{ok:true,kind:'walk_forward',backtest:{generated_at:'2026-04-21',backtest_start:'2025-10-17',backtest_end:'2026-04-15',total_trades:34,options_data_coverage:{entry_source_counts:{real_chain:34}},execution_quality:{avg_entry_slippage_pct:0,avg_exit_slippage_pct:0}}});
 assert.equal(result.generated_at_utc,'2026-04-21');assert.equal(result.backtest_start,'2025-10-17');assert.equal(result.execution_quality.avg_entry_slippage_pct,0);assert.equal(result.options_data_coverage.entry_source_counts.real_chain,34);
});
test('bounded changes keeps newest scans regardless of source ordering', () => {
 const entries=Array.from({length:30},(_,i)=>({run_generated_at_utc:new Date(Date.UTC(2026,0,i+1)).toISOString(),picks:[]})).reverse();
 const value=compactContext('ledger',{entries});const diff=compareScans(value);
 assert.equal(diff.current,'2026-01-30T00:00:00.000Z');assert.equal(diff.previous,'2026-01-29T00:00:00.000Z');
});
test('truncated scan cannot fabricate changes', () => {
 const value=compactContext('ledger',{entries:[{run_generated_at_utc:'2026-01-29',picks:[]},{run_generated_at_utc:'2026-01-30',picks:Array.from({length:21},(_,i)=>({lane:'live',contract_symbol:String(i)}))}]});
 assert.equal(value.entries[1].picks.length,20);assert.equal(compareScans(value).available,false);assert.equal(compareScans(value).reason,'truncated');
});
test('duplicate timestamp fragments are merged before latest-24 retention', () => {
 const entries=[{run_generated_at_utc:'2026-01-29',picks:[{lane:'live',contract_symbol:'KEPT'}]},...Array.from({length:24},()=>({run_generated_at_utc:'2026-01-29',picks:[{lane:'live',contract_symbol:'OLD'}]})),{run_generated_at_utc:'2026-01-30',picks:[{lane:'live',contract_symbol:'KEPT'}]}];
 const compact=compactContext('ledger',{entries});const comparison=compareScans(compact);
 assert.equal(compact.entries.length,2);assert.equal(comparison.available,true);assert.equal(comparison.rows.find(row=>row.contract==='KEPT').status,'retained');
});
test('more than twenty unique picks across fragments suppresses comparison', () => {
 const entries=[...Array.from({length:21},(_,i)=>({run_generated_at_utc:'2026-01-29',picks:[{lane:'live',contract_symbol:String(i)}]})),{run_generated_at_utc:'2026-01-30',picks:[]}];
 assert.equal(compareScans(compactContext('ledger',{entries})).reason,'truncated');
});
