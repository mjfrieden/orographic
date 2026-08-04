const GITHUB_API_VERSION = "2022-11-28";
const CHICAGO_TIME_ZONE = "America/Chicago";
const CHICAGO_SCAN_HOURS = new Set([9, 12, 15]);

function chicagoTimeParts(scheduledTime) {
  const parts = new Intl.DateTimeFormat("en-US", {
    timeZone: CHICAGO_TIME_ZONE,
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hourCycle: "h23",
  }).formatToParts(new Date(scheduledTime));

  return Object.fromEntries(
    parts
      .filter(({ type }) => type !== "literal")
      .map(({ type, value }) => [type, value])
  );
}

export function isChicagoScanSlot(scheduledTime) {
  const { weekday, hour, minute } = chicagoTimeParts(scheduledTime);
  return (
    ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(weekday) &&
    minute === "07" &&
    CHICAGO_SCAN_HOURS.has(Number(hour))
  );
}

export function dispatchUrl(env) {
  const owner = env.GITHUB_OWNER || "mjfrieden";
  const repo = env.GITHUB_REPO || "orographic";
  const workflow = env.GITHUB_WORKFLOW || "orographic_scan.yml";
  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

export async function dispatchScan(env, fetchImpl = fetch) {
  if (!env.GITHUB_DISPATCH_TOKEN) {
    throw new Error("GITHUB_DISPATCH_TOKEN is not configured");
  }

  const response = await fetchImpl(dispatchUrl(env), {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "orographic-cloudflare-cron",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({ ref: env.GITHUB_REF || "main" }),
  });

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub workflow dispatch failed (${response.status}): ${detail}`);
  }

  return { status: response.status, dispatchedAt: new Date().toISOString() };
}

export default {
  async fetch() {
    return new Response("Not Found", { status: 404 });
  },

  async scheduled(controller, env, ctx) {
    const scheduledTime = new Date(controller.scheduledTime).toISOString();
    if (!isChicagoScanSlot(controller.scheduledTime)) {
      console.log("Orographic scan dispatcher skipped non-Chicago slot", {
        cron: controller.cron,
        scheduledTime,
      });
      return;
    }

    ctx.waitUntil(
      dispatchScan(env).then((result) => {
        console.log("Orographic scan dispatched", {
          cron: controller.cron,
          scheduledTime,
          ...result,
        });
      })
    );
  },
};
