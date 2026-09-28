import assert from "node:assert/strict";
import test from "node:test";
import { readSessionJson } from "../../web/session-response.js";
import { onRequest } from "../../functions/_middleware.js";

for (const path of ["/data/latest_run.json", "/api/tradier/account"]) {
  test(`expired session returns JSON 401 for ${path}`, async () => {
    const response = await onRequest({request: new Request(`https://example.com${path}`), env: {}, next() { throw Error("protected content reached"); }});
    assert.equal(response.status, 401);
    assert.equal(response.headers.get("cache-control"), "no-store");
    assert.match((await response.json()).error, /Session expired/);
  });
}
test("page navigation still redirects to login", async () => {
  const response = await onRequest({request: new Request("https://example.com/"), env: {}});
  assert.equal(response.status, 302);
  assert.match(response.headers.get("location"), /login/);
});
test("refresh redirects expired sessions preserving page destination", async () => {
  let destination;
  const location = {pathname: "/", search: "?view=board", hash: "#signal", replace(value) {destination = value;}};
  for (const response of [Response.json({}, {status:401}), {status:200, redirected:true, url:"https://example.com/login/?next=x"}]) {
    await assert.rejects(readSessionJson(response, "Board", location), /Session expired/);
    assert.equal(destination, "/login/?next=%2F%3Fview%3Dboard%23signal");
  }
});
test("valid snapshots parse; upstream HTML errors do not trigger login", async () => {
  const location = { replace() {throw Error("unexpected login");}};
  assert.deepEqual(await readSessionJson(Response.json({council:{}}), "Board", location), {council:{}});
  await assert.rejects(readSessionJson(new Response("<html>", {status:502}), "Board", location), /502/);
  await assert.rejects(readSessionJson(new Response("<html>"), "Board", location), /unexpected response/);
});
test("broker validation errors remain available to the order UI", async () => {
  const response = Response.json({error:"Council gate blocked"}, {status:400});
  assert.deepEqual(await readSessionJson(response, "Broker", {}, true), {error:"Council gate blocked"});
});
