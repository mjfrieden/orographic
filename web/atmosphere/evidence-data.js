import { count, timestamp, validGate, readBoundedJson } from './data.js';
export const STAGES = ['Sources','Point-in-time features','Models','Decisions','Outcomes','Evaluation'];
export const COLUMNS = ['Availability','Freshness','Point-in-time validity','Coverage','Evaluation evidence'];
export function age(value, now = Date.now()) {
  const time = typeof value === 'string' && /(?:Z|[+-]\d\d:\d\d)$/.test(value) ? Date.parse(value) : NaN;
  if (!Number.isFinite(time) || time > now) return {state:'unknown',text:'Unknown clock'};
  const hours = Math.floor((now-time)/3600000);
  return {state:(now-time) > 48*3600000 ? 'stale':'available',text:hours >= 48 ? `${Math.floor(hours/24)}d ${hours%24}h old` : `${hours}h old`};
}
export function validateSnapshot(data) {
  if (data?.schema_version !== 1 || !/^[a-f0-9]{64}$/.test(data.snapshot_id) || !/^[a-f0-9]{40}$/.test(data.source_commit)) throw Error('Unsupported evidence snapshot schema or identity.');
  if (!data.sync?.mart_id || data.sync.mart_id !== data.shadow?.mart_id) throw Error('Mart identities differ. Linked evidence withheld.');
  for (const key of ['sync','shadow','runtime','health','models','ledger']) if (!/^[a-f0-9]{64}$/.test(data.sources?.[key]?.sha256)) throw Error('Source provenance is incomplete.');
  if (!Array.isArray(data.decisions) || data.decisions.length > 24 || !Array.isArray(data.models) || data.models.length > 12) throw Error('Evidence collection is malformed or exceeds its bound.');
  if (!Array.isArray(data.health?.gates) || !data.runtime || !data.publication_review) throw Error('Evidence sections are missing.');
  for (const record of data.decisions) if (typeof record.id !== 'string' || typeof record.date !== 'string' || !Number.isFinite(Date.parse(record.date)) || !record.quote || (record.friday !== null && (typeof record.friday !== 'number' || !Number.isFinite(record.friday)))) throw Error('Malformed decision or outcome.');
  if (new Set(data.decisions.map(r=>r.id)).size !== data.decisions.length) throw Error('Duplicate decision identities.');
  return data;
}
export async function loadEvidence(signal) {
  const response = await fetch('/atmosphere/evidence-snapshot.json',{signal,cache:'no-store',credentials:'same-origin',redirect:'error',headers:{Accept:'application/json'}});
  return validateSnapshot(await readBoundedJson(response,128*1024));
}
const presentCount = v => Number.isSafeInteger(v) && v >= 0 ? 'available' : 'unknown';
const healthState = v => ({red:'failed',amber:'pending',green:'available'}[v] ?? 'unknown');
const percent = v => typeof v === 'number' && Number.isFinite(v) && v>=0 && v<=1 ? `${(v*100).toFixed(2)}%`:'Unknown';
export function buildEvidence(data, now=Date.now()) {
  const nodes=[];
  const add=(id,stage,title,state,value,scope,source,facts,next,extra={}) => nodes.push({id,stage,title,state,value,scope,source,date:data.sources[source]?.generated_at_utc,facts,next,...extra});
  const sync=data.sync, shadow=data.shadow;
  add('quotes',0,'Quote archive',age(sync.generated_at_utc,now).state,`${count(sync.rows?.option_quotes)} rows`,'shared','sync',[
    ['Unit','Stored quote observations; not trades or independent dates.'],['Last ingestion','Not supplied; artifact generation is not ingestion time.'],['Provenance rows',count(sync.rows?.quote_provenance)]
  ],'Restore a fresh versioned mart and retain quote-side clocks.');
  add('features',1,'Feature snapshots',presentCount(sync.rows?.feature_snapshots),`${count(sync.rows?.feature_snapshots)} rows`,'shared','sync',[
    ['Meaning','Generic feature presence; individual family validity is not established.'],['Point-in-time recommendations',count(shadow.training_funnel?.with_point_in_time_feature)],['Population','Shared mart generic training funnel; not a family-specific ablation.']
  ],'Publish family coverage, availability clocks and consumed-feature receipts.');
  add('runs',2,'Recorded model runs','unknown',`${count(sync.rows?.model_runs)} rows`,'shared','sync',[
    ['Immutable model IDs','Not included in this aggregate.'],['Consumption','Configured or present inputs do not prove use.']
  ],'Join recommendation keys to exact model and input identities.');
  add('recommendations',3,'Recommendations',presentCount(sync.rows?.recommendations),`${count(sync.rows?.recommendations)} rows`,'shared','sync',[
    ['Population','Both source systems in the pinned mart; no pooled training.'],['Individual decisions','Aggregate only; no invented links to the newer runtime.']
  ],'Supply a bounded recommendation-level consumer bundle.');
  add('outcomes',4,'Executable labels',presentCount(shadow.training_funnel?.with_valid_label_outcome),`${count(shadow.training_funnel?.with_valid_label_outcome)} recommendations`,'shared','shadow',[
    ['Denominator',`${count(shadow.training_funnel?.recommendations)} generic-funnel recommendations`],['Missing executable outcome',count(shadow.training_funnel?.dropped_missing_executable_outcome)],['Evidence type','Quote-derived research labels; not broker returns.'],['Horizon','Generic funnel can contain differing horizons; never pool into a performance plot.']
  ],'Recover missing exits and use a single compatible horizon and cost contract.');
  const gate=shadow.shadow_entry_gates?.direct_return_comparable_market_dates;
  add('gate',5,'Comparable-date gate',validGate(gate) ? (gate.passed?'available':'failed'):'unknown',validGate(gate)?`${gate.actual} / ${gate.required} dates`:'Unknown','shared','shadow',[
    ['Gate','direct_return_comparable_market_dates'],['Direct pairs',count(shadow.cross_system_comparison?.direct_return_comparable_pairs)],['Exploratory 24h pairs',count(shadow.cross_system_comparison?.fixed_24h_exploratory_shadow_pairs)],['Limitation','Exploratory pairs do not satisfy the direct comparable-return gate.']
  ],'Collect synchronized, risk-comparable after-cost outcomes across the required independent dates.');
  add('publication',5,'Publication connection','failed','Dated failure review','shared','sync',[
    ['Failed stage',data.publication_review.stage],['Reason',data.publication_review.reason],['Review clock',timestamp(data.publication_review.reviewed_at_utc)],['Authority',data.publication_review.authority],['Last observed published ID',sync.iceberg?.latest_publication_mart_id ?? 'Unknown'],['Last observed publication',timestamp(sync.iceberg?.latest_publication_at_utc)],['Readback','No verified publication identity for this local snapshot.']
  ],'Diagnose the owning Iceberg snapshot and recover a reviewed publication; verify exact mart identity.',{reviewLink:data.publication_review.source});
  add('runtime-source',0,'Runtime receipt',age(data.runtime.generated_at_utc,now).state,'Recorded scan','runtime','runtime',[
    ['Evidence','Committed scan metadata; served runtime is unverified.'],['Clock',timestamp(data.runtime.generated_at_utc)],['Source inputs','Individual source availability and consumption clocks require further receipts.']
  ],'Verify source, config and snapshot identity at the served runtime without trading.');
  add('runtime-features',1,'Input provenance','unknown','Family clocks incomplete','runtime','runtime',[
    ['Availability','The health matrix separates feature presence from predictive evidence.'],['Consumption','No full per-decision consumed-feature lineage in this slice.']
  ],'Join source clocks and feature values to each decision and exact model.');
  for (const model of data.models) add(model.id,2,model.title,'available',`Version ${model.version ?? 'unknown'}`,'runtime','models',[
    ['Model SHA-256',model.model_sha256 ?? 'Unknown'],['How it works',model.how_it_works],['Population',model.population],['Evaluation window',model.evaluation_window],['Limits',(model.limitations??[]).join(' ')],['Identity','Recorded model-card identity; binary and served runtime not independently verified here.']
  ],'Inspect frozen evaluation evidence for this exact model.',{model:model.id});
  add('council',3,'Council decision',data.runtime.abstain===true?'available':'unknown',data.runtime.abstain===true?'Abstained':'Inspect receipt','runtime','runtime',[
    ['Recorded reason',data.runtime.reason ?? 'Not supplied'],['Candidates',count(data.runtime.candidate_count)],['Published live selections',count(data.runtime.live_count)],['Scope','Abstention for this board does not disable the live order route.']
  ],'Inspect recorded vetoes; do not infer deployment containment from abstention.');
  add('runtime-outcome',4,'Subsequent outcome','missing','No selection to match','runtime','runtime',[
    ['This scan','An abstaining board has no selected-trade return.'],['Historic ledger','Inspect separately dated recommendations below; no join to this scan is implied.']
  ],'Retain abstention and no-trade opportunities in the evaluation ledger.');
  add('research-gate',5,'Research readiness',healthState(data.health.status),data.health.status ?? 'Unknown','runtime','health',[
    ['Blocking gates',(data.health.gates??[]).filter(g=>g.blocking).map(g=>g.title).join('; ')],['Authority','Research gate, separate from operational workflow success.']
  ],'Resolve blocking evidence gates before performance or promotion claims.');
  const event=data.health.gates.find(g=>g.code==='event_feed_health');
  const labels=data.health.gates.find(g=>g.code==='label_cohort_completion');
  const unknown=(name,next) => ({name,source:'runtime',scope:'runtime',cells:COLUMNS.map((c,i)=>({state:i===4?'not-evaluated':'unknown',value:i===4?'Not evaluated':'Unknown',facts:[['Limitation',`${name} ${c.toLowerCase()} is not established by this bounded evidence snapshot.`]],next}))});
  const matrix=[unknown('Event / sentiment','Archive first-seen clocks and run a matched frozen ablation.'),unknown('Macro','Retain release availability and revision vintage.'),unknown('Sector','Supply point-in-time classifications and matched evaluation.'),unknown('Expectations','Supply pre-release consensus; missing surprise is not zero.'),unknown('Greeks','Retain quote/underlying clocks, units and provider provenance.'),unknown('After-cost labels','Complete due fixed-horizon labels and reconcile costs.')];
  matrix[0].source='health';
  matrix[0].cells[0]={state:healthState(event?.status),value:event?.metrics?.feed_status ?? 'Unknown',facts:[['New rows',count(event?.metrics?.new_rows)],['Scope','Orographic health receipt; not the shared mart population.']],next:'Repair the point-in-time event feed.'};
  matrix[0].cells[3]={state:healthState(event?.status),value:percent(event?.metrics?.complete_outcome_event_coverage_pct),facts:[['Denominator','Complete outcomes; numeric denominator absent in event gate.'],['Required',percent(event?.metrics?.required_min_pct)]],next:'Recover event coverage with valid availability clocks.'};
  matrix[5].source='health';
  matrix[5].cells[0]={state:presentCount(labels?.metrics?.fully_labeled),value:'Label observations',facts:[['Complete',count(labels?.metrics?.fully_labeled)],['Total picks',count(labels?.metrics?.total_picks)]],next:'Backfill due missing exits.'};
  matrix[5].cells[3]={state:healthState(labels?.status),value:percent(labels?.metrics?.total_cohort_completion_pct),facts:[['Numerator',count(labels?.metrics?.fully_labeled)],['Denominator',count(labels?.metrics?.total_picks)],['Population','Core and moonshot emitted picks; fixed-horizon completion, not observed-mark coverage.']],next:'Complete fixed-horizon marks and publish matched cohort cutoffs.'};
  for(const row of matrix) { const fresh=age(data.sources[row.source].generated_at_utc,now); row.cells[1]={state:'unknown',value:'Feature clock unknown',facts:[['Artifact age',fresh.text],['Artifact generated',timestamp(data.sources[row.source].generated_at_utc)],['Limitation','Artifact age only. Feature-level ingestion/availability age remains unknown.'],['Display threshold','48 hours, not a model admission rule.']],next:'Publish family-level freshness clocks.'}; }
  return {nodes,matrix};
}
export function filterEvidence(nodes,{scope='all',model='all',from='',to=''}={}) {
  return nodes.filter(n=>(scope==='all'||n.scope===scope)&&(model==='all'||!n.model||n.model===model)&&(!from||n.date?.slice(0,10)>=from)&&(!to||n.date?.slice(0,10)<=to));
}
