import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const cockpitStylePath = new URL("../../web/cockpit.css", import.meta.url);
const appPath = new URL("../../web/app.js", import.meta.url);
const indexPath = new URL("../../web/index.html", import.meta.url);

test("Book party frames keep quantity and action on a 720px phone", async () => {
  const styles = await readFile(cockpitStylePath, "utf8");
  const source = await readFile(appPath, "utf8");
  const html = await readFile(indexPath, "utf8");

  assert.match(html, /cockpit\.css\?v=20261029-mobile/);
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.cockpit-position-row\s*\{[^}]*grid-template-areas:/s,
  );
  assert.doesNotMatch(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*?\.position-action,\s*\.position-size\s*\{\s*display:\s*none;/s,
  );
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.cockpit-position-row \.position-size\s*\{[^}]*display:\s*block;/s,
  );
  assert.match(
    styles,
    /@media \(max-width:\s*720px\)[\s\S]*\.cockpit-position-row \.position-action\s*\{[^}]*display:\s*block;/s,
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
  assert.ok(
    source.includes(
      '<div class="position-size"><strong>${integer(position.quantity)}</strong><span>contracts</span></div>',
    ),
    "party-row quantity markup stays",
  );
  assert.ok(
    source.includes(
      '<div class="position-action ${warning ? "is-warning" : ""}"><strong>${action}</strong><span>${escapeHtml(positionMarkMeta(position).label)}</span></div>',
    ),
    "party-row action markup stays",
  );
});
