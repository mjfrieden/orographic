import assert from "node:assert/strict";
import test from "node:test";

import { dispatchScan, dispatchUrl } from "../../workers/scan-dispatcher/src/index.js";

test("builds the configured GitHub workflow URL", () => {
  assert.equal(
    dispatchUrl({ GITHUB_OWNER: "owner", GITHUB_REPO: "repo", GITHUB_WORKFLOW: "scan.yml" }),
    "https://api.github.com/repos/owner/repo/actions/workflows/scan.yml/dispatches"
  );
});

test("dispatches the main workflow with authenticated GitHub headers", async () => {
  let captured;
  const result = await dispatchScan(
    { GITHUB_DISPATCH_TOKEN: "secret", GITHUB_REF: "main" },
    async (url, init) => {
      captured = { url, init };
      return new Response(null, { status: 204 });
    }
  );

  assert.equal(result.status, 204);
  assert.equal(captured.init.method, "POST");
  assert.equal(captured.init.headers.Authorization, "Bearer secret");
  assert.deepEqual(JSON.parse(captured.init.body), { ref: "main" });
});

test("fails clearly when the dispatch credential is absent", async () => {
  await assert.rejects(dispatchScan({}, async () => new Response(null, { status: 204 })), {
    message: "GITHUB_DISPATCH_TOKEN is not configured",
  });
});

test("surfaces GitHub API failures", async () => {
  await assert.rejects(
    dispatchScan(
      { GITHUB_DISPATCH_TOKEN: "secret" },
      async () => new Response("forbidden", { status: 403 })
    ),
    /GitHub workflow dispatch failed \(403\): forbidden/
  );
});
