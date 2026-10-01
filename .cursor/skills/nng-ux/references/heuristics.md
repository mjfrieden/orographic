# Nielsen’s 10 usability heuristics

Paraphrased for implementation. The names are Nielsen Norman Group’s; the checks are ours. Score the **chosen surface**, not the whole app.

## H1 — Visibility of system status

The UI should always say what is going on, in a reasonable time.

- Loading, syncing, stale, live, offline, and “last updated” are distinguishable.
- Destructive or money-moving actions have a preview / confirm that reflects the real payload.
- Status is next to the thing it describes, not buried in a footer the user has left.
- Stale data is marked stale. Silent 12-hour-old numbers fail this heuristic.

## H2 — Match between system and the real world

Speak the user’s words. Follow real-world conventions.

- Buttons say the action the user thinks they are taking (`Sign in`, `Preview order`), not internal lore alone.
- Chrome may have flavor. **Contracts, cash, P&L, dates, errors, and risk stay literal.**
- Units, time zones, and signs (`+` / `−`) match the domain.
- Icons have a text label, or a proven aria-label that a screen reader would understand.

## H3 — User control and freedom

People make mistakes. They need a clearly marked way out.

- Cancel, close, and back exist and work from keyboard (including Escape on dialogs).
- Accidental quantity / filter / tab changes are easy to undo.
- Logout and destructive actions are reachable but not one mis-tap from a primary action on a 360px screen.

## H4 — Consistency and standards

Same words, same patterns, same place.

- One type system and one component language across login, app, settings, admin.
- Primary, secondary, and danger actions look different from each other and the same across pages.
- Platform conventions (show password, numeric steppers, native `autocomplete`) are not reinvented without cause.

## H5 — Error prevention

Better to prevent than to toast.

- Constraints (min/max, required, preview-before-send) happen **before** submit.
- Dangerous defaults are hard to hit (no pre-checked “live send”).
- Ambiguous toggles (custom vs preset, sandbox vs live) show the consequence in the same view.

## H6 — Recognition rather than recall

Show options. Do not make people remember ids, gates, or yesterday’s numbers.

- Recent / nearby / “came close” items appear when the system abstains.
- Filters, quantity, and account mode remain visible while the user works.
- Codes and hashes are copyable; they are not the only label if a human name exists.

## H7 — Flexibility and efficiency of use

Shortcuts for experts; a clear path for novices.

- Frequent actions are fewer clicks for return users (refresh, preview, tab switch).
- Power controls (advanced research, raw tables) are behind disclosure, not dumped on the first screen.
- Do not hide a novice’s only path inside an expert control.

## H8 — Aesthetic and minimalist design

Every extra unit of information competes with the units that matter.

- One primary question per view (“what is today’s pick?”, “can I sign in?”).
- Decoration that clips, wraps, or overlaps **content** fails, however pretty.
- Tiny type (<11px) for anything a human must read fails. Kickers can be small; values cannot.
- Competing palettes, leftover system-ui, and 9px Inter on a designed page fail.

## H9 — Help users recognize, diagnose, and recover from errors

Errors in the user’s language. Precise. Constructive.

- No raw stack traces or `{error: true}` as the only message.
- The message names **what failed** and **what to try next** (retry control, check connectivity, re-auth).
- The recovery control is adjacent to the message.
- Success after recovery is obvious (status returns to live / signed-in).

## H10 — Help and documentation

Help should be searchable, concrete, and next to the task.

- First-run and empty states say what will appear here and what the user can do.
- Jargon (preview, sandbox, coverage) has a short definition at the point of use.
- Long manuals are a last resort; a one-line hint next to the control is the default.
