import assert from "node:assert/strict";
import test from "node:test";
import { readFileSync } from "node:fs";

import worker, {
  dispatchOutcomeCapture,
  dispatchScan,
  dispatchCirrus,
  isCirrusScanSlot,
  dispatchUrl,
  isChicagoOutcomeCaptureSlot,
  isChicagoScanSlot,
} from "../../workers/scan-dispatcher/src/index.js";

test("recognizes Chicago scan slots across daylight saving time", () => {
  assert.equal(isChicagoScanSlot(Date.parse("2026-08-03T14:25:00Z")), true);
  assert.equal(isChicagoScanSlot(Date.parse("2026-01-05T15:25:00Z")), true);
  assert.equal(isChicagoScanSlot(Date.parse("2026-08-03T15:25:00Z")), false);
  assert.equal(isChicagoScanSlot(Date.parse("2026-08-02T14:25:00Z")), false);
});

test("scan and outcome capture slots never overlap", () => {
  for (const timestamp of [
    "2026-08-03T14:15:00Z",
    "2026-08-03T14:25:00Z",
    "2026-01-05T15:15:00Z",
    "2026-01-05T15:25:00Z",
  ]) {
    const scheduledTime = Date.parse(timestamp);
    assert.equal(
      isChicagoScanSlot(scheduledTime) && isChicagoOutcomeCaptureSlot(scheduledTime),
      false,
      timestamp
    );
  }
});

test("captures every ten minutes across Chicago market hours and DST", () => {
  for (const [date, offset] of [["2026-08-03", 5], ["2026-01-05", 6]]) {
    for (let minute = 0; minute < 24 * 60; minute++) {
      const utc = Date.parse(`${date}T00:00:00Z`) + minute * 60000;
      const localMinute = minute - offset * 60;
      const expected = localMinute >= 510 && localMinute <= 930 && localMinute % 10 === 0;
      assert.equal(isChicagoOutcomeCaptureSlot(utc), expected, new Date(utc).toISOString());
    }
  }
  assert.equal(isChicagoOutcomeCaptureSlot(Date.parse("2026-08-02T14:40:00Z")), false);
});

test("September 28 one-hour window has a scheduled capture before expiry", () => {
  const due = Date.parse("2026-09-28T15:30:43Z");
  const slots = Array.from({length: 15}, (_, i) => due + (i + 1) * 60000)
    .filter(isChicagoOutcomeCaptureSlot);
  assert.ok(slots.length > 0);
  assert.ok(slots[0] - due < 15 * 60000);
});

test("does not dispatch for paired UTC hours outside a Chicago scan slot", async () => {
  let waited = false;
  await worker.scheduled(
    { cron: "25 14,15 * * MON-FRI", scheduledTime: Date.parse("2026-08-03T15:25:00Z") },
    { GITHUB_DISPATCH_TOKEN: "secret" },
    { waitUntil: () => { waited = true; } }
  );

  assert.equal(waited, false);
});

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

test("dispatches the configured outcome workflow", async () => {
  let captured;
  await dispatchOutcomeCapture(
    {
      GITHUB_DISPATCH_TOKEN: "secret",
      GITHUB_OUTCOME_WORKFLOW: "capture.yml",
    },
    async (url, init) => {
      captured = { url, init };
      return new Response(null, { status: 204 });
    },
    "2026-08-03T14:15:00.000Z"
  );

  assert.match(captured.url, /workflows\/capture\.yml\/dispatches$/);
  assert.deepEqual(JSON.parse(captured.init.body).inputs, {
    scheduled_time_utc: "2026-08-03T14:15:00.000Z",
    scheduler: "cloudflare_cron",
  });
});

test("dispatches a scan at its twenty-five-minute slot", async () => {
  const urls = [];
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async (url) => {
    urls.push(url);
    return new Response(null, { status: 204 });
  };
  try {
    let pending;
    await worker.scheduled(
      { cron: "*", scheduledTime: Date.parse("2026-08-03T14:25:00Z") },
      { GITHUB_DISPATCH_TOKEN: "secret" },
      { waitUntil: (promise) => { pending = promise; } }
    );
    await pending;
  } finally {
    globalThis.fetch = originalFetch;
  }

  assert.equal(urls.length, 1);
  assert.match(urls[0], /orographic_scan\.yml/);
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

test("deployed cron includes every accepted capture and scan minute", () => {
  const config = readFileSync(new URL("../../workers/scan-dispatcher/wrangler.jsonc", import.meta.url), "utf8");
  assert.match(config, /"0,10,20,25,30,40,50 13-22 \* \* MON-FRI"/);
  for (const date of ["2026-08-03", "2026-01-05"]) {
    for (let minute = 0; minute < 1440; minute++) {
      const time = Date.parse(`${date}T00:00:00Z`) + minute * 60000;
      const capture = isChicagoOutcomeCaptureSlot(time);
      const scan = isChicagoScanSlot(time);
      assert.equal(capture && scan, false);
      if (capture || scan) {
        assert.ok([0, 10, 20, 25, 30, 40, 50].includes(minute % 60));
        assert.ok(Math.floor(minute / 60) >= 13 && Math.floor(minute / 60) <= 22);
      }
    }
  }
});

test("Cirrus dispatch runs only at noon Chicago across DST", async () => {
  assert.equal(isCirrusScanSlot(Date.parse("2026-09-30T17:07:00Z")), true);
  assert.equal(isCirrusScanSlot(Date.parse("2026-01-05T18:07:00Z")), true);
  assert.equal(isCirrusScanSlot(Date.parse("2026-09-30T14:07:00Z")), false);
  let request;
  await dispatchCirrus({CIRRUS_DISPATCH_TOKEN:"test-token"}, async (url, options) => {
    request={url,options}; return new Response(null,{status:204});
  });
  assert.match(request.url, /repos\/mjfrieden\/Cirrus\/actions\/workflows\/daily-cirrus.yml\/dispatches$/);
  assert.equal(request.options.headers.Authorization,"Bearer test-token");
});
