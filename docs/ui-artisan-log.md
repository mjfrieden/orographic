# UI Artisan cycle log

Newest cycle first. Each run appends what shipped, which surfaces changed, and what was deliberately left alone.

## Cycle 2 — 2026-09-04 — Legendary foil, rarity gem, stone tablet

- Signal cards now carry a foil sheen, inset gold ring, and a diamond rarity gem. Call cards foil teal; put cards foil amber; HOLD is a sealed/quiet legendary with a dimmed gem and “No legendary drawn.”
- Quantity stepper is a stone tablet (engraved gold chrome) without changing `card-qty-step` / `card-qty-input` hooks.
- Foil animation respects `prefers-reduced-motion`.

Left alone: Scout/Forge/Council, Tradier preview/submit, required DOM ids.

## Cycle 1 — 2026-09-04 — Unify the live board with Harbor fantasy

- Restored the Harbor sunset atmosphere on the Signal & Book cockpit (scene bloom, film grain, `harbor-hero.png`). The cockpit overlay no longer flattens the page into a sterile navy slab or strips Hearthstone card chrome.
- Signal renders as a legendary card (ticker art, call/put gem, foil frame) including the Hold / no-trade state.
- Open positions read as party frames with a P&L vitality bar.
- Login Harbor Gate fields and error toast finally use the shared design system (`label.field` was unstyled).
- Backtest Lab picks up the same gold chrome, Cinzel display face, and sunset wash so it is no longer a third product.
- Added this agent spec so a Cursor Automation can keep shipping visual PRs every other day.

Left alone: Scout/Forge/Council, Tradier preview/submit, DOM ids and JS hooks required by `tests/js/workbench_tradier_contract.test.js`.
