import { MODEL_HISTORY } from './model-history-catalog.js';
import { timestamp, safeText } from './data.js';

const sha = value => typeof value === 'string' && /^[a-f0-9]{40}$/.test(value);
const hash = value => typeof value === 'string' && /^[a-f0-9]{64}$/.test(value);
const text = value => typeof value === 'string' && value.length > 0 && value.length <= 2000;
const path = value => typeof value === 'string' && /^engine\/orographic\/models\/[a-z0-9_]+\.(json|pkl)$/.test(value);
const object = value => value && typeof value === 'object' && !Array.isArray(value);
const date = value => typeof value === 'string' && /Z$/.test(value) && Number.isFinite(Date.parse(value));
const el = (tag, value, cls) => { const node = document.createElement(tag); if (value !== undefined) node.textContent = value; if (cls) node.className = cls; return node; };
const label = key => key.replaceAll('_', ' ');
const sourceUrl = (commit, file) => `https://github.com/mjfrieden/orographic/blob/${commit}/${file}`;
const link = (title, url) => { const node = el('a', title); node.href = url; return node; };

const policyFields = {
  profile: 'text', lane_count: 'count', lane: 'text', ranker_count: 'count',
  ranker_objective: 'text', research_risk_posture: 'text', legacy_runtime_authority: 'boolean',
  probability_sizing_enabled: 'boolean', artifact_change_rule: 'text',
  side_aware_scout_default: 'text', side_aware_scout_active_monitoring_requirements: 'list',
  payoff_ranker_default: 'text', path_model_default: 'text', production_lane_count: 'count',
  production_lane: 'text', challenger_policy: 'list', path_model_required_before_any_live_use: 'list',
  side_aware_scout_required_before_active: 'list'
};
const policyText = value => typeof value === 'string' && value.trim().length > 0 && value.length <= 500;
const calendarDate = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value) && Number.isFinite(Date.parse(value)) && new Date(value).toISOString().slice(0, 10) === value;
function validatePolicy(policy, decision) {
  if (!object(policy) || Object.keys(policy).length > 18 || !object(decision) || Object.keys(decision).length > 3) throw new Error('Invalid historical policy.');
  for (const [key, value] of Object.entries(policy)) {
    const type = Object.hasOwn(policyFields, key) ? policyFields[key] : null;
    const valid = type === 'text' ? policyText(value) : type === 'count' ? Number.isSafeInteger(value) && value >= 0 && value <= 100 : type === 'boolean' ? typeof value === 'boolean' : type === 'list' ? Array.isArray(value) && value.length <= 8 && value.every(policyText) : false;
    if (!valid) throw new Error('Invalid historical policy field.');
  }
  const decisionKeys = Object.keys(decision);
  if (decisionKeys.length && (decisionKeys.length !== 3 || !decisionKeys.every(key => ['effective_date', 'production_default_variant', 'evidence_note_path'].includes(key)) || !calendarDate(decision.effective_date) || !policyText(decision.production_default_variant) || typeof decision.evidence_note_path !== 'string' || decision.evidence_note_path.length > 200 || !/^docs\/[a-z0-9-]+\.md$/.test(decision.evidence_note_path))) throw new Error('Invalid historical decision.');
}

// Values are shallow, allowlisted scalars/lists. Never stringify arbitrary nested
// source objects into the UI, and distinguish an absent declaration from false.
export function policyChanges(event, before) {
  validatePolicy(event.policy, event.decision);
  if (!before) return [];
  validatePolicy(before.policy, before.decision);
  return ['policy', 'decision'].flatMap(group => [...new Set([...Object.keys(before[group]), ...Object.keys(event[group])])].filter(key => JSON.stringify(before[group][key]) !== JSON.stringify(event[group][key])).map(key => ({ group, key, before: before[group][key], after: event[group][key] })));
}
const policyValue = value => value === undefined ? 'Not declared in this revision' : Array.isArray(value) ? value.length ? value.join('; ') : 'Empty requirement list' : String(value);

export function validateHistory(catalog) {
  if (catalog?.schema_version !== 1 || catalog.repository !== 'mjfrieden/orographic' || !sha(catalog.reviewed_revision) || !date(catalog.reviewed_at_utc) || catalog.source_path !== 'engine/orographic/models/artifact_manifest.json' || !text(catalog.coverage) || !Array.isArray(catalog.entries) || catalog.entries.length > 32) throw new Error('Unsupported model history.');
  const seen = new Set(); let previous = Infinity;
  for (const event of catalog.entries) {
    if (!sha(event?.commit) || seen.has(event.commit) || !date(event.committed_at) || Date.parse(event.committed_at) > previous || Date.parse(event.committed_at) > Date.parse(catalog.reviewed_at_utc) || !text(event.title) || !sha(event.manifest_blob) || !hash(event.manifest_sha256) || !object(event.artifacts) || Object.keys(event.artifacts).length > 32 || !object(event.policy) || !object(event.decision)) throw new Error('Invalid model-history event.');
    validatePolicy(event.policy, event.decision);
    seen.add(event.commit); previous = Date.parse(event.committed_at);
    for (const [key, artifact] of Object.entries(event.artifacts)) {
      if (!/^[a-z][a-z0-9_]{0,79}$/.test(key) || !object(artifact) || !path(artifact.path) || !hash(artifact.sha256) || !text(artifact.role) || typeof artifact.required !== 'boolean') throw new Error('Invalid artifact identity.');
      if (artifact.card_path !== undefined && (!path(artifact.card_path) || !hash(artifact.card_sha256) || !(artifact.card_version === null || Number.isSafeInteger(artifact.card_version) && artifact.card_version >= 0) || !(artifact.trained_at === null || text(artifact.trained_at) && Number.isFinite(Date.parse(artifact.trained_at))))) throw new Error('Invalid historical model-card evidence.');
    }
  }
  return catalog;
}

// Hash changes and policy changes are different events. A first retained row is a
// baseline, never proof that the model first existed or was deployed at that time.
export function historyRows(catalog, { artifact = '', kind = 'all' } = {}) {
  validateHistory(catalog);
  return catalog.entries.map((event, index) => {
    const before = catalog.entries[index + 1];
    const keys = [...new Set([...Object.keys(event.artifacts), ...Object.keys(before?.artifacts || {})])];
    const changes = keys.map(key => {
      const prior = before?.artifacts[key] || null, after = event.artifacts[key] || null;
      const change = !before ? 'Retained baseline' : !prior ? 'Added to manifest' : !after ? 'Removed from manifest' : prior.sha256 !== after.sha256 ? 'Artifact hash changed' : JSON.stringify(prior) !== JSON.stringify(after) ? 'Role or card metadata changed' : 'Unchanged';
      return { key, before: prior, after, change };
    }).filter(row => (!artifact || row.key === artifact) && row.change !== 'Unchanged');
    const policyDiff = policyChanges(event, before);
    const policyChanged = policyDiff.length > 0;
    const replacements = changes.some(row => ['Added to manifest', 'Removed from manifest', 'Artifact hash changed'].includes(row.change));
    return { event, changes, policyChanged, policyDiff, replacements, baseline: !before };
  }).filter(row => (!artifact || row.changes.length) && (kind === 'all' || kind === 'artifacts' && row.replacements || kind === 'policy' && (row.policyChanged || row.changes.some(change => change.change === 'Role or card metadata changed'))));
}

const runtimeKeys = { 'scout-direction': 'scout_model', 'scout-side': 'scout_side_model', 'tail-ranker': 'production_payoff_ranker' };
export function observationMatches(catalog, snapshot) {
  const latest = validateHistory(catalog).entries[0];
  return (Array.isArray(snapshot?.cards) ? snapshot.cards.slice(0, 8) : []).filter(card => Object.hasOwn(runtimeKeys, card?.id)).map(card => {
    const artifact = latest?.artifacts[runtimeKeys[card.id]];
    const identityMatched = card.identity_status === 'matched_metadata' && hash(card.model_sha256) && artifact?.sha256 === card.model_sha256 && artifact.card_sha256 === card.source_sha256 && artifact.card_path === card.source_path;
    const scalerMatched = card.id !== 'scout-direction' || latest?.artifacts.scout_scaler?.sha256 === card.scaler_sha256;
    return { title: safeText(card.title), version: card.version, matched: Boolean(identityMatched && scalerMatched) };
  });
}

export function renderModelHistory(root, snapshot, catalog = MODEL_HISTORY) {
  const section = el('section', undefined, 'model-history'); section.setAttribute('aria-label', 'Model version history');
  section.append(el('p', 'Repository versions & runtime observations', 'eyebrow'), el('h3', 'Model version history'));
  root.append(section);
  try { validateHistory(catalog); } catch { section.append(el('p', 'Model history unavailable: its source catalog is invalid.', 'notice')); return; }
  section.append(el('p', 'Inspect artifact replacements and declared policy changes separately from scans. Repository commit times are not verified served-release times. Training dates, card versions and runtime observations are separate evidence.'), el('p', catalog.coverage, 'muted'));
  section.append(el('p', `History reviewed ${timestamp(catalog.reviewed_at_utc)} · pinned revision ${catalog.reviewed_revision.slice(0, 12)}`), link('Inspect reviewed manifest', sourceUrl(catalog.reviewed_revision, catalog.source_path)));
  const observations = observationMatches(catalog, snapshot);
  section.append(el('h4', 'Latest loaded runtime observation'));
  if (!snapshot || !observations.length) section.append(el('p', 'Runtime comparison unavailable. The retained repository history below remains historical evidence only.', 'notice'));
  else {
    section.append(el('p', `Observed ${timestamp(snapshot.runtime_at_utc)}. This date is not a release event; it does not refresh the history catalog.`, 'muted'));
    const list = el('ul');
    for (const row of observations) list.append(el('li', `${row.title} · observed card version ${row.version == null ? 'not reported' : safeText(String(row.version))}: ${row.matched ? 'matches the latest reviewed manifest and source card' : 'does not have a verified match to the latest reviewed manifest; history may need review'}.`));
    section.append(list);
  }
  section.append(el('p', 'Cirrus: the current cards provide dated curated registry and discovery evidence, but no served-release receipt or verified model-version history. Study completion and source-review dates are not releases.', 'muted'));
  const filters = el('div', undefined, 'filters');
  const modelLabel = el('label', 'Artifact'); const model = el('select'); model.setAttribute('aria-label', 'Filter model history by artifact');
  const options = [['', 'All artifacts'], ...[...new Set(catalog.entries.flatMap(event => Object.keys(event.artifacts)))].sort().map(key => [key, label(key)])];
  for (const [value, title] of options) { const option = el('option', title); option.value = value; model.append(option); } model.value = ''; modelLabel.append(model);
  const kindLabel = el('label', 'Change type'); const kind = el('select'); kind.setAttribute('aria-label', 'Filter model history by change type');
  for (const [value, title] of [['all', 'All retained revisions'], ['artifacts', 'Artifact additions / replacements / removals'], ['policy', 'Policy / role / card metadata']]) { const option = el('option', title); option.value = value; kind.append(option); } kind.value = 'all'; kindLabel.append(kind); filters.append(modelLabel, kindLabel); section.append(filters);
  const status = el('p', undefined, 'muted'); status.setAttribute('role', 'status'); const timeline = el('div'); section.append(status, timeline);
  const render = () => {
    const rows = historyRows(catalog, { artifact: model.value, kind: kind.value }); timeline.replaceChildren(); status.textContent = `${rows.length} of ${catalog.entries.length} retained manifest revisions match. Newest repository commit first; all dates show their own evidence type.`;
    for (const row of rows) {
      const { event } = row; const detail = el('details', undefined, 'current-model-card'); const summary = el('summary', `${timestamp(event.committed_at)} · ${event.title}`); summary.setAttribute('aria-label', `Repository revision ${event.commit.slice(0, 12)}: ${event.title}`); detail.append(summary);
      detail.append(el('p', `Repository commit: ${event.committed_at} · ${event.commit}`), link('Inspect repository change', `https://github.com/mjfrieden/orographic/commit/${event.commit}`), el('p', row.baseline ? 'Earliest retained baseline. This is not evidence of the first release.' : `${row.changes.length} changed artifact declaration(s) in this filter.${row.policyChanged ? ' Declared policy also changed.' : ''}`));
      if (event.decision.effective_date) detail.append(el('p', `Declared policy effective date: ${safeText(event.decision.effective_date)} · ${safeText(event.decision.production_default_variant)}. This is a source declaration, not a deployment receipt.`));
      if (row.policyDiff.length) {
        detail.append(el('h4', 'Declared policy changes'));
        const policies = el('ul');
        for (const change of row.policyDiff) {
          const item = el('li');
          item.append(el('strong', `${change.group === 'decision' ? 'Decision' : 'Policy'} · ${label(change.key)}`), el('p', `Before: ${policyValue(change.before)}`), el('p', `After: ${policyValue(change.after)}`));
          policies.append(item);
        }
        detail.append(policies, el('p', 'These changes describe repository declarations only; they do not grant or prove runtime authority.', 'muted'));
      }
      const changes = el('ul');
      for (const change of row.changes) {
        const item = el('li'); const after = change.after, before = change.before;
        item.append(el('strong', `${label(change.key)} · ${change.change}`));
        if (before) item.append(el('p', `Prior declared SHA-256: ${before.sha256}`));
        if (after) {
          item.append(el('p', `Declared SHA-256: ${after.sha256}`), el('p', `Role declared in this revision: ${after.role}`));
          if (after.card_path) item.append(el('p', `Source-card version: ${after.card_version ?? 'not reported'} · trained: ${after.trained_at || 'not reported'}. Card version numbers can be reused; the artifact hash identifies the revision.`), link('Inspect historical source card', sourceUrl(event.commit, after.card_path)));
        } else item.append(el('p', 'Absent from this manifest. Removal alone does not establish when serving stopped.'));
        changes.append(item);
      }
      detail.append(changes, link('Inspect historical manifest', sourceUrl(event.commit, catalog.source_path)), el('p', `Manifest SHA-256: ${event.manifest_sha256}. Artifact hashes are source declarations; binary bytes and served deployment were not independently verified here.`, 'muted')); timeline.append(detail);
    }
    if (!rows.length) timeline.append(el('p', 'No retained revisions match. Choose All artifacts or All retained revisions to broaden the view.'));
  };
  model.addEventListener('change', render); kind.addEventListener('change', render); render();
}
