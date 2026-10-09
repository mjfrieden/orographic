import test from 'node:test';
import assert from 'node:assert/strict';
import { readBoundedJson, fetchSnapshot, LIMITS, compatible, count, timestamp } from '../../web/atmosphere/data.js';
const json = (data, options = {}) => new Response(JSON.stringify(data), { headers: { 'content-type': 'application/json' }, ...options });
test('bounded reader reads aggregate JSON', async () => assert.deepEqual(await readBoundedJson(json({ rows: 1 })), { rows: 1 }));
test('streaming cap applies without content-length', async () => { await assert.rejects(readBoundedJson(json({ value: 'x'.repeat(LIMITS.bytes) })), /size limit/); });
test('declared oversize is rejected before parsing', async () => { await assert.rejects(readBoundedJson(new Response('{}', { headers: { 'content-type': 'application/json', 'content-length': String(LIMITS.bytes + 1) } })), /size limit/); });
test('expired authentication and HTML never parse as empty data', async () => { await assert.rejects(readBoundedJson(json({}, { status: 401 })), e => e.code === 'auth'); await assert.rejects(readBoundedJson(new Response('<html>Login</html>')), /Unexpected/); });
test('malformed JSON, missing and arrays fail explicitly', async () => { for (const raw of ['{', 'null', '[]']) await assert.rejects(readBoundedJson(new Response(raw, { headers: { 'content-type': 'application/json' } }))); });
test('only allowlisted relative requests with auth and no caching', async () => { let observed; const data = await fetchSnapshot('sync', { fetcher: async (...args) => { observed = args; return json({ schema_version: 1 }); } }); assert.equal(data.schema_version, 1); assert.equal(observed[0], '/data/diagnostics/shared_mart_sync_latest.json'); assert.equal(observed[1].credentials, 'same-origin'); assert.equal(observed[1].cache, 'no-store'); assert.equal(observed[1].redirect, 'error'); await assert.rejects(fetchSnapshot('raw', { fetcher: () => assert.fail('must not fetch') }), /Unknown/); });
test('unknown schema rejected rather than silently reinterpreted', async () => { await assert.rejects(fetchSnapshot('shadow', { fetcher: async () => json({ schema_version: 5 }) }), /schema changed/); });
test('absent values never become zero', () => { for (const value of [null, undefined, NaN, -1, '0', 1.5, Number.MAX_SAFE_INTEGER + 1]) assert.equal(count(value), 'Not reported'); assert.equal(count(0), '0'); assert.equal(timestamp(null), 'Not reported'); });
test('only nonmissing matching mart ids can combine', () => { assert.equal(compatible({}, {}), false); assert.equal(compatible({ mart_id: 'a' }, { mart_id: 'b' }), false); assert.equal(compatible({ mart_id: 'a' }, { mart_id: 'a' }), true); });

test('non-string and empty mart IDs are not comparable', () => { for (const id of [true, 123, {}, [], '', '   ']) assert.equal(compatible({ mart_id: id }, { mart_id: id }), false); });
