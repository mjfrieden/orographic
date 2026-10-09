import { readSessionJson } from './session-response.js';

export const MAX_HISTORY_PAGES = 10;
export const MAX_HISTORY_ROWS = 1000;

/** Explicit user-triggered reads only. Does not infer matches to recommendations. */
export function createGainLossLoader(onChange, dependencies = {}) {
  const request = dependencies.fetch || ((...args) => globalThis.fetch(...args));
  const read = dependencies.readJson || ((response) => readSessionJson(response, 'Closed-position history', globalThis.location, true));
  let generation = 0;
  let controller = null;
  let state = { pages: [], loading: false, error: null, loaded: false };
  const publish = () => onChange({ ...state, pages: [...state.pages] });
  return {
    state: () => ({ ...state, pages: [...state.pages] }),
    reset() {
      generation += 1;
      controller?.abort(); controller = null;
      state = { pages: [], loading: false, error: null, loaded: false }; publish();
    },
    async load(page = 1, refresh = false) {
      if (state.loading) return;
      if (!Number.isInteger(page) || page < 1) throw new Error('Invalid history page');
      if (page > MAX_HISTORY_PAGES || (!refresh && !state.pages.some((entry) => entry.pagination.page === page) && (state.pages.length >= MAX_HISTORY_PAGES || state.pages.reduce((sum, entry) => sum + entry.rows.length, 0) >= MAX_HISTORY_ROWS))) {
        state = { ...state, error: 'History load limit reached: 10 pages / 1,000 records. Refresh to start again.' }; publish(); return;
      }
      const requestGeneration = generation;
      controller = new AbortController();
      state = { ...state, loading: true, error: null }; publish();
      try {
        const response = await request(`/api/tradier/gainloss?page=${page}`, { cache: 'no-store', signal: controller.signal });
        if (requestGeneration !== generation) return;
        const payload = await read(response);
        if (requestGeneration !== generation) return;
        if (!response.ok || !payload?.ok) throw new Error(payload?.error || `History unavailable (${response.status})`);
        if (payload.source !== 'tradier_gainloss' || !Array.isArray(payload.rows) || payload.rows.length > 100 || payload.pagination?.page !== page) throw new Error('History response is incomplete. Refresh to retry.');
        const pages = refresh ? [] : state.pages.filter((entry) => entry.pagination.page !== page);
        pages.push(payload); pages.sort((a, b) => a.pagination.page - b.pagination.page);
        state = { pages, loading: false, loaded: true, error: null };
      } catch (error) {
        if (requestGeneration !== generation) return;
        state = { ...state, loading: false, error: String(error?.message || error) };
      }
      controller = null;
      publish();
    },
  };
}
