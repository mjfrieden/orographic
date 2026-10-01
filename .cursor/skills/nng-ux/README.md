# nng-ux

A portable Cursor **Agent Skill** for shipping one UI/UX improvement at a time using [Nielsen Norman Group](https://www.nngroup.com/) usability heuristics, cognitive-load laws, and Nielsen’s severity scale.

Not a restyle-everything prompt. Not a 40-page audit dump. A procedure: bind the product’s constraints, walk one surface, score findings, ship the highest-severity fix, verify.

## Install

**This repo (already done):** `.cursor/skills/nng-ux/`

**Another project:**

```bash
cp -R .cursor/skills/nng-ux /path/to/other-project/.cursor/skills/nng-ux
```

**Every local Cursor project on this machine:**

```bash
mkdir -p ~/.cursor/skills
cp -R .cursor/skills/nng-ux ~/.cursor/skills/nng-ux
```

Cloud Agents pick up **project** skills from git. Personal `~/.cursor/skills` copies need Cursor’s “Sync Skills for Cloud Agents” if you want them on remote agents.

## Invoke

- Agent decides when the task is a UX pass (description is written for that).
- Or type `/nng-ux` in Agent chat.
- Or open it as a Custom Mode so the badge stays on for the session.

## Pair with a product overlay

This skill is product-agnostic on purpose. Put taste, sacred DOM ids, and “do not touch payments/auth/trading” in the **product** agent or `AGENTS.md`. See `references/overlays.md`.
