import test from 'node:test';
import assert from 'node:assert/strict';
import { bindWorkbenchLifecycle, createWorkbench } from '../../web/evidence-workbench.js';
function hostFixture() {
 const listeners = new Map(); let reloads = 0;
 return { listeners, get reloads() { return reloads; }, host: { addEventListener: (type, fn) => listeners.set(type, fn), location: { reload: (...args) => { assert.equal(args.length, 0); reloads++; } } } };
}
function rootFixture() {
 const panel = { innerHTML: '', hidden: false }; const listeners = new Map();
 return { panel, listeners, root: { dataset: { ewMode: 'trading' }, ownerDocument: { activeElement: null }, contains: () => true,
  querySelector: selector => selector === '#ew-panel-performance' ? panel : null, querySelectorAll: () => [], addEventListener: (type, fn) => listeners.set(type, fn) } };
}
const snapshot = { session: { authenticated: true, session: { username: 'test', role: 'admin' } }, broker: { configured: true, accountId: 'TEST', mode: 'sandbox', balances: { total_equity: 1234 } } };
test('initial pageshow does not clear or reload; persisted restore clears then reloads once', () => {
 const f = hostFixture(); let cleared = 0;
 bindWorkbenchLifecycle({ clearSensitiveData: () => { cleared++; } }, f.host);
 f.listeners.get('pageshow')({ persisted: false }); assert.equal(cleared, 0); assert.equal(f.reloads, 0);
 f.listeners.get('pagehide')({ persisted: true }); assert.equal(cleared, 1);
 f.listeners.get('pageshow')({ persisted: true }); assert.equal(cleared, 2); assert.equal(f.reloads, 1);
 f.listeners.get('pageshow')({ persisted: true }); assert.equal(cleared, 2); assert.equal(f.reloads, 1);
});
test('ordinary navigation still performs privacy cleanup without requesting reload', () => {
 const f = hostFixture(); let cleared = 0; bindWorkbenchLifecycle({ clearSensitiveData: () => { cleared++; } }, f.host);
 f.listeners.get('pagehide')({ persisted: false }); assert.equal(cleared, 1); assert.equal(f.reloads, 0);
});
test('BFCache restores through normal boot rather than reactivating cached session/account', () => {
 const ui = rootFixture(), f = hostFixture(), workbench = createWorkbench(ui.root); bindWorkbenchLifecycle(workbench, f.host);
 workbench.update(snapshot); assert.match(ui.panel.innerHTML, /\$1,234\.00/); assert.match(ui.panel.innerHTML, /id="ew-returns-import"/);
 f.listeners.get('pagehide')({ persisted: true }); assert.doesNotMatch(ui.panel.innerHTML, /\$1,234\.00|id="ew-returns-import"/);
 workbench.update(snapshot); assert.doesNotMatch(ui.panel.innerHTML, /\$1,234\.00/);
 f.listeners.get('pageshow')({ persisted: true }); assert.equal(f.reloads, 1); assert.doesNotMatch(ui.panel.innerHTML, /id="ew-returns-import"/);
 // Simulate fresh module boot after reload; only new initialization can restore account UI.
 const fresh = rootFixture(), newWorkbench = createWorkbench(fresh.root); newWorkbench.update(snapshot);
 assert.match(fresh.panel.innerHTML, /\$1,234\.00/); assert.match(fresh.panel.innerHTML, /id="ew-returns-import"/); assert.doesNotMatch(fresh.panel.innerHTML, /<svg/);
});
test('navigation cancels pending receipt read; late completion cannot restore sensitive UI', async () => {
 const ui = rootFixture(), f = hostFixture(), workbench = createWorkbench(ui.root); bindWorkbenchLifecycle(workbench, f.host); workbench.update(snapshot);
 let release;
 ui.listeners.get('change')({ target: { id: 'ew-returns-import', files: [{ size: 10, text: () => new Promise(resolve => { release = resolve; }) }] } });
 assert.match(ui.panel.innerHTML, /Reading local receipt/);
 f.listeners.get('pagehide')({ persisted: true }); f.listeners.get('pageshow')({ persisted: true });
 release('{invalid json}'); await Promise.resolve(); await Promise.resolve();
 assert.doesNotMatch(ui.panel.innerHTML, /Reading local receipt|Receipt is not valid JSON|id="ew-returns-import"|<svg/); assert.equal(f.reloads, 1);
});
