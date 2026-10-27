import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const cockpitStylePath = new URL("../../web/cockpit.css", import.meta.url);
const appPath = new URL("../../web/app.js", import.meta.url);
const indexPath = new URL("../../web/index.html", import.meta.url);

test("HOLD Why no trade? reason stacks on a 720px phone", async () => {
  const styles = await readFile(cockpitStylePath, "utf8");
  const source = await readFile(appPath, "utf8");
  const html = await readFile(indexPath, "utf8");

  assert.match(html, /cockpit\.css\?v=20261026a/);
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.no-trade-funnel summary,\s*\.came-close summary,\s*\.came-close-head\s*\{[^}]*flex-direction:\s*column;/s,
  );
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.no-trade-funnel summary strong[\s\S]*overflow-wrap:\s*anywhere;/s,
  );
  assert.ok(
    source.includes('<details class="no-trade-funnel" open>'),
    "HOLD funnel details markup stays",
  );
  assert.ok(
    source.includes('<div class="no-trade-stages">'),
    "HOLD funnel stage grid markup stays",
  );
  assert.match(styles, /\.positions-toolbar\s*\{[^}]*flex-direction:\s*row;/s);
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.positions-toolbar\s*\{[^}]*flex-direction:\s*column;/s,
  );
  assert.ok(
    source.match(/let className = "sync-line positions-sync-status";/g)?.length >= 2,
    "board and Tradier status rendering must preserve the sync-line layout class",
  );
});
