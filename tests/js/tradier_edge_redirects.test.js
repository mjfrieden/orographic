import assert from "node:assert/strict";
import test from "node:test";
import { fetchBrokerStatus, tradierRequest } from "../../functions/_lib/tradier.js";

const env = {
  TRADIER_ACCESS_TOKEN: "mock-token",
  TRADIER_ACCOUNT_ID: "mock-account",
  TRADIER_SANDBOX_MODE: "true",
};

test("account reads use a Cloudflare-supported redirect mode without following redirects", async (t) => {
  const calls = [];
  t.mock.method(globalThis, "fetch", async (url, init) => {
    // Cloudflare's edge runtime only implements follow and manual.
    if (!["follow", "manual"].includes(init.redirect)) {
      throw new TypeError('Invalid redirect value, must be one of "follow" or "manual"');
    }
    assert.equal(init.redirect, "manual");
    assert.equal(init.method, "GET");
    assert.equal(new URL(url).origin, "https://sandbox.tradier.com");
    calls.push(url);
    return Response.json({});
  });
  const status = await fetchBrokerStatus(env);
  assert.equal(status.configured, true);
  assert.equal(calls.length, 4);
});

test("every redirect status is rejected without reading or following its destination", async (t) => {
  for (let status = 300; status < 400; status++) {
    await t.test(`HTTP ${status}`, async (st) => {
      let calls = 0;
      st.mock.method(globalThis, "fetch", async (url, init) => {
        calls++;
        assert.equal(new URL(url).origin, "https://sandbox.tradier.com");
        assert.equal(init.redirect, "manual");
        return {
          status,
          headers: new Headers({ Location: "https://untrusted.example/redirect" }),
          text() { throw new Error("Redirect body must not be consumed"); },
        };
      });
      await assert.rejects(
        tradierRequest(env, { path: "/user/profile" }),
        /unexpected redirect.*blocked/i,
      );
      assert.equal(calls, 1, "No follow-up request or retry may be made");
    });
  }
});
