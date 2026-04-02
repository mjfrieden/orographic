const source = "./data/latest_run.json";

function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function cardFor(candidate, template) {
  const node = template.content.firstElementChild.cloneNode(true);
  node.querySelector(".title").textContent = `${candidate.symbol} ${candidate.strike} ${candidate.expiry}`;
  node.querySelector(".side").textContent = candidate.option_type.toUpperCase();
  node.querySelector(".meta").textContent =
    `${candidate.contract_symbol} | premium $${candidate.premium.toFixed(2)} | spread ${pct(candidate.spread_pct)}`;
  node.querySelector(".notes").textContent =
    candidate.notes?.join(". ") || "No extra notes.";

  const stats = node.querySelector(".stats");
  const entries = [
    ["Forge Score", candidate.forge_score.toFixed(2)],
    ["Exp Return", pct(candidate.expected_return_pct)],
    ["Breakeven", pct(candidate.breakeven_move_pct)],
    ["Open Interest", String(candidate.open_interest)],
  ];
  for (const [label, value] of entries) {
    const stat = document.createElement("div");
    stat.className = "stat";
    stat.innerHTML = `<span class="stat-label">${label}</span><span class="stat-value">${value}</span>`;
    stats.appendChild(stat);
  }
  return node;
}

function rowHtml(title, body) {
  return `<div class="mini-row"><strong>${title}</strong><span class="muted">${body}</span></div>`;
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

  document.getElementById("generated-at").textContent = payload.generated_at_utc || "No timestamp";
  document.getElementById("regime-pill").textContent =
    `${payload.regime.mode.toUpperCase()} | bias ${payload.regime.bias}`;

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
          `m5 ${pct(row.momentum_5d)} | m20 ${pct(row.momentum_20d)} | RSI ${row.rsi_14}`
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
          `premium $${row.premium.toFixed(2)} | exp ${pct(row.expected_return_pct)} | OI ${row.open_interest}`
        )
    )
    .join("");

  const councilSummary = document.getElementById("council-summary");
  councilSummary.innerHTML = `
    <div><strong>Abstain:</strong> ${payload.council.abstain}</div>
    <div><strong>Live count:</strong> ${payload.council.summary.live_count}</div>
    <div><strong>Shadow count:</strong> ${payload.council.summary.shadow_count}</div>
    <div><strong>Candidate count:</strong> ${payload.council.summary.candidate_count}</div>
    <div><strong>Notes:</strong> ${payload.council.summary.notes.join(" ")}</div>
  `;
}

main().catch((error) => {
  document.getElementById("live-board").innerHTML = `<div class="summary-box">Failed to load latest run: ${error}</div>`;
});
