# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

```bash
python3 crawler.py          # run the crawler (stdlib only, no pip install needed)
python3 -m http.server 8000 # serve the static site at http://localhost:8000
```

To test a single crawler function without writing files:
```bash
python3 -c "import crawler; rows = crawler.crawl_ch_media('Test', 'https://www.tagblatt.ch', 5); print(rows)"
```

Deploy:
```bash
cd cron-worker && npx wrangler deploy   # deploy the cron worker
npx wrangler pages deploy .             # deploy the static site
```

## Architecture

Three moving parts:

1. **Cloudflare Worker** (`cron-worker/`) — fires `workflow_dispatch` to GitHub every hour via the GitHub API. Exists because GitHub's own `schedule:` trigger is unreliable. Secrets managed with `wrangler secret put GITHUB_TOKEN`.

2. **GitHub Actions** (`.github/workflows/crawl.yml`) — runs `crawler.py`, then commits `crawled.json` and `archive/` back to `main` with `git push`. The crawler is stdlib-only so no install step is needed.

3. **Cloudflare Pages** — serves the repo root statically. No build step; connecting the repo in the dashboard is enough.

## Crawler (`crawler.py`)

All source definitions live at the top of the file. There are several crawler strategies, each for a different site type:

- **`FEEDS`** — standard RSS/Atom feeds via `parse_feed()`. Tamedia papers use `partner-feeds.publishing.tamedia.ch/rss/<slug>/`.
- **`CH_MEDIA_SOURCES`** — CH Media regional papers share a monthly sitemap at `/sitemap/YYYY/MM/sitemap.xml`; URLs end in `-ld.NNNNNNN`. Handled by `crawl_ch_media()`.
- **`NEWS_SITEMAPS`** — Google News sitemaps with real `<news:title>` + `<news:publication_date>`. Handled by `crawl_news_sitemap()`.
- **`WP_SOURCES`** — WordPress core/Yoast sitemaps. `crawl_wp()` finds the highest-numbered `wp-sitemap-posts-post-N.xml` or `post-sitemap.xml` page (newest articles).
- **One-off functions** — `crawl_weltwoche()`, `crawl_nebelspalter()`, `crawl_woz()`, `crawl_nau()`, `crawl_bilanz()`, `crawl_republik()`, `crawl_suedostschweiz()` for sites with unique sitemap formats.

**Deduplication:** `archive/seen.json` stores every URL ever crawled. `archive/http_cache.json` stores `ETag`/`Last-Modified` headers so unchanged feeds return `NotModified` and are skipped. Articles are only added to `crawled.json` if their `published` date is today (Swiss local time) and their URL has never been seen before.

**Adding a new source:** check robots.txt allows crawling, confirm the sitemap/feed format, add to the appropriate list at the top of the file, add a color to `SOURCE_COLORS` in `script.js`.

**Never add a source whose robots.txt explicitly disallows the feed/sitemap path being crawled** — even if the site offers the feed and the article links themselves are allowed. (e.g. Kanton Thurgau publishes RSS only under `/route/`, which its robots.txt disallows, so it is not a usable source.)

## Static site (`index.html`, `script.js`, `styles.css`)

Single-page app. `script.js` fetches `crawled.json` (or `archive/YYYY-MM-DD.json` when `?day=YYYY-MM-DD` is in the URL) and renders the article list client-side. `archive.html` fetches `archive/index.json` and lists all archived dates as links.

`SOURCE_COLORS` in `script.js` must have an entry for every source name used in `crawler.py` — missing entries fall back to `#888`.

The article list is JS-rendered, so search engines see an empty `<ul>` without executing JS. Pre-rendering this server-side (or at crawl time) is a known open improvement.
