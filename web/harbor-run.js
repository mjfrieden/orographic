const ARENA_WIDTH = 960;
const ARENA_HEIGHT = 540;
const FIXED_STEP_MS = 1000 / 60;
const PLAYER_SPEED = 270;
const PLAYER_RADIUS = 16;
const PULSE_RADIUS = 128;
const PULSE_COOLDOWN_SECONDS = 1.15;
const MAX_HULL = 100;
const READY_CHARGE = 100;
const MATCH_CHARGE_GAIN = 32;
const CONTRACT_CHARGE_GAIN = 36;
const MISMATCH_CHARGE_PENALTY = 12;
const HAZARD_DAMAGE = 16;
const HAZARD_CHARGE_DRAIN = 12;
const UNARMED_INTEL_SCORE = 5;

function clamp(value, min, max) {
  return Math.min(max, Math.max(min, value));
}

function integer(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return Number(value).toLocaleString();
}

function money(value) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `$${Number(value).toFixed(2)}`;
}

function pct(value, digits = 1) {
  if (value === null || value === undefined || Number.isNaN(Number(value))) {
    return "--";
  }
  return `${(Number(value) * 100).toFixed(digits)}%`;
}

function asArray(value) {
  if (!value) {
    return [];
  }
  return Array.isArray(value) ? value : [value];
}

function sentenceList(notes, fallback) {
  if (Array.isArray(notes) && notes.length) {
    return notes.join(". ");
  }
  return fallback;
}

function optionTone(optionType) {
  return String(optionType || "").toLowerCase() === "call" ? "is-call" : "is-put";
}

function buildTradeLink(contract, quantity, price) {
  const url = new URL("https://web.tradier.com/tradelink");
  url.searchParams.set("class", "option");
  url.searchParams.set("symbol", contract.symbol);
  url.searchParams.set("option_symbol", contract.contract_symbol);
  url.searchParams.set("quantity", String(quantity));
  url.searchParams.set("side", "buy_to_open");
  url.searchParams.set("type", "limit");
  url.searchParams.set("duration", "day");
  if (price) {
    url.searchParams.set("price", Number(price).toFixed(2));
  }
  return url.toString();
}

function createArenaState() {
  return {
    mode: "intro",
    seed: 1337,
    score: 0,
    combo: 0,
    hull: MAX_HULL,
    elapsed: 0,
    spawnTimer: 0.2,
    pulseCooldown: 0,
    pulseFlash: 0,
    notice: "Capture a contract sigil, then charge it with matching intel.",
    player: {
      x: ARENA_WIDTH * 0.18,
      y: ARENA_HEIGHT * 0.5,
      r: PLAYER_RADIUS,
    },
    selectedContractSymbol: null,
    entities: [],
  };
}

function buildContractMap(payload) {
  const map = new Map();
  const push = (lane, candidate) => {
    if (!candidate?.contract_symbol) {
      return;
    }
    if (!map.has(candidate.contract_symbol) || lane === "live") {
      map.set(candidate.contract_symbol, {
        ...candidate,
        lane,
        charge: 0,
        armed: false,
        preview: null,
        lastOrder: null,
        liveQuote: null,
      });
    }
  };

  asArray(payload?.council?.live_board).forEach((candidate) => push("live", candidate));
  asArray(payload?.council?.shadow_board).forEach((candidate) => push("shadow", candidate));
  asArray(payload?.forge_candidates).forEach((candidate) => push("forge", candidate));
  return map;
}

function laneLabel(lane) {
  if (lane === "live") {
    return "Live Board";
  }
  if (lane === "shadow") {
    return "Shadow Lane";
  }
  return "Forge Deck";
}

export function mountHarborRun({ payload, sessionPayload }) {
  const refs = {
    canvas: document.getElementById("signal-arena"),
    overlay: document.getElementById("arena-overlay"),
    startButton: document.getElementById("arena-start-btn"),
    resetButton: document.getElementById("arena-reset-btn"),
    arenaScore: document.getElementById("arena-score"),
    arenaCombo: document.getElementById("arena-combo"),
    arenaNotice: document.getElementById("arena-notice"),
    selectedSymbol: document.getElementById("selected-symbol"),
    selectedLane: document.getElementById("selected-lane"),
    selectedMeta: document.getElementById("selected-meta"),
    selectedContract: document.getElementById("selected-contract"),
    selectedRisk: document.getElementById("selected-risk"),
    tradeQty: document.getElementById("trade-qty"),
    previewButton: document.getElementById("preview-order-btn"),
    submitButton: document.getElementById("submit-order-btn"),
    brokerRefreshButton: document.getElementById("broker-refresh-btn"),
    brokerMode: document.getElementById("broker-mode"),
    accountEquity: document.getElementById("account-equity"),
    buyingPower: document.getElementById("buying-power"),
    positionsCount: document.getElementById("positions-count"),
    snapshotHealth: document.getElementById("snapshot-health"),
    brokerMessage: document.getElementById("broker-message"),
    brokerWarnings: document.getElementById("broker-warnings"),
    previewSummary: document.getElementById("preview-summary"),
    ordersFeed: document.getElementById("orders-feed"),
  };

  if (!refs.canvas) {
    return null;
  }

  const context = refs.canvas.getContext("2d");
  const role = sessionPayload?.session?.role || "viewer";
  const contracts = buildContractMap(payload);
  const scouts = asArray(payload?.scout_signals);
  const state = {
    arena: createArenaState(),
    broker: {
      loading: false,
      configured: false,
      mode: "disabled",
      liveTradingEnabled: false,
      accountIdMasked: "Unavailable",
      balances: null,
      positions: [],
      orders: [],
      statusMessage: "Broker console standing by.",
      snapshot: null,
      error: null,
    },
  };
  const keys = new Set();
  let rafId = 0;
  let lastFrameTime = 0;
  let accumulatedMs = 0;

  function selectedContract() {
    return contracts.get(state.arena.selectedContractSymbol) || null;
  }

  function seededRandom() {
    state.arena.seed = (state.arena.seed * 1664525 + 1013904223) >>> 0;
    return state.arena.seed / 4294967296;
  }

  function quantityValue() {
    const maxContracts = state.broker.mode === "live" ? 1 : 3;
    const numeric = clamp(Number.parseInt(String(refs.tradeQty?.value || "1"), 10) || 1, 1, maxContracts);
    if (refs.tradeQty) {
      refs.tradeQty.value = String(numeric);
      refs.tradeQty.max = String(maxContracts);
    }
    return numeric;
  }

  function quotePrice(contract) {
    return (
      contract?.liveQuote?.ask ||
      contract?.liveQuote?.last ||
      contract?.ask ||
      contract?.premium ||
      contract?.last ||
      null
    );
  }

  function setOverlay(title, body, buttonLabel) {
    const titleNode = refs.overlay?.querySelector("h3");
    const bodyNode = refs.overlay?.querySelector(".muted");
    if (titleNode) {
      titleNode.textContent = title;
    }
    if (bodyNode) {
      bodyNode.textContent = body;
    }
    if (refs.startButton) {
      refs.startButton.textContent = buttonLabel;
    }
  }

  function setNotice(message) {
    state.arena.notice = message;
    if (refs.arenaNotice) {
      refs.arenaNotice.textContent = message;
    }
  }

  function ensureSelectedContract() {
    if (state.arena.selectedContractSymbol && contracts.has(state.arena.selectedContractSymbol)) {
      return;
    }
    const first = contracts.values().next().value || null;
    state.arena.selectedContractSymbol = first?.contract_symbol || null;
  }

  function selectContract(contractSymbol, source = "board") {
    if (!contracts.has(contractSymbol)) {
      return;
    }
    state.arena.selectedContractSymbol = contractSymbol;
    const contract = selectedContract();
    if (contract) {
      setNotice(
        contract.armed
          ? `${contract.symbol} is still armed from the ${source}. Finish the charge with matching intel.`
          : `${contract.symbol} selected from the ${source}. Capture its sigil in the arena to arm the route.`
      );
    }
    syncBoardSelection();
    renderDom();
    render();
  }

  function syncBoardSelection() {
    document.querySelectorAll(".pick-card[data-contract-symbol]").forEach((card) => {
      card.classList.toggle("is-selected", card.dataset.contractSymbol === state.arena.selectedContractSymbol);
    });
  }

  function bindBoardCards() {
    document.querySelectorAll(".pick-card[data-contract-symbol]").forEach((card) => {
      const contractSymbol = card.dataset.contractSymbol;
      const handler = () => selectContract(contractSymbol, "board");
      card.addEventListener("click", handler);
      card.addEventListener("keydown", (event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault();
          handler();
        }
      });
    });
  }

  function buildContractEntity(contract) {
    return {
      id: `contract-${contract.contract_symbol}-${Math.round(state.arena.elapsed * 1000)}`,
      kind: "contract",
      symbol: contract.symbol,
      optionType: contract.option_type,
      contractSymbol: contract.contract_symbol,
      x: ARENA_WIDTH + 30,
      y: 78 + seededRandom() * (ARENA_HEIGHT - 156),
      vx: -(90 + seededRandom() * 40),
      vy: (seededRandom() - 0.5) * 30,
      radius: 24,
    };
  }

  function buildScoutEntity(signal, targetSymbol = null) {
    const symbol = targetSymbol || signal?.symbol || scouts[Math.floor(seededRandom() * Math.max(scouts.length, 1))]?.symbol || "SPY";
    const direction = signal?.direction || "call";
    return {
      id: `scout-${symbol}-${Math.round(state.arena.elapsed * 1000)}`,
      kind: "scout",
      symbol,
      optionType: direction,
      x: ARENA_WIDTH + 24,
      y: 54 + seededRandom() * (ARENA_HEIGHT - 108),
      vx: -(130 + seededRandom() * 55),
      vy: (seededRandom() - 0.5) * 46,
      radius: 15,
    };
  }

  function buildHazardEntity() {
    return {
      id: `hazard-${Math.round(state.arena.elapsed * 1000)}-${Math.round(seededRandom() * 1000)}`,
      kind: "hazard",
      symbol: "IV",
      optionType: "put",
      x: ARENA_WIDTH + 36,
      y: 64 + seededRandom() * (ARENA_HEIGHT - 128),
      vx: -(170 + seededRandom() * 60),
      vy: (seededRandom() - 0.5) * 70,
      radius: 22,
    };
  }

  function spawnEntity() {
    const contract = selectedContract();
    const activeContracts = [...contracts.values()];
    const selectedOrFirst = contract || activeContracts[Math.floor(seededRandom() * Math.max(activeContracts.length, 1))];
    const needsArming = !contract || !contract.armed;
    const roll = seededRandom();

    if (needsArming) {
      if (roll < 0.58 && selectedOrFirst) {
        state.arena.entities.push(buildContractEntity(selectedOrFirst));
      } else if (roll < 0.9) {
        const matchingScout =
          scouts.find((signal) => signal.symbol === selectedOrFirst?.symbol) || scouts[0] || null;
        state.arena.entities.push(buildScoutEntity(matchingScout, selectedOrFirst?.symbol));
      } else {
        state.arena.entities.push(buildHazardEntity());
      }
    } else if (roll < 0.22) {
      const target = activeContracts[Math.floor(seededRandom() * activeContracts.length)];
      if (target) {
        state.arena.entities.push(buildContractEntity(target));
      }
    } else if (roll < 0.8) {
      const matchingScout = scouts.find((signal) => signal.symbol === contract.symbol) || scouts[0] || null;
      state.arena.entities.push(buildScoutEntity(matchingScout, contract.symbol));
    } else {
      state.arena.entities.push(buildHazardEntity());
    }

    state.arena.entities = state.arena.entities.slice(-14);
  }

  function endRun(title, message) {
    state.arena.mode = "ended";
    refs.overlay.hidden = false;
    setOverlay(title, message, "Run Again");
  }

  function resetRun() {
    const preservedSelection = state.arena.selectedContractSymbol;
    state.arena = createArenaState();
    state.arena.selectedContractSymbol = preservedSelection;
    contracts.forEach((contract) => {
      contract.charge = 0;
      contract.armed = false;
      contract.preview = null;
      contract.lastOrder = null;
    });
    ensureSelectedContract();
    refs.overlay.hidden = false;
    setOverlay(
      "Launch The Harbor Run",
      "Capture a contract sigil, then chain matching intel to 100% charge before previewing the order.",
      "Launch Signal Run"
    );
    renderDom();
    render();
  }

  function startRun() {
    if (state.arena.mode === "running") {
      return;
    }
    if (state.arena.mode === "ended" || state.arena.mode === "intro") {
      state.arena.mode = "running";
      state.arena.entities = [];
      state.arena.spawnTimer = 0.1;
      setNotice("Raid live. Arm a contract sigil, then finish the charge with matching intel.");
    }
    refs.overlay.hidden = true;
    renderDom();
  }

  function handleHazardHit() {
    const contract = selectedContract();
    state.arena.hull = clamp(state.arena.hull - HAZARD_DAMAGE, 0, MAX_HULL);
    state.arena.combo = 0;
    state.arena.score = Math.max(0, state.arena.score - 25);
    if (contract?.armed) {
      contract.charge = clamp(contract.charge - HAZARD_CHARGE_DRAIN, 0, READY_CHARGE);
    }
    setNotice("Spread squall hit the hull. Rebuild charge before the next preview window.");
    if (state.arena.hull <= 0) {
      endRun("Hull Breached", "A volatility squall broke the skiff. Reset the raid and tighten the route.");
    }
  }

  function handleContractCapture(entity) {
    const contract = contracts.get(entity.contractSymbol);
    if (!contract) {
      return;
    }
    state.arena.selectedContractSymbol = contract.contract_symbol;
    contract.armed = true;
    contract.charge = clamp(contract.charge + CONTRACT_CHARGE_GAIN, 0, READY_CHARGE);
    state.arena.score += 40;
    state.arena.combo += 1;
    setNotice(
      contract.charge >= READY_CHARGE
        ? `${contract.symbol} is fully charged. The preview lane is live.`
        : `${contract.symbol} sigil captured. Charge is now ${contract.charge}%`
    );
  }

  function handleScoutCapture(entity) {
    const contract = selectedContract();
    if (!contract) {
      state.arena.score += 8;
      setNotice(`${entity.symbol} intel banked. Capture a contract sigil to focus the raid.`);
      return;
    }
    if (!contract.armed) {
      state.arena.score += UNARMED_INTEL_SCORE;
      state.arena.combo = 0;
      setNotice(`${entity.symbol} intel spotted. Capture the ${contract.symbol} sigil before the charge can build.`);
      return;
    }

    const matches = entity.symbol === contract.symbol;
    state.arena.score += matches ? 18 + state.arena.combo * 2 : 6;
    state.arena.combo = matches ? state.arena.combo + 1 : Math.max(0, state.arena.combo - 1);

    if (matches) {
      contract.charge = clamp(contract.charge + MATCH_CHARGE_GAIN, 0, READY_CHARGE);
      setNotice(
        contract.charge >= READY_CHARGE
          ? `${contract.symbol} is fully charged. Preview is unlocked.`
          : `${contract.symbol} charge climbed to ${contract.charge}%`
      );
    } else {
      contract.charge = clamp(contract.charge - MISMATCH_CHARGE_PENALTY, 0, READY_CHARGE);
      setNotice(`${entity.symbol} intel did not match ${contract.symbol}. Charge slipped to ${contract.charge}%`);
    }
  }

  function collectEntity(entity) {
    if (entity.kind === "hazard") {
      handleHazardHit();
      return;
    }
    if (entity.kind === "contract") {
      handleContractCapture(entity);
      return;
    }
    handleScoutCapture(entity);
  }

  function pulse() {
    if (state.arena.mode !== "running" || state.arena.pulseCooldown > 0) {
      return;
    }

    state.arena.pulseCooldown = 1.6;
    state.arena.pulseCooldown = PULSE_COOLDOWN_SECONDS;
    state.arena.pulseFlash = 0.28;

    let captured = 0;
    state.arena.entities = state.arena.entities.filter((entity) => {
      const dx = entity.x - state.arena.player.x;
      const dy = entity.y - state.arena.player.y;
      const distance = Math.hypot(dx, dy);
      if (distance <= PULSE_RADIUS) {
        collectEntity(entity);
        captured += 1;
        return false;
      }
      return true;
    });

    if (!captured) {
      setNotice("Pulse missed. Drift closer before firing the capture ring.");
    }
    renderDom();
    render();
  }

  function step(dt) {
    if (state.arena.mode !== "running") {
      return;
    }

    state.arena.elapsed += dt;
    state.arena.spawnTimer -= dt;
    state.arena.pulseCooldown = Math.max(0, state.arena.pulseCooldown - dt);
    state.arena.pulseFlash = Math.max(0, state.arena.pulseFlash - dt);

    const moveX = (keys.has("ArrowRight") || keys.has("d") ? 1 : 0) - (keys.has("ArrowLeft") || keys.has("a") ? 1 : 0);
    const moveY = (keys.has("ArrowDown") || keys.has("s") ? 1 : 0) - (keys.has("ArrowUp") || keys.has("w") ? 1 : 0);
    state.arena.player.x = clamp(state.arena.player.x + moveX * PLAYER_SPEED * dt, 42, ARENA_WIDTH - 42);
    state.arena.player.y = clamp(state.arena.player.y + moveY * PLAYER_SPEED * dt, 42, ARENA_HEIGHT - 42);

    if (state.arena.spawnTimer <= 0) {
      spawnEntity();
      state.arena.spawnTimer = 0.35 + seededRandom() * 0.55;
    }

    const survivors = [];
    for (const entity of state.arena.entities) {
      entity.x += entity.vx * dt;
      entity.y += entity.vy * dt;
      if (entity.y < entity.radius + 18 || entity.y > ARENA_HEIGHT - entity.radius - 18) {
        entity.vy *= -1;
        entity.y = clamp(entity.y, entity.radius + 18, ARENA_HEIGHT - entity.radius - 18);
      }
      if (entity.x < -entity.radius - 20) {
        if (entity.kind === "contract") {
          const contract = contracts.get(entity.contractSymbol);
          if (contract?.armed) {
            contract.charge = clamp(contract.charge - 6, 0, READY_CHARGE);
          }
        }
        continue;
      }

      const distance = Math.hypot(entity.x - state.arena.player.x, entity.y - state.arena.player.y);
      if (distance <= entity.radius + state.arena.player.r) {
        if (entity.kind === "hazard") {
          collectEntity(entity);
          continue;
        }
      }

      survivors.push(entity);
    }
    state.arena.entities = survivors;

    if (state.arena.elapsed >= 90) {
      const contract = selectedContract();
      endRun(
        "Watch Complete",
        contract && contract.charge >= READY_CHARGE
          ? `${contract.symbol} stayed armed through the full watch. Reset to raid a new route.`
          : "The watch timer expired before a full charge. Reset and tighten the capture path."
      );
    }
  }

  function drawBackground() {
    const gradient = context.createLinearGradient(0, 0, 0, ARENA_HEIGHT);
    gradient.addColorStop(0, "#08111a");
    gradient.addColorStop(0.34, "#15354e");
    gradient.addColorStop(0.68, "#f09c5b");
    gradient.addColorStop(1, "#061019");
    context.fillStyle = gradient;
    context.fillRect(0, 0, ARENA_WIDTH, ARENA_HEIGHT);

    const sun = context.createRadialGradient(ARENA_WIDTH * 0.75, 86, 10, ARENA_WIDTH * 0.75, 86, 180);
    sun.addColorStop(0, "rgba(255, 234, 175, 0.96)");
    sun.addColorStop(0.32, "rgba(241, 163, 94, 0.76)");
    sun.addColorStop(1, "rgba(241, 163, 94, 0)");
    context.fillStyle = sun;
    context.beginPath();
    context.arc(ARENA_WIDTH * 0.75, 86, 180, 0, Math.PI * 2);
    context.fill();

    context.strokeStyle = "rgba(255, 247, 220, 0.12)";
    context.lineWidth = 1;
    for (let index = 0; index < 5; index += 1) {
      const y = 82 + index * 84;
      context.beginPath();
      context.moveTo(18, y);
      context.lineTo(ARENA_WIDTH - 18, y);
      context.stroke();
    }

    context.fillStyle = "rgba(4, 9, 14, 0.84)";
    context.beginPath();
    context.moveTo(0, ARENA_HEIGHT);
    context.lineTo(0, ARENA_HEIGHT - 88);
    context.lineTo(165, ARENA_HEIGHT - 110);
    context.lineTo(252, ARENA_HEIGHT - 68);
    context.lineTo(342, ARENA_HEIGHT - 100);
    context.lineTo(430, ARENA_HEIGHT - 72);
    context.lineTo(525, ARENA_HEIGHT - 108);
    context.lineTo(610, ARENA_HEIGHT - 82);
    context.lineTo(705, ARENA_HEIGHT - 103);
    context.lineTo(800, ARENA_HEIGHT - 66);
    context.lineTo(ARENA_WIDTH, ARENA_HEIGHT - 90);
    context.lineTo(ARENA_WIDTH, ARENA_HEIGHT);
    context.closePath();
    context.fill();
  }

  function drawPlayer() {
    context.save();
    context.translate(state.arena.player.x, state.arena.player.y);
    context.fillStyle = "#f4dda4";
    context.strokeStyle = "#10273a";
    context.lineWidth = 4;
    context.beginPath();
    context.moveTo(18, 0);
    context.lineTo(-15, -13);
    context.lineTo(-4, 0);
    context.lineTo(-15, 13);
    context.closePath();
    context.fill();
    context.stroke();
    context.fillStyle = "rgba(113, 210, 209, 0.9)";
    context.fillRect(-2, -10, 6, 20);
    context.restore();

    if (state.arena.pulseFlash > 0) {
      context.strokeStyle = `rgba(113, 210, 209, ${state.arena.pulseFlash * 2.2})`;
      context.lineWidth = 3;
      context.beginPath();
      context.arc(
        state.arena.player.x,
        state.arena.player.y,
        PULSE_RADIUS * (1 - state.arena.pulseFlash * 0.45),
        0,
        Math.PI * 2
      );
      context.stroke();
    }
  }

  function drawEntity(entity) {
    const fill =
      entity.kind === "hazard"
        ? "rgba(241, 144, 114, 0.96)"
        : entity.kind === "contract"
          ? String(entity.optionType).toLowerCase() === "call"
            ? "rgba(113, 210, 209, 0.95)"
            : "rgba(244, 180, 130, 0.95)"
          : "rgba(255, 223, 160, 0.94)";
    const selected = entity.contractSymbol && entity.contractSymbol === state.arena.selectedContractSymbol;

    context.save();
    context.shadowColor = fill;
    context.shadowBlur = selected ? 26 : 16;
    context.fillStyle = fill;
    context.strokeStyle = "rgba(5, 12, 18, 0.78)";
    context.lineWidth = 3;

    if (entity.kind === "contract") {
      context.translate(entity.x, entity.y);
      context.rotate(Math.sin(state.arena.elapsed + entity.x * 0.01) * 0.2);
      context.beginPath();
      context.moveTo(0, -entity.radius);
      context.lineTo(entity.radius * 0.8, 0);
      context.lineTo(0, entity.radius);
      context.lineTo(-entity.radius * 0.8, 0);
      context.closePath();
      context.fill();
      context.stroke();
      context.fillStyle = "rgba(6, 12, 18, 0.82)";
      context.font = "700 14px Cinzel";
      context.textAlign = "center";
      context.textBaseline = "middle";
      context.fillText(entity.symbol.slice(0, 3), 0, 1);
      context.restore();
      return;
    }

    context.beginPath();
    context.arc(entity.x, entity.y, entity.radius, 0, Math.PI * 2);
    context.fill();
    context.stroke();
    context.fillStyle = "rgba(6, 12, 18, 0.86)";
    context.font = "700 11px Cinzel";
    context.textAlign = "center";
    context.textBaseline = "middle";
    context.fillText(entity.symbol.slice(0, 3), entity.x, entity.y + 1);
    context.restore();
  }

  function drawHud() {
    const contract = selectedContract();
    context.fillStyle = "rgba(8, 18, 28, 0.82)";
    context.fillRect(18, 18, 308, 92);
    context.strokeStyle = "rgba(244, 221, 164, 0.18)";
    context.strokeRect(18, 18, 308, 92);

    context.fillStyle = "#f4dda4";
    context.font = "700 15px Marcellus SC";
    context.textAlign = "left";
    context.fillText(`Score ${state.arena.score}`, 34, 44);
    context.fillText(`Combo ${state.arena.combo}`, 34, 68);
    context.fillText(`Hull ${state.arena.hull}`, 34, 92);

    context.textAlign = "right";
    context.fillText(`Pulse ${state.arena.pulseCooldown > 0 ? state.arena.pulseCooldown.toFixed(1) : "READY"}`, ARENA_WIDTH - 28, 44);
    context.fillText(`Watch ${Math.max(0, Math.ceil(90 - state.arena.elapsed))}s`, ARENA_WIDTH - 28, 68);
    context.fillText(
      contract ? `${contract.symbol} ${contract.armed ? `${contract.charge}%` : "SIGIL"}` : "No contract",
      ARENA_WIDTH - 28,
      92
    );
  }

  function render() {
    drawBackground();
    state.arena.entities.forEach(drawEntity);
    drawPlayer();
    drawHud();
  }

  async function loadBrokerStatus() {
    state.broker.loading = true;
    renderDom();
    try {
      const response = await fetch("/api/tradier/status", { cache: "no-store" });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        throw new Error(payload.error || `Broker status failed with ${response.status}`);
      }
      state.broker = {
        ...state.broker,
        ...payload.broker,
        loading: false,
        error: null,
        statusMessage: payload.broker.configured
          ? `${String(payload.broker.environment || payload.broker.mode || "sandbox").toUpperCase()} broker connected`
          : "Tradier is not configured for this environment.",
      };
    } catch (error) {
      state.broker.loading = false;
      state.broker.error = error;
      state.broker.statusMessage = String(error.message || error);
    }
    renderDom();
  }

  async function refreshQuotes() {
    if (!contracts.size) {
      return;
    }
    const symbols = [...contracts.values()]
      .map((contract) => contract.contract_symbol)
      .join(",");
    try {
      const response = await fetch(`/api/tradier/quotes?symbols=${encodeURIComponent(symbols)}`, {
        cache: "no-store",
      });
      const payload = await response.json();
      if (!response.ok || !payload.ok) {
        return;
      }
      const bySymbol = new Map(payload.quotes.map((quote) => [quote.symbol, quote]));
      contracts.forEach((contract) => {
        contract.liveQuote = bySymbol.get(contract.contract_symbol) || null;
      });
      renderDom();
    } catch {
      // keep the fallback snapshot quote
    }
  }

  function previewEligible(contract) {
    return Boolean(contract && contract.armed && contract.charge >= READY_CHARGE);
  }

  function previewMessage(contract) {
    if (!contract) {
      return "Lock a contract in the arena to stage a preview.";
    }
    const blocks = [
      `<div class="summary-item"><span class="summary-label">Target</span><span class="summary-value">${contract.symbol} ${contract.option_type.toUpperCase()}</span></div>`,
      `<div class="summary-item"><span class="summary-label">Contract</span><span class="summary-value summary-value-wrap">${contract.contract_symbol}</span></div>`,
      `<div class="summary-item"><span class="summary-label">Status</span><span class="summary-value">${contract.armed ? "Armed" : "Hunting sigil"}</span></div>`,
      `<div class="summary-item"><span class="summary-label">Charge</span><span class="summary-value">${contract.charge}%</span></div>`,
    ];
    if (contract.preview) {
      blocks.push(
        `<div class="summary-item"><span class="summary-label">Preview Cost</span><span class="summary-value">${money(
          contract.preview.cost ?? contract.preview.order_cost
        )}</span></div>`
      );
      blocks.push(
        `<div class="summary-item"><span class="summary-label">Fees</span><span class="summary-value">${money(
          contract.preview.fees ?? contract.preview.commission
        )}</span></div>`
      );
    }
    if (contract.lastOrder) {
      blocks.push(
        `<div class="summary-item"><span class="summary-label">Order Result</span><span class="summary-value">${contract.lastOrder.status || "submitted"}</span></div>`
      );
    }
    return blocks.join("");
  }

  function renderOrders() {
    if (!refs.ordersFeed) {
      return;
    }
    const orders = asArray(state.broker.orders);
    refs.ordersFeed.innerHTML = orders.length
      ? orders
          .slice(0, 5)
          .map(
            (order) =>
              `<div class="mini-row ${String(order.side || "").includes("buy") ? "is-call" : "is-put"}"><span class="mini-slot">${order.status || "open"}</span><strong>${order.option_symbol || order.symbol}</strong><span class="muted">${order.side} x${integer(order.quantity)} | ${order.create_date || "recent"}</span></div>`
          )
          .join("")
      : '<div class="summary-item"><span class="summary-label">Recent Orders</span><span class="summary-value">No recent Tradier orders.</span></div>';
  }

  function renderDom() {
    const contract = selectedContract();
    if (refs.arenaScore) {
      refs.arenaScore.textContent = integer(state.arena.score);
    }
    if (refs.arenaCombo) {
      refs.arenaCombo.textContent = `${state.arena.combo}x | Hull ${state.arena.hull}`;
    }
    if (refs.arenaNotice) {
      refs.arenaNotice.textContent = state.arena.notice;
    }

    if (refs.selectedSymbol) {
      refs.selectedSymbol.textContent = contract ? `${contract.symbol} ${contract.option_type.toUpperCase()}` : "No contract locked";
    }
    if (refs.selectedLane) {
      refs.selectedLane.textContent = contract
        ? `${laneLabel(contract.lane)} | ${contract.armed ? `${contract.charge}% charged` : "sigil not captured"}`
        : "Awaiting pickup";
    }
    if (refs.selectedMeta) {
      refs.selectedMeta.textContent = contract
        ? contract.armed
          ? `Premium ${money(quotePrice(contract))} | Forge ${Number(contract.forge_score || 0).toFixed(2)} | Break-even ${pct(contract.breakeven_move_pct)}`
          : `Capture the ${contract.symbol} sigil to arm this route, then chain matching scout intel to 100% charge.`
        : "Capture a live or shadow sigil in the arena, or tap a card below to arm a contract.";
    }
    if (refs.selectedContract) {
      refs.selectedContract.textContent = contract ? contract.contract_symbol : "--";
    }
    if (refs.selectedRisk) {
      refs.selectedRisk.textContent = contract
        ? !contract.armed
          ? "Preview stays locked until you capture this contract's sigil in the arena."
          : previewEligible(contract)
          ? "Preview lane unlocked. Hazards can still drain charge before transmission."
          : "Preview stays locked until the charge reaches 100%."
        : "Limit orders stay disabled until a contract is selected.";
    }

    if (refs.brokerMode) {
      const mode = state.broker.mode || state.broker.environment || "disabled";
      refs.brokerMode.textContent = state.broker.configured
        ? `${String(mode).toUpperCase()}${state.broker.liveTradingEnabled ? " | live armed" : " | preview first"}`
        : "BROKER OFFLINE";
      refs.brokerMode.className = `pill ${
        mode === "live" ? "is-put" : state.broker.configured ? "is-call" : "is-neutral"
      }`;
    }
    if (refs.accountEquity) {
      refs.accountEquity.textContent = money(
        state.broker.balances?.total_equity || state.broker.account?.total_equity
      );
    }
    if (refs.buyingPower) {
      refs.buyingPower.textContent = money(
        state.broker.balances?.option_buying_power ||
          state.broker.account?.option_buying_power ||
          state.broker.balances?.stock_buying_power
      );
    }
    if (refs.positionsCount) {
      refs.positionsCount.textContent = integer(state.broker.positions?.length || 0);
    }
    if (refs.snapshotHealth) {
      if (state.broker.snapshot) {
        refs.snapshotHealth.textContent = `${state.broker.snapshot.is_fresh ? "Fresh" : "Stale"} | ${
          state.broker.snapshot.age_minutes ?? "--"
        } min`;
      } else {
        refs.snapshotHealth.textContent = "No snapshot gate";
      }
    }
    if (refs.brokerMessage) {
      refs.brokerMessage.textContent = state.broker.loading
        ? "Refreshing broker status..."
        : state.broker.statusMessage;
    }
    if (refs.brokerWarnings) {
      const warnings = [];
      if (contract && contract.charge < READY_CHARGE) {
        warnings.push(`Charge ${contract.charge}%`);
      }
      if (state.broker.snapshot?.reason) {
        warnings.push(state.broker.snapshot.reason);
      }
      refs.brokerWarnings.innerHTML = warnings
        .map((warning) => `<span class="hero-tag">${warning}</span>`)
        .join("");
    }
    if (refs.previewSummary) {
      refs.previewSummary.innerHTML = previewMessage(contract);
    }

    if (refs.previewButton) {
      refs.previewButton.disabled = !previewEligible(contract) || state.broker.loading;
    }
    if (refs.submitButton) {
      refs.submitButton.disabled =
        !previewEligible(contract) ||
        role !== "admin" ||
        state.broker.loading ||
        (state.broker.mode === "live" && contract?.lane !== "live");
      refs.submitButton.textContent = state.broker.mode === "live" ? "Transmit Live Order" : "Send Sandbox Order";
    }

    renderOrders();
    syncBoardSelection();
  }

  async function requestPreview() {
    const contract = selectedContract();
    if (!previewEligible(contract)) {
      setNotice("Charge the selected contract to 100% before requesting a Tradier preview.");
      renderDom();
      return;
    }
    const price = quotePrice(contract);
    if (!price) {
      setNotice("No usable limit price is available yet.");
      return;
    }

    refs.previewButton.disabled = true;
    try {
      const response = await fetch("/api/tradier/orders", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          preview: true,
          symbol: contract.symbol,
          option_symbol: contract.contract_symbol,
          side: "buy_to_open",
          quantity: quantityValue(),
          type: "limit",
          duration: "day",
          price,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || `Preview failed with ${response.status}`);
      }
      contract.preview = data.order;
      setNotice(`Preview staged for ${contract.symbol}.`);
    } catch (error) {
      setNotice(String(error.message || error));
    }
    renderDom();
  }

  async function submitOrder() {
    const contract = selectedContract();
    if (!previewEligible(contract)) {
      setNotice("Preview stays locked until the selected contract reaches 100% charge.");
      renderDom();
      return;
    }
    if (role !== "admin") {
      setNotice("Only the admin session can transmit orders.");
      renderDom();
      return;
    }
    const price = quotePrice(contract);
    if (!price) {
      setNotice("No usable limit price is available for transmission.");
      return;
    }

    const confirmation = window.confirm(
      `${state.broker.mode === "live" ? "Transmit" : "Send"} ${quantityValue()} ${contract.symbol} ${
        contract.option_type.toUpperCase()
      } limit order through Tradier?`
    );
    if (!confirmation) {
      return;
    }

    refs.submitButton.disabled = true;
    try {
      const response = await fetch("/api/tradier/orders", {
        method: "POST",
        headers: {
          "content-type": "application/json",
        },
        body: JSON.stringify({
          preview: false,
          confirm_live: state.broker.mode === "live" ? true : undefined,
          symbol: contract.symbol,
          option_symbol: contract.contract_symbol,
          side: "buy_to_open",
          quantity: quantityValue(),
          type: "limit",
          duration: "day",
          price,
        }),
      });
      const data = await response.json();
      if (!response.ok || !data.ok) {
        throw new Error(data.error || `Order failed with ${response.status}`);
      }
      contract.lastOrder = data.confirmation || data.order;
      setNotice(`Order accepted for ${contract.symbol}.`);
      await loadBrokerStatus();
    } catch (error) {
      setNotice(String(error.message || error));
      renderDom();
    }
  }

  function onKeyDown(event) {
    const target = event.target;
    const tagName = target?.tagName?.toLowerCase();
    if (tagName === "input" || tagName === "textarea" || tagName === "select" || target?.isContentEditable) {
      return;
    }

    if (event.key === "f") {
      event.preventDefault();
      const frame = document.getElementById("arena-frame");
      if (!document.fullscreenElement) {
        frame?.requestFullscreen?.();
      } else {
        document.exitFullscreen?.();
      }
      return;
    }

    if (event.key === "p" || event.key === "Escape") {
      if (state.arena.mode === "running") {
        state.arena.mode = "paused";
        refs.overlay.hidden = false;
        setOverlay("Run Paused", "Press launch to resume the current watch.", "Resume Run");
      } else if (state.arena.mode === "paused") {
        state.arena.mode = "running";
        refs.overlay.hidden = true;
      }
      renderDom();
      return;
    }

    if (event.key === " " || event.key === "e" || event.key === "E") {
      event.preventDefault();
      if (state.arena.mode === "intro" || state.arena.mode === "ended") {
        startRun();
        return;
      }
      pulse();
      return;
    }

    if (["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "w", "a", "s", "d"].includes(event.key)) {
      keys.add(event.key);
      event.preventDefault();
    }
  }

  function onKeyUp(event) {
    keys.delete(event.key);
  }

  function animationLoop(now) {
    if (!lastFrameTime) {
      lastFrameTime = now;
    }
    const delta = Math.min(120, now - lastFrameTime);
    lastFrameTime = now;
    if (!state.arena.automationMode) {
      accumulatedMs += delta;
      while (accumulatedMs >= FIXED_STEP_MS) {
        step(FIXED_STEP_MS / 1000);
        accumulatedMs -= FIXED_STEP_MS;
      }
    }
    renderDom();
    render();
    rafId = window.requestAnimationFrame(animationLoop);
  }

  refs.startButton?.addEventListener("click", () => {
    if (state.arena.mode === "paused") {
      state.arena.mode = "running";
      refs.overlay.hidden = true;
      renderDom();
      return;
    }
    if (state.arena.mode === "ended") {
      resetRun();
    }
    startRun();
  });
  refs.resetButton?.addEventListener("click", resetRun);
  refs.previewButton?.addEventListener("click", requestPreview);
  refs.submitButton?.addEventListener("click", submitOrder);
  refs.brokerRefreshButton?.addEventListener("click", loadBrokerStatus);
  refs.tradeQty?.addEventListener("change", () => {
    quantityValue();
    renderDom();
  });

  window.addEventListener("keydown", onKeyDown);
  window.addEventListener("keyup", onKeyUp);

  window.render_game_to_text = () =>
    JSON.stringify({
      coordinate_system: "origin top-left, x increases right, y increases down",
      mode: state.arena.mode,
      score: state.arena.score,
      combo: state.arena.combo,
      hull: state.arena.hull,
      time_remaining: Math.max(0, Math.ceil(90 - state.arena.elapsed)),
      notice: state.arena.notice,
      player: {
        x: Number(state.arena.player.x.toFixed(1)),
        y: Number(state.arena.player.y.toFixed(1)),
        r: state.arena.player.r,
      },
      selected_contract: selectedContract()
        ? {
            symbol: selectedContract().symbol,
            contract_symbol: selectedContract().contract_symbol,
            lane: selectedContract().lane,
            armed: selectedContract().armed,
            charge: selectedContract().charge,
            premium: quotePrice(selectedContract()),
          }
        : null,
      broker: {
        configured: state.broker.configured,
        mode: state.broker.mode || state.broker.environment || "disabled",
        liveTradingEnabled: state.broker.liveTradingEnabled,
        role,
      },
      entities: state.arena.entities.slice(0, 8).map((entity) => ({
        kind: entity.kind,
        symbol: entity.symbol,
        option_type: entity.optionType,
        contract_symbol: entity.contractSymbol || null,
        x: Number(entity.x.toFixed(1)),
        y: Number(entity.y.toFixed(1)),
        r: entity.radius,
      })),
    });

  window.advanceTime = async (ms) => {
    state.arena.automationMode = true;
    lastFrameTime = 0;
    accumulatedMs = 0;
    const steps = Math.max(1, Math.round(ms / FIXED_STEP_MS));
    const dt = ms / 1000 / steps;
    for (let index = 0; index < steps; index += 1) {
      step(dt);
    }
    renderDom();
    render();
  };

  window.resumeRealtime = () => {
    state.arena.automationMode = false;
    lastFrameTime = 0;
    accumulatedMs = 0;
    renderDom();
    render();
  };

  ensureSelectedContract();
  bindBoardCards();
  syncBoardSelection();
  resetRun();
  loadBrokerStatus();
  refreshQuotes();
  rafId = window.requestAnimationFrame(animationLoop);

  return {
    destroy() {
      if (rafId) {
        window.cancelAnimationFrame(rafId);
      }
      window.removeEventListener("keydown", onKeyDown);
      window.removeEventListener("keyup", onKeyUp);
    },
  };
}
