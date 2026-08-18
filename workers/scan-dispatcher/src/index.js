const GITHUB_API_VERSION = "2022-11-28";
const CHICAGO_TIME_ZONE = "America/Chicago";
const CHICAGO_SCAN_HOURS = new Set([9, 12, 15]);
const OUTCOME_CAPTURE_START_MINUTE = 8 * 60 + 25;
const OUTCOME_CAPTURE_END_MINUTE = 15 * 60 + 10;

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

export function isChicagoOutcomeCaptureSlot(scheduledTime) {
  const { weekday, hour, minute } = chicagoTimeParts(scheduledTime);
  const minuteOfDay = Number(hour) * 60 + Number(minute);
  return (
    ["Mon", "Tue", "Wed", "Thu", "Fri"].includes(weekday) &&
    Number(minute) % 5 === 0 &&
    minuteOfDay >= OUTCOME_CAPTURE_START_MINUTE &&
    minuteOfDay <= OUTCOME_CAPTURE_END_MINUTE
  );
}

export function dispatchUrl(env, workflow = env.GITHUB_WORKFLOW || "orographic_scan.yml") {
  const owner = env.GITHUB_OWNER || "mjfrieden";
  const repo = env.GITHUB_REPO || "orographic";
  return `https://api.github.com/repos/${owner}/${repo}/actions/workflows/${workflow}/dispatches`;
}

export async function dispatchWorkflow(env, workflow, fetchImpl = fetch, inputs = undefined) {
  if (!env.GITHUB_DISPATCH_TOKEN) {
    throw new Error("GITHUB_DISPATCH_TOKEN is not configured");
  }

  const response = await fetchImpl(dispatchUrl(env, workflow), {
    method: "POST",
    headers: {
      Accept: "application/vnd.github+json",
      Authorization: `Bearer ${env.GITHUB_DISPATCH_TOKEN}`,
      "Content-Type": "application/json",
      "User-Agent": "orographic-cloudflare-cron",
      "X-GitHub-Api-Version": GITHUB_API_VERSION,
    },
    body: JSON.stringify({
      ref: env.GITHUB_REF || "main",
      ...(inputs ? { inputs } : {}),
    }),
  });

  if (!response.ok) {
    const detail = (await response.text()).slice(0, 500);
    throw new Error(`GitHub workflow dispatch failed (${response.status}): ${detail}`);
  }

  return { status: response.status, dispatchedAt: new Date().toISOString() };
}

export function dispatchScan(env, fetchImpl = fetch) {
  return dispatchWorkflow(env, env.GITHUB_WORKFLOW || "orographic_scan.yml", fetchImpl);
}

export function dispatchOutcomeCapture(env, fetchImpl = fetch, scheduledTime = undefined) {
  return dispatchWorkflow(
    env,
    env.GITHUB_OUTCOME_WORKFLOW || "orographic_outcome_capture.yml",
    fetchImpl,
    scheduledTime
      ? { scheduled_time_utc: scheduledTime, scheduler: "cloudflare_cron" }
      : undefined
  );
}

export default {
  async fetch() {
    return new Response("Not Found", { status: 404 });
  },

  async scheduled(controller, env, ctx) {
    const scheduledTime = new Date(controller.scheduledTime).toISOString();
    const tasks = [];
    if (isChicagoScanSlot(controller.scheduledTime)) {
      tasks.push(
        dispatchScan(env).then((result) => {
          console.log("Orographic scan dispatched", {
            cron: controller.cron,
            scheduledTime,
            ...result,
          });
        })
      );
    }
    if (isChicagoOutcomeCaptureSlot(controller.scheduledTime)) {
      tasks.push(
        dispatchOutcomeCapture(env, fetch, scheduledTime).then((result) => {
          console.log("Orographic outcome capture dispatched", {
            cron: controller.cron,
            scheduledTime,
            ...result,
          });
        })
      );
    }
    if (tasks.length === 0) {
      console.log("Orographic scan dispatcher skipped non-Chicago slot", {
        cron: controller.cron,
        scheduledTime,
      });
      return;
    }
    ctx.waitUntil(Promise.all(tasks));
  },
};
