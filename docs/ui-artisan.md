# UI Artisan — every-other-day visual agent

Orographic already has a Harbor fantasy design system in `web/styles.css` (Hearthstone cards, Pacific sunset, gold foil). A later cockpit overlay in `web/cockpit.css` stripped that atmosphere and left login, the live board, and the backtest lab speaking three different visual languages.

The **UI Artisan** agent exists to keep unifying that board, one focused visual PR every other day, without touching Scout / Forge / Council or Tradier.

## What already exists in the repo

| File | Role |
| --- | --- |
| `.cursor/agents/ui-artisan.md` | Operating prompt for every run |
| `docs/ui-artisan-log.md` | Cycle history so runs do not repeat work |
| `web/styles.css` | Shared Harbor / Hearthstone design system |
| `web/cockpit.css` | Live Signal & Book overlay |
| `web/login/index.html` | Harbor Gate |
| `web/backtest/` | Research dungeon |
| `web/admin/` | Master Harbor ledger |

## Create the Cursor Automation

Cursor Automations cannot be created from a cloud-agent PR. Marshall should add one beside the existing **Data Expert** and **Orographic Continuous Improvement** automations:

1. Open [Cursor Automations](https://cursor.com/automations).
2. Create **UI Artisan**.
3. Repository: `mjfrieden/orographic`.
4. Schedule: every 2 days (or cron `0 16 */2 * *` UTC).
5. Model: Claude or Grok, high reasoning.
6. Prompt: paste the block below.

```text
You are the Orographic UI Artisan. Ship one focused visual improvement to the web cockpit.

Follow `.cursor/agents/ui-artisan.md`. Read `docs/ui-artisan-log.md` first and do not repeat a finished cycle.

Taste: World of Warcraft UI chrome, Zelda adventure warmth, Hearthstone legendary cards. Marshall lives in Cursor and Claude — keep the board diegetic and beautiful, never a generic SaaS dashboard.

Hard rules:
- Do not change Scout/Forge/Council trading logic, Tradier payloads, auth, or required DOM ids/hooks in tests/js/workbench_tradier_contract.test.js
- Preserve the sync-line and positions-toolbar CSS contracts in that test file
- One PR per run
- Verify login, cockpit, and any other page you touch in the browser
- Append the cycle to docs/ui-artisan-log.md

Pick the next unfinished backlog item from the agent spec.
```

The Data Expert automation is the pattern to copy for repo targeting and PR behavior.

## Local preview

```bash
npx wrangler pages dev web
```

Then open the local Pages URL. Login, Signal & Book, Backtest Lab, and Admin must share one gold/navy Harbor language.
