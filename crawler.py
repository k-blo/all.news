#!/usr/bin/env python3
"""swissnews crawler — POC.

Fetches RSS feeds from Swiss news sites, extracts title + link (+ short summary),
writes crawled.json for the static site to consume.

Stdlib only. Run: python3 crawler.py
"""

import json
import os
import re
import sys
import urllib.request
import urllib.error
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape
from zoneinfo import ZoneInfo

ZURICH = ZoneInfo("Europe/Zurich")
ARCHIVE_DIR = "archive"
SEEN_FILE = os.path.join(ARCHIVE_DIR, "seen.json")
INDEX_FILE = os.path.join(ARCHIVE_DIR, "index.json")
HTTP_CACHE_FILE = os.path.join(ARCHIVE_DIR, "http_cache.json")

# RSS-first: only sites that publish a feed (syndication intent). Title + link
# always safe to aggregate; summary truncated. Edit/extend this list freely.
FEEDS = [
    {"source": "SRF",           "url": "https://www.srf.ch/news/bnf/rss/1890"},
    #{"source": "RTS",           "url": "https://www.rts.ch/info/?format=rss/news"},
    #{"source": "Le Temps",      "url": "https://www.letemps.ch/articles.rss"},
    {"source": "Blick",         "url": "https://www.blick.ch/news/rss.xml"},
    {"source": "20 Minuten",    "url": "https://partner-feeds.20min.ch/rss/20minuten"},
    {"source": "Tages-Anzeiger","url": "https://partner-feeds.publishing.tamedia.ch/rss/tagesanzeiger/"},
    {"source": "Berner Zeitung","url": "https://partner-feeds.publishing.tamedia.ch/rss/bernerzeitung/"},
    {"source": "Der Bund",      "url": "https://partner-feeds.publishing.tamedia.ch/rss/derbund/"},
    {"source": "Basler Zeitung","url": "https://partner-feeds.publishing.tamedia.ch/rss/bazonline/"},
    #{"source": "Tribune de Genève","url": "https://partner-feeds.publishing.tamedia.ch/rss/tdg/"},
    {"source": "Zentralplus",   "url": "https://www.zentralplus.ch/feed/"},
    #{"source": "Heidi.news",    "url": "https://www.heidi.news/articles.rss"},
    {"source": "Finews",        "url": "https://www.finews.ch/news?format=feed"},
    {"source": "Netzwoche",     "url": "https://www.netzwoche.ch/rss.xml"},
    #{"source": "Le Courrier",   "url": "https://lecourrier.ch/feed/"},
    {"source": "Inside IT",     "url": "https://www.inside-it.ch/rss.xml"},
    {"source": "NZZ",           "url": "https://www.nzz.ch/recent.rss", "summary": False},
    {"source": "Persönlich",    "url": "https://www.persoenlich.com/rss/news.xml"},
    {"source": "Tachles",       "url": "https://www.tachles.ch/feed"},
    {"source": "ETH Zürich",     "url": "https://www.ethz.ch/de/news-und-veranstaltungen/eth-news/news/_jcr_content.feed"},
]

# Descriptive UA + contact. Generic bot UAs get 403'd by these sites.
USER_AGENT = "SwissNewsBot/0.1 (+https://github.com/yourname/swissnews; POC)"
TIMEOUT = 15
SUMMARY_MAX = 200  # keep snippets short — legal caution
WELTWOCHE_SITEMAP_INDEX = "https://weltwoche.ch/sitemap_index.xml"
WELTWOCHE_MAX = 50  # newest N stories from the latest weekly sitemap
NEBELSPALTER_SITEMAP = "https://nebelspalter.ch/sitemap.xml"
NEBELSPALTER_MAX = 50  # newest N /themen/YYYY/MM/slug articles
# Google-News sitemaps: real <news:title> + publication_date (no slug guessing).
NEWS_SITEMAPS = [
    {"source": "Watson",               "url": "https://www.watson.ch/api/2.0/feed/googlesitemap.xml",            "max": 50},
    {"source": "Freiburger Nachrichten","url": "https://www.freiburger-nachrichten.ch/sitemap_latest_news.xml", "max": 50},
    #{"source": "Watson FR", "url": "https://www.watson.ch/fr/api/2.0/feed/googlesitemap.xml", "max": 50},
]
# WordPress-core sitemap sources: (source, index_url, max). Newest = highest
# wp-sitemap-posts-post-N. Titles from URL slug (last path segment).
WP_SOURCES = [
    {"source": "Inside Paradeplatz", "index": "https://insideparadeplatz.ch/wp-sitemap.xml", "max": 50},
    {"source": "Infosperber",        "index": "https://www.infosperber.ch/wp-sitemap.xml",   "max": 50},
    {"source": "Rathuus",            "index": "https://rathuus.ch/sitemap.xml",              "max": 50},
    {"source": "Vorwärts",           "index": "https://www.vorwaerts.ch/wp-sitemap.xml",      "max": 50},
]
BILANZ_MAX = 30      # https://www.bilanz.ch/sitemap-articles-time-limited-YYYY-MM.xml
REPUBLIK_SITEMAP = "https://www.republik.ch/sitemap.xml"  # index of per-year sitemaps
REPUBLIK_MAX = 50
SUEDOSTSCHWEIZ_MAX = 50
BAUERNZEITUNG_MAX = 50
# CH Media regional papers: /sitemap/YYYY/MM/sitemap.xml, URLs end in -ld.NNNNNNN
CH_MEDIA_SOURCES = [
    {"source": "Luzerner Zeitung",   "base": "https://www.luzernerzeitung.ch",   "max": 50},
    {"source": "Aargauer Zeitung",   "base": "https://www.aargauerzeitung.ch",   "max": 50},
    {"source": "St. Galler Tagblatt","base": "https://www.tagblatt.ch",          "max": 50},
    {"source": "Thurgauer Zeitung",  "base": "https://www.thurgauerzeitung.ch",  "max": 50},
    {"source": "bz Basel",           "base": "https://www.bzbasel.ch",           "max": 50},
    {"source": "Solothurner Zeitung","base": "https://www.solothurnerzeitung.ch","max": 50},
    {"source": "Oltner Tagblatt",    "base": "https://www.oltnertagblatt.ch",    "max": 50},
    {"source": "Badener Tagblatt",   "base": "https://www.badenertagblatt.ch",   "max": 50},
    {"source": "Grenchner Tagblatt", "base": "https://www.grenchnertagblatt.ch", "max": 50},
    {"source": "Limmattaler Zeitung","base": "https://www.limmattalerzeitung.ch","max": 50},
    {"source": "Zofinger Tagblatt",  "base": "https://www.zofingertagblatt.ch",  "max": 50},
    {"source": "Appenzeller Zeitung","base": "https://www.appenzellerzeitung.ch","max": 50},
    {"source": "Zuger Zeitung",      "base": "https://www.zugerzeitung.ch",      "max": 50},
    {"source": "Nidwaldner Zeitung", "base": "https://www.nidwaldnerzeitung.ch", "max": 50},
    {"source": "Obwaldner Zeitung",  "base": "https://www.obwaldnerzeitung.ch",  "max": 50},
    {"source": "Urner Zeitung",      "base": "https://www.urnerzeitung.ch",      "max": 50},
]


class NotModified(Exception):
    pass


_http_cache: dict = {}


def fetch(url):
    headers = {"User-Agent": USER_AGENT}
    entry = _http_cache.get(url, {})
    if entry.get("last_modified"):
        headers["If-Modified-Since"] = entry["last_modified"]
    if entry.get("etag"):
        headers["If-None-Match"] = entry["etag"]
    req = urllib.request.Request(url, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            lm, etag = resp.headers.get("Last-Modified"), resp.headers.get("ETag")
            if lm or etag:
                _http_cache[url] = {k: v for k, v in (("last_modified", lm), ("etag", etag)) if v}
            return resp.read().lstrip(b"\xef\xbb\xbf \t\r\n")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified(url)
        raise


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def parse_date(s):
    """Parse RSS pubDate or ISO/sitemap lastmod into an aware datetime, or None."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        return None


def is_today(s):
    """True if the source date falls on today's date in Swiss local time."""
    dt = parse_date(s)
    if dt is None:
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZURICH).date() == datetime.now(ZURICH).date()


def load_seen():
    """All article URLs ever crawled — persists across days to block re-adds."""
    try:
        with open(SEEN_FILE, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def archive_dates():
    """Sorted (newest first) list of archived crawl dates."""
    names = os.listdir(ARCHIVE_DIR)
    dates = [n[:-5] for n in names
             if n.endswith(".json") and n not in ("seen.json", "index.json", "http_cache.json")]
    return sorted(dates, reverse=True)


def text_of(item, *tags):
    """First non-empty matching child text, namespace-insensitive."""
    for tag in tags:
        for child in item:
            local = child.tag.split("}")[-1].lower()
            if local == tag and child.text and child.text.strip():
                return child.text.strip()
    return ""


def local(el):
    return el.tag.split("}")[-1].lower()


def parse_feed(source, xml_bytes, allow_summary=True):
    root = ET.fromstring(xml_bytes)
    out = []
    # RSS <item> and Atom <entry>, namespace-insensitive
    items = [e for e in root.iter() if local(e) == "item"]
    items = items or [e for e in root.iter() if local(e) == "entry"]
    for item in items:
        title = strip_html(text_of(item, "title"))
        link = text_of(item, "link")
        if not link:  # Atom: link is in href attribute
            for child in item:
                if child.tag.split("}")[-1].lower() == "link" and child.get("href"):
                    link = child.get("href")
                    break
        if not title or not link:
            continue
        summary = ""
        if allow_summary:
            summary = strip_html(text_of(item, "description", "summary"))[:SUMMARY_MAX]
        out.append({
            "source": source,
            "title": title,
            "url": link,
            "summary": summary,
            "published": text_of(item, "pubdate", "published", "updated"),
        })
    return out


# Words where literal ae/oe/ue is NOT an umlaut — left untouched.
UMLAUT_SKIP = {
    "neue", "neuen", "neuer", "neues", "aktuell", "aktuelle", "aktuellen",
    "aktueller", "aktuelles", "steuer", "steuern", "duell", "individuell",
    "manuell", "israel", "michael", "raphael", "museum", "aktuellste",
    "venezuela", "venezuelas", "oecd",
}
# German function words kept lowercase in title-case (unless first word).
LOWER_WORDS = {
    "der", "die", "das", "den", "dem", "des", "ein", "eine", "einen", "einer",
    "eines", "und", "oder", "aber", "in", "im", "auf", "mit", "von", "vom",
    "zu", "zur", "zum", "aus", "an", "am", "als", "bei", "ist", "für", "über",
    "vor", "nach", "um", "es", "er", "sie", "wie", "wer", "was", "ob", "dass",
}


def restore_umlauts(word):
    if word in UMLAUT_SKIP:
        return word
    # ue -> ü only when not preceded by a vowel (skips "neue", "treue", ...)
    out, i = [], 0
    while i < len(word):
        pair = word[i:i + 2]
        prev = word[i - 1] if i else ""
        if pair == "ae":
            out.append("ä"); i += 2
        elif pair == "oe":
            out.append("ö"); i += 2
        elif pair == "ue" and (i == 0 or prev not in "aeiou"):
            out.append("ü"); i += 2
        else:
            out.append(word[i]); i += 1
    return "".join(out)


def slug_to_title(slug):
    words = [restore_umlauts(w) for w in slug.split("-") if w]
    titled = [
        w if (idx and w in LOWER_WORDS) else (w[:1].upper() + w[1:])
        for idx, w in enumerate(words)
    ]
    return " ".join(titled)


def sitemap_rows(xml_bytes, match, sort=True):
    """Extract (lastmod, loc) pairs from a urlset whose loc matches `match` regex."""
    sm = ET.fromstring(xml_bytes)
    rows = []
    for url_el in sm.iter():
        if local(url_el) != "url":
            continue
        loc = lastmod = ""
        for c in url_el:
            if local(c) == "loc":
                loc = (c.text or "").strip()
            elif local(c) == "lastmod":
                lastmod = (c.text or "").strip()
        if loc and match.search(loc):
            rows.append((lastmod, loc))
    if sort:
        rows.sort(reverse=True)  # newest lastmod first
    return rows


def crawl_sitemap_source(source, rows, slug_re, limit):
    """Build articles from sitemap rows. Title from URL slug, no page fetch."""
    out = []
    for lastmod, loc in rows[:limit]:
        m = slug_re.search(loc)
        if not m:
            continue
        out.append({"source": source, "title": slug_to_title(m.group(1)),
                    "url": loc, "summary": "", "published": lastmod})
    return out


def crawl_weltwoche():
    """No RSS — titles from /story/ slugs in the newest weekly sitemap."""
    index = ET.fromstring(fetch(WELTWOCHE_SITEMAP_INDEX))
    weekly = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text and "weekly-sitemap" in loc.text:
            m = re.search(r"weekly-sitemap(\d+)\.xml", loc.text)
            if m:
                weekly.append((int(m.group(1)), loc.text))
    if not weekly:
        raise ValueError("no weekly-sitemap entries found")
    newest = max(weekly)[1]  # highest number = newest
    rows = sitemap_rows(fetch(newest), re.compile(r"/story/"))
    return crawl_sitemap_source(
        "Weltwoche", rows, re.compile(r"/story/([^/]+)/?$"), WELTWOCHE_MAX)


def crawl_nebelspalter():
    """No RSS — titles from /themen/YYYY/MM/slug paths in the sitemap."""
    # Sitemap has no lastmod and appends newest entries at the bottom.
    base_re = re.compile(r"/themen/\d{4}/\d{2}/[^/]+$")
    detail_re = re.compile(r"/themen/(\d{4}/\d{2})/([^/]+)$")
    rows = sitemap_rows(fetch(NEBELSPALTER_SITEMAP), base_re, sort=False)
    rows = list(reversed(rows[-NEBELSPALTER_MAX:]))  # newest N, newest first

    today = datetime.now(ZURICH)
    current_ym = today.strftime("%Y/%m")
    out = []
    for _, loc in rows:
        m = detail_re.search(loc)
        if not m:
            continue
        url_ym, slug = m.group(1), m.group(2)
        # No precise date in sitemap — use today for current-month articles
        # so they pass the is_today() filter; older months keep their month date.
        pub = today.isoformat() if url_ym == current_ym else f"{url_ym.replace('/', '-')}-01T00:00:00+01:00"
        out.append({"source": "Nebelspalter", "title": slug_to_title(slug),
                    "url": loc, "summary": "", "published": pub})
    return out


# Post-sitemap names: WP-core "wp-sitemap-posts-post-N" or Yoast "post-sitemapN".
POST_SITEMAP_RE = re.compile(r"(?:wp-sitemap-posts-post-|post-sitemap)(\d*)\.xml")


def crawl_wp(source, index_url, limit):
    """WordPress sitemaps (WP-core or Yoast) — newest = highest-numbered post
    sub-sitemap. Title from URL slug (last path segment)."""
    index = ET.fromstring(fetch(index_url))
    posts = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text:
            m = POST_SITEMAP_RE.search(loc.text)
            if m:
                posts.append((int(m.group(1) or 1), loc.text))
    if not posts:
        raise ValueError("no post sitemap entries found")
    newest = max(posts)[1]
    rows = sitemap_rows(fetch(newest), re.compile(r"."))
    return crawl_sitemap_source(source, rows, re.compile(r"/([^/]+)/?$"), limit)


def crawl_suedostschweiz():
    """Monthly sitemap. URL = /category/slug-ARTICLEID — strip trailing numeric ID."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    url = f"https://www.suedostschweiz.ch/sitemap-{ym}.xml"
    rows = sitemap_rows(fetch(url), re.compile(r"/[^/]+-\d+$"))
    return crawl_sitemap_source(
        "Südostschweiz", rows, re.compile(r"/([^/]+)-\d+$"), SUEDOSTSCHWEIZ_MAX)


def crawl_ch_media(source, base, limit):
    """CH Media regional papers — monthly sitemap /sitemap/YYYY/MM/sitemap.xml.
    URLs end in -ld.NNNNNNN; strip that suffix for the title slug."""
    y = datetime.now(timezone.utc).strftime("%Y")
    m = datetime.now(timezone.utc).strftime("%m")
    url = f"{base}/sitemap/{y}/{m}/sitemap.xml"
    rows = sitemap_rows(fetch(url), re.compile(r"/[^/]+-ld\.\d+$"))
    return crawl_sitemap_source(source, rows, re.compile(r"/([^/]+)-ld\.\d+$"), limit)


def crawl_woz():
    """Drupal sitemapindex — newest articles are on the last page. URL pattern:
    /ISSUE/rubric/slug/!HASH — title from slug (second-to-last segment)."""
    index = ET.fromstring(fetch("https://www.woz.ch/sitemaps/editorial_content/sitemap.xml"))
    pages = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text:
            m = re.search(r"[?&]page=(\d+)", loc.text)
            if m:
                pages.append((int(m.group(1)), loc.text))
    if not pages:
        raise ValueError("no sitemap pages found")
    newest_url = max(pages)[1]
    article_re = re.compile(r"/\d+/[^/]+/([^/]+)/![A-Z0-9]+$")
    rows = sitemap_rows(fetch(newest_url), article_re)
    return crawl_sitemap_source("WOZ", rows, article_re, 50)


def crawl_bauernzeitung():
    """TYPO3 sitemapindex of paged article sitemaps — newest articles are on the
    highest page=N (ascending lastmod). Article URLs are /artikel/[category/]
    slug-<id> (id may be prefixed -0); title from slug, trailing id stripped."""
    index = ET.fromstring(fetch("https://www.bauernzeitung.ch/sitemap.xml"))
    pages = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text and "sitemap=articles" in loc.text:
            m = re.search(r"[?&]page=(\d+)", loc.text)
            if m:
                pages.append((int(m.group(1)), loc.text))
    if not pages:
        raise ValueError("no paged article sitemap entries found")
    newest = max(pages)[1]
    rows = sitemap_rows(fetch(newest), re.compile(r"/artikel/"))
    return crawl_sitemap_source(
        "Bauernzeitung", rows,
        re.compile(r"/artikel/(?:[^/]+/)*([^/]+?)(?:-0)?-\d+$"), BAUERNZEITUNG_MAX)


def crawl_nau():
    """Monthly Google-News sitemap — real <news:title> + publication_date."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    url = f"https://www.nau.ch/_sitemap/monthly/{ym}"
    return crawl_news_sitemap("Nau", url, 50)


def crawl_bilanz():
    """Monthly time-limited sitemap. URL = .../slug/<id>, so slug is the
    second-to-last path segment. Builds current month's URL."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    url = f"https://www.bilanz.ch/sitemap-articles-time-limited-{ym}.xml"
    rows = sitemap_rows(fetch(url), re.compile(r"/[^/]+/[^/]+$"))
    return crawl_sitemap_source(
        "Bilanz", rows, re.compile(r"/([^/]+)/[^/]+/?$"), BILANZ_MAX)


def crawl_republik():
    """Index of per-year sitemaps — newest year = highest. Article URLs are
    /YYYY/MM/DD/slug, title from last path segment."""
    index = ET.fromstring(fetch(REPUBLIK_SITEMAP))
    years = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text:
            m = re.search(r"/(\d{4})/sitemap", loc.text)
            if m:
                years.append((int(m.group(1)), loc.text))
    if not years:
        raise ValueError("no per-year sitemap entries found")
    newest = max(years)[1]
    article_re = re.compile(r"/\d{4}/\d{2}/\d{2}/([^/]+)$")
    rows = sitemap_rows(fetch(newest), article_re)
    return crawl_sitemap_source("Republik", rows, article_re, REPUBLIK_MAX)


def crawl_news_sitemap(source, url, limit):
    """Google-News sitemap — real <news:title> + publication_date, no slug guessing."""
    sm = ET.fromstring(fetch(url))
    rows = []
    for url_el in sm.iter():
        if local(url_el) != "url":
            continue
        loc = title = pub = ""
        for el in url_el.iter():
            name = local(el)
            if name == "loc" and not loc:
                loc = (el.text or "").strip()
            elif name == "title" and "sitemap-news" in el.tag:
                # news:title is the headline; image:title is the photo caption,
                # which also has local name "title" and would otherwise clobber it.
                title = (el.text or "").strip()
            elif name == "publication_date":
                pub = (el.text or "").strip()
        if loc and title:
            rows.append((pub, title, loc))
    rows.sort(reverse=True)  # newest publication_date first
    return [
        {"source": source, "title": t, "url": u, "summary": "", "published": p}
        for p, t, u in rows[:limit]
    ]


SOURCE_COLORS = {
    "SRF": "#d52b1e",
    "RTS": "#e2001a",
    "Le Temps": "#1a3c5e",
    "Blick": "#e2001a",
    "20 Minuten": "#0055aa",
    "Tages-Anzeiger": "#1c1c1c",
    "NZZ": "#444444",
    "Weltwoche": "#7a0019",
    "Nebelspalter": "#282f5c",
    "Watson": "#ff0066",
    "Watson FR": "#cc0052",
    "Inside Paradeplatz": "#2e7d32",
    "Infosperber": "#6a1b9a",
    "Berner Zeitung": "#003a70",
    "Tribune de Genève": "#0a4a8f",
    "Zentralplus": "#e94e1b",
    "Heidi.news": "#00897b",
    "Finews": "#1565c0",
    "Netzwoche": "#d81e05",
    "Le Courrier": "#b71c1c",
    "Inside IT": "#00838f",
    "Bilanz": "#9e2a2b",
    "Republik": "#111111",
    "Südostschweiz": "#2e6b3e",
    "Luzerner Zeitung": "#0277bd",
    "Aargauer Zeitung": "#e65100",
    "St. Galler Tagblatt": "#005ca9",
    "Thurgauer Zeitung": "#388e3c",
    "bz Basel": "#c62828",
    "Solothurner Zeitung": "#7b5e3a",
    "Oltner Tagblatt": "#4a7c59",
    "Badener Tagblatt": "#a0522d",
    "Grenchner Tagblatt": "#1976d2",
    "Limmattaler Zeitung": "#00796b",
    "Zofinger Tagblatt": "#558b2f",
    "Appenzeller Zeitung": "#ad1457",
    "Zuger Zeitung": "#1a237e",
    "Nidwaldner Zeitung": "#d32f2f",
    "Obwaldner Zeitung": "#bf360c",
    "Urner Zeitung": "#f9a825",
    "Freiburger Nachrichten": "#37474f",
    "Der Bund": "#1a3a5c",
    "Basler Zeitung": "#8b0000",
    "Nau": "#e67e22",
    "WOZ": "#c2b501",
    "Rathuus": "#5c6bc0",
    "Vorwärts": "#c62828",
    "Persönlich": "#6d4c41",
    "Tachles": "#1565c0",
    "Bauernzeitung": "#558b2f",
    "ETH Zürich": "#0072ac",
}


def fmt_datetime(iso_str):
    """Format ISO datetime as Swiss local time YYYY-MM-DD HH:MM:SS (mirrors fmtDateTime in script.js)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ZURICH).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""


def render_article_html(article):
    color = SOURCE_COLORS.get(article["source"], "#888")
    return (
        f'  <li>'
        f'<a href="{escape(article["url"])}" target="_blank" rel="noopener">{escape(article["title"])}</a>'
        f'<div class="info">'
        f'<span class="date">{escape(fmt_datetime(article.get("published", "")))}</span>'
        f'<span class="source" style="background:{color}">{escape(article["source"])}</span>'
        f'</div>'
        f'</li>'
    )


def write_colors_js():
    pairs = ",\n  ".join(f'"{k}": "{v}"' for k, v in SOURCE_COLORS.items())
    with open("colors.js", "w", encoding="utf-8") as f:
        f.write(f"const SOURCE_COLORS = {{\n  {pairs}\n}};\n")


def write_rendered_html(articles, dest_path, *, title, description, canonical, day_link):
    with open("template.html", encoding="utf-8") as f:
        tmpl = f.read()
    articles = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    items = "\n".join(render_article_html(a) for a in articles)
    count = f"{len(articles)} artikel"
    html = (tmpl
            .replace("<!-- TITLE -->", escape(title))
            .replace("<!-- DESCRIPTION -->", escape(description))
            .replace("<!-- CANONICAL -->", escape(canonical))
            .replace("<!-- META_COUNT -->", count)
            .replace("<!-- DAY_LINK -->", day_link)
            .replace("<!-- ARTICLES -->", items))
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(html)


def write_sitemap(dates):
    urls = [
        '  <url><loc>https://www.swissnews.org/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>',
        '  <url><loc>https://www.swissnews.org/archive.html</loc><changefreq>daily</changefreq><priority>0.5</priority></url>',
    ]
    for d in dates:
        urls.append(f'  <url><loc>https://www.swissnews.org/archive/{d}.html</loc><changefreq>never</changefreq><priority>0.3</priority></url>')
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.write("\n".join(urls))
        f.write("\n</urlset>\n")


def main():
    global _http_cache
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    try:
        with open(HTTP_CACHE_FILE, encoding="utf-8") as f:
            _http_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _http_cache = {}

    articles = []
    for feed in FEEDS:
        src, url = feed["source"], feed["url"]
        try:
            articles += parse_feed(src, fetch(url), feed.get("summary", True))
            print(f"  ok   {src}: {url}", file=sys.stderr)
        except NotModified:
            print(f"  skip {src}: not modified", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError) as e:
            print(f"  fail {src}: {url} -> {e}", file=sys.stderr)

    sitemap_jobs = [
        ("Weltwoche", crawl_weltwoche),
        ("Nebelspalter", crawl_nebelspalter),
        ("Bilanz", crawl_bilanz),
        ("Republik", crawl_republik),
        ("Südostschweiz", crawl_suedostschweiz),
        # Nau disabled: mostly reposts copied from other outlets, little original content
        # ("Nau", crawl_nau),
        ("WOZ", crawl_woz),
        ("Bauernzeitung", crawl_bauernzeitung),
    ]
    sitemap_jobs += [
        (s["source"], (lambda s: lambda: crawl_ch_media(s["source"], s["base"], s["max"]))(s))
        for s in CH_MEDIA_SOURCES
    ]
    sitemap_jobs += [
        (n["source"], (lambda n: lambda: crawl_news_sitemap(n["source"], n["url"], n["max"]))(n))
        for n in NEWS_SITEMAPS
    ]
    sitemap_jobs += [
        (w["source"], (lambda w: lambda: crawl_wp(w["source"], w["index"], w["max"]))(w))
        for w in WP_SOURCES
    ]
    for name, fn in sitemap_jobs:
        try:
            rows = fn()
            articles += rows
            print(f"  ok   {name}: {len(rows)} stories (sitemap)", file=sys.stderr)
        except NotModified:
            print(f"  skip {name}: not modified", file=sys.stderr)
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError, ValueError) as e:
            print(f"  fail {name}: {e}", file=sys.stderr)

    # One crawl per day. Keep only articles whose SOURCE date is today AND whose
    # URL was never crawled before (seen.json) — so a sitemap re-dating an old
    # article never re-adds it. Each kept article is stamped with the crawl time.
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(ZURICH).date().isoformat()

    # Preserve articles already saved for today (keep their first-seen crawl time)
    # so re-running the crawler on the same day appends rather than overwrites.
    try:
        with open("crawled.json", encoding="utf-8") as f:
            prev = json.load(f)
        existing_today = prev["articles"] if prev.get("date") == today else []
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        existing_today = []

    seen = load_seen()
    seen_titles = {a["title"].lower() for a in existing_today}
    new, batch = [], set()
    for a in articles:
        u = a["url"]
        if u in seen or u in batch:
            continue
        if not is_today(a.get("published")):
            continue
        t = a["title"].lower()
        if t in seen_titles:
            continue
        new.append({**a, "published": now_iso})  # date = crawl time
        batch.add(u)
        seen_titles.add(t)

    result = existing_today + new
    data = {"generated": now_iso, "date": today, "count": len(result), "articles": result}
    write_json("crawled.json", data)                       # newest crawl
    write_json(os.path.join(ARCHIVE_DIR, f"{today}.json"), data)  # this date's crawl
    write_json(SEEN_FILE, sorted(seen | batch))
    all_dates = archive_dates()
    write_json(INDEX_FILE, {"dates": all_dates})
    write_json(HTTP_CACHE_FILE, _http_cache)

    write_colors_js()
    write_rendered_html(
        result, "index.html",
        title="swissnews – Schweizer Nachrichten im Überblick",
        description="Alle Schweizer Nachrichtenquellen auf einen Blick: SRF, NZZ, Tages-Anzeiger, Blick, Watson und 40+ weitere Medien. Stündlich aktualisiert.",
        canonical="https://www.swissnews.org/",
        day_link='<a id="dayLink" href="/archive.html">archiv</a>',
    )
    write_rendered_html(
        result, os.path.join(ARCHIVE_DIR, f"{today}.html"),
        title=f"swissnews – {today}",
        description=f"Schweizer Nachrichtenlinks vom {today}.",
        canonical=f"https://www.swissnews.org/archive/{today}.html",
        day_link='<a id="dayLink" href="/archive.html">← archiv</a>',
    )
    write_sitemap(all_dates)
    print(f"wrote crawled.json: +{len(new)} new, {len(result)} total today ({today})",
          file=sys.stderr)


if __name__ == "__main__":
    main()
