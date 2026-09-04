# UI Artisan cycle log

Newest cycle first. Each run appends what shipped, which surfaces changed, and what was deliberately left alone.

## Cycle 8 — 2026-09-04 — Harbor sigils instead of Material Symbols

- Cockpit and Backtest Lab drop the Google Material Symbols font. Icons are a small CSS-mask sigil set (lock, refresh, book, shield, flask, check, warning).
- JS still finds icons via `.sigil` (quality banner, research-claims banner, live card lock/forward, party chevron). Button ids and Tradier hooks are unchanged.

Left alone: Scout/Forge/Council, Tradier preview/submit, required DOM ids.

## Cycle 7 — 2026-09-04 — Backtest Lab as a research dungeon

- Control rail is a shrine altar: Marcellus labels, stone-gold fields, Cinzel delve heading.
- KPI tiles, parchment P&L chart (compass cartouche), and quest-receipt trade table share the Harbor gold language instead of the sterile Inter lab.
- Flavor kickers only (Shrine controls, Research dungeon, Map parchment, Rival builds, Reliquary, Quest receipts). Metric ids and command builder unchanged.
- 820px keeps the Live / Lab / Audit nav instead of hiding it.

Left alone: `buildBacktestCommand` / trade filters / mart hooks, Scout/Forge/Council, Tradier, auth.

## Cycle 6 — 2026-09-04 — Master Harbor auction house and map parchment

- Admin overview tiles read as inventory slots (gold corner ticks, stone wells).
- Portfolio trend sits on a map parchment with compass rose and cartouche; empty history is a blank map.
- Capture and order tables use auction-house gold headers and leather row wash. Kickers are flavor-only (Auction House, Map Parchment, Quest Receipts, Reliquary).
- Mobile keeps the Master Harbor emblem instead of hiding it at 600px.

Left alone: admin fetch URLs, snapshot/P&L math, table ids, Scout/Forge/Council, Tradier, auth.

## Cycle 5 — 2026-09-04 — Harbor Gate crest, stone fields, error toast

- Login crest now orbits with dashed gold rings, rune ticks, a pulsing mark, and a Hearthstone-style rarity gem.
- Credential fields match the cockpit stone-tablet chrome (gold labels, inset stone wells) instead of flat glass inputs.
- Failed login raises a live `role="alert"` toast with a sealed-gate shake. Motion respects `prefers-reduced-motion`.
- Portal card and fields stay aligned at ~720px (`100svh`, full-width card, larger tap targets).

Left alone: `/api/login` payload and auth, Scout/Forge/Council, Tradier preview/submit, required DOM ids.

## Cycle 4 — 2026-09-04 — Talent tree and spellbook

- Production evidence ribbon is a talent tree: circular sockets, conic rank fills, connecting gold lines, and an unlearned experiment-lane node.
- Research drawer opens as a spellbook (leather spine, parchment pages). Toggle copy is “Open spellbook.” Governance cards stay the same ids.

Left alone: Scout/Forge/Council, Tradier preview/submit, required DOM ids.

## Cycle 3 — 2026-09-04 — Party frames and quest log

- Open positions render as WoW unit frames: circular portrait, call/put gem color, HP-like P&L bar plus a thinner mana-like mark bar. Empty book shows three dashed empty party slots.
- Orders tab is a quest log (`!` / `?` bangs) while the existing `orders-table` / `orders-tbody` ledger remains for the Tradier DOM contract.

Left alone: Scout/Forge/Council, Tradier preview/submit, required DOM ids.

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
