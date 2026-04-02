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

function regimeToneClass(value) {
  const normalized = String(value).toLowerCase();
  if (normalized === "risk_on") {
    return "is-call";
  }
  if (normalized === "risk_off") {
    return "is-put";
  }
  return "is-neutral";
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

function sentenceList(notes, fallback) {
  if (Array.isArray(notes) && notes.length) {
    return notes.join(". ");
  }
  return fallback;
}

function boardMessage(title, body, extra = "") {
  return `
    <div class="summary-box board-message">
      <div class="summary-item">
        <span class="summary-label">${title}</span>
        <span class="summary-value">${body}</span>
      </div>
      ${extra ? `<div class="summary-item"><span class="summary-value">${extra}</span></div>` : ""}
    </div>
  `;
}

function cardFor(candidate, template, options = {}) {
  const node = template.content.firstElementChild.cloneNode(true);
  const tone = toneClass(candidate.option_type);
  node.classList.add(tone);
  if (options.featured) {
    node.classList.add("is-featured");
  }
  node.querySelector(".crest-letter").textContent = candidate.symbol.slice(0, 3);
  node.querySelector(".crest-side").textContent = candidate.option_type.toUpperCase();
  node.querySelector(".card-kicker").textContent = options.kicker || "Harbor Contract";
  node.querySelector(".title").textContent = `${candidate.symbol} ${candidate.strike} ${candidate.expiry}`;
  node.querySelector(".contract").textContent = candidate.contract_symbol;
  node.querySelector(".side").textContent = candidate.option_type.toUpperCase();
  node.querySelector(".meta").textContent =
    `Premium ${money(candidate.premium)} | Spread ${pct(candidate.spread_pct)} | Cost ${money(candidate.contract_cost)}`;
  node.querySelector(".notes").textContent =
    sentenceList(candidate.notes, options.featured ? "No extra council notes on the featured contract." : "No extra notes.");

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

function rowHtml(title, body, tone, slotLabel) {
  return `<div class="mini-row ${tone}"><span class="mini-slot">${slotLabel}</span><strong>${title}</strong><span class="muted">${body}</span></div>`;
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
  const liveBoardCards = payload.council.live_board;
  const shadowBoardCards = payload.council.shadow_board;
  const featuredCandidate = liveBoardCards[0] || null;
  const reserveCandidates = liveBoardCards.slice(1);

  document.getElementById("generated-at").textContent = formatTimestamp(payload.generated_at_utc);

  const regimePill = document.getElementById("regime-pill");
  regimePill.textContent = `${payload.regime.mode.replace("_", " ").toUpperCase()} | bias ${payload.regime.bias}`;
  regimePill.classList.add(regimeToneClass(payload.regime.mode));
  document.body.dataset.regime = payload.regime.mode;
  document.body.dataset.boardState = payload.council.abstain ? "abstain" : "active";

  const template = document.getElementById("card-template");
  const featuredSlot = document.getElementById("featured-slot");
  const liveBoard = document.getElementById("live-board");
  const shadowBoard = document.getElementById("shadow-board");
  const boardStatus = document.getElementById("board-status");
  const boardStatusNote = document.getElementById("board-status-note");

  if (featuredCandidate) {
    featuredSlot.appendChild(
      cardFor(featuredCandidate, template, {
        featured: true,
        kicker: "Captain's Pick",
      })
    );
    boardStatus.textContent = "Harbor live";
    boardStatusNote.textContent = sentenceList(
      payload.council.summary.notes,
      "Council opened the harbor for a live contract."
    );
  } else {
    featuredSlot.innerHTML = boardMessage(
      payload.council.abstain ? "Council Abstained" : "No Live Contract",
      "No contract cleared the featured live threshold for this run.",
      sentenceList(
        payload.council.summary.notes,
        "Council kept the featured stage empty rather than forcing a low-conviction contract."
      )
    );
    boardStatus.textContent = payload.council.abstain ? "Council abstained" : "Live board quiet";
    boardStatusNote.textContent = sentenceList(
      payload.council.summary.notes,
      "No live contracts cleared the threshold."
    );
  }

  if (!reserveCandidates.length) {
    liveBoard.innerHTML = boardMessage(
      "Reserve Lane",
      featuredCandidate ? "No additional live contracts are waiting on deck." : "The reserve lane is quiet until Council clears a live contract."
    );
  } else {
    reserveCandidates.forEach((candidate) =>
      liveBoard.appendChild(
        cardFor(candidate, template, {
          kicker: "On Deck",
        })
      )
    );
  }

  if (!shadowBoardCards.length) {
    shadowBoard.innerHTML = boardMessage("Shadow Lane", "No shadow contracts are available for this run.");
  } else {
    shadowBoardCards.forEach((candidate) =>
      shadowBoard.appendChild(
        cardFor(candidate, template, {
          kicker: "Shadow Preview",
        })
      )
    );
  }

  document.getElementById("reserve-count").textContent = String(reserveCandidates.length);
  document.getElementById("live-count-hud").textContent = integer(payload.council.summary.live_count);
  document.getElementById("shadow-count-hud").textContent = integer(payload.council.summary.shadow_count);
  document.getElementById("regime-source").textContent = payload.regime.source_symbol || "No source";
  document.getElementById("regime-source-note").textContent = sentenceList(
    payload.regime.notes,
    `Watching ${payload.regime.source_symbol || "the market"} for ${payload.regime.mode.replace("_", " ")} tides.`
  );

  const scoutBoard = document.getElementById("scout-board");
  scoutBoard.innerHTML = payload.scout_signals
    .slice(0, 5)
    .map(
      (row, index) =>
        rowHtml(
          `${row.symbol} ${row.direction.toUpperCase()} | scout ${row.scout_score}`,
          `m5 ${pct(row.momentum_5d)} | m20 ${pct(row.momentum_20d)} | RSI ${row.rsi_14}`,
          toneClass(row.direction),
          `Scout ${String(index + 1).padStart(2, "0")}`
        )
    )
    .join("");

  const forgeBoard = document.getElementById("forge-board");
  forgeBoard.innerHTML = payload.forge_candidates
    .slice(0, 5)
    .map(
      (row, index) =>
        rowHtml(
          `${row.symbol} ${row.option_type.toUpperCase()} | forge ${row.forge_score}`,
          `premium ${money(row.premium)} | exp ${pct(row.expected_return_pct)} | OI ${integer(row.open_interest)}`,
          toneClass(row.option_type),
          `Forge ${String(index + 1).padStart(2, "0")}`
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
