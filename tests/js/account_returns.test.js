import test from 'node:test';
import assert from 'node:assert/strict';
import { calculateAccountReturns, parseReturnReceipt, accountReturnsHtml, createReturnReceiptReader, MAX_RETURN_BYTES } from '../../web/account-returns.js';
import { readFileSync } from 'node:fs';
const account = { accountId: 'TEST', mode: 'sandbox' };
const session = { authenticated: true, session: { role: 'admin' } };
const point = (day, before, flow = 0) => ({ at: `2026-01-${String(day).padStart(2, '0')}T00:00:00.000Z`, equity_before_flow: before, external_flow: flow, equity_after_flow: before + flow });
const fixture = () => ({ schema: 'orographic.account-return-receipt.v1', currency: 'USD', account_id: 'TEST', environment: 'sandbox', source: 'Synthetic test only', coverage: { all_external_flows_included: true, valued_at_every_external_flow: true }, valuations: [point(1, 1000), point(2, 1100, 500), point(3, 1760)] });
const calc = (r) => calculateAccountReturns(r, account, Date.parse('2026-02-01T00:00:00.000Z'));
const near = (a, b) => assert.ok(Math.abs(a - b) < 1e-9, `${a} vs ${b}`);
test('compounds subperiod growth and separates funding from dollar change', () => {
 const result = calc(fixture()); near(result.return, .21); near(result.pnl, 260); near(result.netFlows, 500); near(result.rows.at(-1).index, 121);
});
test('deposit and withdrawal alone produce zero return and dollar gain', () => {
 const r = fixture(); r.valuations = [point(1, 1000), point(2, 1000, 500), point(3, 1500, -750)];
 const v = calc(r); near(v.return, 0); near(v.pnl, 0); near(v.netFlows, -250);
});
test('negative investment performance with withdrawal remains negative', () => {
 const r=fixture(); r.valuations=[point(1,1000), point(2,900,-400),point(3,450)]; const v=calc(r);near(v.return,-.19);near(v.pnl,-150);
});
test('does not coerce strings, nulls, booleans or nonfinite amounts', () => {
 for (const value of ['100', null, false, NaN, Infinity, {}, 1e13]) {
  for (const field of ['equity_before_flow','equity_after_flow','external_flow']) { const r=fixture();r.valuations[1][field]=value;assert.throws(()=>calc(r)); }
 }
});
test('rejects zero/negative equity, unreconciled flows, opening flow and missing coverage', () => {
 for (const mutate of [r=>r.valuations[1]=point(2,0,100),r=>r.valuations[1]=point(2,-1,100),r=>r.valuations[1]=point(2,100,-100),r=>r.valuations[1].external_flow=20,r=>r.valuations[0]=point(1,1000,100),r=>r.coverage.all_external_flows_included=false,r=>delete r.coverage,r=>r.coverage.valued_at_every_external_flow=false]) {const r=fixture();mutate(r);assert.throws(()=>calc(r));}
});
test('rejects reversed, duplicate, ambiguous, invalid and future times', () => {
 for (const value of ['2026-01-01T00:00:00.000Z','2025-01-01T00:00:00.000Z','2026-01-02','2026-02-30T00:00:00.000Z','2027-01-02T00:00:00.000Z',null]) {const r=fixture();r.valuations[1].at=value;assert.throws(()=>calc(r));}
});
test('requires matching known account, environment, schema, currency and source', () => {
 for (const a of [{}, {accountId:'OTHER',mode:'sandbox'}, {accountId:'TEST',mode:'live'}, {accountId:'TEST',mode:'unknown'}]) assert.throws(()=>calculateAccountReturns(fixture(),a));
 for (const [field,value] of [['schema','unknown'],['currency','EUR'],['source',''],['source','x'.repeat(201)]]) {const r=fixture();r[field]=value;assert.throws(()=>calc(r));}
});
test('bounded parser rejects oversized, malformed and invalid observation counts', () => {
 assert.throws(()=>parseReturnReceipt(' '.repeat(MAX_RETURN_BYTES+1),account));assert.throws(()=>parseReturnReceipt('{',account));
 for (const values of [[],[point(1,1000)],Array(1001).fill(point(1,1000)),null]) {const r=fixture();r.valuations=values;assert.throws(()=>calc(r));}
});
test('missing data never renders a chart or zero result', () => {
 const html=accountReturnsHtml({},session,account);assert.match(html,/Period return: unavailable/);assert.doesNotMatch(html,/<svg|0\.00%/);
});
test('local results label their source, window and separate curves; escape source', () => {
 const r=fixture();r.source='<img src=x onerror=bad>';const html=accountReturnsHtml({result:calc(r)},session,account);
 assert.match(html,/not broker-verified/);assert.match(html,/21\.00%/);assert.match(html,/includes funding/);assert.match(html,/growth index/);assert.match(html,/&lt;img/);assert.doesNotMatch(html,/<img|TEST/);assert.match(html,/<table/);
});
test('unauthenticated and non-admin views conceal imported results', () => {
 for(const s of [{}, {authenticated:false,session:{role:'admin'}},{authenticated:true,session:{role:'viewer'}}]) {const html=accountReturnsHtml({result:calc(fixture())},s,account);assert.doesNotMatch(html,/21\.00%|type="file"|<svg/);}
});
test('clear cancels pending reads and stale replacement cannot overwrite newer receipt',async()=>{
 let state;const reader=createReturnReceiptReader(s=>state=s);let release;const pending=reader.load({size:10,text:()=>new Promise(r=>release=r)},account);reader.clear();release(JSON.stringify(fixture()));await pending;assert.equal(state.result,null);
 let old;const stale=reader.load({size:10,text:()=>new Promise(r=>old=r)},account);await reader.load({size:10,text:async()=>JSON.stringify(fixture())},account);const accepted=state.result;old('invalid');await stale;assert.equal(state.result,accepted);
});
test('failed replacement clears old results and rejects oversized file before reading',async()=>{
 let state;const reader=createReturnReceiptReader(s=>state=s);await reader.load({size:10,text:async()=>JSON.stringify(fixture())},account);assert.ok(state.result);
 await reader.load({size:MAX_RETURN_BYTES+1,text:()=>{throw Error('must not read')}},account);assert.equal(state.result,null);assert.match(state.error,/1 MiB/);
});
test('workbench connects import and clears receipt on identity changes and shutdown',()=>{
 const source=readFileSync(new URL('../../web/evidence-workbench.js',import.meta.url),'utf8');assert.match(source,/accountReturnsHtml\(returns, data.session, data.broker\)/);assert.match(source,/if \(changedIdentity\) \{\s*receiptReader.clear\(\)/);assert.match(source,/stopped = true; receiptReader.clear\(\)/);assert.match(source,/session\?\.authenticated !== true/);
});
test('pure return module contains no network, persistence, broker or execution paths',()=>{
 const source=readFileSync(new URL('../../web/account-returns.js',import.meta.url),'utf8');assert.doesNotMatch(source,/\bfetch\s*\(|localStorage|sessionStorage|\/api\/|XMLHttpRequest|sendBeacon/);
});

test('tiny-scale cancellation cannot fabricate total loss; sub-cent values reject', () => {
 const r=fixture();r.valuations=[point(1,1e12),point(2,1e-6),point(3,1e12)];assert.throws(()=>calc(r),/one cent/);
 r.valuations=[point(1,1e12),point(2,1),point(3,1e12)];near(calc(r).return,0);
});
test('rejects accumulated growth outside safe numeric rendering bounds', () => {
 const r=fixture();r.valuations=[point(1,.01),point(2,1e12,-(1e12-.01)),point(3,1e12)];assert.throws(()=>calc(r),/numeric bounds/);
});

test('pending imports expose explicit cancellation without showing an old result', () => {
 const html=accountReturnsHtml({loading:true},session,account);assert.match(html,/id="ew-returns-clear"/);assert.match(html,/Cancel local receipt/);assert.doesNotMatch(html,/<svg/);
});
