# Orographic

Orographic is a new short-term options platform built from the useful parts of the prior weather systems, but with a much cleaner contract between signal generation, contract selection, portfolio construction, and presentation.

It is split into three first-party layers:

- `Scout`: a Cirrus-style symbol and direction engine. It decides whether a name has enough short-term edge to even deserve option-chain work.
- `Forge`: a Cumulus-style contract engine. It chooses the actual weekly contract and scores quote quality, breakeven burden, and payoff shape.
- `Council`: a Stratus-style portfolio gate. It selects the live board, keeps a shadow board, and enforces side concentration and diversification rules.

The game layer lives in `web/`. It is designed to deploy cleanly to Cloudflare Pages as a static site.

The protected access layer lives in `functions/` and runs as Cloudflare Pages Functions. It signs a short-lived session cookie and validates users from environment secrets, so the public repository never needs to store login credentials.

The current game loop also uses Pages Functions as a thin Tradier proxy. The browser never sees the Tradier token directly. Order previews and submissions stay server-side.

## Why this version is different

- No synthetic bid/ask fallback in the scan engine.
- One canonical snapshot schema from Scout to Forge to Council.
- Hard abstain support instead of forcing a pick.
- Live and shadow lanes are first-class from day one.
- Deployment path is intentionally cheap:
  - static game board on Cloudflare Pages
  - scheduled scan by GitHub Actions or a self-hosted runner
  - optional Worker later for lightweight API glue, not heavy scanning

## Local run

Create a venv and install dependencies:

```bash
cd /Users/mjfrieden/Desktop/2026/Orographic
python3 -m venv .venv
./.venv/bin/pip install -r engine/requirements.txt
```

Run a fresh scan:

```bash
./.venv/bin/python engine/run_scan.py --output web/data/latest_run.json
```

Optionally capture standing-position value on each run into a private local file:

```bash
./.venv/bin/python engine/run_scan.py \
  --output web/data/latest_run.json \
  --positions-log-output .local/position_history.json
```

Use a non-public path such as `.local/position_history.json`. Do not point position history at a git-tracked file or anything under `web/`.

Build the local historical options store and coverage manifest:

```bash
./.venv/bin/python -m engine.backtest.options_store --data-dir engine/data/optionsdx --force
```

The store builder accepts raw `.csv`, `.csv.gz`, `.gz`, and `.zip` archives dropped into `engine/data/optionsdx`.

Run a strict replay that only accepts real historical option-chain data:

```bash
./.venv/bin/python -m engine.backtest.runner --months 3 --base-budget-usd 300 --hard-cost-ceiling-usd 600 --strict-options-data --min-real-coverage-pct 0.9
```

Run the walk-forward alpha experiment with the same sizing policy:

```bash
./.venv/bin/python -m engine.backtest.alpha_experiment --months 12 --base-budget-usd 300 --hard-cost-ceiling-usd 600 --cost-cap-usd 600 --strict-options-data --min-real-coverage-pct 0.9
```

Preview the game board:

```bash
npx wrangler pages dev web
```

Then open the local Pages URL shown by Wrangler.

Controls on the main page:

- `WASD` or arrow keys: move the cutter
- `E`: tractor the nearest signal into the command deck
- `Space`: start or resume a run
- `F`: toggle fullscreen

## Repo layout

- `engine/`: Python scan pipeline and tests
- `web/`: static game board for Cloudflare Pages
- `functions/`: Pages Functions for login, logout, session lookup, and admin gating
- `docs/`: architecture and deployment notes
- `.github/workflows/`: scheduled scan workflow
- `scripts/hash_auth_users.py`: helper to hash viewer/admin accounts for the `OROGRAPHIC_AUTH_USERS_JSON` secret

## Auth secrets

Orographic expects two Cloudflare Pages secrets:

- `OROGRAPHIC_SESSION_SECRET`: random signing secret for the session cookie
- `OROGRAPHIC_AUTH_USERS_JSON`: JSON array of hashed users with `username`, `role`, `salt`, `hash`, and `iterations`

Keep both in local ignored files or Cloudflare secrets only. Do not commit them to the public repository.

Tradier integration expects these additional Pages secrets or local `.dev.vars` entries:

- `TRADIER_ACCESS_TOKEN`: your Tradier API token
- `TRADIER_ACCOUNT_ID`: the brokerage account id
- `TRADIER_SANDBOX_MODE`: `true` for paper trading, `false` for production base URLs
- `TRADIER_LIVE_TRADING_ENABLED`: `true` only when you explicitly want production order submission enabled
- `TRADIER_MAX_CONTRACTS`: hard cap for this arena's order quantity control, default `3`
- `OROGRAPHIC_INTERNAL_CAPTURE_TOKEN`: shared secret used only for the private hosted position-history capture endpoint

Recommended default:

- keep `TRADIER_SANDBOX_MODE=true`
- keep `TRADIER_LIVE_TRADING_ENABLED=false`
- validate previews and account snapshots locally before enabling live order traffic

The Tradier workflow in this repo currently supports:

1. Server-side status check
2. Server-side account snapshot via the status route
3. Server-side option quote refresh for the arena contracts
4. Quote-derived market value fallback for option positions when the broker omits `current_value`
5. Server-side option order preview using `preview=true`
6. Admin-only limit-order placement for both entries and manual exits
7. Optional private per-run position history capture during Python scan runs
8. Live entry placement gated by admin access, current live-board membership, and fresh snapshot timing

## Hosted Position History

Hosted runs can persist private position snapshots in Cloudflare D1 without committing brokerage history into the repo.

- D1 binding: `POSITIONS_DB`
- Private capture route: `POST /api/internal/positions/capture`
- Admin read route: `GET /api/admin/positions-history?limit=20`

The scheduled GitHub Actions scan now posts to the private capture route after each run. The route is protected by `OROGRAPHIC_INTERNAL_CAPTURE_TOKEN`, which should exist in both Cloudflare Pages secrets and the GitHub repo secrets.

## Recommended free deployment

As of April 1, 2026, the default recommendation is:

1. Put the repo on GitHub.
2. Connect `web/` to Cloudflare Pages.
3. Let a scheduled GitHub Actions workflow write `web/data/latest_run.json`.
4. Let Cloudflare Pages redeploy on commit.

An optional `pages_deploy.yml` workflow is included for direct-upload deploys if you would rather use GitHub Actions plus a Cloudflare API token instead of dashboard Git integration.

If you want $0 with a private repo, use a self-hosted GitHub runner on your machine instead of GitHub-hosted minutes.

More detail lives in [deployment-options.md](/Users/mjfrieden/Desktop/2026/Orographic/docs/deployment-options.md).
