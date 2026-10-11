import { compatible, count, timestamp, SOURCES, validGate } from './data.js';

const pick = (object, path) => path.split('.').reduce((value, key) => value?.[key], object);
const sourceNames = { all: 'Shared evidence', orographic: 'Orographic', cirrus: 'Cirrus' };
export function snapshotAge(value, now = Date.now()) {
  const normalized = timestamp(value);
  const time = normalized === 'Not reported' ? NaN : Date.parse(normalized.replace(' UTC', 'Z').replace(' ', 'T'));
  if (!Number.isFinite(time) || time > now) return 'Freshness unknown';
  const hours = Math.floor((now - time) / 3600000);
  return `${hours >= 48 ? 'Stale snapshot' : 'Recent snapshot'} · ${hours}h old (48h display threshold)`;
}
export function readinessModel(sync, shadow, source = 'all', now = Date.now()) {
  if (!Object.hasOwn(sourceNames, source)) source = 'all';
  const matched = compatible(sync, shadow);
  // Evidence can stand alone, but a conflicting sync must never lend it identity.
  if (sync && shadow && !matched) shadow = null;
  const metrics = [];
  const add = (label, data, path, scope, note, kind = 'shadow') => metrics.push({ label, value: pick(data, path), path, scope, note, kind });
  if (source === 'all') {
    add('Model runs', sync, 'rows.model_runs', 'Stored table rows', 'Runs are not independent trades.', 'sync');
    add('Recommendations', sync, 'rows.recommendations', 'Stored table rows', 'Shared mart total; per-source totals are not supplied here.', 'sync');
    add('Feature snapshots', sync, 'rows.feature_snapshots', 'Stored table rows', 'Row count alone does not verify model-native feature provenance.', 'sync');
    add('Quote observations', sync, 'rows.option_quotes', 'Stored table rows', 'Multiple observations per contract; these are not distinct opportunities.', 'sync');
    add('Quote provenance rows', sync, 'rows.quote_provenance', 'Stored table rows', 'A separate table with a different scope; no coverage percentage is available.', 'sync');
    add('Outcome rows', sync, 'rows.execution_outcomes', 'Stored table rows', 'Can include multiple outcomes per recommendation. Do not subtract from recommendation counts.', 'sync');
    add('Valid-label recommendations', shadow, 'training_funnel.with_valid_label_outcome', 'Generic training funnel', 'This is a recommendation count, not the outcome-table row count.');
    add('Missing executable outcome', shadow, 'training_funnel.dropped_missing_executable_outcome', 'Generic training funnel', 'Reported directly by the funnel; not inferred from other populations.');
    add('Legacy feature rows', shadow, 'joint_learning.legacy_feature_rows', 'Joint learning candidates', 'Preserved features need model-native identity checks before reuse.');
    add('Lane-only model identities', shadow, 'joint_learning.lane_only_model_rows', 'Joint learning candidates', 'A lane label is not an exact model artifact identity.');
    add('Direct comparable return pairs', shadow, 'cross_system_comparison.direct_return_comparable_pairs', 'Cross-system comparison', 'Same-window, contract and return-policy comparability must be established.');
    add('Exploratory 24h pairs', shadow, 'common_replay.fixed_24h_exploratory_shadow_pairs', 'Orographic LIVE × Cirrus SHADOW', 'Exploratory equal-premium replay does not establish production alpha.');
  } else {
    const oro = source === 'orographic';
    add('Source-specific training rows', shadow, `joint_learning.${source}_source_specific_training_rows`, `${sourceNames[source]} source-specific training`, 'The eligible per-source denominator is not reported. No conversion rate is inferred.');
    add('Replay recommendations', shadow, `common_replay.${oro ? 'orographic_live' : 'cirrus_shadow'}_recommendations`, `${sourceNames[source]} ${oro ? 'LIVE' : 'SHADOW'} common replay`, 'This replay population is not the source-specific training population.');
    add('Available 24h replays', shadow, `common_replay.${oro ? 'orographic_live' : 'cirrus_shadow'}_replays`, `${sourceNames[source]} ${oro ? 'LIVE' : 'SHADOW'} common replay`, 'Decision ask to nearest 24h bid within 3h. No broker-realized results implied.');
    if (!oro) add('Vertical-spread replays', shadow, 'common_replay.cirrus_shadow_vertical_replays', 'Cirrus SHADOW common replay', 'Multi-leg replay requires its own contract and risk normalization.');
  }
  const gates = ['paired_executable_outcomes', 'paired_market_dates', 'direct_return_comparable_pairs', 'direct_return_comparable_market_dates', 'live_direct_return_comparable_pairs', 'live_direct_return_comparable_market_dates'].map(key => {
    const gate = shadow?.shadow_entry_gates?.[key];
    const available = validGate(gate);
    return { key, actual: gate?.actual, required: gate?.required, status: available ? (gate.passed ? 'Met' : 'Not met') : 'Not reported' };
  });
  return { source, sync, shadow, matched, metrics, gates, age: snapshotAge(shadow?.generated_at_utc ?? sync?.generated_at_utc, now) };
}
const node = (doc, tag, text, className) => { const element = doc.createElement(tag); if (text !== undefined) element.textContent = text; if (className) element.className = className; return element; };
const human = value => value.replaceAll('_', ' ');
export function mountReadiness(root, { doc = document, now = () => Date.now() } = {}) {
  if (!root) return () => {};
  const make = (tag, text, className) => node(doc, tag, text, className);
  root.replaceChildren();
  const heading = make('div', undefined, 'section-heading');
  const title = make('div'); title.append(make('p', 'Connected research', 'eyebrow'), make('h2', 'What is ready to test?'));
  heading.append(title, make('span', 'Saved evidence only · no trading authority', 'badge'));
  const label = make('label', 'Evidence scope', 'readiness-scope'); const select = make('select');
  for (const [value, text] of Object.entries(sourceNames)) { const option = make('option', text); option.value = value; select.append(option); }
  label.append(select); const body = make('div');
  root.append(heading, make('p', 'Follow the evidence from connected snapshots to usable research. Counts below have different units and populations; they are not a subtraction funnel.', 'muted'), label, body);
  let current = { sync: null, shadow: null };
  function render() {
    const model = readinessModel(current.sync, current.shadow, select.value, now());
    const { sync, shadow } = model;
    body.replaceChildren();
    const status = make('p', !sync && !shadow ? 'Evidence unavailable. Refresh snapshots to retry.' : `${model.age}. ${model.matched ? 'Matching mart IDs connect the two snapshots.' : 'Partial evidence; a matching pair of snapshots is not available.'}`, 'readiness-status');
    status.setAttribute('role', 'status'); body.append(status);
    if (!sync && !shadow) return;
    const connections = make('div', undefined, 'readiness-connections');
    const sourceCard = (title, lines, kind) => { const card = make('article', undefined, 'readiness-connection'); card.append(make('h3', title)); for (const line of lines) card.append(make('p', line)); if ((kind === 'sync' ? sync : shadow)) { const link = make('a', 'Inspect aggregate source'); link.href = SOURCES[kind]; card.append(link); } connections.append(card); };
    sourceCard('Current export', [sync ? `Generated ${timestamp(sync.generated_at_utc)} · ${snapshotAge(sync.generated_at_utc, now())}` : 'Sync snapshot unavailable.', `Cirrus export marked current: ${sync?.cirrus_export_is_current === true ? 'Yes' : sync?.cirrus_export_is_current === false ? 'No' : 'Not reported'}.`, 'This describes the saved export, not a live connection.'], 'sync');
    sourceCard('Observed Iceberg publication', [`Published ${timestamp(sync?.iceberg?.latest_publication_at_utc)} · ${snapshotAge(sync?.iceberg?.latest_publication_at_utc, now())}`, `Current export: ${sync?.iceberg?.current_export === false ? 'No' : sync?.iceberg?.current_export === true ? 'Yes' : 'Not reported'}.`, 'A separate, pre-publication probe. It does not prove the current export was published or that storage is current.'], 'sync');
    sourceCard('Evidence contract', [`Generated ${timestamp(shadow?.generated_at_utc)} · ${snapshotAge(shadow?.generated_at_utc, now())}`, `Mart identity: ${typeof shadow?.mart_id === 'string' ? shadow.mart_id.slice(0, 80) : 'Not reported'}`, `Common replay contract: ${typeof shadow?.common_replay?.label_contract_id === 'string' ? shadow.common_replay.label_contract_id.slice(0, 120) : 'Not reported'}`], 'shadow');
    body.append(connections);
    const grid = make('div', undefined, 'readiness-grid');
    for (const metric of model.metrics) {
      const card = make('article', undefined, 'readiness-metric');
      card.append(make('h3', metric.label), make('strong', count(metric.value)), make('p', metric.scope, 'muted'));
      const details = make('details'); const summary = make('summary', 'Scope & provenance'); summary.setAttribute('aria-label', `${metric.label}: scope and provenance`); details.append(summary, make('p', metric.note), make('p', `Field: ${metric.path}`, 'readiness-field'));
      if ((metric.kind === 'sync' ? sync : shadow)) { const link = make('a', 'Open bounded snapshot'); link.href = SOURCES[metric.kind]; details.append(link); }
      card.append(details); grid.append(card);
    }
    body.append(grid);
    const criteria = make('details', undefined, 'readiness-criteria');
    criteria.append(make('summary', 'Backtest criteria & remaining gaps'));
    criteria.append(make('p', 'These gates are shared cross-system evidence thresholds, including when a single source is selected. Passing them alone does not validate a strategy or authorize live routing.'));
    const list = make('ul'); for (const gate of model.gates) list.append(make('li', `${human(gate.key)}: ${gate.status}${gate.status === 'Not reported' ? '' : ` · ${count(gate.actual)} / ${count(gate.required)} required`}`)); criteria.append(list);
    criteria.append(make('h3', 'Before interpreting a connected backtest'));
    const checks = make('ul');
    for (const text of ['Use matching mart IDs, exact model identities, and point-in-time feature provenance.', 'Preserve source, LIVE/SHADOW cohort, decision windows, contract legs and quote-age provenance.', 'Apply the same entry/exit label contract and risk normalization before comparing returns.', 'Check integrity anomalies and missing labels; stored coverage alone does not establish quality.', 'Source-specific training and generic eligibility are different contracts. Per-source eligible denominators are unavailable in these snapshots.']) checks.append(make('li', text));
    criteria.append(checks, make('p', shadow?.data_quality?.integrity_clean === false ? `Integrity warning: ${count(shadow.data_quality.cohorts_with_integrity_anomalies)} cohort(s) report anomalies.` : shadow?.data_quality?.integrity_clean === true ? 'Snapshot reports integrity clean; other criteria still apply.' : 'Integrity status not reported.', 'notice'));
    body.append(criteria);
  }
  select.addEventListener('change', render);
  return state => { current = state; render(); };
}
