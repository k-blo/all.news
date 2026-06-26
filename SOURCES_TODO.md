# Sources — backlog & inclusion policy (TODO)

Tracks sources we've **deliberately deferred** and the open editorial decisions
behind them. For how sources are wired up, see `CLAUDE.md` and `crawler.py`.

## Inclusion philosophy

all.news is a **neutral aggregator** — we don't pre-select or curate the "right"
information. Working principle:

> **If Google News indexes a source, it's a candidate for us too.**

That keeps inclusion judgment-free. Only three things hard-override it:

1. **Legality** in served jurisdictions. A genuine legal prohibition where we
   operate overrides inclusion. Note this is **geo-scoped and often grey**: e.g.
   the EU's 2022 **distribution ban** on RT/Sputnik targets broadcasters/ISPs in
   the EU, **Switzerland did not adopt it** (all.news is Swiss-rooted), and whether
   an aggregator merely *linking out* counts as "facilitating distribution" is
   legally untested. So treat such cases as a **risk-based decision**, not an
   automatic no. (Not legal advice — get a real check before it matters at scale.)
2. **robots.txt** — we never crawl a feed/sitemap path a site disallows (standing
   project rule). A `Disallow: /` for our UA = not usable.
3. **Technical viability** — a working RSS feed or news sitemap with real titles +
   parseable dates; must dedupe cleanly.

The earlier interim heuristic — *"skip party organs, blogs, machine-translated
editions"* — is a **quality preference, not a neutrality rule.** Whether to relax
it (and thus include state media, party papers, etc.) is an open decision below.

---

## China & Russia (added — state + independent/exile)

Now in `crawler.py` under full Google-News parity (the interim "skip state/party
organs" heuristic is relaxed). `ru` (Русский) added to `LANG_NAMES`, and `CN`/`RU`
to `COUNTRY_NAMES` + `TZ_COUNTRY` (script.js). RT is included as a risk-based,
Swiss-rooted call (EU's 2022 distribution ban was **not** adopted by CH).

### China (CN — en)
| Source | Type | Status |
|---|---|---|
| **CGTN** | state broadcaster | ✅ added — `/subscribe/rss/section/world.xml`, fresh dates |
| **China Digital Times** | independent, in exile (US) | ✅ added — `/feed/`, robots `*: Allow /` |
| SCMP | Hong Kong (Alibaba-owned) | ✅ already included (under HK) |
| Xinhua · People's Daily · China Daily · Global Times | state / CCP organ | ❌ **not usable** — English RSS ship static/broken dates (2012–2018) or a popularity feed (GT, newest ~8 days); no working news sitemap. People's Daily *zh* is also stale (newest 2025). Re-probe only if they fix dates. |
| Caixin · Sixth Tone | independent / state-backed | no public RSS (404) / near-empty feed |

### Russia (RU — ru / en)
| Source | Type | Status |
|---|---|---|
| **TASS** (EN) | state agency | ✅ added — `/rss/v2.xml` |
| **RT** (EN) | state | ✅ added — `/rss/` (EU ban 2022; CH did *not* adopt — risk-based) |
| **RIA Novosti** (ru) | state agency | ✅ added — `/export/rss2/archive/index.xml` |
| **Meduza** (ru) | independent, exiled (Riga) | ✅ added — `/rss/all` |
| **The Moscow Times** (EN) | independent, exiled (Amsterdam) | ✅ added — `/rss/news` |
| **Novaya Gazeta Europe** (ru) | independent, exiled | ✅ added — `/feed/rss` |
| **Mediazona** (ru) | independent, exiled | ✅ added — `zona.media/rss` |
| Kommersant · RBC · Interfax | domestic, under pressure | candidate — not yet probed |

---

## Smaller German political-spectrum outlets (deferred)

Probed earlier; **none added yet**. Held back under the "skip party organs/blogs"
heuristic. Revisit under the neutral principle above. Note: for a DE-served
audience several carry legal/extremism context (Verfassungsschutz-monitored), which
is a real consideration independent of editorial neutrality. `today` = articles
seen on the probe day.

### Left-wing (DE)
| Source | Feed | today | robots | Note |
|---|---|---|---|---|
| Junge Welt | `/feeds/newsticker.rss` | 35 | ok | Marxist daily; VS-observed |
| nd (nd-aktuell) | news sitemap | 13 | ok | socialist daily |
| Kontext:Wochenzeitung | news sitemap (`?type=9819`) | 14 | ok | investigative-left weekly |
| der Freitag | `/@@RSS` | 8 | ok | left-liberal weekly |
| Nachdenkseiten | `/?feed=rss2` | 6 | ok | left / anti-establishment (blog-format) |
| Overton Magazin | `/feed/` | 3 | ok | low volume |
| Hintergrund | `/feed/` | 1 | ok | low volume |
| Perspektive | news sitemap | 1 | ok | communist youth org |
| Unsere Zeit | `/feed/` | 30 | ok | **DKP party organ** → excluded under interim rule |
| Netzpolitik.org | sitemap (RSS `Disallow`'d) | — | feed blocked | digital-rights; would need sitemap path |
| Lower Class Mag · Marx21 · Jacobin DE | `/feed` | low / 0 | ok | low frequency / magazine |
| analyse&kritik · Telepolis · Katapult | — | — | — | no usable feed (410 / paywall / none) |

### Right-wing (DE)
| Source | Feed | today | robots | Note |
|---|---|---|---|---|
| Compact | `/feed/` | 7 | ok | **2024 federal ban, court-suspended** → legal risk |
| Zuerst! | `/feed/` | 4 | ok | far-right monthly |
| Sezession | `/feed` | 0 (low) | ok | Neue Rechte (IfS milieu) |
| Eigentümlich frei | `/feed` | 0 (low) | ok | libertarian-right |
| Deutsche Stimme | `/feed/` | 0 (low) | ok | **NPD / "Die Heimat" party organ** → excluded under interim rule |
| Junge Freiheit | — | — | **`Disallow: /`** | blocked by robots → not usable |
| PI-News | `/feed/` | — | **403 to our UA** | not crawlable without UA spoofing |
| Kopp Report | `/feed/` | — | parse error / sitemap 500 | broken |
| Umwelt & Aktiv | — | — | no feed/sitemap | not usable |
| Blaue Narzisse · National-Zeitung · DMZ · Mensch und Maß | — | — | defunct / stale | dead |

---

## Blocked / not crawlable (don't re-investigate)

Legitimate sources we *can't* technically ingest — logged so we don't keep re-probing.

| Source | Country | Why |
|---|---|---|
| Times of Israel | IL | RSS `/feed/` + `/rss-feed/` robots-disallowed; `sitemap_index.xml` is robots-allowed but the server **403s every non-residential client** (Cloudflare bot-mgmt / datacenter-IP block — a browser UA also 403s from our IPs). Also opted **out of Google News** (`Googlebot-News: Disallow: /`). Would need a residential/JS-capable fetch — not worth it for one source. |

(Same datacenter-IP 403 pattern as CH Media, which we solve via the Swiss VPN —
but ToI also fails with a browser UA, so it's likely a JS challenge, not just IP.)

## Open decisions

1. ~~Relax the "skip party organs/blogs/MT" heuristic to full Google-News parity?~~
   **Decided: yes.** CN/RU state media (CGTN, TASS, RT, RIA) + independents are in.
   The German party organs (*Unsere Zeit*, *Deutsche Stimme*) and blog-format
   outlets are now eligible on the same basis — add when wanted.
2. **Compact** — include despite the suspended ban? (legal caution for a DE/EU audience)
3. ~~Independent CN/RU~~ — **done** (Meduza, Moscow Times, Novaya Gazeta Europe,
   Mediazona, China Digital Times). Caixin has no public RSS.
