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

## China & Russia (deferred — decision needed)

Deferred because the prominent outlets are mostly **state / party organs**, the
genuinely independent ones operate **in exile**, and RT/Sputnik are EU-banned.
None are currently in `crawler.py`.

### China (CN — zh / en)
| Source | Type | Status |
|---|---|---|
| **Caixin** (caixinglobal.com) | relatively independent business/investigative | **candidate** — probe |
| SCMP | Hong Kong (Alibaba-owned) | ✅ already included (under HK) |
| The Paper / Sixth Tone (thepaper.cn) | state-backed, news-focused | candidate if relaxing |
| Xinhua · People's Daily · Global Times · CGTN · China Daily | state / **CCP organ** | excluded under interim rule; add only as a deliberate "official line" inclusion |

### Russia (RU — ru / en)
| Source | Type | Status |
|---|---|---|
| **Meduza** (meduza.io) | independent, exiled (Riga); RU labels it "undesirable" | **candidate** — probe |
| **The Moscow Times** (EN) | independent, exiled (Amsterdam) | **candidate** — probe |
| Novaya Gazeta Europe | independent, exiled | candidate — probe |
| Kommersant · RBC · Interfax | domestic, under pressure | candidate if relaxing |
| TASS · RIA Novosti | state agency | excluded under interim rule |
| RT · Sputnik | state | **EU distribution ban (2022); CH did *not* adopt it** — risk-based call, not an automatic no. Google complied in EU News/YouTube/Ads, but content stays findable via mirrors and outside the EU |

> If Russian sources land, add language **`ru` (Русский)** to `LANG_NAMES`
> (script.js). `zh` and `ar`/`he` already added.

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

1. **Relax the "skip party organs/blogs/MT" heuristic to full Google-News parity?**
   This is the big one — it gates CN/RU state media, *Unsere Zeit* (DKP),
   *Deutsche Stimme* (NPD), and the blog-format outlets.
2. **Compact** — include despite the suspended ban? (legal caution for a DE/EU audience)
3. **Independent CN/RU** (Caixin, Meduza, Moscow Times, Novaya Gazeta Europe) —
   these are rule-compliant *today*; probe + add whenever we want, independent of #1.
