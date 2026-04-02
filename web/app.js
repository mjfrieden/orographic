const source = "./data/latest_run.json";

function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `$${Number(value).toFixed(2)}`;
}

function integer(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toLocaleString();
}

function toneClass(value) {
  return String(value).toLowerCase() === "call" ? "is-call" : "is-put";
}

function formatTimestamp(value) {
  if (!value) {
    return "No timestamp";
  }
  const date = new Date(value);
  if (Number.isNaN(date.getTime())) {
    return value;
  }
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
    timeZoneName: "short",
  }).format(date);
}

function cardFor(candidate, template) {
  const node = template.content.firstElementChild.cloneNode(true);
  const tone = toneClass(candidate.option_type);
  node.classList.add(tone);
  node.querySelector(".crest-letter").textContent = candidate.symbol.slice(0, 3);
  node.querySelector(".crest-side").textContent = candidate.option_type.toUpperCase();
  node.querySelector(".title").textContent = `${candidate.symbol} ${candidate.strike} ${candidate.expiry}`;
  node.querySelector(".contract").textContent = candidate.contract_symbol;
  node.querySelector(".side").textContent = candidate.option_type.toUpperCase();
  node.querySelector(".meta").textContent =
    `Premium ${money(candidate.premium)} | Spread ${pct(candidate.spread_pct)} | Cost ${money(candidate.contract_cost)}`;
  node.querySelector(".notes").textContent =
    candidate.notes?.join(". ") || "No extra notes.";

  const stats = node.querySelector(".stats");
  const entries = [
    ["Forge Score", candidate.forge_score.toFixed(2)],
    ["Exp Return", pct(candidate.expected_return_pct)],
    ["Breakeven", pct(candidate.breakeven_move_pct)],
    ["Open Interest", integer(candidate.open_interest)],
  ];
  for (const [label, value] of entries) {
    const stat = document.createElement("div");
    stat.className = "stat";
    stat.innerHTML = `<span class="stat-label">${label}</span><span class="stat-value">${value}</span>`;
    stats.appendChild(stat);
  }
  return node;
}

function rowHtml(title, body, tone) {
  return `<div class="mini-row ${tone}"><strong>${title}</strong><span class="muted">${body}</span></div>`;
}

function summaryItemHtml(label, value) {
  return `<div class="summary-item"><span class="summary-label">${label}</span><span class="summary-value">${value}</span></div>`;
}

async function loadSession() {
  const response = await fetch("/api/session", { cache: "no-store" });
  if (!response.ok) {
    throw new Error(`session request failed with ${response.status}`);
  }
  return response.json();
}

function bindLogout() {
  const logoutButton = document.getElementById("logout-btn");
  logoutButton.addEventListener("click", async () => {
    logoutButton.disabled = true;
    logoutButton.textContent = "Signing out...";
    try {
      await fetch("/api/logout", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
      });
      window.location.href = "/login";
    } catch (error) {
      logoutButton.disabled = false;
      logoutButton.textContent = "Log Out";
      document.getElementById("session-user").textContent = `Logout failed: ${error.message}`;
    }
  });
}

async function main() {
  const sessionPayload = await loadSession();
  const userLabel = document.getElementById("session-user");
  if (sessionPayload.authenticated && sessionPayload.session) {
    userLabel.textContent =
      `${sessionPayload.session.username} | ${sessionPayload.session.role.toUpperCase()} access`;
  } else {
    userLabel.textContent = "Session unavailable";
  }
  bindLogout();

  const response = await fetch(source, { cache: "no-store" });
  const payload = await response.json();

  document.getElementById("generated-at").textContent = formatTimestamp(payload.generated_at_utc);

  const regimePill = document.getElementById("regime-pill");
  regimePill.textContent = `${payload.regime.mode.replace("_", " ").toUpperCase()} | bias ${payload.regime.bias}`;
  regimePill.classList.add(payload.regime.mode === "risk_on" ? "is-call" : "is-put");
  document.body.dataset.regime = payload.regime.mode;

  const template = document.getElementById("card-template");
  const liveBoard = document.getElementById("live-board");
  const shadowBoard = document.getElementById("shadow-board");

  if (!payload.council.live_board.length) {
    liveBoard.innerHTML = `<div class="summary-box">Council abstained. No live contract cleared the threshold.</div>`;
  } else {
    payload.council.live_board.forEach((candidate) => liveBoard.appendChild(cardFor(candidate, template)));
  }

  if (!payload.council.shadow_board.length) {
    shadowBoard.innerHTML = `<div class="summary-box">No shadow contracts available.</div>`;
  } else {
    payload.council.shadow_board.forEach((candidate) => shadowBoard.appendChild(cardFor(candidate, template)));
  }

  const scoutBoard = document.getElementById("scout-board");
  scoutBoard.innerHTML = payload.scout_signals
    .slice(0, 5)
    .map(
      (row) =>
        rowHtml(
          `${row.symbol} ${row.direction.toUpperCase()} | scout ${row.scout_score}`,
          `m5 ${pct(row.momentum_5d)} | m20 ${pct(row.momentum_20d)} | RSI ${row.rsi_14}`,
          toneClass(row.direction)
        )
    )
    .join("");

  const forgeBoard = document.getElementById("forge-board");
  forgeBoard.innerHTML = payload.forge_candidates
    .slice(0, 5)
    .map(
      (row) =>
        rowHtml(
          `${row.symbol} ${row.option_type.toUpperCase()} | forge ${row.forge_score}`,
          `premium ${money(row.premium)} | exp ${pct(row.expected_return_pct)} | OI ${integer(row.open_interest)}`,
          toneClass(row.option_type)
        )
    )
    .join("");

  const councilSummary = document.getElementById("council-summary");
  councilSummary.innerHTML = [
    summaryItemHtml("Abstain", payload.council.abstain ? "Yes" : "No"),
    summaryItemHtml("Live Count", integer(payload.council.summary.live_count)),
    summaryItemHtml("Shadow Count", integer(payload.council.summary.shadow_count)),
    summaryItemHtml("Candidate Count", integer(payload.council.summary.candidate_count)),
    summaryItemHtml("Notes", payload.council.summary.notes.join(" ") || "No extra council notes."),
  ].join("");
}

main().catch((error) => {
  document.getElementById("live-board").innerHTML = `<div class="summary-box">Failed to load latest run: ${error}</div>`;
});
