import assert from "node:assert/strict";
import test from "node:test";
import { isPublicPath } from "../../functions/_lib/public_paths.js";

test("signed-out Harbor Gate can load login chrome", () => {
  for (const path of [
    "/login",
    "/login/",
    "/styles.css",
    "/login-gate.css",
    "/harbor-scene.css",
    "/sigils.css",
    "/harbor-hero.png",
    "/assets/orographic-mark.png",
    "/api/login",
  ]) {
    assert.equal(isPublicPath(path), true, path);
  }
});

test("signed-out visitors cannot read the cockpit or research data", () => {
  for (const path of ["/", "/app.js", "/cockpit.css", "/data/latest_run.json", "/admin/"]) {
    assert.equal(isPublicPath(path), false, path);
  }
});
