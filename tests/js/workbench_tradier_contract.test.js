import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

const indexPath = new URL("../../web/index.html", import.meta.url);
const appPath = new URL("../../web/app.js", import.meta.url);
const cockpitStylePath = new URL("../../web/cockpit.css", import.meta.url);

const REQUIRED_STATIC_IDS = [
  // Session and broker ribbon.
  "session-user",
  "logout-btn",
  "ribbon-equity",
  "ribbon-obp",
  "ribbon-cash",
  "ribbon-pl",
  "ribbon-positions",
  "ribbon-broker-mode",
  // Recommendation mounts are where quote-backed order controls are created.
  "board-sync-status",
  "board-refresh-btn",
  "live-picks-grid",
  "shadow-picks-grid",
  "moonshot-picks-grid",
  // Account positions and orders.
  "positions-sync-status",
  "positions-refresh-btn",
  "positions-table-wrap",
  "positions-table",
  "positions-tbody",
  "orders-table-wrap",
  "orders-table",
  "orders-tbody",
  "signal-selector",
  "book-tab-positions",
  "book-tab-orders",
  "book-positions-panel",
  "book-orders-panel",
  // Preview and submission modal.
  "preview-modal",
  "modal-title",
  "modal-close-btn",
  "modal-body",
  "modal-execute-btn",
  "modal-cancel-btn",
  "modal-message",
];

const REQUIRED_DYNAMIC_HOOKS = [
  "trade-card",
  "card-qty-input",
  "card-qty-step",
  "card-preview-btn",
  "card-execute-btn",
  "close-position-btn",
  "data-contract",
  "data-symbol",
  "data-lane",
  "data-ask",
  "data-alloc",
  "data-qty",
];

test("Signal & Book cockpit preserves the static Tradier DOM contract", async () => {
  const html = await readFile(indexPath, "utf8");
  const ids = new Set(
    [...html.matchAll(/\bid=["']([^"']+)["']/g)].map((match) => match[1]),
  );

  for (const id of REQUIRED_STATIC_IDS) {
    assert.ok(ids.has(id), `missing required Tradier DOM id: ${id}`);
  }
});

test("Signal & Book cockpit preserves dynamic order and position hooks", async () => {
  const source = await readFile(appPath, "utf8");

  for (const hook of REQUIRED_DYNAMIC_HOOKS) {
    assert.ok(source.includes(hook), `missing required Tradier JS hook: ${hook}`);
  }
});

test("Signal & Book cockpit preserves broker endpoint paths and request modes", async () => {
  const source = await readFile(appPath, "utf8");

  for (const endpoint of [
    "/api/session",
    "/api/logout",
    "/api/tradier/account",
    "/api/tradier/quotes?symbols=",
    "/api/tradier/orders",
  ]) {
    assert.ok(source.includes(endpoint), `missing required fetch path: ${endpoint}`);
  }

  assert.match(source, /preview:\s*true[\s\S]*side:\s*["']buy_to_open["']/);
  assert.match(source, /preview:\s*true[\s\S]*side:\s*["']sell_to_close["']/);
  assert.match(source, /preview:\s*false[\s\S]*confirm_live:/);
  assert.match(source, /type:\s*["']limit["']/);
  assert.match(source, /duration:\s*["']day["']/);
});

test("Signal & Book boot initializes auth, account, board, and modal bindings", async () => {
  const source = await readFile(appPath, "utf8");

  for (const call of [
    "loadSession()",
    "bindLogout()",
    "bindModal()",
    "bindPositionsControls()",
    "bindBoardControls()",
    "loadAccount()",
    "refreshBoard()",
  ]) {
    assert.ok(source.includes(call), `missing required initialization call: ${call}`);
  }
});

test("Signal & Book exposes interactive sizing, candidate, book, and evidence controls", async () => {
  const html = await readFile(indexPath, "utf8");
  const source = await readFile(appPath, "utf8");

  for (const control of [
    "data-research-focus=\"observations\"",
    "role=\"tablist\"",
    "aria-controls=\"book-orders-panel\"",
  ]) {
    assert.ok(html.includes(control), `missing interactive control: ${control}`);
  }

  for (const behavior of [
    "renderCockpitSignalSelector",
    "syncTradeCardQuantity",
    "selectedCardQuantity(btn)",
    "event.key === \"Escape\"",
  ]) {
    assert.ok(source.includes(behavior), `missing interaction behavior: ${behavior}`);
  }
});

test("Signal & Book preserves readable synchronization spacing", async () => {
  const source = await readFile(appPath, "utf8");
  const styles = await readFile(cockpitStylePath, "utf8");

  assert.ok(
    source.match(/let className = "sync-line positions-sync-status";/g)?.length >= 2,
    "board and Tradier status rendering must preserve the sync-line layout class",
  );
  assert.match(styles, /\.signal-pane \.pane-heading > \.sync-line\s*\{[^}]*margin:\s*14px 0 0;/s);
  assert.match(styles, /\.positions-toolbar\s*\{[^}]*flex-direction:\s*row;/s);
  assert.match(styles, /@media \(max-width:\s*720px\)[\s\S]*\.positions-toolbar\s*\{[^}]*flex-direction:\s*column;/s);
});
