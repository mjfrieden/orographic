# Product overlays

Heuristics are universal. **Implementation constraints are not.** Bind them before you edit.

## Discovery order

1. A file the user or agent named as the design spec (example: `.cursor/agents/ui-artisan.md`).
2. `docs/ui-artisan-log.md`, `docs/ux-log.md`, or similar cycle logs — skip work already shipped.
3. Tests that freeze DOM ids, CSS contracts, copy, or payloads.
4. `AGENTS.md` / README “do not change” sections.

## What an overlay may require

- **Sacred ids and hooks** — do not rename to “fix” semantics. Change labels and CSS instead.
- **Payload and auth contracts** — usability copy wraps them; it does not reshape JSON.
- **Design language** — diegetic chrome, fonts, density. NN/g H8 means “reduce competing information,” not “make it generic SaaS.”
- **Cadence** — one focused PR, then stop, is a valid overlay even when more findings exist.

## Portable use on other projects

Copy this folder:

```text
.cursor/skills/nng-ux/
  SKILL.md
  references/heuristics.md
  references/laws.md
  references/severity.md
  references/overlays.md
  README.md
```

into:

- another repo’s `.cursor/skills/nng-ux/` (shared with the team via git), or
- `~/.cursor/skills/nng-ux/` (all of your local Cursor projects).

Then add a short overlay in *that* product (a page of “do not change X; verify Y”). Do not fork the heuristic files per product.

## When overlay and heuristic conflict

Overlay wins on **safety and contracts**. Heuristic wins on **what the user sees and can do** within those contracts.

Example: a test requires `id="logout-btn"`. You may change the visible label, size, and recovery copy. You may not delete the control to simplify the header.
