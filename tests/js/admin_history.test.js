import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const htmlPath = new URL("../../web/admin/index.html", import.meta.url);
const cssPath = new URL("../../web/admin-harbor.css", import.meta.url);
const jsPath = new URL("../../web/admin/admin.js", import.meta.url);

test("Master Harbor history errors wrap on a 720px phone without changing recovery copy", async () => {
  const html = await readFile(htmlPath, "utf8");
  const css = await readFile(cssPath, "utf8");
  const source = await readFile(jsPath, "utf8");

  assert.ok(html.includes('id="admin-overview-grid"'));
  assert.ok(html.includes('id="admin-history-tbody"'));
  assert.ok(html.includes('id="admin-order-ledger-tbody"'));
  assert.ok(html.includes('id="admin-mart-status"'));
  assert.ok(html.includes('id="admin-weekly-alpha"'));
  assert.match(html, /admin-harbor\.css\?v=20261028a/);
  assert.ok(source.includes("function renderWeeklyAlpha"));
  assert.ok(source.includes("Next action."));
  assert.ok(source.includes("Kill switch"));
  assert.ok(source.includes("Name concentration"));

  assert.match(css, /\.admin-page \.admin-history-table\s*\{[^}]*min-width:\s*720px;/s);
  assert.match(css, /\.admin-page \.admin-history-loading\s*\{[^}]*overflow-wrap:\s*anywhere;/s);
  assert.match(
    css,
    /@media \(max-width:\s*720px\)[\s\S]*\.admin-page \.admin-history-table:has\(td\.admin-history-loading\)\s*\{[^}]*min-width:\s*0;/s,
  );

  assert.ok(source.includes("function friendlyLoadError"));
  assert.ok(source.includes("Position history is unavailable in this session"));
  assert.ok(source.includes("Refresh this page, or open the hosted admin after you sign in."));
  assert.match(source, /fetch\(["'`]\/api\/admin\//);
});
