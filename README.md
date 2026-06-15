# all.news

News aggregator. Hourly crawl, filterable by source. Currently covers Swiss
sources; scope is expanding to more countries. Runs its own pipeline and
analytics, separate from the original swissnews.org project.

## Architecture

- **Cloudflare Worker** (`cron-worker/`) — fires `workflow_dispatch` hourly via GitHub API (GitHub's `schedule:` is unreliable)
- **GitHub Actions** (`.github/workflows/crawl.yml`) — runs `crawler.py`, commits `crawled.json` back to main
- **Cloudflare Pages** — serves the static site from the repo root

## Run locally

```bash
python3 crawler.py          # writes crawled.json; stdlib only
python3 -m http.server 8000 # then open http://localhost:8000
```

## Setup

**Worker (cron trigger)**
```bash
cd cron-worker
npx wrangler secret put GITHUB_TOKEN   # fine-grained PAT: Actions = Read & write
npx wrangler deploy
```

**Pages (static site)**
```bash
npx wrangler pages deploy .
```
Or connect the repo in the Cloudflare dashboard — no build step needed.
