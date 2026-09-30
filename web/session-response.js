// Shared by data refreshes and broker responses; never parse login HTML as JSON.
export async function readSessionJson(response, source, location = globalThis.location, allowHttpError = false) {
  const redirectedToLogin = response.redirected &&
    new URL(response.url).pathname.replace(/\/$/, "") === "/login";
  if (response.status === 401 || redirectedToLogin) {
    const next = `${location.pathname}${location.search}${location.hash}`;
    location.replace(`/login/?next=${encodeURIComponent(next)}`);
    throw new Error("Session expired. Please sign in again.");
  }
  if (!response.ok && !allowHttpError) throw new Error(`${source} unavailable (${response.status})`);
  if (!(response.headers.get("content-type") || "").includes("json")) {
    throw new Error(`${source} returned an unexpected response. Please try again.`);
  }
  return response.json();
}
