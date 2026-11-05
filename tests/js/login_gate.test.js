import test from "node:test";
import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";

const htmlPath = new URL("../../web/login/index.html", import.meta.url);
const cssPath = new URL("../../web/login-gate.css", import.meta.url);

test("Harbor Gate keeps login contracts and adds a show-password control", async () => {
  const html = await readFile(htmlPath, "utf8");
  const css = await readFile(cssPath, "utf8");
  assert.ok(html.includes('id="login-form"'));
  assert.ok(html.includes('id="username"'));
  assert.ok(html.includes('id="password"'));
  assert.ok(html.includes('id="error"'));
  assert.ok(html.includes('autocomplete="current-password"'));
  assert.ok(html.includes('id="toggle-password"'));
  assert.ok(html.includes('aria-controls="password"'));
  assert.ok(html.includes("function setPasswordVisible"));
  assert.ok(html.includes('fetch("/api/login"'));
  assert.ok(
    html.includes("username: document.getElementById(\"username\").value"),
    "login payload must still send username",
  );
  assert.ok(
    html.includes("password: document.getElementById(\"password\").value"),
    "login payload must still send password",
  );
  assert.match(html, /body:\s*JSON\.stringify\(payload\)/);
  assert.ok(css.includes(".login-page #toggle-password"));
  assert.match(css, /#toggle-password[\s\S]*min-height:\s*44px/);
  assert.match(css, /\.login-page \.field input[\s\S]*min-height:\s*44px/);
  assert.match(css, /\.login-page \.skip-link[\s\S]*min-height:\s*44px/);
  assert.ok(html.includes('class="skip-link"'));
  assert.ok(html.includes('href="#login-form"'));
  assert.match(html, /login-gate\.css\?v=20261028a/);
});
