import { createWorkbench } from '../evidence-workbench.js';
import { loadContextArtifact } from './context-loader.js';

const mount = document.getElementById('atmosphere-workbench');
if (mount) {
  mount.hidden = false;
  mount.innerHTML = `<div class="atmosphere-context-intro"><h2>Model & scan context</h2><p>Optional context from existing Orographic artifacts. Each control loads only its named evidence; no broker account or order requests are made here.</p><div class="ew-history-actions"><button class="quiet-button" type="button" data-context-load="model">Load model context</button><button class="quiet-button" type="button" data-context-load="changes">Load scan changes</button><button class="quiet-button" type="button" data-context-load="performance">Load archived study</button></div><p id="context-load-status" class="ew-source" role="status">Nothing loaded yet. Model context: up to 1 MiB + 64 KiB. Changes: up to 2 MiB from the compact recent export. Archived study: up to 512 KiB.</p></div>
  <div class="evidence-workbench" id="atmosphere-context-panels" hidden><header class="ew-header"><div><p class="ew-kicker">Read-only context</p><h2 id="context-title">Behind the recommendations</h2></div><p>Published model state, recent recommendation differences and archived simulation are separate from shared-data analytics.</p></header><div class="ew-tabs" role="tablist" aria-label="Model and research context"><button id="ew-tab-model" type="button" role="tab" data-ew-tab="model" aria-controls="ew-panel-model" aria-selected="true">Model</button><button id="ew-tab-changes" type="button" role="tab" data-ew-tab="changes" aria-controls="ew-panel-changes" aria-selected="false" tabindex="-1">Changes</button><button id="ew-tab-performance" type="button" role="tab" data-ew-tab="performance" aria-controls="ew-panel-performance" aria-selected="false" tabindex="-1">Archived research</button></div><section id="ew-panel-model" class="ew-panel" role="tabpanel" aria-labelledby="ew-tab-model" tabindex="0"></section><section id="ew-panel-changes" class="ew-panel" role="tabpanel" aria-labelledby="ew-tab-changes" tabindex="0" hidden></section><section id="ew-panel-performance" class="ew-panel" role="tabpanel" aria-labelledby="ew-tab-performance" tabindex="0" hidden></section></div>`;
  const root = document.getElementById('atmosphere-context-panels');
  const workbench = createWorkbench(root, { mode: 'research' });
  let loading = false;
  mount.addEventListener('click', async (event) => {
    const button = event.target.closest('[data-context-load]');
    if (!button || loading) return;
    const section = button.dataset.contextLoad;
    const keys = section === 'model' ? ['snapshot', 'governance'] : section === 'changes' ? ['ledger'] : ['backtest'];
    loading = true;
    mount.querySelectorAll('[data-context-load]').forEach((item) => { item.disabled = true; });
    const status = document.getElementById('context-load-status');
    status.textContent = 'Loading selected context within the documented size limits…';
    root.hidden = false; workbench.selectTab(section);
    try {
      const results = await Promise.allSettled(keys.map((key) => loadContextArtifact(key)));
      const next = {}; const messages = [];
      results.forEach((result, index) => {
        const key = keys[index];
        if (result.status === 'fulfilled') { next[key] = result.value; messages.push(`${key}: loaded`); }
        else { next[key] = null; messages.push(`${key}: ${String(result.reason?.message || result.reason)}`); }
      });
      workbench.update(next); status.textContent = messages.join(' · ');
    } finally {
      loading = false; mount.querySelectorAll('[data-context-load]').forEach((item) => { item.disabled = false; });
    }
  });
}
