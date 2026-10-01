---
name: nng-ux
description: Ship one focused UI/UX improvement using Nielsen Norman Group heuristics, cognitive-load laws, and severity ratings. Use when auditing or improving usability, clarity, forms, dashboards, errors, empty states, mobile layout, information architecture, or interaction cost — on this product or any other.
icon: book-open
color: cyan
---

# NN/g UX pass

You are running a **Nielsen Norman Group** usability pass, not a visual restyle for its own sake. Taste may vary by product. Heuristics do not.

This skill is **portable**. Copy `.cursor/skills/nng-ux/` into another repo or into `~/.cursor/skills/nng-ux/` for every local project. Product-specific constraints live in an overlay file, never inside the heuristic checklists.

## When to use

- The user asks to improve UI/UX, usability, clarity, IA, forms, errors, empty states, dashboards, or mobile.
- A recurring artisan / design agent has finished chrome work and needs a **user-problem** finding.
- A review should cite heuristics instead of taste-only opinions.

Do **not** use this skill to invent a new product surface, change backend contracts, or restyle everything that looks “not fancy enough.”

## Progressive disclosure

1. Follow this file as the procedure.
2. Load `references/heuristics.md` when scoring H1–H10.
3. Load `references/laws.md` when the issue is speed, memory, or click cost rather than a named heuristic.
4. Load `references/severity.md` before picking what to ship.
5. Load `references/overlays.md` if this repo (or the target repo) has sacred contracts, a design language, or a cycle log.

## Procedure

### 1. Bind the product overlay

In the target repo, look for any of:

- `.cursor/agents/*artisan*`
- `docs/ui-artisan-log.md` or `docs/ux-log.md`
- `AGENTS.md`, product README UX sections
- Test files that freeze DOM ids / copy / payloads

If an overlay exists, **obey it**. Heuristics never authorize breaking auth, payments, trading, or contracted selectors.

If none exists, treat the current product’s own visual language as the overlay: match existing type, color, and components; do not import a third palette.

### 2. Pick one surface

Name the surface before inspecting (example: “signed-out login”, “primary dashboard”, “settings form”). Prefer the path a first-time or daily user actually hits.

Walk it as a person, not as a stylesheet:

- Desktop (~1280–1440) **and** a narrow viewport (~360–720).
- Keyboard: tab order, skip link, focus visible, Escape to dismiss.
- At least one **failure path**: empty, error, stale, unauthorized, offline.
- Do not trust a single screenshot. Confirm behavior.

### 3. Diagnose with heuristics, then laws

For the chosen surface, score H1–H10 from `references/heuristics.md`. Then ask the law questions in `references/laws.md` only for leftover friction.

Write findings as:

```
H# or Law · surface · evidence · user harm · severity
```

Evidence is a control, a string, a clip, or a missing state — not “it feels off.”

### 4. Ship exactly one fix

Use `references/severity.md`. Ship the **highest-severity** finding you can finish in this pass without violating the overlay.

Tie-breakers, in order:

1. Recovery / error / lost money or lost work (H5, H9, H1)
2. Cannot complete the task on mobile or keyboard
3. Recognition vs recall, match to the user’s language (H2, H6)
4. Interaction cost, Fitts, Hick, clutter (H8, laws)
5. Cosmetic consistency

Rules for the patch:

- One problem. Adjacent CSS on the same control is allowed; a second surface is not.
- Prefer copy, hierarchy, spacing, focus, and recovery over new decoration.
- Flavor in chrome is fine. **Status, money, identity, errors, and actions stay literal.**
- Preserve contracted ids, hooks, payloads, and tests named by the overlay.
- Respect `prefers-reduced-motion`.

### 5. Verify

- Re-walk the same surface desktop + narrow viewport.
- Hit the empty / error path you claimed to fix.
- Run the overlay’s required tests. If none, run the smallest UI test suite that exists.
- If you cannot use a browser, say what you could not verify.

### 6. Record the pass

If the repo has a UX/artisan log, append a cycle: heuristic, surface, what shipped, what was left alone.

In the PR / summary, name the heuristic in plain language (example: “Error recovery (H9): Tradier refresh now says what to do next”). Do not dump a 10-row audit table unless the user asked for an audit-only report.

## Audit-only mode

If the user asked for a review, not a patch: produce the findings list, stop after step 3, and do not edit product code.

## Stop conditions

- Overlay forbids the change.
- The only remaining issues are severity 0–1 and the user did not ask for polish.
- You would need new product behavior (new API, new setting, new page). File it as a finding; do not build it in this pass.
