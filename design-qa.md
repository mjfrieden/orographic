# Design QA — Orographic Option 3 “Signal & Book”

## Comparison Target

- Source visual: `/Users/mjfrieden/.codex/generated_images/019fdcb8-9c23-7710-9196-0b73475441fb/exec-3819370a-640f-417b-859c-7ece76e5ab99.png`
- Rendered implementation: `/Users/mjfrieden/Desktop/2026/Orographic/output/ui-audit/05-signal-book-1440-refined.png`
- Side-by-side comparison input: `/Users/mjfrieden/Desktop/2026/Orographic/output/ui-audit/10-source-vs-implementation.png`
- Responsive evidence: `/Users/mjfrieden/Desktop/2026/Orographic/output/ui-audit/08-signal-book-720.png`
- Target viewport: 1440 × 1024 CSS pixels. The generated source was normalized from 1487 × 1058 to the implementation capture for the comparison input.
- State: real repository research artifacts and a local, unauthenticated broker state. The source illustrates a trade-ready, populated account; the implementation truthfully renders the current `HOLD · SHADOW ONLY` state and an unavailable Tradier account without inventing positions or balances.

## Findings

No actionable P0, P1, or P2 findings remain.

- Hierarchy: The primary surface is reduced to two decisions: the best signal on the left and the live portfolio book on the right. Research evidence is compressed into a persistent bottom ribbon and opens into a detailed drawer.
- Visual fidelity: The implementation matches the selected navy/brass/teal direction, vertical two-pane split, restrained rule system, serif display hierarchy, compact account rail, primary preview action, and evidence footer.
- Scientific integrity: Synthetic source values were replaced by canonical artifacts. The live UI shows 224 independent observations after collapsing 286 repeated scans, −$573 executable net P&L, 0.340 Brier score, and −99.2% cluster-adjusted maximum drawdown.
- Broker integrity: The existing Tradier account, quote, preview, execute, position, order, and logout hooks remain data-driven. The local static preview shows a broker-unavailable state; it does not mock a funded account or positions.
- Safety: A live trade cannot be transmitted from the primary CTA. The flow requests a broker preview, then requires a separately permissioned confirmation. Non-JSON broker failures are converted to a clear availability message and escaped before modal rendering.
- Accessibility and responsiveness: Controls retain accessible names, state labels do not rely on color alone, the research drawer is a named region with a close control, and the 720px layout has no horizontal document overflow (`scrollWidth 714` at a 720px viewport).

## Comparison History

1. Initial implementation put the evidence ribbon below the target viewport and allowed the refresh label to replace its icon.
2. The second pass compressed the primary panes, anchored the evidence summary, and restored the icon-only refresh control.
3. The final functional pass sanitized the unauthenticated broker-preview failure, retained the real API contract, and confirmed the selected signal can be advanced without losing the trade-preview action.

## Primary Interaction and Functional Checks

- Advanced from signal 1 of 3 to signal 2 of 3 and confirmed the contract, premium, edge, confidence, and navigation state changed together.
- Opened the research drawer and verified the evidence window selector, promotion gates, active-versus-shadow metrics, lineage, and metadata.
- Triggered the guarded preview action. The local static server returned the expected unavailable state; the UI displayed a safe `Tradier order preview is unavailable in this session.` message and kept execution disabled.
- Verified the desktop and 720px responsive layouts in the in-app browser.
- Verified the final normal visual state has no browser console errors.
- JavaScript contract tests: 19 passed.
- Python engine tests: 207 passed.
- JavaScript syntax check: passed.

## Intentional State Differences

- The source mock contains profitable spreads and a green account connection. No authenticated Tradier session was available to the local QA server, so visual row fidelity for populated positions was not fabricated. The renderer and endpoint contracts are covered by the JavaScript contract tests.
- The source says `TRADE READY`; current scientific evidence requires `HOLD · SHADOW ONLY`. This is the correct safety and profit-preservation behavior.

final result: passed
