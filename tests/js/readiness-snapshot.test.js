import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { readinessModel } from '../../web/atmosphere/readiness.js';
import { timestamp, validGate } from '../../web/atmosphere/data.js';

// Integration smoke only: current snapshots may refresh their counts and timestamps.
// Exact values, freshness thresholds and malformed inputs belong in readiness.test.js.
test('committed snapshots retain the readiness consumer contract', () => {
 const snapshot = name => JSON.parse(readFileSync(new URL(`../../web/data/diagnostics/shared_mart_${name}_latest.json`, import.meta.url), 'utf8'));
 const sync = snapshot('sync'), shadow = snapshot('shadow_evidence');
 assert.equal(sync.schema_version, 1);
 assert.equal(shadow.schema_version, 4);
 const model = readinessModel(sync, shadow);
 assert.equal(model.matched, true, 'snapshots must identify the same nonempty mart');
 for (const source of ['all', 'orographic', 'cirrus']) {
  for (const metric of readinessModel(sync, shadow, source).metrics) {
   assert.ok(Number.isSafeInteger(metric.value) && metric.value >= 0, `${source}: ${metric.path} must remain a nonnegative count`);
  }
 }
 for (const gate of model.gates) assert.ok(validGate(shadow.shadow_entry_gates[gate.key]), `${gate.key} must agree with its threshold`);
 for (const value of [sync.generated_at_utc, shadow.generated_at_utc, sync.iceberg.latest_publication_at_utc]) {
  assert.notEqual(timestamp(value), 'Not reported');
 }
 assert.equal(typeof sync.cirrus_export_is_current, 'boolean');
 assert.equal(typeof sync.iceberg.current_export, 'boolean');
 assert.equal(typeof shadow.common_replay.label_contract_id, 'string');
 assert.ok(shadow.common_replay.label_contract_id.trim());
});
