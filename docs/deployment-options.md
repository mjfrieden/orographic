# Deployment Options

This note reflects the official docs I checked on April 1, 2026.

## Option A: Cloudflare Pages + scheduled GitHub Actions

This is the best default.

How it works:

- Cloudflare Pages hosts the game board from `web/`.
- Cloudflare Pages Functions protects the app with a signed-cookie login gate.
- A scheduled GitHub Actions workflow runs the Python scanner.
- The workflow writes `web/data/latest_run.json` and commits it back to the repo.
- Cloudflare Pages sees the commit and redeploys the static site.

Why I recommend it:

- The UI stays static and cheap.
- The auth layer stays lightweight and edge-close.
- The heavy market-data scan runs in Python where `yfinance` and data science tooling are natural.
- No Worker CPU budget is spent on option-chain ranking.
- The deployment story is simple and easy to debug.

Official notes:

- Cloudflare Pages free projects can contain up to `20,000` files: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
- Pages Functions count against Workers quotas: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)
- GitHub-hosted runners are free and unlimited on public repositories: [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)
- GitHub-hosted runners on private repositories use included minutes and can become billable: [GitHub-hosted runners](https://docs.github.com/en/actions/reference/runners/github-hosted-runners)

Best for:

- Public repo and zero-cost hosted automation
- Private repo with a self-hosted runner

Setup notes:

- The initial `Connect to GitHub` step for a Pages project is still a dashboard flow.
- After the repo is connected, every commit to the production branch redeploys the site automatically.
- Runtime secrets such as `OROGRAPHIC_SESSION_SECRET` and `OROGRAPHIC_AUTH_USERS_JSON` can be added with Wrangler or the Cloudflare dashboard and do not belong in the repository.
- If you prefer not to connect the repo in the dashboard, Orographic can also deploy by direct upload from GitHub Actions once `CLOUDFLARE_ACCOUNT_ID` and `CLOUDFLARE_API_TOKEN` are present in repo secrets.

## Option B: Cloudflare Pages + self-hosted GitHub runner

This is the best zero-dollar private-repo path.

How it works:

- Same as Option A for Pages.
- Instead of GitHub-hosted runners, you register your Mac or another machine as a self-hosted runner.
- The machine runs the scheduled scan and pushes the updated JSON.

Pros:

- Private repo can stay private.
- No GitHub-hosted runner minutes.
- Still easy to wire into Pages.

Cons:

- Your machine must be awake and reachable.
- You own runner maintenance.

## Option C: Cloudflare Pages + Cloudflare Worker or Pages Functions

This is good for lightweight glue, not for the main scan engine.

What Workers are good for here:

- serving the latest snapshot through a stable API route
- lightweight auth gates
- leaderboards, replay index, or signed fetches
- cache normalization

What Workers are not good for here:

- large option-chain scans
- Python-centric data pulls
- long scoring loops across many contracts

Official notes:

- Workers Free includes `100,000` requests per day: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- Workers Free CPU time is `10 ms` per HTTP request: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- Workers Free allows `5` Cron Triggers per account: [Workers limits](https://developers.cloudflare.com/workers/platform/limits/)
- Pages Functions use the Workers quota: [Pages limits](https://developers.cloudflare.com/pages/platform/limits/)

Bottom line:

- Use Workers for API polish.
- Do not use Workers Free for the full options scan unless we rewrite the engine into a very small TypeScript edge job and accept a much lighter model.

## Option D: GitHub Actions only

This works, but it does not give you the Cloudflare edge layer the game board deserves.

Pros:

- simplest to set up
- one vendor

Cons:

- weaker front-end hosting and caching story than Pages
- less flexible if you later want an API or replay service at the edge

## Recommended path

For Orographic, I recommend:

1. `Cloudflare Pages` for the game board.
2. `GitHub Actions` for scheduled scans if the repo is public.
3. `Self-hosted GitHub Actions runner` if the repo is private and you want to stay at $0.
4. Add a `Cloudflare Worker` later only if we want a thin API facade.

## Important operational gotcha

GitHub notes that scheduled workflows in public repositories may be automatically disabled after `60` days of no repository activity: [Disable and enable workflows](https://docs.github.com/en/enterprise-server%403.14/actions/how-tos/manage-workflow-runs/disable-and-enable-workflows)

That means if you choose scheduled GitHub Actions, the repo still needs occasional activity or a manual re-enable.
