# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Overview

**all.news** is a news aggregator served at `https://www.all.news/`. It currently covers
Swiss sources only, but the project was forked from swissnews.org with the intent to
expand its scope to more countries. all.news runs its own independent pipeline and
analytics; the original swissnews.org project keeps running separately. When generalizing
beyond Switzerland, the Swiss-specific framing still in the codebase will need revisiting:
the "Schweizer Nachrichten" taglines, the Swiss-flag logo
(`favicon.svg` and the inline SVG in `template.html`/`archive.html`), and the
single-timezone "today" check (`ZURICH`) in `crawler.py`.

Analytics is intentionally left as a placeholder (see the "Analytics placeholder" comment
in `template.html` and `archive.html`) — wire up all.news's own analytics there rather than
reusing swissnews.org's tag.

## Issue tracker

Issues do **not** live in this code repo. They are filed in a separate GitHub repo:
**`k-blo/all.news.issues`** (browser: https://github.com/k-blo/all.news.issues/issues).
The `.issues` is part of the repo name, not a path. Commit messages here reference those
issue numbers (e.g. `#30`, `#25`). The code repo itself is `k-blo/all.news`.

**Access:** a `GITHUB_TOKEN` in `.env` (gitignored; see `.env.example`) exists solely to
read those issues. It is **read-only** — use it to list and read issues, not to create,
edit, close, or comment on them. Load it from the file rather than expecting it in the
environment:

```bash
set -a; . ./.env; set +a
curl -s -H "Authorization: Bearer $GITHUB_TOKEN" \
  "https://api.github.com/repos/k-blo/all.news.issues/issues?state=open&per_page=100"
```

Quote the URL — an unquoted `?` is a glob in zsh. A `401 Bad credentials` means the token
expired or was revoked (a valid token merely lacking repo access returns 403/404 instead);
regenerate it at https://github.com/settings/tokens?type=beta.

## Conventions

- **Git commit messages are always a single line** — no body, no trailing
  co-author block. One short summary line per commit.

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

**Per-country shards:** `write_country_shards()` splits today's feed into
`data/<cc>.json` (one file per country) plus `data/manifest.json` (every country +
the languages it publishes). The site (`script.js`) fetches only the shards for the
countries a visitor filters to, instead of the whole `crawled.json`, so the download
scales with the selection. The manifest drives the country/language picker so it's
complete before any shard loads. The "all countries" view still loads `crawled.json`.
Archive day pages keep their single-file model. Shards are `rclone sync`ed to R2 (not
`copy`), since they only ever hold today's feed.

**Canonical host — always `https://www.all.news`.** The apex 301-redirects to
`www` (a Cloudflare-side rule we can't currently remove), so every absolute URL we
emit must use `www`: a canonical or `hreflang` pointing at a redirect is a signal
Google discards outright. `SITE_ORIGIN` in `crawler.py` is the single source for
generated URLs; `template.html`, `archive.html`, `robots.txt` and `HUB_TEMPLATE`'s
`{origin}` cover the rest. **Never hardcode the bare apex.**

**`<html lang>`** defaults to `en` (`write_rendered_html`) — the home feed, archive
index and archive day pages are English pages listing many languages. Only the
`/news/<country>/<lang>/` landing pages override it, with their own language.

**`/archive`, not `/archive.html`.** Cloudflare Pages strips `.html` from static
assets and 308s, so the `.html` form costs a hop and makes the served page's
canonical circular. Internal links, the canonical and the sitemap all use `/archive`.
(Archive *day* pages keep `.html` — they come from R2 via `functions/[[path]].js`,
which doesn't strip anything, and answer 200 directly.)

**Deduplication:** `archive/seen.json` stores every URL ever crawled. `archive/http_cache.json` stores `ETag`/`Last-Modified` headers so unchanged feeds return `NotModified` and are skipped. Articles are only added to `crawled.json` if their `published` date is today (Swiss local time) and their URL has never been seen before. A cached `Last-Modified` older than 7 days is not sent (`stale_validator()`): some servers keep a months-old one on fresh content and answer 304 forever. A date-only stamp (local midnight, e.g. `00:00+08:00`) counts on the source's own calendar day, else outlets east of Zurich always land on "yesterday".

**Crawl groups:** `main` (3 sharded GitHub Actions jobs) runs everything except the
`vpn` group, which runs behind a Swiss WireGuard VPN: CH Media papers plus
`VPN_SOURCES` (feeds that 403 GitHub's datacenter IPs). A feed entry with
`"ua": "compat"` is fetched with a `Mozilla/5.0 (compatible; …)` UA (VnExpress fakes
404s otherwise). **Never change `USER_AGENT` globally** — any UA mentioning
`www.all.news` or starting with `Mozilla/5.0` gets 403s from News18 and others.
`run_jobs()` logs and skips any per-source exception, so one bad feed never aborts a
shard.

**Sitemap:** `write_sitemap()` emits `<lastmod>` on every URL (the crawl timestamp
for the hourly pages, the day itself for settled archive days) — the only hint here
Google acts on; `changefreq`/`priority` are ignored. Archive days split across
several pages list *all* their pages, using the `pages` map in `archive/index.json`.
A crawl can only ever learn *today's* page count (past days' HTML isn't local during
the reduce step), so past days are seeded by `regen_archive.py`, which re-renders
them and writes their counts back to `index.json`. `dates` is unchanged, and
`archive.html` / `script.js` ignore `pages`.

**Programmatic landing pages:** `write_landing_pages()` writes one server-rendered
page per (country, language) we carry at `/news/<country>/<lang>/` (e.g.
`/news/switzerland/french/`), plus a `/news/` hub (`write_news_hub()`) linking them
all. Each is server-rendered from that slice of today's feed, so crawlers get real
headlines per country/language instead of one undifferentiated feed. They *hydrate*:
`script.js` recognises the `/news/<country>/<lang>/` path, resolves the slugs back to
codes (`COUNTRY_BY_SLUG`/`LANG_BY_SLUG`), loads that country's shard and applies the
filter — so the page is fully interactive. The (country, lang) matrix comes from the
source config (`known_country_lang_pairs()` over `jobs_for(None)`), not a single day's
feed, so the URL set is stable and `rclone sync`ed to R2. `COUNTRY_NAMES` +
`LANG_EN_NAMES` (English names → slugs) are duplicated in `crawler.py` and `script.js`
and must stay in sync. The `functions/[[path]].js` worker serves `/news/*` from R2 and
301-redirects the no-trailing-slash form to the canonical slash form.

**Topic sections** (`/space/`, `/business/`, `/tech/`, `/gaming/`): `SECTIONS` in
`crawler.py`. Each is an independent daily feed of its sources, **not split by
country** (visitors filter by language and outlet). `SECTION_SOURCES` lists
section-only outlets (with inline `lang`/`country`, never in the news feed);
`SECTION_CROSSLIST` adds existing news sources whose whole feed is on-topic.
`sections_of()` maps a source to its feeds ("news" by default). `write_sections()`
gives every section its own seen-set (`archive/seen-<key>.json`), so a URL can be
in both news and a section; it writes `<key>/feed.json` (today), `<key>/index.html`
(SSR) and `archive/<key>/<date>.json` (durable record — no section archive pages
yet). The news feed, country shards and landing pages only take "news" sources.
`functions/[[path]].js` serves the section dirs from R2; the section list is also
in `script.js` (`SECTIONS`) and the workflow loops — keep all three in sync.

**Adding a new source:** check robots.txt allows crawling, confirm the sitemap/feed format, add to the appropriate list at the top of the file, add a color to `SOURCE_COLORS` in `crawler.py` (written out to `colors.js` by `write_colors_js()`). If the source introduces a **new country or language**, add its English name to `COUNTRY_NAMES`/`LANG_EN_NAMES` in **both** `crawler.py` and `script.js` (and the display name to `COUNTRY_NAMES`/`LANG_NAMES` in `script.js`), or its landing page shows the raw code and won't hydrate.

**Never add a source whose robots.txt explicitly disallows the feed/sitemap path being crawled** — even if the site offers the feed and the article links themselves are allowed. (e.g. Kanton Thurgau publishes RSS only under `/route/`, which its robots.txt disallows, so it is not a usable source.) Judge robots.txt by RFC 9309 / Google semantics — longest match wins, `*` and `$` wildcards, a 4xx robots.txt means no rules. Python's `urllib.robotparser` gets this wrong (first match, no wildcards, 403 = disallow all), so don't rely on it. The rule is for *adding* sources: an existing source that still works stays even if its robots.txt has since tightened; only sources that stopped working are removed.

## Static site (`index.html`, `script.js`, `styles.css`)

Single-page app. `script.js` fetches `crawled.json` (or `archive/YYYY-MM-DD.json` when `?day=YYYY-MM-DD` is in the URL) and renders the article list client-side. `archive.html` fetches `archive/index.json` and lists all archived dates as links.

**Sections in the client:** `script.js` detects `/<section>/` (`SECTION`), loads
`/<section>/feed.json` (single-file, like archive days), hides the Countries and
Archive filter groups, and stores filters under `allnews.<section>.*`. A first visit
seeds the language filter from the browser languages (relaxed to all if empty).

**Menu** lists the sections (current one marked by `script.js`) plus Settings.

**Settings** (`settings.html`, `/settings`): theme (dark default / light / system) in
`allnews.theme`, layout (comfortable / compact) in `allnews.density`. Every page has a
tiny inline `<head>` script that sets `data-theme` / `data-density` on `<html>`
before first paint; `styles.css` holds the light palette (`:root[data-theme="light"]`)
and the compact overrides. Keep that inline script identical in `template.html`,
`archive.html`, `HUB_TEMPLATE` and the static pages.

**Legal pages** (`about`, `privacy`, `cookies`, `terms`, `imprint` — static `.html`,
served without the extension): linked from every footer. The privacy/cookie text
covers Google Analytics + AdSense as AdSense requires. Every page sets Google
Consent Mode defaults (denied in EEA/UK/CH until the CMP grants consent); the CMP
itself is Google's, configured in AdSense → Privacy & messaging. `site.js` makes
the footer "Cookie settings" link reopen it (falls back to `/cookies#manage`).

**Filter state lives in web storage, never the URL.** The durable filters — excluded sources, selected countries, selected languages — are persisted in **localStorage** under `allnews.*` keys (`STORE_EXCLUDE`/`STORE_COUNTRY`/`STORE_LANG`) and read back at module init by `script.js`; there are no `?exclude=`/`?country=`/`?lang=`/`?q=` params. Because localStorage is per-origin, the same filter automatically applies on the home feed, every archive day page and every open tab, so archive-day links are plain paths (`persistFilters()` writes the selection; landing pages are pinned by their path and skip it). **Search (`STORE_QUERY`) is deliberately session-only — it uses `sessionStorage`**, so it survives a reload but is forgotten between visits rather than greeting a returning visitor with a stale query. The only remaining query param is a legacy `?day=` → `/archive/<day>.html` redirect for old inbound links; the `#hash` still deep-links individual articles.

`SOURCE_COLORS` is defined in `crawler.py` and generated into `colors.js` (loaded by `script.js`). It must have an entry for every source name used in `crawler.py` — missing entries fall back to `#888`.

The article list is server-rendered at crawl time by `write_rendered_html()` — the home
feed ships its newest `SSR_LIMIT` (120) rows in the HTML and `script.js` lazy-loads the
rest, archive day pages and landing pages ship their full slice. Bots get real headlines
without executing JS.
