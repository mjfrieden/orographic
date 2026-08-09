# Orographic Interaction Audit

## Scope

The primary Signal & Book journey: inspect a candidate, size an order, review the live book, and drill into model evidence. Captured in the in-app browser against the local application using real repository artifacts. Tradier was unauthenticated, so broker account values and live preview responses were not fabricated.

## Outcome

The first version concentrated the right information but left too much of the surface read-only. The revised cockpit now exposes the main decision path through explicit controls while keeping the scientific and broker safeguards intact.

## Flow

1. **Choose and size a signal — healthy.** Candidate chips switch the complete signal state. The contract stepper recalculates estimated debit and feeds the selected quantity into the existing Tradier preview request.
   - Evidence: `output/interaction-audit/08-final.png`
2. **Inspect the model rationale — healthy.** “Why this signal?” progressively reveals liquidity, spread, projected move, breakeven move, and Council risk flags without crowding the default view.
   - Evidence: `output/interaction-audit/03-signal-evidence.png`
3. **Review the portfolio book — healthy with an environment limit.** Keyboard-operable Positions and Orders tabs expose the real broker collections. The local session correctly shows empty broker data because it is unauthenticated.
   - Evidence: `output/interaction-audit/04-orders-tab.png`
4. **Drill into scientific evidence — healthy.** Every evidence tile opens the detailed promotion drawer, focuses the relevant analysis, and can be dismissed with Escape while returning focus to the trigger.
   - Evidence: `output/interaction-audit/05-research-drilldown.png`
5. **Use the responsive layout — healthy.** At a 720px viewport, the page reflows without horizontal document overflow (`714px` scroll width at `720px` viewport width), and the sizing and candidate controls remain usable.
   - Evidence: `output/interaction-audit/06-responsive.png`

## Accessibility Checks

- Candidate buttons expose pressed state.
- Portfolio views use tab and tabpanel semantics plus arrow-key switching.
- Quantity controls have explicit accessible names.
- Evidence tiles are real buttons rather than clickable containers.
- The research drawer supports Escape dismissal and focus restoration.
- Status meaning remains available in text and does not depend only on color.

## Evidence Limits

- An authenticated Tradier account was not available in the local static QA session, so populated positions, orders, and a successful broker preview could not be visually captured.
- The real `/api/tradier/orders` route, payload fields, preview-first constraint, and separate execution confirmation remain covered by the JavaScript contract suite.
- Screenshot review does not establish full WCAG compliance; assistive-technology testing remains separate.
