import { readBoundedJson, timestamp, safeText } from './data.js';
export const MODEL_SOURCE = '/data/diagnostics/current_model_cards_latest.json';
export function freshness(data, now = Date.now()) {
  const time = typeof data?.runtime_at_utc === 'string' ? Date.parse(data.runtime_at_utc) : NaN;
  if (!Number.isFinite(time) || time > now + 300000) return 'Runtime date unavailable or invalid';
  return now - time > 96 * 3600000 ? 'Stale runtime snapshot (over 96 hours old)' : 'Recent runtime snapshot';
}
export function curatedFreshness(card, now = Date.now()) {
  const time = typeof card?.verified_at_utc === 'string' ? Date.parse(card.verified_at_utc) : NaN;
  if (!Number.isFinite(time) || time > now + 300000) return 'Source verification date unavailable or invalid';
  return now - time > 96 * 3600000 ? 'Stale curated source review (over 96 hours old)' : 'Recent curated source review';
}
export function validateCards(data) {
  if (data?.schema_version !== 1 || !Array.isArray(data.cards) || data.cards.length > 8) throw new Error('Model-card schema is unsupported.');
  for (const card of data.cards) {
    if (!card || typeof card !== 'object' || !Array.isArray(card.metrics) || card.metrics.length > 12 || !Array.isArray(card.limitations) || card.limitations.length > 12) throw new Error('Model-card schema is unsupported.');
    if (card.identity_status === 'curated_snapshot') {
      const text = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 2000;
      const date = value => typeof value === 'string' && /(?:Z|[+-]\d{2}:\d{2})$/.test(value) && Number.isFinite(Date.parse(value));
      if (card.system !== 'Cirrus' || card.metrics.length || !['registry_role', 'discovery_study'].includes(card.evidence_kind) || !date(card.verified_at_utc) || !/^[a-f0-9]{64}$/.test(card.source_sha256 || '') || !/^[a-f0-9]{40}$/.test(card.source_revision || '') || card.source_path !== 'web/data/model-evidence/cirrus_public_summary.json' || !['title', 'role', 'how_it_works', 'evaluation_window', 'population', 'source_description', 'refresh_policy'].every(key => text(card[key])) || !Array.isArray(card.evaluation_summary) || card.evaluation_summary.length < 1 || card.evaluation_summary.length > 8 || !card.evaluation_summary.every(text) || !card.limitations.every(text)) throw new Error('Curated model evidence is incomplete.');
      if (card.evidence_kind === 'discovery_study' ? !date(card.evaluation_completed_at) : card.evaluation_completed_at !== null) throw new Error('Curated evaluation date is invalid.');
    }
    if (card.identity_status === 'matched_metadata') {
      const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
      const text = value => typeof value === 'string' && value.trim().length > 0;
      if (!hash(card.model_sha256) || !hash(card.source_sha256) || !text(card.source_path) || !text(card.population) || !text(card.evaluation_window) || !text(card.trained_at) || !Number.isFinite(Date.parse(card.trained_at)) || (card.id === 'scout-direction' && !hash(card.scaler_sha256))) throw new Error('Model identity or evaluation context is incomplete.');
      if (card.sample_size !== null && (!Number.isSafeInteger(card.sample_size) || card.sample_size < 0)) throw new Error('Invalid sample size.');
      const quantity = value => value === null || (Number.isSafeInteger(value) && value >= 0);
      if (card.training_class_counts && !['call_edge', 'put_edge', 'no_trade'].every(key => quantity(card.training_class_counts[key]))) throw new Error('Invalid training class counts.');
      if (card.replay && !quantity(card.replay.sample_size)) throw new Error('Invalid replay sample size.');
      for (const metric of card.metrics) {
        if (!metric || !text(metric.label) || !text(metric.context) || (metric.value !== null && (typeof metric.value !== 'number' || !Number.isFinite(metric.value) || metric.value < 0 || metric.value > 1))) throw new Error('Invalid model metric.');
      }
    }
  }
  return data;
}
export function formatMetric(value) { return typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 4 }) : 'Not reported'; }
const el = (tag, text, cls) => { const node = document.createElement(tag); if (text !== undefined) node.textContent = text; if (cls) node.className = cls; return node; };
export function renderCards(root, data) {
  root.replaceChildren(el('p', 'Model identity & evaluation', 'eyebrow'), el('h2', 'Current model cards'));
  root.append(el('p', `${freshness(data)} · Last observed Orographic runtime: ${timestamp(data.runtime_at_utc)}`), el('p', 'Orographic cards follow the existing scan publication. Cirrus uses a separately dated, manually reviewed summary. A new scan does not mean a new model, source review, or evaluation. Each task has its own labels and population.', 'muted'));
  const grid = el('div', undefined, 'model-card-grid');
  for (const card of data.cards) {
    const box = el('article', undefined, 'current-model-card');
    box.append(el('p', safeText(card.system), 'eyebrow'), el('h3', safeText(card.title)), el('p', `Model version: ${card.version == null ? 'Not reported' : safeText(String(card.version))} · Runtime mode: ${safeText(card.runtime_mode) || 'Not reported'}`), el('p', `Model trained: ${timestamp(card.trained_at)}`), el('p', safeText(card.how_it_works)));
    const matched = card.identity_status === 'matched_metadata';
    box.append(el('p', matched ? 'Identity matched to runtime metadata and source-card bytes' : card.identity_status === 'curated_snapshot' ? 'Curated source review only; current served identity and live performance unverified' : 'Identity unavailable or unverified; performance withheld', matched ? 'muted' : 'notice'));
    if (card.identity_status === 'curated_snapshot') {
      box.append(el('p', safeText(card.role)), el('p', `${curatedFreshness(card)} · Verified as of: ${timestamp(card.verified_at_utc)}`), el('p', safeText(card.refresh_policy), 'muted'), el('p', `Evidence window: ${safeText(card.evaluation_window)}`), el('p', `Population: ${safeText(card.population)}`));
      if (card.evidence_kind === 'discovery_study') box.append(el('p', `Research completed: ${timestamp(card.evaluation_completed_at)} · Historical discovery only`, 'notice'));
      const results = el('ul'); for (const item of card.evaluation_summary) results.append(el('li', safeText(item))); box.append(results);
      const provenance = el('details'); provenance.append(el('summary', 'Curated source provenance'), el('p', safeText(card.source_description)), el('p', `Reviewed source revision: ${card.source_revision}`), el('p', `Public summary SHA-256: ${card.source_sha256}`)); box.append(provenance);
    }
    if (matched) {
      box.append(el('p', `Historical evaluation: ${safeText(card.evaluation_window)}`), el('p', `Population: ${safeText(card.population)}`), el('p', `Validation sample: ${formatMetric(card.sample_size)} rows`));
      const metrics = el('dl', undefined, 'model-card-metrics');
      const quantity = value => value === null || (Number.isSafeInteger(value) && value >= 0);
      if (card.training_class_counts && !['call_edge', 'put_edge', 'no_trade'].every(key => quantity(card.training_class_counts[key]))) throw new Error('Invalid training class counts.');
      if (card.replay && !quantity(card.replay.sample_size)) throw new Error('Invalid replay sample size.');
      for (const metric of card.metrics) { const group = el('div'); group.append(el('dt', safeText(metric.label)), el('dd', formatMetric(metric.value)), el('p', safeText(metric.context), 'muted')); metrics.append(group); }
      box.append(metrics);
      if (card.training_class_counts) box.append(el('p', `Training classes: call ${formatMetric(card.training_class_counts.call_edge)}, put ${formatMetric(card.training_class_counts.put_edge)}, no trade ${formatMetric(card.training_class_counts.no_trade)}.`));
      if (card.replay) box.append(el('p', `Separate selected-policy quote replay: ${formatMetric(card.replay.sample_size)} selections; mean return ${formatMetric(typeof card.replay.mean_return === 'number' ? card.replay.mean_return * 100 : null)}%; median ${formatMetric(typeof card.replay.median_return === 'number' ? card.replay.median_return * 100 : null)}%. Not broker P&L.`));
    }
    const list = el('ul'); for (const item of card.limitations) list.append(el('li', safeText(item))); box.append(list);
    if (/^[a-f0-9]{64}$/.test(card.model_sha256 || '')) { const details = el('details'); details.append(el('summary', 'Model and source identity'), el('p', `Model artifact SHA-256 (declared): ${card.model_sha256}`), el('p', `Scaler SHA-256 (when applicable): ${safeText(card.scaler_sha256) || 'Not applicable'}`), el('p', `Source-card SHA-256: ${safeText(card.source_sha256)}`), el('p', `Source: ${safeText(card.source_path)}`)); box.append(details); }
    grid.append(box);
  }
  root.append(grid);
  const architecture = el('section', undefined, 'model-architecture'); architecture.setAttribute('aria-label', 'Model architecture');
  architecture.append(el('h3', 'How the systems fit together'), el('p', 'Separate model paths, separate evidence. Arrows show ranking flow, not a shared model or pooled training.'));
  for (const [title, steps] of [
    ['Orographic', ['Stock and market features → Scout direction / side / abstention', 'Eligible option contracts → Forge tail-utility ranking', 'Council policy → eligible recommendation or abstention']],
    ['Cirrus · source-reviewed design', ['Eligible option features → Boosting and the logistic / forest / boosting tail ensemble', 'Tail-ensemble rank + expert rank → Hybrid', 'Tournament governance + freshness checks → experimental or qualified email model; may abstain']]
  ]) {
    const lane = el('div', undefined, 'model-flow'); lane.append(el('h4', title)); const list = el('ol'); for (const step of steps) list.append(el('li', step)); lane.append(list); architecture.append(lane);
  }
  architecture.append(el('p', 'Cirrus design is a curated snapshot, not verification of its currently served release. The two inactive studies above are outside the email-model path. These cards do not change trading authority.'));
  root.append(architecture);
  const source = el('a', 'View generated model-card snapshot'); source.href = MODEL_SOURCE; root.append(source);
}
export function mountModelCards(root, button, { fetcher = fetch } = {}) {
  let controller; let generation = 0;
  async function refresh() {
    controller?.abort(); controller = new AbortController(); const active = controller; const current = ++generation;
    root.setAttribute('aria-busy', 'true'); root.replaceChildren(el('h2', 'Current model cards'), el('p', 'Loading model identity and historical evaluations…'));
    const timer = setTimeout(() => active.abort(), 15000);
    try {
      const response = await fetcher(MODEL_SOURCE, { signal: active.signal, credentials: 'same-origin', cache: 'no-store', redirect: 'error', headers: { Accept: 'application/json' } });
      const data = validateCards(await readBoundedJson(response, 64 * 1024));
      if (current !== generation) return;
      renderCards(root, data);
    } catch (error) {
      if (current !== generation) return;
      root.replaceChildren(el('h2', 'Current model cards'), el('p', 'Model cards unavailable. No saved performance values are being shown. Refresh to retry.', 'notice'));
      if (error?.code === 'auth') { const link = el('a', 'Sign in again'); link.href = '/login/?next=%2Fatmosphere%2F'; root.append(link); }
    } finally { clearTimeout(timer); if (current === generation) root.setAttribute('aria-busy', 'false'); }
  }
  button?.addEventListener('click', refresh);
  window.addEventListener('pagehide', () => { generation++; controller?.abort(); });
  window.addEventListener('pageshow', event => { if (event.persisted) refresh(); });
  refresh();
  return refresh;
}
if (typeof document !== 'undefined' && document.getElementById('current-model-cards')) mountModelCards(document.getElementById('current-model-cards'), document.getElementById('refresh'));
