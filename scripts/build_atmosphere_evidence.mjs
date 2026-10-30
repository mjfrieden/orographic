// Offline, allowlisted projection of existing research artifacts. No API or trading calls.
import { writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
const digest = bytes => createHash('sha256').update(bytes).digest('hex');
const sources = {};
async function source(key, path) {
  // Read immutable Git blobs, not platform-dependent checkout line endings or
  // another task's uncommitted artifact refresh.
  const bytes = execFileSync('git',['show',`HEAD:${path}`],{maxBuffer:4*1024*1024}); const data = JSON.parse(bytes);
  sources[key] = { path: '/' + path.replace(/^web\//, ''), sha256: digest(bytes), generated_at_utc: data.generated_at_utc ?? data.updated_at_utc ?? null };
  return data;
}
const sync = await source('sync', 'web/data/diagnostics/shared_mart_sync_latest.json');
const shadow = await source('shadow', 'web/data/diagnostics/shared_mart_shadow_evidence_latest.json');
const health = await source('health', 'web/data/diagnostics/research_readiness_health_latest.json');
const runtime = await source('runtime', 'web/data/latest_run.json');
const cards = await source('models', 'web/data/diagnostics/current_model_cards_latest.json');
const ledger = await source('ledger', 'web/data/diagnostics/prospective_dashboard_summary_latest.json');
if (sync.schema_version !== 1 || shadow.schema_version !== 4 || health.schema_version !== 1 || cards.schema_version !== 1 || sync.mart_id !== shadow.mart_id) throw Error('Unsupported or mismatched source snapshot');
const pick = (row, keys) => Object.fromEntries(keys.map(key => [key, row?.[key] ?? null]));
const records = []; const seen = new Set();
for (const entry of [...ledger.entries].sort((a,b) => Date.parse(b.run_generated_at_utc)-Date.parse(a.run_generated_at_utc))) {
  for (const row of entry.picks ?? []) {
    if (row.lane !== 'live') continue;
    const date = row.run_generated_at_utc ?? entry.run_generated_at_utc;
    const id = `${date}|${row.lane}|${row.contract_symbol}`;
    if (seen.has(id)) throw Error('Duplicate decision key in compact ledger');
    seen.add(id);
    records.push({ id, date, ...pick(row,['symbol','contract_symbol','lane']), quote: pick(row.emission_quote,['captured_at_utc','chain_snapshot_at_utc','entry_data_source','entry_quote_type','ask','bid']), status: row.outcomes?.status ?? 'unknown', friday: row.outcomes?.fixed_exit_marks?.friday_close?.pnl_pct_from_emission ?? null });
  }
}
const payload = {
  schema_version: 1, source_commit: execFileSync('git',['rev-parse','HEAD'],{encoding:'utf8'}).trim(), sources,
  sync: pick(sync,['mart_id','generated_at_utc','rows','iceberg','production_changes_allowed']),
  shadow: pick(shadow,['mart_id','generated_at_utc','common_replay','cross_system_comparison','training_funnel','joint_learning','shadow_entry_gates','data_quality']),
  health: pick(health,['generated_at_utc','status','gates']),
  runtime: { generated_at_utc:runtime.generated_at_utc, models:runtime.model_artifacts, abstain:runtime.council?.abstain, reason:runtime.council?.summary?.abstain_audit?.primary_reason_label, candidate_count:runtime.council?.summary?.candidate_count, live_count:runtime.council?.live_board?.length },
  models: cards.cards.filter(row => row.system === 'Orographic').map(row => pick(row,['id','title','version','model_sha256','how_it_works','population','evaluation_window','limitations'])),
  decisions: records.slice(0,24), decision_total:records.length,
  publication_review: { reviewed_at_utc:'2026-10-29T14:09:00Z', run:'40774763527', status:'failed', stage:'Iceberg publication', reason:'Missing delete Parquet object (HTTP 404); persistence and downstream refresh skipped.', source:'https://github.com/mjfrieden/orographic/actions/runs/40774763527', authority:'Dated October 29 handoff review; current service state not reverified.' }
};
payload.snapshot_id = digest(JSON.stringify(payload));
await writeFile('web/atmosphere/evidence-snapshot.json',JSON.stringify(payload,null,2)+'\n');
console.log(`Pinned ${payload.snapshot_id}: ${payload.decisions.length} of ${records.length} recent live-lane records; six source hashes. Source ages retained.`);
