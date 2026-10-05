#!/usr/bin/env python3
"""all.news crawler — POC.

Fetches RSS feeds from Swiss news sites, extracts title + link (+ short summary),
writes crawled.json for the static site to consume.

Stdlib only. Run: python3 crawler.py
"""

import gzip
import json
import os
import re
import sys
import unicodedata
import urllib.request
import urllib.error
import zlib
from urllib.parse import urlsplit
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import escape, unescape
from html.entities import html5 as HTML5_ENTITIES
from zoneinfo import ZoneInfo

ZURICH = ZoneInfo("Europe/Zurich")
# Canonical origin for every absolute URL we emit (canonical, og:url, hreflang,
# sitemap). Must be the host that actually serves 200s: the apex 301s to www, and
# a canonical/hreflang pointing at a redirect is a signal Google discards. Keep
# this in sync with the absolute URLs hardcoded in template.html and archive.html.
SITE_ORIGIN = "https://www.all.news"
ARCHIVE_DIR = "archive"
SEEN_FILE = os.path.join(ARCHIVE_DIR, "seen.json")
INDEX_FILE = os.path.join(ARCHIVE_DIR, "index.json")
HTTP_CACHE_FILE = os.path.join(ARCHIVE_DIR, "http_cache.json")
# Per-country shards of today's feed (data/<cc>.json) + data/manifest.json, so the
# site downloads only the countries a visitor filters to instead of the whole world.
DATA_DIR = "data"

# RSS-first: only sites that publish a feed (syndication intent). Title + link
# always safe to aggregate; summary truncated. Edit/extend this list freely.
FEEDS = [
    {"source": "SRF",           "url": "https://www.srf.ch/news/bnf/rss/1890"},
    {"source": "RTS",           "url": "https://www.rts.ch/info/?format=rss/news"},
    {"source": "Le Temps",      "url": "https://www.letemps.ch/articles.rss"},
    {"source": "Blick",         "url": "https://www.blick.ch/news/rss.xml"},
    {"source": "20 Minuten",    "url": "https://partner-feeds.20min.ch/rss/20minuten"},
    {"source": "Tages-Anzeiger","url": "https://partner-feeds.publishing.tamedia.ch/rss/tagesanzeiger/"},
    {"source": "Berner Zeitung","url": "https://partner-feeds.publishing.tamedia.ch/rss/bernerzeitung/"},
    {"source": "Der Bund",      "url": "https://partner-feeds.publishing.tamedia.ch/rss/derbund/"},
    {"source": "Basler Zeitung","url": "https://partner-feeds.publishing.tamedia.ch/rss/bazonline/"},
    {"source": "Tribune de Genève","url": "https://partner-feeds.publishing.tamedia.ch/rss/tdg/"},
    {"source": "Zentralplus",   "url": "https://www.zentralplus.ch/feed/"},
    {"source": "Heidi.news",    "url": "https://www.heidi.news/articles.rss"},
    {"source": "Finews",        "url": "https://www.finews.ch/news?format=feed"},
    {"source": "Netzwoche",     "url": "https://www.netzwoche.ch/rss.xml"},
    {"source": "Le Courrier",   "url": "https://lecourrier.ch/feed/"},
    {"source": "Inside IT",     "url": "https://www.inside-it.ch/rss.xml"},
    {"source": "NZZ",           "url": "https://www.nzz.ch/recent.rss", "summary": False},
    {"source": "Persönlich",    "url": "https://www.persoenlich.com/rss/news.xml"},
    {"source": "Schaffhauser Nachrichten", "url": "https://www.shn.ch/rss.xml"},
    {"source": "Schweizer Monat","url": "https://schweizermonat.ch/feed/"},
    #{"source": "ETH Zürich",     "url": "https://www.ethz.ch/de/news-und-veranstaltungen/eth-news/news/_jcr_content.feed"},
    # --- Germany (DE) — see SOURCE_ORIGIN for country labels ---
    {"source": "Tagesschau",    "url": "https://www.tagesschau.de/index~rss2.xml"},
    {"source": "Süddeutsche",   "url": "https://rss.sueddeutsche.de/rss/Topthemen"},
    {"source": "FAZ",           "url": "https://www.faz.net/rss/aktuell/"},
    {"source": "Die Welt",      "url": "https://www.welt.de/feeds/latest.rss"},
    {"source": "taz",           "url": "https://taz.de/!p4608;rss/"},
    {"source": "n-tv",          "url": "https://www.n-tv.de/rss"},
    {"source": "Der Spiegel",   "url": "https://www.spiegel.de/schlagzeilen/tops/index.rss"},
    {"source": "Stern",         "url": "https://www.stern.de/feed/standard/all/"},
    {"source": "DW",            "url": "https://rss.dw.com/rdf/rss-de-all"},
    # --- France (FR) — see SOURCE_ORIGIN for lang/country labels ---
    {"source": "Le Monde",      "url": "https://www.lemonde.fr/rss/une.xml"},
    {"source": "Le Figaro",     "url": "https://www.lefigaro.fr/rss/figaro_actualites.xml"},
    {"source": "Libération",    "url": "https://www.liberation.fr/arc/outboundfeeds/rss-all/?outputType=xml"},
    {"source": "franceinfo",    "url": "https://www.francetvinfo.fr/titres.rss"},
    {"source": "France 24",     "url": "https://www.france24.com/fr/rss"},
    {"source": "RFI",           "url": "https://www.rfi.fr/fr/rss"},
    {"source": "L'Express",     "url": "https://www.lexpress.fr/rss/alaune.xml"},
    {"source": "L'Obs",         "url": "https://www.nouvelobs.com/rss.xml"},
    {"source": "La Croix",      "url": "https://www.la-croix.com/RSS"},
    {"source": "20 Minutes",    "url": "https://www.20minutes.fr/feeds/rss-une.xml"},
    {"source": "La Tribune",    "url": "https://www.latribune.fr/rss/rubriques/actualite.html"},
    {"source": "BFM TV",        "url": "https://www.bfmtv.com/rss/news-24-7/"},
    {"source": "Mediapart",     "url": "https://www.mediapart.fr/articles/feed"},
    # --- United Kingdom (GB, en) ---
    {"source": "BBC News",      "url": "https://feeds.bbci.co.uk/news/rss.xml"},
    {"source": "The Guardian",  "url": "https://www.theguardian.com/uk/rss"},
    {"source": "The Independent","url": "https://www.independent.co.uk/news/uk/rss"},
    {"source": "The Telegraph", "url": "https://www.telegraph.co.uk/rss.xml"},
    {"source": "Sky News",      "url": "https://feeds.skynews.com/feeds/rss/home.xml"},
    {"source": "Daily Mail",    "url": "https://www.dailymail.co.uk/articles.rss"},
    {"source": "Mirror",        "url": "https://www.mirror.co.uk/news/?service=rss"},
    {"source": "Metro",         "url": "https://metro.co.uk/feed/"},
    {"source": "Evening Standard","url": "https://www.standard.co.uk/rss"},
    {"source": "Financial Times","url": "https://www.ft.com/rss/home"},
    # --- United States (US, en) ---
    {"source": "The New York Times","url": "https://rss.nytimes.com/services/xml/rss/nyt/HomePage.xml"},
    {"source": "NPR",           "url": "https://feeds.npr.org/1001/rss.xml"},
    {"source": "ABC News",      "url": "https://abcnews.go.com/abcnews/topstories"},
    {"source": "NBC News",      "url": "http://feeds.nbcnews.com/feeds/topstories"},
    {"source": "Fox News",      "url": "https://moxie.foxnews.com/google-publisher/latest.xml"},
    {"source": "The Hill",      "url": "https://thehill.com/news/feed/"},
    {"source": "Washington Post","url": "https://feeds.washingtonpost.com/rss/world"},
    {"source": "LA Times",      "url": "https://www.latimes.com/local/rss2.0.xml"},
    # --- Italy (IT, it) ---
    {"source": "la Repubblica", "url": "https://www.repubblica.it/rss/homepage/rss2.0.xml"},
    {"source": "ANSA",          "url": "https://www.ansa.it/sito/ansait_rss.xml"},
    {"source": "Il Giornale",   "url": "https://www.ilgiornale.it/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Il Sole 24 Ore","url": "https://www.ilsole24ore.com/rss/italia.xml"},
    # --- Spain (ES, es) ---
    {"source": "El Mundo",      "url": "https://e00-elmundo.uecdn.es/elmundo/rss/portada.xml"},
    {"source": "ABC",           "url": "https://www.abc.es/rss/feeds/abc_EspanaEspana.xml"},
    {"source": "elDiario.es",   "url": "https://www.eldiario.es/rss/"},
    {"source": "20minutos",     "url": "https://www.20minutos.es/rss/"},
    {"source": "El Confidencial","url": "https://rss.elconfidencial.com/espana/"},
    # ===== Core-country expansion (≥20 sources/country; see SOURCE_ORIGIN) =====
    # --- Germany (DE) ---
    {"source": "Handelsblatt",       "url": "https://www.handelsblatt.com/contentexport/feed/schlagzeilen"},
    {"source": "Tagesspiegel",       "url": "https://www.tagesspiegel.de/contentexport/feed/home"},
    {"source": "Frankfurter Rundschau","url": "https://www.fr.de/rssfeed.rdf"},
    {"source": "Heise",              "url": "https://www.heise.de/rss/heise-atom.xml"},
    {"source": "WirtschaftsWoche",   "url": "https://www.wiwo.de/contentexport/feed/rss/schlagzeilen"},
    {"source": "Manager Magazin",    "url": "https://www.manager-magazin.de/news/index.rss"},
    {"source": "RP Online",          "url": "https://rp-online.de/feed.rss"},
    {"source": "Merkur",             "url": "https://www.merkur.de/rssfeed.rdf"},
    {"source": "MDR",                "url": "https://www.mdr.de/nachrichten/index-rss.xml"},
    {"source": "Berliner Zeitung",   "url": "https://www.berliner-zeitung.de/feed.xml"},
    {"source": "t-online",           "url": "https://www.t-online.de/nachrichten/feed.rss"},
    # --- France (FR) ---
    {"source": "Courrier International","url": "https://www.courrierinternational.com/feed/all/rss.xml"},
    {"source": "La Dépêche",         "url": "https://www.ladepeche.fr/rss.xml"},
    {"source": "France Inter",       "url": "https://www.radiofrance.fr/franceinter/rss"},
    {"source": "Europe 1",           "url": "https://www.europe1.fr/rss.xml"},
    {"source": "Slate FR",           "url": "https://www.slate.fr/rss.xml"},
    {"source": "Challenges",         "url": "https://www.challenges.fr/rss.xml"},
    {"source": "France Bleu",        "url": "https://www.radiofrance.fr/francebleu/rss"},
    {"source": "Numerama",           "url": "https://www.numerama.com/feed/"},
    {"source": "Télérama",           "url": "https://www.telerama.fr/rss/une.xml"},
    {"source": "HuffPost FR",        "url": "https://www.huffingtonpost.fr/feeds/index.xml"},
    # --- United Kingdom (GB) ---
    {"source": "Daily Star",         "url": "https://www.dailystar.co.uk/?service=rss"},
    {"source": "iNews",              "url": "https://inews.co.uk/feed"},
    {"source": "City AM",            "url": "https://www.cityam.com/feed/"},
    {"source": "New Statesman",      "url": "https://www.newstatesman.com/feed"},
    {"source": "Wales Online",       "url": "https://www.walesonline.co.uk/?service=rss"},
    {"source": "The Scotsman",       "url": "https://www.scotsman.com/rss"},
    {"source": "The Herald",         "url": "https://www.heraldscotland.com/news/rss/"},
    {"source": "Manchester Evening News","url": "https://www.manchestereveningnews.co.uk/?service=rss"},
    {"source": "Belfast Telegraph",  "url": "https://www.belfasttelegraph.co.uk/rss/"},
    {"source": "The Conversation",   "url": "https://theconversation.com/uk/articles.atom"},
    # --- United States (US) ---
    {"source": "CBS News",           "url": "https://www.cbsnews.com/latest/rss/main"},
    {"source": "CNBC",               "url": "https://www.cnbc.com/id/100003114/device/rss/rss.html"},
    {"source": "The Atlantic",       "url": "https://www.theatlantic.com/feed/all/"},
    {"source": "Vox",                "url": "https://www.vox.com/rss/index.xml"},
    {"source": "The Verge",          "url": "https://www.theverge.com/rss/index.xml"},
    {"source": "TechCrunch",         "url": "https://techcrunch.com/feed/"},
    {"source": "Newsweek",           "url": "https://www.newsweek.com/rss"},
    {"source": "PBS NewsHour",       "url": "https://www.pbs.org/newshour/feeds/rss/headlines"},
    {"source": "NY Post",            "url": "https://nypost.com/feed/"},
    {"source": "The Daily Beast",    "url": "https://www.thedailybeast.com/arc/outboundfeeds/rss/"},
    {"source": "Wired",              "url": "https://www.wired.com/feed/rss"},
    {"source": "ProPublica",         "url": "https://www.propublica.org/feeds/propublica/main"},
    # --- Italy (IT) ---
    {"source": "Rai News",           "url": "https://www.rainews.it/rss/cronaca"},
    {"source": "Adnkronos",          "url": "https://www.adnkronos.com/RSS_PrimaPagina.xml"},
    {"source": "TGcom24",            "url": "https://www.tgcom24.mediaset.it/rss/homepage.xml"},
    {"source": "Open",               "url": "https://www.open.online/feed/"},
    {"source": "Il Giorno",          "url": "https://www.ilgiorno.it/rss"},
    {"source": "Il Resto del Carlino","url": "https://www.ilrestodelcarlino.it/rss"},
    {"source": "La Nazione",         "url": "https://www.lanazione.it/rss"},
    {"source": "AGI",                "url": "https://www.agi.it/cronaca/rss"},
    {"source": "Today",              "url": "https://www.today.it/rss"},
    {"source": "Il Mattino",         "url": "https://www.ilmattino.it/rss/home.xml"},
    {"source": "Il Messaggero",      "url": "https://www.ilmessaggero.it/rss/home.xml"},
    {"source": "Il Gazzettino",      "url": "https://www.ilgazzettino.it/rss/home.xml"},
    {"source": "Quotidiano.net",     "url": "https://www.quotidiano.net/rss"},
    {"source": "askanews",           "url": "https://www.askanews.it/feed/"},
    {"source": "Domani",             "url": "https://www.editorialedomani.it/rss"},
    # --- Spain (ES) ---
    {"source": "El Español",         "url": "https://www.elespanol.com/rss/"},
    {"source": "COPE",               "url": "https://www.cope.es/api/es/news/rss.xml"},
    {"source": "Europa Press",       "url": "https://www.europapress.es/rss/rss.aspx"},
    {"source": "Marca",              "url": "https://www.marca.com/rss/portada.xml"},
    {"source": "Expansión",          "url": "https://e00-expansion.uecdn.es/rss/portada.xml"},
    {"source": "La Vanguardia",      "url": "https://www.lavanguardia.com/rss/home.xml"},
    {"source": "El Correo",          "url": "https://www.elcorreo.com/rss/2.0/portada"},
    {"source": "infoLibre",          "url": "https://www.infolibre.es/rss/"},
    {"source": "Mundo Deportivo",    "url": "https://www.mundodeportivo.com/rss/home.xml"},
    {"source": "El Salto",           "url": "https://www.elsaltodiario.com/general/feed"},
    {"source": "Las Provincias",     "url": "https://www.lasprovincias.es/rss/2.0/portada"},
    {"source": "La Verdad",          "url": "https://www.laverdad.es/rss/2.0/portada"},
    {"source": "Ideal",              "url": "https://www.ideal.es/rss/2.0/portada"},
    {"source": "Diario Sur",         "url": "https://www.diariosur.es/rss/2.0/portada"},
    {"source": "El Diario Vasco",    "url": "https://www.diariovasco.com/rss/2.0/portada"},
    {"source": "Newtral",            "url": "https://www.newtral.es/feed/"},
    {"source": "Maldita",            "url": "https://maldita.es/feed/"},
    {"source": "El Independiente",   "url": "https://www.elindependiente.com/feed/"},
    # ===== Wider expansion: rest of the countries (see SOURCE_ORIGIN) =====
    # Netherlands
    {"source": "De Telegraaf","url": "https://www.telegraaf.nl/rss"},
    {"source": "de Volkskrant","url": "https://www.volkskrant.nl/voorpagina/rss.xml"},
    {"source": "NRC","url": "https://www.nrc.nl/rss/"},
    {"source": "Trouw","url": "https://www.trouw.nl/voorpagina/rss.xml"},
    {"source": "Het Parool","url": "https://www.parool.nl/voorpagina/rss.xml"},
    {"source": "AD","url": "https://www.ad.nl/home/rss.xml"},
    {"source": "Het Financieele Dagblad","url": "https://fd.nl/?rss"},
    {"source": "De Limburger","url": "https://www.limburger.nl/rss"},
    {"source": "Nederlands Dagblad","url": "https://www.nd.nl/rss"},
    {"source": "De Gelderlander","url": "https://www.gelderlander.nl/home/rss.xml"},
    {"source": "Brabants Dagblad","url": "https://www.bd.nl/home/rss.xml"},
    {"source": "Tubantia","url": "https://www.tubantia.nl/home/rss.xml"},
    {"source": "BN DeStem","url": "https://www.bndestem.nl/home/rss.xml"},
    {"source": "Eindhovens Dagblad","url": "https://www.ed.nl/home/rss.xml"},
    {"source": "PZC","url": "https://www.pzc.nl/home/rss.xml"},
    {"source": "De Stentor","url": "https://www.destentor.nl/home/rss.xml"},
    # Belgium
    {"source": "Het Laatste Nieuws","url": "https://www.hln.be/home/rss.xml"},
    {"source": "7sur7","url": "https://www.7sur7.be/home/rss.xml"},
    {"source": "La Libre","url": "https://www.lalibre.be/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Le Vif","url": "https://www.levif.be/feed/"},
    # Austria
    {"source": "Kurier","url": "https://kurier.at/xml/rss"},
    {"source": "Kleine Zeitung","url": "https://www.kleinezeitung.at/rss"},
    {"source": "futurezone","url": "https://futurezone.at/xml/rss"},
    # Portugal
    {"source": "Observador","url": "https://observador.pt/feed/"},
    {"source": "Expresso","url": "https://feeds.feedburner.com/expresso-geral"},
    {"source": "ECO","url": "https://eco.sapo.pt/feed/"},
    {"source": "Notícias ao Minuto","url": "https://www.noticiasaominuto.com/rss/ultima-hora"},
    {"source": "Jornal de Negócios","url": "https://www.jornaldenegocios.pt/rss"},
    {"source": "Sapo24","url": "https://24.sapo.pt/rss"},
    # Sweden
    {"source": "Dagens Nyheter","url": "https://www.dn.se/rss/"},
    {"source": "Svenska Dagbladet","url": "https://www.svd.se/feed/articles.rss"},
    {"source": "Expressen","url": "https://feeds.expressen.se/nyheter/"},
    {"source": "Dagens Industri","url": "https://www.di.se/rss"},
    {"source": "Sydsvenskan","url": "https://www.sydsvenskan.se/rss"},
    {"source": "Göteborgs-Posten","url": "https://www.gp.se/rss"},
    # Norway
    {"source": "Aftenposten","url": "https://www.aftenposten.no/rss"},
    {"source": "Bergens Tidende","url": "https://www.bt.no/rss"},
    {"source": "Nettavisen","url": "https://www.nettavisen.no/service/rich-rss"},
    {"source": "E24","url": "https://e24.no/rss"},
    # Denmark
    {"source": "Politiken","url": "https://politiken.dk/rss/senestenyt.rss"},
    {"source": "Berlingske","url": "https://www.berlingske.dk/content/rss"},
    {"source": "BT","url": "https://www.bt.dk/bt/seneste/rss"},
    {"source": "Børsen","url": "https://borsen.dk/rss"},
    # Finland
    {"source": "Helsingin Sanomat","url": "https://www.hs.fi/rss/tuoreimmat.xml"},
    {"source": "Ilta-Sanomat","url": "https://www.is.fi/rss/tuoreimmat.xml"},
    {"source": "MTV Uutiset","url": "https://www.mtvuutiset.fi/api/feed/rss/uutiset_uusimmat"},
    # Poland
    {"source": "Rzeczpospolita","url": "https://www.rp.pl/rss_main"},
    {"source": "TVN24","url": "https://tvn24.pl/najnowsze.xml"},
    {"source": "Polsat News","url": "https://www.polsatnews.pl/rss/wszystkie.xml"},
    {"source": "Interia","url": "https://fakty.interia.pl/feed"},
    {"source": "Gazeta.pl","url": "https://rss.gazeta.pl/pub/rss/wiadomosci.xml"},
    {"source": "Wprost","url": "https://www.wprost.pl/rss"},
    {"source": "Newsweek Polska","url": "https://www.newsweek.pl/rss.xml"},
    # Greece
    {"source": "Ta Nea","url": "https://www.tanea.gr/feed/"},
    {"source": "Naftemporiki","url": "https://www.naftemporiki.gr/feed/"},
    {"source": "iefimerida","url": "https://www.iefimerida.gr/rss.xml"},
    {"source": "in.gr","url": "https://www.in.gr/feed/"},
    # Czechia
    {"source": "Seznam Zprávy","url": "https://www.seznamzpravy.cz/rss"},
    {"source": "Deník","url": "https://www.denik.cz/rss/zpravy.html"},
    {"source": "České noviny","url": "https://www.ceskenoviny.cz/sluzby/rss/zpravy.php"},
    {"source": "iROZHLAS","url": "https://www.irozhlas.cz/rss/irozhlas"},
    {"source": "Deník N","url": "https://denikn.cz/feed/"},
    # Hungary
    {"source": "Index","url": "https://index.hu/24ora/rss/"},
    {"source": "444","url": "https://444.hu/feed"},
    {"source": "Portfolio","url": "https://www.portfolio.hu/rss/all.xml"},
    {"source": "24.hu","url": "https://24.hu/feed/"},
    {"source": "Qubit","url": "https://qubit.hu/feed"},
    # Romania
    {"source": "Adevărul","url": "https://adevarul.ro/rss"},
    {"source": "Libertatea","url": "https://www.libertatea.ro/feed"},
    {"source": "Gândul","url": "https://www.gandul.ro/rss"},
    {"source": "ProTV Știrile","url": "https://stirileprotv.ro/rss"},
    {"source": "G4Media","url": "https://www.g4media.ro/feed"},
    # Ukraine
    {"source": "Unian","url": "https://rss.unian.net/site/news_ukr.rss"},
    {"source": "NV","url": "https://nv.ua/rss/all.xml"},
    {"source": "Ukrinform","url": "https://www.ukrinform.net/rss/block-lastnews"},
    # Turkey
    {"source": "Sabah","url": "https://www.sabah.com.tr/rss/anasayfa.xml"},
    {"source": "Milliyet","url": "https://www.milliyet.com.tr/rss/rssNew/gundemRss.xml"},
    {"source": "Cumhuriyet","url": "https://www.cumhuriyet.com.tr/rss/son_dakika.xml"},
    {"source": "NTV","url": "https://www.ntv.com.tr/gundem.rss"},
    {"source": "TRT Haber","url": "https://www.trthaber.com/sondakika.rss"},
    # Canada
    {"source": "Global News","url": "https://globalnews.ca/feed/"},
    {"source": "National Post","url": "https://nationalpost.com/feed/"},
    {"source": "Financial Post","url": "https://financialpost.com/feed/"},
    {"source": "Toronto Sun","url": "https://torontosun.com/feed/"},
    {"source": "Le Devoir","url": "https://www.ledevoir.com/rss/manchettes.xml"},
    # Brazil
    {"source": "Veja","url": "https://veja.abril.com.br/feed/"},
    {"source": "Metrópoles","url": "https://www.metropoles.com/feed"},
    {"source": "Poder360","url": "https://www.poder360.com.br/feed/"},
    # Argentina
    {"source": "Clarín","url": "https://www.clarin.com/rss/lo-ultimo/"},
    {"source": "Infobae","url": "https://www.infobae.com/arc/outboundfeeds/rss/"},
    {"source": "Ámbito","url": "https://www.ambito.com/rss/pages/home.xml"},
    {"source": "Perfil","url": "https://www.perfil.com/feed"},
    {"source": "TN","url": "https://tn.com.ar/feed/"},
    # Colombia
    {"source": "La República","url": "https://www.larepublica.co/rss"},
    # Peru
    {"source": "Andina","url": "https://andina.pe/agencia/rss.aspx"},
    # Australia
    {"source": "The Age","url": "https://www.theage.com.au/rss/feed.xml"},
    {"source": "Guardian Australia","url": "https://www.theguardian.com/australia-news/rss"},
    {"source": "Brisbane Times","url": "https://www.brisbanetimes.com.au/rss/feed.xml"},
    {"source": "AFR","url": "https://www.afr.com/rss/feed.xml"},
    {"source": "Conversation AU","url": "https://theconversation.com/au/articles.atom"},
    # New Zealand
    {"source": "The Spinoff","url": "https://thespinoff.co.nz/api/rss"},
    {"source": "Newsroom","url": "https://www.newsroom.co.nz/feed"},
    # India
    {"source": "Times of India","url": "https://timesofindia.indiatimes.com/rssfeedstopstories.cms"},
    {"source": "Hindustan Times","url": "https://www.hindustantimes.com/feeds/rss/india-news/rssfeed.xml"},
    {"source": "Economic Times","url": "https://economictimes.indiatimes.com/rssfeedstopstories.cms"},
    {"source": "News18","url": "https://www.news18.com/rss/india.xml"},
    {"source": "India Today","url": "https://www.indiatoday.in/rss/1206578"},
    {"source": "Livemint","url": "https://www.livemint.com/rss/news"},
    # Japan
    {"source": "Mainichi","url": "https://mainichi.jp/rss/etc/mainichi-flash.rss"},
    {"source": "Japan Today","url": "https://japantoday.com/feed"},
    # South Korea
    {"source": "Korea Times","url": "https://www.koreatimes.co.kr/www/rss/nation.xml"},
    # Singapore
    {"source": "The Independent SG","url": "https://theindependent.sg/feed/"},
    # Indonesia
    {"source": "CNN Indonesia","url": "https://www.cnnindonesia.com/rss"},
    {"source": "Antara","url": "https://www.antaranews.com/rss/terkini"},
    # Philippines
    {"source": "Philstar","url": "https://www.philstar.com/rss/headlines"},
    {"source": "GMA News","url": "https://data.gmanetwork.com/gno/rss/news/feed.xml"},
    # Vietnam
    {"source": "Thanh Nien","url": "https://thanhnien.vn/rss/home.rss"},
    {"source": "Dan Tri","url": "https://dantri.com.vn/rss/home.rss"},
    {"source": "VnExpress Intl","url": "https://e.vnexpress.net/rss/news.rss"},
    # Pakistan
    {"source": "ARY News","url": "https://arynews.tv/feed/"},
    # Israel
    {"source": "Ynet","url": "https://www.ynet.co.il/Integration/StoryRss2.xml"},
    # Hong Kong
    {"source": "HKFP","url": "https://hongkongfp.com/feed/"},
    {"source": "RTHK","url": "https://rthk.hk/rthk/news/rss/e_expressnews_elocal.xml"},
    # Ireland
    {"source": "Irish Independent","url": "https://www.independent.ie/rss"},
    {"source": "The Journal","url": "https://www.thejournal.ie/feed/"},
    {"source": "Irish Mirror","url": "https://www.irishmirror.ie/?service=rss"},
    # ===== Additional countries (see SOURCE_ORIGIN for lang/country) =====
    # --- Netherlands (NL, nl) ---
    {"source": "NOS",           "url": "https://feeds.nos.nl/nosnieuwsalgemeen"},
    {"source": "NU.nl",         "url": "https://www.nu.nl/rss/Algemeen"},
    # --- Belgium (BE, nl) ---
    {"source": "VRT NWS",       "url": "https://www.vrt.be/vrtnws/nl.rss.articles.xml"},
    # --- Austria (AT, de) ---
    {"source": "ORF",           "url": "https://rss.orf.at/news.xml"},
    {"source": "Der Standard",  "url": "https://www.derstandard.at/rss"},
    # --- Portugal (PT, pt) ---
    {"source": "RTP",           "url": "https://www.rtp.pt/noticias/rss"},
    # --- Ireland (IE, en) ---
    {"source": "RTÉ",           "url": "https://www.rte.ie/feeds/rss/?index=/news/&limit=50"},
    # --- Poland (PL, pl) ---
    {"source": "Onet",          "url": "https://wiadomosci.onet.pl/.feed"},
    {"source": "WP.pl",         "url": "https://wiadomosci.wp.pl/rss.xml"},
    # --- Sweden (SE, sv) ---
    {"source": "SVT",           "url": "https://www.svt.se/nyheter/rss.xml"},
    {"source": "Aftonbladet",   "url": "https://rss.aftonbladet.se/rss2/small/pages/sections/senastenytt/"},
    # --- Norway (NO, no) ---
    {"source": "NRK",           "url": "https://www.nrk.no/toppsaker.rss"},
    {"source": "VG",            "url": "https://www.vg.no/rss/feed"},
    # --- Denmark (DK, da) ---
    {"source": "DR",            "url": "https://www.dr.dk/nyheder/service/feeds/allenyheder"},
    # --- Finland (FI, fi) ---
    {"source": "YLE",           "url": "https://feeds.yle.fi/uutiset/v1/majorHeadlines/YLE_UUTISET.rss"},
    {"source": "Iltalehti",     "url": "https://www.iltalehti.fi/rss/uutiset.xml"},
    # --- Greece (GR, el) ---
    {"source": "To Vima",       "url": "https://www.tovima.gr/feed/"},
    # --- Czechia (CZ, cs) ---
    {"source": "Novinky",       "url": "https://www.novinky.cz/rss"},
    {"source": "ČT24",          "url": "https://ct24.ceskatelevize.cz/rss/hlavni-zpravy"},
    # --- Hungary (HU, hu) ---
    {"source": "Telex",         "url": "https://telex.hu/rss"},
    {"source": "HVG",           "url": "https://hvg.hu/rss"},
    # --- Romania (RO, ro) ---
    {"source": "Digi24",        "url": "https://www.digi24.ro/rss"},
    {"source": "HotNews",       "url": "https://www.hotnews.ro/rss"},
    # --- Ukraine (UA, uk) ---
    {"source": "Ukrainska Pravda","url": "https://www.pravda.com.ua/rss/"},
    # --- Turkey (TR, tr) ---
    {"source": "Hürriyet",      "url": "https://www.hurriyet.com.tr/rss/anasayfa"},
    # --- Canada (CA, fr) ---
    {"source": "Radio-Canada",  "url": "https://ici.radio-canada.ca/rss/4159"},
    # --- Mexico (MX, es) ---
    {"source": "La Jornada",    "url": "https://www.jornada.com.mx/rss/edicion.xml"},
    # --- Brazil (BR, pt) ---
    {"source": "G1",            "url": "https://g1.globo.com/rss/g1/"},
    {"source": "Folha",         "url": "https://feeds.folha.uol.com.br/emcimadahora/rss091.xml"},
    # --- Argentina (AR, es) ---
    {"source": "La Nación",     "url": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/"},
    # --- Colombia (CO, es) ---
    {"source": "El Tiempo",     "url": "https://www.eltiempo.com/rss/colombia.xml"},
    # --- Peru (PE, es) ---
    {"source": "RPP",           "url": "https://rpp.pe/rss"},
    # --- Australia (AU, en) ---
    {"source": "ABC News AU",   "url": "https://www.abc.net.au/news/feed/2942460/rss.xml"},
    {"source": "SMH",           "url": "https://www.smh.com.au/rss/feed.xml"},
    # --- New Zealand (NZ, en) ---
    {"source": "RNZ",           "url": "https://www.rnz.co.nz/rss/national.xml"},
    # --- India (IN, en) ---
    {"source": "The Hindu",     "url": "https://www.thehindu.com/news/national/feeder/default.rss"},
    {"source": "NDTV",          "url": "https://feeds.feedburner.com/ndtvnews-top-stories"},
    # --- Japan (JP, ja) ---
    {"source": "NHK",           "url": "https://www.nhk.or.jp/rss/news/cat0.xml"},
    # --- South Korea (KR, en) ---
    {"source": "Yonhap",        "url": "https://en.yna.co.kr/RSS/news.xml"},
    # --- Singapore (SG, en) ---
    {"source": "Straits Times", "url": "https://www.straitstimes.com/news/singapore/rss.xml"},
    {"source": "CNA",           "url": "https://www.channelnewsasia.com/rssfeeds/8395986"},
    # --- Indonesia (ID, id) ---
    # --- Philippines (PH, en) ---
    {"source": "Rappler",       "url": "https://www.rappler.com/feed/"},
    {"source": "Inquirer",      "url": "https://www.inquirer.net/fullfeed"},
    # --- Vietnam (VN, vi) ---
    {"source": "VnExpress",     "url": "https://vnexpress.net/rss/tin-moi-nhat.rss", "ua": "compat"},
    # --- Pakistan (PK, en) ---
    {"source": "Dawn",          "url": "https://www.dawn.com/feed"},
    # --- Israel (IL, en) ---
    {"source": "Jerusalem Post","url": "https://www.jpost.com/rss/rssfeedsfrontpage.aspx"},
    # --- Qatar (QA, en) ---
    {"source": "Al Jazeera",    "url": "https://www.aljazeera.com/xml/rss/all.xml"},
    # --- Hong Kong (HK, en) ---
    {"source": "SCMP",          "url": "https://www.scmp.com/rss/91/feed"},
    # ===== China & Russia: state + independent/exile (see SOURCES_TODO.md) =====
    # China state broadcaster (English). The other CN state outlets (Xinhua,
    # People's Daily, China Daily, Global Times) ship broken/static RSS dates, so
    # only CGTN is viable under the today-filter; see SOURCES_TODO.md.
    {"source": "CGTN",                 "url": "https://www.cgtn.com/subscribe/rss/section/world.xml"},
    # China independent, in exile.
    {"source": "China Digital Times",  "url": "https://chinadigitaltimes.net/feed/"},
    # Russia state agencies/broadcaster. RT carries an EU distribution ban (2022)
    # that Switzerland did not adopt — included as a risk-based, Swiss-rooted call
    # (SOURCES_TODO.md), since it stays findable and CH never banned it.
    {"source": "TASS",                 "url": "https://tass.com/rss/v2.xml"},
    {"source": "RT",                   "url": "https://www.rt.com/rss/"},
    {"source": "RIA Novosti",          "url": "https://ria.ru/export/rss2/archive/index.xml"},
    # Russia independent, operating in exile.
    {"source": "Meduza",               "url": "https://meduza.io/rss/all"},
    {"source": "The Moscow Times",     "url": "https://www.themoscowtimes.com/rss/news"},
    {"source": "Novaya Gazeta Europe", "url": "https://novayagazeta.eu/feed/rss"},
    {"source": "Mediazona",            "url": "https://zona.media/rss"},
    # ===== Regional/national expansion toward ~50 sources per country
    # (added in bulk; each feed validated to fetch+parse with dated items). =====
    # --- AR ---
    {"source": "Cenital", "url": "https://www.cenital.com/feed/"},
    {"source": "Clarín Economía", "url": "https://www.clarin.com/rss/economia/"},
    {"source": "Clarín Mundo", "url": "https://www.clarin.com/rss/mundo/"},
    {"source": "Clarín Política", "url": "https://www.clarin.com/rss/politica/"},
    {"source": "Clarín Sociedad", "url": "https://www.clarin.com/rss/sociedad/"},
    {"source": "Diario Uno", "url": "https://www.diariouno.com.ar/rss/home.xml"},
    {"source": "El Cohete a la Luna", "url": "https://www.elcohetealaluna.com/feed/"},
    {"source": "El Cronista", "url": "https://www.cronista.com/files/rss/news.xml"},
    {"source": "iProfesional Economía", "url": "https://www.iprofesional.com/rss/economia"},
    {"source": "La Gaceta", "url": "https://www.lagaceta.com.ar/rss/"},
    {"source": "La Nación Economía", "url": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/economia/"},
    {"source": "La Nación Mundo", "url": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/el-mundo/"},
    {"source": "La Nación Política", "url": "https://www.lanacion.com.ar/arc/outboundfeeds/rss/category/politica/"},
    {"source": "Letra P", "url": "https://www.letrap.com.ar/rss/pages/home.xml"},
    {"source": "Minuto Uno", "url": "https://www.minutouno.com/rss/pages/home.xml"},
    {"source": "Tiempo Argentino", "url": "https://www.tiempoar.com.ar/politica/feed/"},
    {"source": "Ámbito Economía", "url": "https://www.ambito.com/rss/economia.xml"},
    {"source": "Ámbito Finanzas", "url": "https://www.ambito.com/rss/finanzas.xml"},
    {"source": "Ámbito Política", "url": "https://www.ambito.com/rss/politica.xml"},
    # --- AT ---
    {"source": "Der Standard Inland", "url": "https://www.derstandard.at/rss/inland"},
    {"source": "Der Standard International", "url": "https://www.derstandard.at/rss/international"},
    {"source": "Der Standard Web", "url": "https://www.derstandard.at/rss/web"},
    {"source": "Der Standard Wirtschaft", "url": "https://www.derstandard.at/rss/wirtschaft"},
    {"source": "Die Presse Wirtschaft", "url": "https://www.diepresse.com/rss/Wirtschaft"},
    {"source": "Kleine Zeitung Kärnten", "url": "https://www.kleinezeitung.at/rss/kaernten"},
    {"source": "Kleine Zeitung Politik", "url": "https://www.kleinezeitung.at/rss/politik"},
    {"source": "Kleine Zeitung Wirtschaft", "url": "https://www.kleinezeitung.at/rss/wirtschaft"},
    {"source": "Kurier Politik", "url": "https://kurier.at/politik/inland/xml/rss"},
    {"source": "Kurier Wirtschaft", "url": "https://kurier.at/wirtschaft/xml/rss"},
    {"source": "Meinbezirk", "url": "https://www.meinbezirk.at/rss"},
    {"source": "Oberösterreichische Nachrichten", "url": "https://www.nachrichten.at/storage/rss/rss/nachrichten.xml"},
    {"source": "ORF Oberösterreich", "url": "https://rss.orf.at/ooe.xml"},
    {"source": "ORF Salzburg", "url": "https://rss.orf.at/salzburg.xml"},
    {"source": "ORF Steiermark", "url": "https://rss.orf.at/steiermark.xml"},
    {"source": "ORF Tirol", "url": "https://rss.orf.at/tirol.xml"},
    {"source": "ORF Wien", "url": "https://rss.orf.at/wien.xml"},
    {"source": "OÖ Nachrichten Politik", "url": "https://www.nachrichten.at/storage/rss/rss/politik.xml"},
    # --- AU ---
    {"source": "ABC Business AU", "url": "https://www.abc.net.au/news/feed/51892/rss.xml"},
    {"source": "ABC News Just In", "url": "https://www.abc.net.au/news/feed/45910/rss.xml"},
    {"source": "ABC Politics AU", "url": "https://www.abc.net.au/news/feed/51120/rss.xml"},
    {"source": "Brisbane Times National", "url": "https://www.brisbanetimes.com.au/rss/national.xml"},
    {"source": "Canberra Times", "url": "https://www.canberratimes.com.au/rss.xml"},
    {"source": "Crikey", "url": "https://www.crikey.com.au/feed/"},
    {"source": "Newcastle Herald", "url": "https://www.newcastleherald.com.au/rss.xml"},
    {"source": "Pedestrian TV", "url": "https://www.pedestrian.tv/feed/"},
    {"source": "Perth Now", "url": "https://www.perthnow.com.au/rss"},
    {"source": "SBS News", "url": "https://www.sbs.com.au/news/topic/latest/feed"},
    {"source": "SBS World News", "url": "https://www.sbs.com.au/news/topic/world/feed"},
    {"source": "SMH National", "url": "https://www.smh.com.au/rss/national.xml"},
    {"source": "The Age National", "url": "https://www.theage.com.au/rss/national.xml"},
    {"source": "The Guardian AU Politics", "url": "https://www.theguardian.com/australia-news/australian-politics/rss"},
    {"source": "The Guardian AU World", "url": "https://www.theguardian.com/world/rss"},
    {"source": "The Mandarin", "url": "https://www.themandarin.com.au/feed/"},
    {"source": "The West Australian", "url": "https://thewest.com.au/rss"},
    {"source": "WAtoday", "url": "https://www.watoday.com.au/rss/feed.xml"},
    # --- BE ---
    {"source": "Bruzz", "url": "https://www.bruzz.be/rss.xml"},
    {"source": "De Morgen", "url": "https://www.demorgen.be/rss.xml"},
    {"source": "De Morgen Politiek", "url": "https://www.demorgen.be/politiek/rss.xml"},
    {"source": "De Tijd", "url": "https://www.tijd.be/rss/nieuws.xml"},
    {"source": "De Tijd Ondernemen", "url": "https://www.tijd.be/rss/ondernemen.xml"},
    {"source": "De Tijd Politiek", "url": "https://www.tijd.be/rss/politiek.xml"},
    {"source": "Gazet van Antwerpen", "url": "https://www.gva.be/rss"},
    {"source": "Het Belang van Limburg", "url": "https://www.hbvl.be/rss"},
    {"source": "Het Laatste Nieuws Binnenland", "url": "https://www.hln.be/binnenland/rss.xml"},
    {"source": "HLN Buitenland", "url": "https://www.hln.be/buitenland/rss.xml"},
    {"source": "Knack", "url": "https://www.knack.be/feed/"},
    {"source": "Knack Nieuws", "url": "https://www.knack.be/nieuws/feed/"},
    {"source": "L'Echo", "url": "https://www.lecho.be/rss/actualite.xml"},
    {"source": "L'Echo Politique", "url": "https://www.lecho.be/rss/politique.xml"},
    {"source": "La DH", "url": "https://www.dhnet.be/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "La DH Sports", "url": "https://www.dhnet.be/arc/outboundfeeds/rss/category/sports/?outputType=xml"},
    {"source": "Le Vif Belgique", "url": "https://www.levif.be/belgique/feed/"},
    {"source": "Trends", "url": "https://trends.levif.be/feed/"},
    {"source": "VRT NWS Politiek", "url": "https://www.vrt.be/vrtnws/nl.rss.headlines.xml"},
    # --- BR ---
    {"source": "A Gazeta ES", "url": "https://www.agazeta.com.br/rss"},
    {"source": "A Tarde", "url": "https://www.atarde.com.br/rss"},
    {"source": "Correio Braziliense", "url": "https://www.correiobraziliense.com.br/rss/noticia/brasil/rss.xml"},
    {"source": "Agência Brasil", "url": "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml"},
    {"source": "BBC Brasil", "url": "https://www.bbc.com/portuguese/index.xml"},
    {"source": "CartaCapital", "url": "https://www.cartacapital.com.br/feed/"},
    {"source": "CNN Brasil", "url": "https://www.cnnbrasil.com.br/feed/"},
    {"source": "Congresso em Foco", "url": "https://www.congressoemfoco.com.br/feed/"},
    {"source": "Crusoé", "url": "https://crusoe.com.br/feed/"},
    {"source": "Estadão Economia", "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/economia/?outputType=xml"},
    {"source": "Estadão Política", "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/politica/?outputType=xml"},
    {"source": "Exame", "url": "https://exame.com/feed/"},
    {"source": "Folha Mercado", "url": "https://feeds.folha.uol.com.br/mercado/rss091.xml"},
    {"source": "Folha Mundo", "url": "https://feeds.folha.uol.com.br/mundo/rss091.xml"},
    {"source": "Folha Poder", "url": "https://feeds.folha.uol.com.br/poder/rss091.xml"},
    {"source": "G1 Economia", "url": "https://g1.globo.com/rss/g1/economia/"},
    {"source": "G1 Mundo", "url": "https://g1.globo.com/rss/g1/mundo/"},
    {"source": "G1 Política", "url": "https://g1.globo.com/rss/g1/politica/"},
    {"source": "Gazeta do Povo", "url": "https://www.gazetadopovo.com.br/feed/rss/republica.xml"},
    {"source": "Gazeta do Povo Mundo", "url": "https://www.gazetadopovo.com.br/feed/rss/mundo.xml"},
    {"source": "InfoMoney", "url": "https://www.infomoney.com.br/feed/"},
    {"source": "IstoÉ", "url": "https://istoe.com.br/feed/"},
    {"source": "Jota", "url": "https://www.jota.info/feed"},
    {"source": "Nexo Jornal", "url": "https://www.nexojornal.com.br/rss.xml"},
    {"source": "O Antagonista", "url": "https://oantagonista.com.br/feed/"},
    {"source": "O Globo", "url": "https://oglobo.globo.com/rss/oglobo"},
    {"source": "O Globo Economia", "url": "https://oglobo.globo.com/rss/oglobo/economia"},
    {"source": "O Globo Política", "url": "https://oglobo.globo.com/rss/oglobo/politica"},
    {"source": "Terra Brasil", "url": "https://www.terra.com.br/rss/"},
    {"source": "The Intercept Brasil", "url": "https://www.intercept.com.br/feed/"},
    # --- CA ---
    {"source": "Calgary Herald", "url": "https://calgaryherald.com/feed/"},
    {"source": "Edmonton Journal", "url": "https://edmontonjournal.com/feed/"},
    {"source": "Financial Post News", "url": "https://financialpost.com/category/news/feed"},
    {"source": "Global News Money", "url": "https://globalnews.ca/money/feed/"},
    {"source": "Global News Politics", "url": "https://globalnews.ca/politics/feed/"},
    {"source": "iPolitics", "url": "https://www.ipolitics.ca/feed/"},
    {"source": "Journal de Montréal", "url": "https://www.journaldemontreal.com/rss.xml"},
    {"source": "La Presse", "url": "https://www.lapresse.ca/actualites/rss"},
    {"source": "Le Journal de Québec", "url": "https://www.journaldequebec.com/rss.xml"},
    {"source": "National Observer", "url": "https://www.nationalobserver.com/front/rss"},
    {"source": "National Post Politics", "url": "https://nationalpost.com/category/news/politics/feed"},
    {"source": "Ottawa Citizen", "url": "https://ottawacitizen.com/feed/"},
    {"source": "Rabble.ca", "url": "https://rabble.ca/feed/"},
    {"source": "The Conversation CA", "url": "https://theconversation.com/ca/articles.atom"},
    {"source": "The Globe and Mail", "url": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/canada/"},
    {"source": "The Globe and Mail Politics", "url": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/politics/"},
    {"source": "The Globe and Mail World", "url": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/world/"},
    {"source": "The Narwhal", "url": "https://thenarwhal.ca/feed/"},
    {"source": "The Tyee", "url": "https://thetyee.ca/rss2.xml"},
    {"source": "The Walrus", "url": "https://thewalrus.ca/feed/"},
    {"source": "Vancouver Sun", "url": "https://vancouversun.com/feed/"},
    {"source": "Winnipeg Free Press", "url": "https://www.winnipegfreepress.com/rss/?path=/breakingnews"},
    # --- CL ---
    {"source": "BioBioChile", "url": "https://feeds.feedburner.com/radiobiobio/NNeJ"},
    {"source": "CIPER", "url": "https://www.ciperchile.cl/feed/"},
    {"source": "Diario Financiero", "url": "https://www.df.cl/noticias/site/list/port/rss.xml"},
    {"source": "Ex-Ante", "url": "https://www.ex-ante.cl/feed/"},
    {"source": "Interferencia", "url": "https://interferencia.cl/rss.xml"},
    {"source": "La Discusión", "url": "https://www.ladiscusion.cl/feed/"},
    {"source": "La Nación Chile", "url": "https://www.lanacion.cl/feed/"},
    {"source": "La Tercera", "url": "https://www.latercera.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Publimetro Chile", "url": "https://www.publimetro.cl/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Radio Universidad de Chile", "url": "https://radio.uchile.cl/feed/"},
    {"source": "The Clinic", "url": "https://www.theclinic.cl/feed/"},
    # --- CN ---
    {"source": "Bitter Winter", "url": "https://bitterwinter.org/feed/"},
    {"source": "CGTN China", "url": "https://www.cgtn.com/subscribe/rss/section/china.xml"},
    {"source": "China Media Project", "url": "https://chinamediaproject.org/feed/"},
    {"source": "Ecns.cn", "url": "https://www.ecns.cn/rss/rss.xml"},
    {"source": "Pekingnology", "url": "https://www.pekingnology.com/feed"},
    {"source": "Radio Free Asia", "url": "https://www.rfa.org/english/rss2.xml"},
    {"source": "SCMP China", "url": "https://www.scmp.com/rss/4/feed"},
    {"source": "The Wire China", "url": "https://www.thewirechina.com/feed/"},
    {"source": "Trivium China", "url": "https://triviumchina.com/feed/"},
    {"source": "What's on Weibo", "url": "https://www.whatsonweibo.com/feed/"},
    # --- CO ---
    {"source": "Cuestión Pública", "url": "https://cuestionpublica.com/feed/"},
    {"source": "El Colombiano Antioquia", "url": "https://www.elcolombiano.com/rss/antioquia.xml"},
    {"source": "El Colombiano Nacional", "url": "https://www.elcolombiano.com/rss/colombia.xml"},
    {"source": "El Tiempo Mundo", "url": "https://www.eltiempo.com/rss/mundo.xml"},
    {"source": "El Tiempo Política", "url": "https://www.eltiempo.com/rss/politica.xml"},
    {"source": "La Opinión", "url": "https://www.laopinion.com.co/rss.xml"},
    {"source": "La República CO", "url": "https://www.larepublica.co/rss/economia"},
    {"source": "La Silla Vacía", "url": "https://www.lasillavacia.com/feed/"},
    {"source": "Razón Pública", "url": "https://razonpublica.com/feed/"},
    {"source": "Semana Mundo", "url": "https://www.semana.com/arc/outboundfeeds/rss/category/mundo/?outputType=xml"},
    {"source": "Semana Nación", "url": "https://www.semana.com/arc/outboundfeeds/rss/category/nacion/?outputType=xml"},
    # --- CZ ---
    {"source": "Aktuálně Domácí", "url": "https://www.aktualne.cz/rss/domaci/"},
    {"source": "Aktuálně Zahraničí", "url": "https://www.aktualne.cz/rss/zahranici/"},
    {"source": "Aktuálně.cz", "url": "https://www.aktualne.cz/rss/"},
    {"source": "Blesk", "url": "https://www.blesk.cz/rss"},
    {"source": "Blesk Zprávy", "url": "https://www.blesk.cz/rss/zpravy"},
    {"source": "Deník Ekonomika", "url": "https://www.denik.cz/rss/ekonomika.html"},
    {"source": "E15", "url": "https://www.e15.cz/rss"},
    {"source": "E15 Byznys", "url": "https://www.e15.cz/rss/byznys"},
    {"source": "Forbes Česko", "url": "https://forbes.cz/feed/"},
    {"source": "Forum24", "url": "https://www.forum24.cz/feed/"},
    {"source": "Hospodářské noviny", "url": "https://ihned.cz/?p=000000_rss"},
    {"source": "iDNES", "url": "https://servis.idnes.cz/rss.aspx?c=zpravodaj"},
    {"source": "iDNES Ekonomika", "url": "https://servis.idnes.cz/rss.aspx?c=ekonomikah"},
    {"source": "iDNES Zahraničí", "url": "https://servis.idnes.cz/rss.aspx?c=zahranicni"},
    {"source": "Info.cz", "url": "https://www.info.cz/rss"},
    {"source": "Lidovky", "url": "https://servis.lidovky.cz/rss.aspx?c=ln_domov"},
    {"source": "Reflex", "url": "https://www.reflex.cz/rss"},
    {"source": "ČT24 Domácí", "url": "https://ct24.ceskatelevize.cz/rss/rubrika/domaci-5"},
    {"source": "ČT24 Ekonomika", "url": "https://ct24.ceskatelevize.cz/rss/rubrika/ekonomika-17"},
    {"source": "ČT24 Svět", "url": "https://ct24.ceskatelevize.cz/rss/rubrika/svet-16"},
    # --- DE ---
    {"source": "Berliner Morgenpost", "url": "https://www.morgenpost.de/rss"},
    {"source": "Braunschweiger Zeitung", "url": "https://www.braunschweiger-zeitung.de/rss"},
    {"source": "Cicero", "url": "https://www.cicero.de/rss.xml"},
    {"source": "Der Freitag", "url": "https://www.freitag.de/@@RSS"},
    {"source": "Deutschlandfunk", "url": "https://www.deutschlandfunk.de/nachrichten-100.rss"},
    {"source": "General-Anzeiger Bonn", "url": "https://ga.de/feed.rss"},
    {"source": "golem.de", "url": "https://rss.golem.de/rss.php?feed=RSS2.0"},
    {"source": "Hamburger Abendblatt", "url": "https://www.abendblatt.de/rss"},
    {"source": "hessenschau", "url": "https://www.hessenschau.de/index.rss"},
    {"source": "Junge Welt", "url": "https://www.jungewelt.de/feeds/newsticker.rss"},
    {"source": "Kieler Nachrichten", "url": "https://www.kn-online.de/arc/outboundfeeds/rss/"},
    {"source": "Kreiszeitung", "url": "https://www.kreiszeitung.de/rssfeed.rdf"},
    {"source": "Lübecker Nachrichten", "url": "https://www.ln-online.de/arc/outboundfeeds/rss/"},
    {"source": "MDR Sachsen", "url": "https://www.mdr.de/nachrichten/sachsen/index-rss.xml"},
    {"source": "Netzpolitik", "url": "https://netzpolitik.org/feed/"},
    {"source": "Neue Osnabrücker Zeitung", "url": "https://www.noz.de/rss"},
    {"source": "Ostthüringer Zeitung", "url": "https://www.otz.de/rss"},
    {"source": "rbb24", "url": "https://www.rbb24.de/index.xml/feed=rss.xml"},
    {"source": "Rheinische Post Politik", "url": "https://rp-online.de/politik/feed.rss"},
    {"source": "Ruhr Nachrichten", "url": "https://www.ruhrnachrichten.de/feed/"},
    {"source": "Saarbrücker Zeitung", "url": "https://www.saarbruecker-zeitung.de/feed.rss"},
    {"source": "Tagesspiegel Politik", "url": "https://www.tagesspiegel.de/contentexport/feed/politik"},
    {"source": "Telepolis", "url": "https://www.telepolis.de/news-atom.xml"},
    {"source": "Thüringer Allgemeine", "url": "https://www.thueringer-allgemeine.de/rss"},
    {"source": "Trierischer Volksfreund", "url": "https://www.volksfreund.de/feed.rss"},
    {"source": "tz München", "url": "https://www.tz.de/rssfeed.rdf"},
    {"source": "WAZ", "url": "https://www.waz.de/rss"},
    {"source": "WDR", "url": "https://www1.wdr.de/uebersicht-100.feed"},
    {"source": "Wolfsburger Nachrichten", "url": "https://www.waz-online.de/arc/outboundfeeds/rss/"},
    {"source": "Zeit Online", "url": "https://newsfeed.zeit.de/index"},
    # --- DK ---
    {"source": "Altinget", "url": "https://www.altinget.dk/rss/"},
    {"source": "Avisen.dk", "url": "https://www.avisen.dk/rss.aspx"},
    {"source": "DR Indland", "url": "https://www.dr.dk/nyheder/service/feeds/indland"},
    {"source": "DR Kultur", "url": "https://www.dr.dk/nyheder/service/feeds/kultur"},
    {"source": "DR Penge", "url": "https://www.dr.dk/nyheder/service/feeds/penge"},
    {"source": "DR Politik", "url": "https://www.dr.dk/nyheder/service/feeds/politik"},
    {"source": "DR Udland", "url": "https://www.dr.dk/nyheder/service/feeds/udland"},
    {"source": "Information", "url": "https://www.information.dk/feed"},
    {"source": "Ingeniøren", "url": "https://ing.dk/rss/nyheder"},
    {"source": "Politiken Kultur", "url": "https://politiken.dk/rss/kultur.rss"},
    {"source": "Politiken Udland", "url": "https://politiken.dk/rss/udland.rss"},
    {"source": "TV2 Lorry", "url": "https://www.tv2lorry.dk/rss"},
    # --- ES ---
    {"source": "ABC Internacional", "url": "https://www.abc.es/rss/feeds/abc_internacional.xml"},
    {"source": "Ara", "url": "https://www.ara.cat/rss/"},
    {"source": "Canarias7", "url": "https://www.canarias7.es/rss/2.0/portada"},
    {"source": "Diari de Tarragona", "url": "https://www.diaridetarragona.com/rss"},
    {"source": "El Comercio", "url": "https://www.elcomercio.es/rss/2.0/portada"},
    {"source": "El Confidencial Digital", "url": "https://www.elconfidencialdigital.com/rss"},
    {"source": "El Confidencial Mundo", "url": "https://rss.elconfidencial.com/mundo/"},
    {"source": "El Diario Montañés", "url": "https://www.eldiariomontanes.es/rss/2.0/portada"},
    {"source": "El Español Mundo", "url": "https://www.elespanol.com/rss/mundo/"},
    {"source": "El Independiente España", "url": "https://www.elindependiente.com/politica/feed/"},
    {"source": "El Mundo España", "url": "https://e00-elmundo.uecdn.es/elmundo/rss/espana.xml"},
    {"source": "El Mundo Internacional", "url": "https://e00-elmundo.uecdn.es/elmundo/rss/internacional.xml"},
    {"source": "El Norte de Castilla", "url": "https://www.elnortedecastilla.es/rss/2.0/portada"},
    {"source": "El País", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/portada"},
    {"source": "El País España", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/espana/portada"},
    {"source": "El País Internacional", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/elpais.com/section/internacional/portada"},
    {"source": "El Salto Diario Política", "url": "https://www.elsaltodiario.com/politica/feed"},
    {"source": "elDiario Economía", "url": "https://www.eldiario.es/rss/economia/"},
    {"source": "elDiario Política", "url": "https://www.eldiario.es/rss/politica/"},
    {"source": "Heraldo", "url": "https://www.heraldo.es/rss/"},
    {"source": "Hoy Extremadura", "url": "https://www.hoy.es/rss/2.0/portada"},
    {"source": "La Marea", "url": "https://www.lamarea.com/feed/"},
    {"source": "La Rioja", "url": "https://www.larioja.com/rss/2.0/portada"},
    {"source": "La Vanguardia Internacional", "url": "https://www.lavanguardia.com/rss/internacional.xml"},
    {"source": "La Vanguardia Política", "url": "https://www.lavanguardia.com/rss/politica.xml"},
    {"source": "Nació Digital", "url": "https://www.naciodigital.cat/rss/portada"},
    {"source": "Okdiario", "url": "https://okdiario.com/feed"},
    {"source": "Sur in English", "url": "https://www.surinenglish.com/rss/2.0/portada"},
    # --- FI ---
    {"source": "Etelä-Suomen Sanomat", "url": "https://www.ess.fi/rss"},
    {"source": "Helsingin Sanomat Politiikka", "url": "https://www.hs.fi/rss/politiikka.xml"},
    {"source": "Hufvudstadsbladet", "url": "https://www.hbl.fi/rss/"},
    {"source": "Ilta-Sanomat Kotimaa", "url": "https://www.is.fi/rss/kotimaa.xml"},
    {"source": "Ilta-Sanomat Taloussanomat", "url": "https://www.is.fi/rss/taloussanomat.xml"},
    {"source": "Iltalehti Talous", "url": "https://www.iltalehti.fi/rss/talous.xml"},
    {"source": "Iltalehti Ulkomaat", "url": "https://www.iltalehti.fi/rss/ulkomaat.xml"},
    {"source": "IS Ulkomaat", "url": "https://www.is.fi/rss/ulkomaat.xml"},
    {"source": "Karjalainen", "url": "https://www.karjalainen.fi/rss"},
    {"source": "Keskisuomalainen", "url": "https://www.ksml.fi/feed/rss"},
    {"source": "Maaseudun Tulevaisuus", "url": "https://www.maaseuduntulevaisuus.fi/rss"},
    {"source": "MTV Uutiset Kotimaa", "url": "https://www.mtvuutiset.fi/api/feed/rss/uutiset_kotimaa"},
    {"source": "MTV Uutiset Ulkomaat", "url": "https://www.mtvuutiset.fi/api/feed/rss/uutiset_ulkomaat"},
    {"source": "Savon Sanomat", "url": "https://www.savonsanomat.fi/feed/rss"},
    {"source": "Suomenmaa", "url": "https://www.suomenmaa.fi/feed/"},
    {"source": "Talouselämä", "url": "https://www.talouselama.fi/rss.xml"},
    {"source": "Verkkouutiset", "url": "https://www.verkkouutiset.fi/feed/"},
    {"source": "Yle Politiikka", "url": "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET"},
    # --- FR ---
    {"source": "Basta!", "url": "https://basta.media/spip.php?page=backend"},
    {"source": "BFM Business", "url": "https://www.bfmtv.com/rss/economie/"},
    {"source": "DNA", "url": "https://www.dna.fr/rss"},
    {"source": "France Culture", "url": "https://www.radiofrance.fr/franceculture/rss"},
    {"source": "L'Est Républicain", "url": "https://www.estrepublicain.fr/rss"},
    {"source": "La Croix Monde", "url": "https://www.la-croix.com/RSS/MONDE"},
    {"source": "La Croix Régional", "url": "https://www.la-croix.com/RSS/UNIVERS"},
    {"source": "Le Bien Public", "url": "https://www.bienpublic.com/rss"},
    {"source": "Le Dauphiné Libéré", "url": "https://www.ledauphine.com/rss"},
    {"source": "Le Figaro Éco", "url": "https://www.lefigaro.fr/rss/figaro_economie.xml"},
    {"source": "Le Journal de Saône-et-Loire", "url": "https://www.lejsl.com/rss"},
    {"source": "Le Monde Politique", "url": "https://www.lemonde.fr/politique/rss_full.xml"},
    {"source": "Le Monde Éco", "url": "https://www.lemonde.fr/economie/rss_full.xml"},
    {"source": "Le Progrès", "url": "https://www.leprogres.fr/rss"},
    {"source": "Le Républicain Lorrain", "url": "https://www.republicain-lorrain.fr/rss"},
    {"source": "Mediacités", "url": "https://www.mediacites.fr/feed/"},
    {"source": "Midi Libre", "url": "https://www.midilibre.fr/rss.xml"},
    {"source": "Nice-Matin", "url": "https://www.nicematin.com/rss"},
    {"source": "Ouest-France", "url": "https://www.ouest-france.fr/rss-en-continu.xml"},
    {"source": "Reporterre", "url": "https://reporterre.net/spip.php?page=backend"},
    {"source": "RMC", "url": "https://rmc.bfmtv.com/rss/actualites/"},
    {"source": "Sciences et Avenir", "url": "https://www.sciencesetavenir.fr/rss.xml"},
    {"source": "Var-Matin", "url": "https://www.nicematin.com/var/rss"},
    {"source": "Vosges Matin", "url": "https://www.vosgesmatin.fr/rss"},
    # --- GB ---
    {"source": "BBC UK", "url": "https://feeds.bbci.co.uk/news/uk/rss.xml"},
    {"source": "Belfast Live", "url": "https://www.belfastlive.co.uk/?service=rss"},
    {"source": "Birmingham Live", "url": "https://www.birminghammail.co.uk/news/?service=rss"},
    {"source": "Birmingham Mail", "url": "https://www.birminghammail.co.uk/?service=rss"},
    {"source": "Bristol Post", "url": "https://www.bristolpost.co.uk/?service=rss"},
    {"source": "Byline Times", "url": "https://bylinetimes.com/feed/"},
    {"source": "Cambridge News", "url": "https://www.cambridge-news.co.uk/?service=rss"},
    {"source": "Chronicle Live", "url": "https://www.chroniclelive.co.uk/?service=rss"},
    {"source": "Coventry Telegraph", "url": "https://www.coventrytelegraph.net/?service=rss"},
    {"source": "Daily Record", "url": "https://www.dailyrecord.co.uk/?service=rss"},
    {"source": "Devon Live", "url": "https://www.devonlive.com/?service=rss"},
    {"source": "Edinburgh Live", "url": "https://www.edinburghlive.co.uk/?service=rss"},
    {"source": "Express", "url": "https://www.express.co.uk/posts/rss/1/uk"},
    {"source": "Glasgow Live", "url": "https://www.glasgowlive.co.uk/?service=rss"},
    {"source": "Gloucestershire Live", "url": "https://www.gloucestershirelive.co.uk/?service=rss"},
    {"source": "Grimsby Live", "url": "https://www.grimsbytelegraph.co.uk/?service=rss"},
    {"source": "Hull Daily Mail", "url": "https://www.hulldailymail.co.uk/?service=rss"},
    {"source": "Leeds Live", "url": "https://www.leeds-live.co.uk/?service=rss"},
    {"source": "Liverpool Echo", "url": "https://www.liverpoolecho.co.uk/?service=rss"},
    {"source": "Manchester Evening News UK", "url": "https://www.manchestereveningnews.co.uk/news/?service=rss"},
    {"source": "Morning Star", "url": "https://morningstaronline.co.uk/rss.xml"},
    {"source": "MyLondon", "url": "https://www.mylondon.news/?service=rss"},
    {"source": "Nottingham Post", "url": "https://www.nottinghampost.com/?service=rss"},
    {"source": "openDemocracy", "url": "https://www.opendemocracy.net/rss/"},
    {"source": "Oxford Mail", "url": "https://www.oxfordmail.co.uk/news/rss/"},
    {"source": "Reading Chronicle", "url": "https://www.readingchronicle.co.uk/news/rss/"},
    {"source": "Sky News UK", "url": "https://feeds.skynews.com/feeds/rss/uk.xml"},
    {"source": "The Big Issue", "url": "https://www.bigissue.com/feed/"},
    {"source": "The Canary", "url": "https://www.thecanary.co/feed/"},
    {"source": "The National", "url": "https://www.thenational.scot/news/rss/"},
    {"source": "The Northern Echo", "url": "https://www.thenorthernecho.co.uk/news/rss/"},
    {"source": "The Register", "url": "https://www.theregister.com/headlines.atom"},
    {"source": "The Sun", "url": "https://www.thesun.co.uk/feed/"},
    {"source": "Wales Online News", "url": "https://www.walesonline.co.uk/news/?service=rss"},
    {"source": "Yorkshire Post", "url": "https://www.yorkshirepost.co.uk/rss"},
    # --- GR ---
    {"source": "Alfavita", "url": "https://www.alfavita.gr/rss.xml"},
    {"source": "Documento", "url": "https://www.documentonews.gr/feed/"},
    {"source": "Efimerida ton Syntakton", "url": "https://www.efsyn.gr/rss.xml"},
    {"source": "Ethnos", "url": "https://www.ethnos.gr/rss"},
    {"source": "in.gr Oikonomia", "url": "https://www.in.gr/economy/feed/"},
    {"source": "In.gr Politiki", "url": "https://www.in.gr/politics/feed/"},
    {"source": "Lifo", "url": "https://www.lifo.gr/rss.xml"},
    {"source": "Newsbeast", "url": "https://www.newsbeast.gr/feed"},
    {"source": "Newsit", "url": "https://www.newsit.gr/feed/"},
    {"source": "Protagon", "url": "https://www.protagon.gr/feed/"},
    {"source": "Protothema", "url": "https://www.protothema.gr/rss/"},
    {"source": "Real.gr", "url": "https://www.real.gr/feed/"},
    {"source": "Star.gr", "url": "https://www.star.gr/rss/"},
    {"source": "ThePressProject", "url": "https://thepressproject.gr/feed/"},
    {"source": "To Vima Politiki", "url": "https://www.tovima.gr/category/politics/feed/"},
    # --- HK ---
    {"source": "HKFP Politics", "url": "https://hongkongfp.com/category/hong-kong/feed/"},
    {"source": "HKFP World", "url": "https://hongkongfp.com/category/world/feed/"},
    {"source": "Hong Kong Business", "url": "https://hongkongbusiness.hk/rss.xml"},
    {"source": "Ming Pao", "url": "https://news.mingpao.com/rss/pns/s00001.xml"},
    {"source": "RTHK Greater China", "url": "https://rthk.hk/rthk/news/rss/e_expressnews_egreaterchina.xml"},
    {"source": "SCMP Asia", "url": "https://www.scmp.com/rss/3/feed"},
    {"source": "SCMP Business", "url": "https://www.scmp.com/rss/92/feed"},
    {"source": "SCMP Hong Kong", "url": "https://www.scmp.com/rss/2/feed"},
    {"source": "SCMP World", "url": "https://www.scmp.com/rss/5/feed"},
    {"source": "The Witness HK", "url": "https://thewitnesshk.com/feed/"},
    # --- HU ---
    {"source": "Blikk", "url": "https://www.blikk.hu/rss"},
    {"source": "Daily News Hungary", "url": "https://dailynewshungary.com/feed/"},
    {"source": "Direkt36", "url": "https://www.direkt36.hu/feed/"},
    {"source": "HungaryToday", "url": "https://hungarytoday.hu/feed/"},
    {"source": "HVG Gazdaság", "url": "https://hvg.hu/rss/gazdasag"},
    {"source": "HVG Itthon", "url": "https://hvg.hu/rss/itthon"},
    {"source": "HVG Világ", "url": "https://hvg.hu/rss/vilag"},
    {"source": "Index Belföld", "url": "https://index.hu/belfold/rss/"},
    {"source": "Index Gazdaság", "url": "https://index.hu/gazdasag/rss/"},
    {"source": "Index Külföld", "url": "https://index.hu/kulfold/rss/"},
    {"source": "Infostart", "url": "https://infostart.hu/24ora/rss/"},
    {"source": "Magyar Hang", "url": "https://hang.hu/rss"},
    {"source": "Magyar Nemzet", "url": "https://magyarnemzet.hu/feed"},
    {"source": "Média1", "url": "https://media1.hu/feed/"},
    {"source": "Népszava", "url": "https://nepszava.hu/feed"},
    {"source": "Portfolio Deviza", "url": "https://www.portfolio.hu/rss/deviza.xml"},
    {"source": "Portfolio Gazdaság", "url": "https://www.portfolio.hu/rss/gazdasag.xml"},
    {"source": "Telex Belföld", "url": "https://telex.hu/rss/belfold"},
    {"source": "Telex Gazdaság", "url": "https://telex.hu/rss/gazdasag"},
    {"source": "Telex Külföld", "url": "https://telex.hu/rss/kulfold"},
    {"source": "VG.hu", "url": "https://www.vg.hu/feed/"},
    {"source": "Válasz Online", "url": "https://www.valaszonline.hu/feed/"},
    {"source": "Átlátszó", "url": "https://atlatszo.hu/feed/"},
    # --- ID ---
    {"source": "Antara Politik", "url": "https://www.antaranews.com/rss/politik"},
    {"source": "CNBC Indonesia", "url": "https://www.cnbcindonesia.com/rss"},
    {"source": "CNBC Indonesia News", "url": "https://www.cnbcindonesia.com/news/rss"},
    {"source": "CNN Indonesia Nasional", "url": "https://www.cnnindonesia.com/nasional/rss"},
    {"source": "Detik Finance", "url": "https://finance.detik.com/rss"},
    {"source": "JPNN", "url": "https://www.jpnn.com/index.php?mib=rss"},
    {"source": "Katadata", "url": "https://katadata.co.id/rss"},
    {"source": "Kontan Nasional", "url": "https://nasional.kontan.co.id/rss"},
    {"source": "Liputan6 News", "url": "https://feed.liputan6.com/rss/news"},
    {"source": "Media Indonesia", "url": "https://mediaindonesia.com/feed"},
    {"source": "Okezone", "url": "https://sindikasi.okezone.com/index.php/rss/0/RSS2.0"},
    {"source": "Republika", "url": "https://www.republika.co.id/rss"},
    {"source": "Sindonews", "url": "https://nasional.sindonews.com/rss"},
    {"source": "Viva", "url": "https://www.viva.co.id/get/all"},
    # --- IE ---
    {"source": "Cork Beo", "url": "https://www.corkbeo.ie/?service=rss"},
    {"source": "Dublin Live", "url": "https://www.dublinlive.ie/?service=rss"},
    {"source": "Extra.ie", "url": "https://extra.ie/feed"},
    {"source": "Gript", "url": "https://gript.ie/feed/"},
    {"source": "Hot Press", "url": "https://www.hotpress.com/feed/"},
    {"source": "Irish Independent Business", "url": "https://www.independent.ie/business/rss/"},
    {"source": "Irish Independent News", "url": "https://www.independent.ie/rss/"},
    {"source": "Irish Independent Sport", "url": "https://www.independent.ie/sport/rss/"},
    {"source": "Irish Independent World", "url": "https://www.independent.ie/world-news/rss/"},
    {"source": "Kilkenny People", "url": "https://www.kilkennypeople.ie/rss/"},
    {"source": "Limerick Leader", "url": "https://www.limerickleader.ie/rss/"},
    {"source": "RTÉ Business", "url": "https://www.rte.ie/feeds/rss/?index=/news/business/"},
    {"source": "RTÉ News", "url": "https://www.rte.ie/feeds/rss/?index=/news/"},
    {"source": "RTÉ World", "url": "https://www.rte.ie/feeds/rss/?index=/news/world/"},
    {"source": "Silicon Republic", "url": "https://www.siliconrepublic.com/feed"},
    {"source": "The Ditch", "url": "https://www.ontheditch.com/rss/"},
    {"source": "The Irish Sun", "url": "https://www.thesun.ie/feed/"},
    {"source": "The Irish Times", "url": "https://www.irishtimes.com/arc/outboundfeeds/rss/"},
    {"source": "The42", "url": "https://www.the42.ie/feed/"},
    # --- IL ---
    {"source": "+972 Magazine", "url": "https://www.972mag.com/feed/"},
    {"source": "Al-Monitor", "url": "https://www.al-monitor.com/rss"},
    {"source": "Arutz Sheva", "url": "https://www.israelnationalnews.com/Rss.aspx"},
    {"source": "Israel Hayom", "url": "https://www.israelhayom.com/feed/"},
    {"source": "Maariv", "url": "https://www.maariv.co.il/Rss/RssFeedsMivzakiaux"},
    {"source": "The Jerusalem Post Israel News", "url": "https://www.jpost.com/rss/rssfeedsisraelnews.aspx"},
    {"source": "The Media Line", "url": "https://themedialine.org/feed/"},
    {"source": "Walla", "url": "https://rss.walla.co.il/feed/1?type=main"},
    {"source": "Ynetnews", "url": "https://www.ynetnews.com/Integration/StoryRss3082.xml"},
    {"source": "Ynetnews World", "url": "https://www.ynetnews.com/Integration/StoryRss1854.xml"},
    # IL, Hebrew-language
    {"source": "Arutz Sheva HE", "url": "https://www.inn.co.il/Rss.aspx"},
    {"source": "Davar", "url": "https://www.davar1.co.il/feed/"},
    {"source": "Israel Hayom HE", "url": "https://www.israelhayom.co.il/rss.xml"},
    {"source": "Shakuf", "url": "https://shakuf.co.il/feed"},
    # --- IN ---
    {"source": "DNA India", "url": "https://www.dnaindia.com/feeds/india.xml"},
    {"source": "Economic Times Markets", "url": "https://economictimes.indiatimes.com/markets/rssfeeds/1977021501.cms"},
    {"source": "Free Press Journal", "url": "https://www.freepressjournal.in/stories.rss"},
    {"source": "Hindustan Times Business", "url": "https://www.hindustantimes.com/feeds/rss/business/rssfeed.xml"},
    {"source": "Hindustan Times World", "url": "https://www.hindustantimes.com/feeds/rss/world-news/rssfeed.xml"},
    {"source": "India Today Feed", "url": "https://www.indiatoday.in/rss/home"},
    {"source": "India Today India", "url": "https://www.indiatoday.in/rss/1206577"},
    {"source": "India Today World", "url": "https://www.indiatoday.in/rss/1206514"},
    {"source": "Livemint Companies", "url": "https://www.livemint.com/rss/companies"},
    {"source": "Mint Politics", "url": "https://www.livemint.com/rss/politics"},
    {"source": "NDTV India News", "url": "https://feeds.feedburner.com/ndtvnews-india-news"},
    {"source": "NDTV World News", "url": "https://feeds.feedburner.com/ndtvnews-world-news"},
    {"source": "News18 World", "url": "https://www.news18.com/rss/world.xml"},
    {"source": "Telangana Today", "url": "https://telanganatoday.com/feed"},
    {"source": "The Economic Times Politics", "url": "https://economictimes.indiatimes.com/news/politics-and-nation/rssfeeds/1052732854.cms"},
    {"source": "The Hindu Business Line", "url": "https://www.thehindubusinessline.com/feeder/default.rss"},
    {"source": "The Hindu World", "url": "https://www.thehindu.com/news/international/feeder/default.rss"},
    {"source": "The Print India", "url": "https://theprint.in/category/india/feed/"},
    {"source": "Times of India Business", "url": "https://timesofindia.indiatimes.com/rssfeeds/1898055.cms"},
    {"source": "Times of India India", "url": "https://timesofindia.indiatimes.com/rssfeeds/-2128936835.cms"},
    {"source": "Times of India World", "url": "https://timesofindia.indiatimes.com/rssfeeds/296589292.cms"},
    {"source": "Zee News", "url": "https://zeenews.india.com/rss/india-national-news.xml"},
    # --- IT ---
    {"source": "Bari Today", "url": "https://www.baritoday.it/rss"},
    {"source": "Bologna Today", "url": "https://www.bolognatoday.it/rss"},
    {"source": "Corriere Cronache", "url": "https://www.corriere.it/dynamic-feed/rss/section/Cronache.xml"},
    {"source": "Corriere della Sera", "url": "https://xml2.corriereobjects.it/feed-hp/homepage-restyle-2025.xml"},
    {"source": "Corriere Economia", "url": "https://www.corriere.it/dynamic-feed/rss/section/Economia.xml"},
    {"source": "Formiche", "url": "https://formiche.net/feed/"},
    {"source": "Gazzetta dello Sport", "url": "https://www.gazzetta.it/dynamic-feed/rss/section/last.xml"},
    {"source": "Genova Today", "url": "https://www.genovatoday.it/rss"},
    {"source": "Il Fatto Quotidiano", "url": "https://www.ilfattoquotidiano.it/feed/"},
    {"source": "Il Manifesto", "url": "https://ilmanifesto.it/feed"},
    {"source": "Il Messaggero Politica", "url": "https://www.ilmessaggero.it/rss/politica.xml"},
    {"source": "Il Quotidiano del Sud", "url": "https://www.quotidianodelsud.it/feed/"},
    {"source": "Il Riformista", "url": "https://www.ilriformista.it/feed"},
    {"source": "Il Sole 24 Ore Mondo", "url": "https://www.ilsole24ore.com/rss/mondo.xml"},
    {"source": "La Repubblica Cronaca", "url": "https://www.repubblica.it/rss/cronaca/rss2.0.xml"},
    {"source": "La Repubblica Esteri", "url": "https://www.repubblica.it/rss/esteri/rss2.0.xml"},
    {"source": "La Verità", "url": "https://www.laverita.info/feed"},
    {"source": "Lettera43", "url": "https://www.lettera43.it/feed/"},
    {"source": "Linkiesta", "url": "https://www.linkiesta.it/feed/"},
    {"source": "Milano Today", "url": "https://www.milanotoday.it/rss"},
    {"source": "Money.it", "url": "https://www.money.it/spip.php?page=backend"},
    {"source": "Open Politica", "url": "https://www.open.online/c/politica/feed/"},
    {"source": "Palermo Today", "url": "https://www.palermotoday.it/rss"},
    {"source": "Panorama", "url": "https://www.panorama.it/feed"},
    {"source": "Roma Today", "url": "https://www.romatoday.it/rss"},
    {"source": "Valigia Blu", "url": "https://www.valigiablu.it/feed/"},
    # --- JP ---
    {"source": "Asahi Politics", "url": "https://www.asahi.com/rss/asahi/politics.rdf"},
    {"source": "Asahi Shimbun", "url": "https://www.asahi.com/rss/asahi/newsheadlines.rdf"},
    {"source": "Diamond", "url": "https://diamond.jp/list/feed/rss/dol"},
    {"source": "ITmedia", "url": "https://rss.itmedia.co.jp/rss/2.0/itmedia_all.xml"},
    {"source": "J-CAST", "url": "https://www.j-cast.com/index.xml"},
    {"source": "Japan Forward", "url": "https://japan-forward.com/feed/"},
    {"source": "Jiji", "url": "https://www.jiji.com/rss/ranking.rdf"},
    {"source": "NHK Politics", "url": "https://www.nhk.or.jp/rss/news/cat4.xml"},
    {"source": "SoraNews24", "url": "https://soranews24.com/feed/"},
    {"source": "The Japan Times", "url": "https://www.japantimes.co.jp/feed/"},
    {"source": "The Mainichi", "url": "https://mainichi.jp/rss/etc/english_latest.rss"},
    {"source": "Yahoo Japan News", "url": "https://news.yahoo.co.jp/rss/topics/top-picks.xml"},
    # JP regional dailies + business weeklies (many share the /list/feed/rss CMS)
    {"source": "Akita Sakigake", "url": "https://feeds.feedburner.com/akita_news"},
    {"source": "Bunshun", "url": "https://bunshun.jp/list/feed/rss"},
    {"source": "Chiba Nippo", "url": "https://www.chibanippo.co.jp/rss.xml"},
    {"source": "Fukui Shimbun", "url": "https://www.fukuishimbun.co.jp/list/feed/rss"},
    {"source": "Kumamoto Nichinichi", "url": "https://kumanichi.com/rss.xml"},
    {"source": "Kyoto Shimbun", "url": "https://www.kyoto-np.co.jp/list/feed/rss"},
    {"source": "Okinawa Times", "url": "https://www.okinawatimes.co.jp/list/feed/rss"},
    {"source": "Saga Shimbun", "url": "https://www.saga-s.co.jp/list/feed/rss"},
    {"source": "Shikoku Shimbun", "url": "http://rss.shikoku-np.co.jp/rss/national_main.aspx"},
    {"source": "Toyo Keizai", "url": "https://toyokeizai.net/list/feed/rss"},
    # --- KR ---
    {"source": "KBS World", "url": "https://world.kbs.co.kr/rss/rss_news.htm?lang=e"},
    {"source": "Korea Pro", "url": "https://koreapro.org/feed/"},
    {"source": "Maeil Business", "url": "https://www.mk.co.kr/rss/30000001/"},
    {"source": "MK Business", "url": "https://www.mk.co.kr/rss/40300001/"},
    {"source": "NK News", "url": "https://www.nknews.org/feed/"},
    {"source": "The Korea Times Business", "url": "https://www.koreatimes.co.kr/www/rss/biz.xml"},
    # --- KR, Korean-language (ko) — national dailies, wires and business press ---
    {"source": "Chosun Ilbo", "url": "https://www.chosun.com/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Donga Economy", "url": "https://rss.donga.com/economy.xml"},
    {"source": "Donga Ilbo", "url": "https://rss.donga.com/total.xml"},
    {"source": "Donga Politics", "url": "https://rss.donga.com/politics.xml"},
    {"source": "ETNews", "url": "https://rss.etnews.com/Section901.xml"},
    {"source": "ETNews IT", "url": "https://rss.etnews.com/Section902.xml"},
    {"source": "Hankyung", "url": "https://www.hankyung.com/feed/all-news"},
    {"source": "Hankyung Politics", "url": "https://www.hankyung.com/feed/politics"},
    {"source": "Kyunghyang", "url": "https://www.khan.co.kr/rss/rssdata/total_news.xml"},
    {"source": "Kyunghyang Economy", "url": "https://www.khan.co.kr/rss/rssdata/economy_news.xml"},
    {"source": "Kyunghyang Politics", "url": "https://www.khan.co.kr/rss/rssdata/politic_news.xml"},
    {"source": "Money Today", "url": "https://rss.mt.co.kr/mt_news.xml"},
    {"source": "Newsis Economy", "url": "https://newsis.com/RSS/economy.xml"},
    {"source": "Newsis Politics", "url": "https://newsis.com/RSS/politics.xml"},
    {"source": "Newsis Society", "url": "https://newsis.com/RSS/society.xml"},
    {"source": "Nocut News", "url": "https://rss.nocutnews.co.kr/nocutnews.xml"},
    {"source": "OhmyNews", "url": "http://rss.ohmynews.com/rss/ohmynews.xml"},
    {"source": "Pressian", "url": "https://www.pressian.com/api/v3/site/rss/news"},
    {"source": "Segye Ilbo", "url": "https://www.segye.com/Articles/RSSList/segye_recent.xml"},
    {"source": "Seoul Shinmun", "url": "https://www.seoul.co.kr/xml/rss/rss_politics.xml"},
    {"source": "Sisa Journal", "url": "https://www.sisajournal.com/rss/allArticle.xml"},
    {"source": "Yonhap Economy", "url": "https://www.yna.co.kr/rss/economy.xml"},
    {"source": "Yonhap News", "url": "https://www.yna.co.kr/rss/news.xml"},
    {"source": "Yonhap Politics", "url": "https://www.yna.co.kr/rss/politics.xml"},
    # --- KR, Korean-language regional dailies (shared /rss/allArticle.xml CMS) ---
    {"source": "Chungcheong Today", "url": "https://www.cctoday.co.kr/rss/allArticle.xml"},
    {"source": "Incheon Ilbo", "url": "https://www.incheonilbo.com/rss/allArticle.xml"},
    {"source": "Jeju Sori", "url": "https://www.jejusori.net/rss/allArticle.xml"},
    {"source": "Jeonnam Ilbo", "url": "https://www.jnilbo.com/rss/allArticle.xml"},
    {"source": "Kangwon Domin Ilbo", "url": "https://www.kado.net/rss/allArticle.xml"},
    {"source": "Kyongbuk Ilbo", "url": "https://www.kyongbuk.co.kr/rss/allArticle.xml"},
    {"source": "Ulsan Jeil Ilbo", "url": "https://www.ujeil.com/rss/allArticle.xml"},
    # --- MX ---
    {"source": "Contralínea", "url": "https://contralinea.com.mx/feed/"},
    {"source": "Diario de Yucatán", "url": "https://www.yucatan.com.mx/feed"},
    {"source": "El Economista MX", "url": "https://www.eleconomista.com.mx/rss/ultimas-noticias"},
    {"source": "El Heraldo de México", "url": "https://heraldodemexico.com.mx/rss/feed.html"},
    {"source": "El Sol de México", "url": "https://www.elsoldemexico.com.mx/rss.xml"},
    {"source": "Expansión Economía", "url": "https://expansion.mx/rss/economia"},
    {"source": "Expansión MX", "url": "https://expansion.mx/rss"},
    {"source": "La Jornada Política", "url": "https://www.jornada.com.mx/rss/politica.xml"},
    {"source": "La Voz de Michoacán", "url": "https://www.lavozdemichoacan.com.mx/feed/"},
    {"source": "Periódico AM", "url": "https://www.am.com.mx/feed"},
    {"source": "Pie de Página", "url": "https://piedepagina.mx/feed/"},
    {"source": "Vanguardia MX", "url": "https://vanguardia.com.mx/rss.xml"},
    {"source": "Reforma", "url": "https://www.reforma.com/rss/portada.xml"},
    {"source": "Zeta Tijuana", "url": "https://zetatijuana.com/feed/"},
    # --- NL ---
    {"source": "AD Binnenland", "url": "https://www.ad.nl/binnenland/rss.xml"},
    {"source": "AD Politiek", "url": "https://www.ad.nl/politiek/rss.xml"},
    {"source": "BN DeStem Regio", "url": "https://www.bndestem.nl/breda/rss.xml"},
    {"source": "Brabant Dagblad Nieuws", "url": "https://www.bd.nl/brabant/rss.xml"},
    {"source": "Brabants Dagblad Binnenland", "url": "https://www.bd.nl/binnenland/rss.xml"},
    {"source": "Dagblad van het Noorden", "url": "https://www.dvhn.nl/rss"},
    {"source": "De Gelderlander Binnenland", "url": "https://www.gelderlander.nl/binnenland/rss.xml"},
    {"source": "De Gooi- en Eemlander", "url": "https://www.gooieneemlander.nl/rss"},
    {"source": "De Stentor Nieuws", "url": "https://www.destentor.nl/binnenland/rss.xml"},
    {"source": "De Telegraaf Nieuws", "url": "https://www.telegraaf.nl/nieuws/rss"},
    {"source": "De Volkskrant Nieuws", "url": "https://www.volkskrant.nl/nieuws/rss.xml"},
    {"source": "De Volkskrant Politiek", "url": "https://www.volkskrant.nl/politiek/rss.xml"},
    {"source": "Eindhovens Dagblad Regio", "url": "https://www.ed.nl/eindhoven/rss.xml"},
    {"source": "EW Magazine", "url": "https://www.ewmagazine.nl/feed/"},
    {"source": "Follow the Money", "url": "https://www.ftm.nl/feed"},
    {"source": "Haarlems Dagblad", "url": "https://www.haarlemsdagblad.nl/rss"},
    {"source": "Het Parool Amsterdam", "url": "https://www.parool.nl/amsterdam/rss.xml"},
    {"source": "Het Parool Nieuws", "url": "https://www.parool.nl/nieuws/rss.xml"},
    {"source": "IJmuider Courant", "url": "https://www.ijmuidercourant.nl/rss"},
    {"source": "Leeuwarder Courant", "url": "https://www.lc.nl/rss"},
    {"source": "Leidsch Dagblad", "url": "https://www.leidschdagblad.nl/rss"},
    {"source": "Metro NL", "url": "https://www.metronieuws.nl/feed/"},
    {"source": "Nederlands Dagblad Nieuws", "url": "https://www.nd.nl/nieuws/rss"},
    {"source": "Noordhollands Dagblad", "url": "https://www.noordhollandsdagblad.nl/rss"},
    {"source": "NOS Politiek", "url": "https://feeds.nos.nl/nosnieuwspolitiek"},
    {"source": "NU.nl Economie", "url": "https://www.nu.nl/rss/Economie"},
    {"source": "Trouw Groen", "url": "https://www.trouw.nl/duurzaamheid-economie/rss.xml"},
    {"source": "Trouw Politiek", "url": "https://www.trouw.nl/politiek/rss.xml"},
    {"source": "Tweakers", "url": "https://tweakers.net/feeds/nieuws.xml"},
    # --- NO ---
    {"source": "Adresseavisen", "url": "https://www.adressa.no/rss"},
    {"source": "Aftenposten Nyheter", "url": "https://www.aftenposten.no/rss/"},
    {"source": "Fædrelandsvennen", "url": "https://www.fvn.no/rss"},
    {"source": "iTromsø", "url": "https://www.itromso.no/rss"},
    {"source": "Morgenbladet", "url": "https://www.morgenbladet.no/rss"},
    {"source": "NRK Norge", "url": "https://www.nrk.no/norge/toppsaker.rss"},
    {"source": "NRK Urix", "url": "https://www.nrk.no/urix/toppsaker.rss"},
    {"source": "Stavanger Aftenblad", "url": "https://www.aftenbladet.no/rss"},
    {"source": "Sunnmørsposten", "url": "https://www.smp.no/rss"},
    {"source": "TV 2", "url": "https://www.tv2.no/rss/nyheter"},
    {"source": "VG Nyheter", "url": "https://www.vg.no/rss/feed/?categories=1068"},
    {"source": "Dagsavisen", "url": "https://www.dagsavisen.no/rss"},
    # --- NZ ---
    {"source": "Newsroom NZ", "url": "https://newsroom.co.nz/feed/"},
    {"source": "Kiwiblog", "url": "https://www.kiwiblog.co.nz/feed"},
    {"source": "NZ Herald", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/rss/curated/78/?outputType=xml"},
    {"source": "NZ Herald Business", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/business/?outputType=xml"},
    {"source": "Otago Daily Times", "url": "https://www.odt.co.nz/sitemaps/odt/rss"},
    {"source": "RNZ Business", "url": "https://www.rnz.co.nz/rss/business.xml"},
    {"source": "RNZ Political", "url": "https://www.rnz.co.nz/rss/political.xml"},
    {"source": "RNZ Te Ao Māori", "url": "https://www.rnz.co.nz/rss/te-manu-korihi.xml"},
    {"source": "RNZ World", "url": "https://www.rnz.co.nz/rss/world.xml"},
    {"source": "Stuff Politics", "url": "https://www.stuff.co.nz/rss/national/politics"},
    {"source": "The Post", "url": "https://www.thepost.co.nz/rss"},
    {"source": "The Press", "url": "https://www.stuff.co.nz/rss"},
    {"source": "Waikato Times", "url": "https://www.waikatotimes.co.nz/rss"},
    # --- PE ---
    {"source": "Andina Economía", "url": "https://andina.pe/agencia/rss.aspx?tipo=3"},
    {"source": "Andina Nacional", "url": "https://andina.pe/agencia/rss.aspx?tipo=1"},
    {"source": "Andina Regional", "url": "https://andina.pe/agencia/rss.aspx?tipo=2"},
    {"source": "Diario Correo", "url": "https://diariocorreo.pe/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "El Comercio Perú", "url": "https://elcomercio.pe/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Gestión", "url": "https://gestion.pe/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "IDL-Reporteros", "url": "https://www.idl-reporteros.pe/feed/"},
    {"source": "Wayka", "url": "https://wayka.pe/feed/"},
    # --- PH ---
    {"source": "Business World", "url": "https://www.bworldonline.com/feed/"},
    {"source": "BusinessWorld Economy", "url": "https://www.bworldonline.com/economy/feed/"},
    {"source": "GMA Money", "url": "https://data.gmanetwork.com/gno/rss/money/feed.xml"},
    {"source": "GMA News Nation", "url": "https://data.gmanetwork.com/gno/rss/news/nation/feed.xml"},
    {"source": "GMA News World", "url": "https://data.gmanetwork.com/gno/rss/news/world/feed.xml"},
    {"source": "Inquirer Nation", "url": "https://newsinfo.inquirer.net/feed"},
    {"source": "Interaksyon", "url": "https://interaksyon.philstar.com/feed/"},
    {"source": "Manila Times News", "url": "https://www.manilatimes.net/news/feed"},
    {"source": "PhilNews", "url": "https://philnews.ph/feed/"},
    {"source": "PhilStar Business", "url": "https://www.philstar.com/rss/business"},
    {"source": "Philstar Nation", "url": "https://www.philstar.com/rss/nation"},
    {"source": "Philstar World", "url": "https://www.philstar.com/rss/world"},
    {"source": "Rappler Business", "url": "https://www.rappler.com/business/feed/"},
    {"source": "Rappler World", "url": "https://www.rappler.com/world/feed/"},
    # --- PK ---
    {"source": "ARY News Pakistan", "url": "https://arynews.tv/category/pakistan/feed/"},
    {"source": "Business Recorder Pakistan", "url": "https://www.brecorder.com/feeds/latest-news"},
    {"source": "Daily Times", "url": "https://dailytimes.com.pk/feed/"},
    {"source": "Dawn Business", "url": "https://www.dawn.com/feeds/business"},
    {"source": "Dawn Pakistan", "url": "https://www.dawn.com/feeds/home"},
    {"source": "Dawn World", "url": "https://www.dawn.com/feeds/world"},
    {"source": "Minute Mirror", "url": "https://minutemirror.com.pk/feed/"},
    {"source": "Pakistan Observer", "url": "https://pakobserver.net/feed/"},
    {"source": "The Current", "url": "https://thecurrent.pk/feed/"},
    {"source": "The Express Tribune", "url": "https://tribune.com.pk/feed/home"},
    {"source": "The Express Tribune Business", "url": "https://tribune.com.pk/feed/business"},
    {"source": "The Express Tribune Pakistan", "url": "https://tribune.com.pk/feed/pakistan"},
    {"source": "The Express Tribune World", "url": "https://tribune.com.pk/feed/world"},
    # --- PL ---
    {"source": "Bankier.pl", "url": "https://www.bankier.pl/rss/wiadomosci.xml"},
    {"source": "Defence24", "url": "https://www.defence24.pl/rss"},
    {"source": "Gazeta Wyborcza", "url": "https://wyborcza.pl/pub/rss/najnowsze_wyborcza.xml"},
    {"source": "Do Rzeczy", "url": "https://dorzeczy.pl/rss"},
    {"source": "Dziennik Zachodni", "url": "https://dziennikzachodni.pl/rss"},
    {"source": "Fakt", "url": "https://www.fakt.pl/rss"},
    {"source": "Gazeta Krakowska", "url": "https://gazetakrakowska.pl/rss"},
    {"source": "Gazeta Pomorska", "url": "https://pomorska.pl/rss"},
    {"source": "Interia Biznes", "url": "https://biznes.interia.pl/feed"},
    {"source": "Krytyka Polityczna", "url": "https://krytykapolityczna.pl/feed/"},
    {"source": "Money.pl", "url": "https://www.money.pl/rss/"},
    {"source": "Money.pl Gospodarka", "url": "https://www.money.pl/rss/gospodarka/"},
    {"source": "Newsweek Polska Polska", "url": "https://www.newsweek.pl/polska/rss.xml"},
    {"source": "Notes from Poland", "url": "https://notesfrompoland.com/feed/"},
    {"source": "OKO.press", "url": "https://oko.press/feed"},
    {"source": "Onet Kraj", "url": "https://wiadomosci.onet.pl/kraj.feed"},
    {"source": "Onet Świat", "url": "https://wiadomosci.onet.pl/swiat.feed"},
    {"source": "Polsat News Polska", "url": "https://www.polsatnews.pl/rss/polska.xml"},
    {"source": "Polsat News Świat", "url": "https://www.polsatnews.pl/rss/swiat.xml"},
    {"source": "Press.pl", "url": "https://www.press.pl/rss"},
    {"source": "RMF FM", "url": "https://www.rmf24.pl/feed"},
    {"source": "Rmf24 Fakty", "url": "https://www.rmf24.pl/fakty/feed"},
    {"source": "Rzeczpospolita Ekonomia", "url": "https://www.rp.pl/rss/1019"},
    {"source": "Rzeczpospolita Polityka", "url": "https://www.rp.pl/rss/1447"},
    {"source": "TVN24 Świat", "url": "https://tvn24.pl/swiat.xml"},
    {"source": "Wprost Biznes", "url": "https://www.wprost.pl/rss/biznes"},
    {"source": "Wprost Polityka", "url": "https://www.wprost.pl/rss/polityka"},
    {"source": "Wprost Wiadomości", "url": "https://www.wprost.pl/rss/wiadomosci"},
    {"source": "Wprost Świat", "url": "https://www.wprost.pl/rss/swiat"},
    {"source": "Wyborcza Kraj", "url": "https://rss.gazeta.pl/pub/rss/najnowsze_wyborcza.xml"},
    # --- PT ---
    {"source": "Dinheiro Vivo", "url": "https://www.dinheirovivo.pt/feed/"},
    {"source": "Fumaça", "url": "https://fumaca.pt/feed/"},
    {"source": "Jornal Económico", "url": "https://jornaleconomico.pt/feed"},
    {"source": "Mensagem de Lisboa", "url": "https://amensagem.pt/feed/"},
    {"source": "Notícias ao Minuto Mundo", "url": "https://www.noticiasaominuto.com/rss/mundo"},
    {"source": "Notícias ao Minuto País", "url": "https://www.noticiasaominuto.com/rss/pais"},
    {"source": "Observador Economia", "url": "https://observador.pt/seccao/economia/feed/"},
    {"source": "Observador Política", "url": "https://observador.pt/seccao/politica/feed/"},
    {"source": "Público Economia", "url": "https://feeds.feedburner.com/PublicoEconomia"},
    {"source": "Público Mundo", "url": "https://feeds.feedburner.com/PublicoMundo"},
    {"source": "Público Política", "url": "https://feeds.feedburner.com/PublicoPolitica"},
    {"source": "Público PT", "url": "https://feeds.feedburner.com/PublicoRSS"},
    {"source": "RTP Mundo", "url": "https://www.rtp.pt/noticias/rss/mundo"},
    {"source": "Visão", "url": "https://visao.pt/feed/"},
    {"source": "Diário de Notícias da Madeira", "url": "https://www.dnoticias.pt/rss.xml"},
    # --- QA ---
    {"source": "Doha News", "url": "https://dohanews.co/feed/"},
    # --- RO ---
    {"source": "Adevărul Internațional", "url": "https://adevarul.ro/international/rss"},
    {"source": "Aktual24", "url": "https://www.aktual24.ro/feed/"},
    {"source": "Antena 3 CNN", "url": "https://www.antena3.ro/rss"},
    {"source": "Cotidianul", "url": "https://www.cotidianul.ro/feed/"},
    {"source": "Digi Sport", "url": "https://www.digisport.ro/rss"},
    {"source": "Digi24 Economie", "url": "https://www.digi24.ro/rss/stiri/economie"},
    {"source": "Digi24 Externe", "url": "https://www.digi24.ro/rss/stiri/externe"},
    {"source": "Digi24 Politică", "url": "https://www.digi24.ro/rss/stiri/politica"},
    {"source": "Economica.net", "url": "https://www.economica.net/rss"},
    {"source": "Europa FM", "url": "https://www.europafm.ro/feed/"},
    {"source": "Mediafax", "url": "https://www.mediafax.ro/rss/"},
    {"source": "Mediafax Externe", "url": "https://www.mediafax.ro/externe/rss/"},
    {"source": "News.ro", "url": "https://www.news.ro/rss"},
    {"source": "Newsweek România", "url": "https://newsweek.ro/rss"},
    {"source": "PressOne", "url": "https://pressone.ro/feed"},
    {"source": "Profit.ro", "url": "https://www.profit.ro/rss"},
    {"source": "Recorder", "url": "https://recorder.ro/feed/"},
    {"source": "Republica", "url": "https://republica.ro/rss"},
    {"source": "Spotmedia", "url": "https://spotmedia.ro/feed"},
    {"source": "Stirile ProTV Feed", "url": "https://stirileprotv.ro/rss/"},
    {"source": "Ziarul Financiar", "url": "https://www.zf.ro/rss"},
    {"source": "Ziarul Financiar Business", "url": "https://www.zf.ro/rss/business-international"},
    {"source": "Ziarul Financiar Companii", "url": "https://www.zf.ro/rss/companii"},
    # --- RU ---
    {"source": "Agentstvo", "url": "https://www.agents.media/feed/"},
    {"source": "Gazeta Politics", "url": "https://www.gazeta.ru/export/rss/politics.xml"},
    {"source": "Holod", "url": "https://holod.media/feed/"},
    {"source": "Interfax", "url": "https://www.interfax.ru/rss.asp"},
    {"source": "It's My City", "url": "https://itsmycity.ru/rss"},
    {"source": "Kommersant", "url": "https://www.kommersant.ru/RSS/news.xml"},
    {"source": "Kommersant Politics", "url": "https://www.kommersant.ru/RSS/section-politics.xml"},
    {"source": "Kommersant World", "url": "https://www.kommersant.ru/RSS/section-world.xml"},
    {"source": "Lenta World", "url": "https://lenta.ru/rss/news/world"},
    {"source": "Lenta.ru", "url": "https://lenta.ru/rss/news"},
    {"source": "Meduza English", "url": "https://meduza.io/rss/en/all"},
    {"source": "RBC", "url": "https://rssexport.rbc.ru/rbcnews/news/30/full.rss"},
    {"source": "TASS Russia", "url": "https://tass.ru/rss/v2.xml"},
    {"source": "The Bell", "url": "https://thebell.io/feed"},
    {"source": "The Insider", "url": "https://theins.ru/feed"},
    {"source": "Vedomosti", "url": "https://www.vedomosti.ru/rss/news"},
    {"source": "Vedomosti Politics", "url": "https://www.vedomosti.ru/rss/rubric/politics"},
    # --- SE ---
    {"source": "Aftonbladet Nyheter", "url": "https://rss.aftonbladet.se/rss2/small/pages/sections/nyheter/"},
    {"source": "Aftonbladet Sport", "url": "https://rss.aftonbladet.se/rss2/small/pages/sections/sportbladet/"},
    {"source": "Arbetet", "url": "https://arbetet.se/feed/"},
    {"source": "Barometern", "url": "https://www.barometern.se/feed"},
    {"source": "Blekinge Läns Tidning", "url": "https://www.blt.se/feed"},
    {"source": "Borås Tidning", "url": "https://www.bt.se/feed"},
    {"source": "Dagens Arena", "url": "https://www.dagensarena.se/feed/"},
    {"source": "Dagens ETC", "url": "https://www.etc.se/rss.xml"},
    {"source": "Dagens Samhälle", "url": "https://www.dagenssamhalle.se/rss/"},
    {"source": "Dala-Demokraten", "url": "https://www.dalademokraten.se/feed"},
    {"source": "DN Ekonomi", "url": "https://www.dn.se/ekonomi/rss/"},
    {"source": "Expressen Sport", "url": "https://feeds.expressen.se/sport/"},
    {"source": "Gefle Dagblad", "url": "https://www.gd.se/feed"},
    {"source": "GT", "url": "https://www.expressen.se/rss/gt/"},
    {"source": "Helsingborgs Dagblad", "url": "https://www.hd.se/rss.xml"},
    {"source": "Kristianstadsbladet", "url": "https://www.kristianstadsbladet.se/feed"},
    {"source": "Länstidningen Östersund", "url": "https://www.ltz.se/feed"},
    {"source": "Nerikes Allehanda", "url": "https://www.na.se/feed"},
    {"source": "Nya Wermlands-Tidningen", "url": "https://www.nwt.se/rss.xml"},
    {"source": "Smålandsposten", "url": "https://www.smp.se/feed"},
    {"source": "Sundsvalls Tidning", "url": "https://www.st.nu/feed"},
    {"source": "Sveriges Radio Ekot", "url": "https://api.sr.se/api/rss/program/83?format=145"},
    {"source": "SVT Ekonomi", "url": "https://www.svt.se/nyheter/ekonomi/rss.xml"},
    {"source": "SVT Inrikes", "url": "https://www.svt.se/nyheter/inrikes/rss.xml"},
    {"source": "SVT Lokalt Skåne", "url": "https://www.svt.se/nyheter/lokalt/skane/rss.xml"},
    {"source": "SVT Lokalt Stockholm", "url": "https://www.svt.se/nyheter/lokalt/stockholm/rss.xml"},
    {"source": "SVT Lokalt Väst", "url": "https://www.svt.se/nyheter/lokalt/vast/rss.xml"},
    {"source": "SVT Utrikes", "url": "https://www.svt.se/nyheter/utrikes/rss.xml"},
    {"source": "Sydsvenskan Malmö", "url": "https://www.sydsvenskan.se/rss?category=malmo"},
    {"source": "Vestmanlands Läns Tidning", "url": "https://www.vlt.se/feed"},
    {"source": "Ystads Allehanda", "url": "https://www.ystadsallehanda.se/feed"},
    # --- SG ---
    {"source": "CNA Asia", "url": "https://www.channelnewsasia.com/rssfeeds/8395884"},
    {"source": "CNA Business SG", "url": "https://www.channelnewsasia.com/rssfeeds/8395954"},
    {"source": "Rice Media", "url": "https://www.ricemedia.co/feed/"},
    {"source": "Straits Times Business", "url": "https://www.straitstimes.com/news/business/rss.xml"},
    {"source": "The Business Times SG", "url": "https://www.businesstimes.com.sg/rss/top-stories"},
    {"source": "The Business Times Singapore", "url": "https://www.businesstimes.com.sg/rss/singapore"},
    {"source": "The Business Times World", "url": "https://www.businesstimes.com.sg/rss/international"},
    {"source": "The Straits Times Asia", "url": "https://www.straitstimes.com/news/asia/rss.xml"},
    {"source": "The Straits Times World", "url": "https://www.straitstimes.com/news/world/rss.xml"},
    {"source": "Vulcan Post", "url": "https://vulcanpost.com/feed/"},
    # --- TR ---
    {"source": "Anadolu Agency", "url": "https://www.aa.com.tr/tr/rss/default?cat=guncel"},
    {"source": "BBC Türkçe", "url": "https://feeds.bbci.co.uk/turkce/rss.xml"},
    {"source": "CNN Türk", "url": "https://www.cnnturk.com/feed/rss/all/news"},
    {"source": "CNN Türk Dünya", "url": "https://www.cnnturk.com/feed/rss/dunya/news"},
    {"source": "Cumhuriyet Dünya", "url": "https://www.cumhuriyet.com.tr/rss/dunya"},
    {"source": "Cumhuriyet Ekonomi", "url": "https://www.cumhuriyet.com.tr/rss/ekonomi"},
    {"source": "Cumhuriyet Türkiye", "url": "https://www.cumhuriyet.com.tr/rss/turkiye"},
    {"source": "Daily Sabah", "url": "https://www.dailysabah.com/rssFeed/home"},
    {"source": "Diken", "url": "https://www.diken.com.tr/feed/"},
    {"source": "Dünya Gazetesi", "url": "https://www.dunya.com/rss"},
    {"source": "Ekonomim", "url": "https://www.ekonomim.com/rss"},
    {"source": "Euronews Türkçe", "url": "https://tr.euronews.com/rss"},
    {"source": "Evrensel", "url": "https://www.evrensel.net/rss/haber.xml"},
    {"source": "HaberGlobal", "url": "https://haberglobal.com.tr/rss"},
    {"source": "Habertürk", "url": "https://www.haberturk.com/rss"},
    {"source": "Habertürk Ekonomi", "url": "https://www.haberturk.com/rss/ekonomi.xml"},
    {"source": "Habertürk Gündem", "url": "https://www.haberturk.com/rss/gundem.xml"},
    {"source": "Hürriyet Dünya", "url": "https://www.hurriyet.com.tr/rss/dunya"},
    {"source": "Hürriyet Ekonomi", "url": "https://www.hurriyet.com.tr/rss/ekonomi"},
    {"source": "Hürriyet Gündem", "url": "https://www.hurriyet.com.tr/rss/gundem"},
    {"source": "Independent Türkçe", "url": "https://www.indyturk.com/rss.xml"},
    {"source": "Karar", "url": "https://www.karar.com/rss"},
    {"source": "Milliyet Dünya", "url": "https://www.milliyet.com.tr/rss/rssnew/dunyarss.xml"},
    {"source": "Milliyet Ekonomi", "url": "https://www.milliyet.com.tr/rss/rssnew/ekonomirss.xml"},
    {"source": "Milliyet Gündem", "url": "https://www.milliyet.com.tr/rss/rssnew/gundemrss.xml"},
    {"source": "NTV Dünya", "url": "https://www.ntv.com.tr/dunya.rss"},
    {"source": "NTV Türkiye", "url": "https://www.ntv.com.tr/turkiye.rss"},
    {"source": "Sabah Dünya", "url": "https://www.sabah.com.tr/rss/dunya.xml"},
    {"source": "Sabah Ekonomi", "url": "https://www.sabah.com.tr/rss/ekonomi.xml"},
    {"source": "Sabah Gündem", "url": "https://www.sabah.com.tr/rss/gundem.xml"},
    {"source": "Star Gazete", "url": "https://www.star.com.tr/rss/rss.asp"},
    {"source": "Türkiye Gazetesi", "url": "https://www.turkiyegazetesi.com.tr/rss"},
    {"source": "Yeni Şafak", "url": "https://www.yenisafak.com/rss"},
    {"source": "Yeni Şafak Gündem", "url": "https://www.yenisafak.com/rss?xml=gundem"},
    {"source": "Yeniçağ", "url": "https://www.yenicaggazetesi.com.tr/rss"},
    # --- UA ---
    {"source": "Censor.NET", "url": "https://censor.net/includes/news_uk.xml"},
    {"source": "Espreso", "url": "https://espreso.tv/rss"},
    {"source": "Interfax Ukraine", "url": "https://ua.interfax.com.ua/news/last.rss"},
    {"source": "LB.ua", "url": "https://lb.ua/rss/ukr/news.xml"},
    {"source": "Novoe Vremya Ukr", "url": "https://nv.ua/ukr/rss/all.xml"},
    {"source": "RBC Ukraine", "url": "https://www.rbc.ua/static/rss/all.ukr.rss.xml"},
    {"source": "Suspilne", "url": "https://suspilne.media/rss/all.rss"},
    {"source": "TSN", "url": "https://tsn.ua/rss"},
    {"source": "Ukrainform Ukr", "url": "https://www.ukrinform.ua/rss/block-lastnews"},
    {"source": "Ukrainska Pravda Economy", "url": "https://www.epravda.com.ua/rss/"},
    {"source": "Ukrainska Pravda Life", "url": "https://life.pravda.com.ua/rss/"},
    {"source": "Ukrainska Pravda Politics", "url": "https://www.pravda.com.ua/rss/view_news/"},
    # --- US ---
    {"source": "Ars Technica", "url": "https://feeds.arstechnica.com/arstechnica/index"},
    {"source": "Axios", "url": "https://api.axios.com/feed/"},
    {"source": "Bloomberg Politics", "url": "https://feeds.bloomberg.com/politics/news.rss"},
    {"source": "Business Insider", "url": "https://www.businessinsider.com/rss"},
    {"source": "Chicago Sun-Times", "url": "https://chicago.suntimes.com/rss/index.xml"},
    {"source": "Cleveland.com", "url": "https://www.cleveland.com/arc/outboundfeeds/rss/"},
    {"source": "Common Dreams", "url": "https://www.commondreams.org/feeds/news.rss"},
    {"source": "Fortune", "url": "https://fortune.com/feed/"},
    {"source": "Grist", "url": "https://grist.org/feed/"},
    {"source": "MarketWatch", "url": "https://feeds.marketwatch.com/marketwatch/topstories/"},
    {"source": "Mother Jones", "url": "https://www.motherjones.com/feed/"},
    {"source": "National Review", "url": "https://www.nationalreview.com/feed/"},
    {"source": "Politico", "url": "https://rss.politico.com/politics-news.xml"},
    {"source": "Reason", "url": "https://reason.com/latest/feed/"},
    {"source": "Salon", "url": "https://www.salon.com/feed/"},
    {"source": "Seattle Times", "url": "https://www.seattletimes.com/feed/"},
    {"source": "Slate", "url": "https://slate.com/feeds/all.rss"},
    {"source": "Star Tribune", "url": "https://www.startribune.com/rss/"},
    {"source": "STAT News", "url": "https://www.statnews.com/feed/"},
    {"source": "The American Conservative", "url": "https://www.theamericanconservative.com/feed/"},
    {"source": "The Conversation US", "url": "https://theconversation.com/us/articles.atom"},
    {"source": "The Guardian US", "url": "https://www.theguardian.com/us-news/rss"},
    {"source": "The Hill Homenews", "url": "https://thehill.com/homenews/feed/"},
    {"source": "The Intercept", "url": "https://theintercept.com/feed/"},
    {"source": "The Nation", "url": "https://www.thenation.com/feed/?post_type=article"},
    {"source": "The New Yorker", "url": "https://www.newyorker.com/feed/news"},
    {"source": "The Oregonian", "url": "https://www.oregonlive.com/arc/outboundfeeds/rss/"},
    {"source": "The Verge US", "url": "https://www.theverge.com/rss/full.xml"},
    # --- VN ---
    {"source": "Bao Giao Thong", "url": "https://www.baogiaothong.vn/rss/home.rss"},
    {"source": "Cong An Nhan Dan", "url": "https://cand.com.vn/rss/home.rss"},
    {"source": "Dan Tri Kinh doanh", "url": "https://dantri.com.vn/rss/kinh-doanh.rss"},
    {"source": "Dan Tri Su Kien", "url": "https://dantri.com.vn/rss/su-kien.rss"},
    {"source": "Thanh Nien Chinh Tri", "url": "https://thanhnien.vn/rss/chinh-tri.rss"},
    {"source": "Thanh Nien Thoi su", "url": "https://thanhnien.vn/rss/thoi-su.rss"},
    {"source": "Tien Phong", "url": "https://tienphong.vn/rss/home.rss"},
    {"source": "Vietnamnet Thoi su", "url": "https://vietnamnet.vn/rss/thoi-su.rss"},
    {"source": "VietnamPlus VN", "url": "https://www.vietnamplus.vn/rss/tin-moi.rss"},
    {"source": "VnExpress Kinh doanh", "url": "https://vnexpress.net/rss/kinh-doanh.rss", "ua": "compat"},
    {"source": "VnExpress Thế giới", "url": "https://vnexpress.net/rss/the-gioi.rss", "ua": "compat"},
    {"source": "VnExpress Thời sự", "url": "https://vnexpress.net/rss/thoi-su.rss", "ua": "compat"},
    # ===== Asia, Middle East & Pacific expansion (2026-10) =====
    # --- CN (zh) ---
    {"source": "BBC 中文", "url": "https://feeds.bbci.co.uk/zhongwen/simp/rss.xml"},
    {"source": "Chinanews", "url": "https://www.chinanews.com.cn/rss/scroll-news.xml"},
    {"source": "DW 中文", "url": "https://rss.dw.com/rdf/rss-chi-all"},
    {"source": "Initium Media", "url": "https://theinitium.com/rss/"},
    {"source": "IT之家", "url": "https://www.ithome.com/rss/"},
    {"source": "RFA 中文", "url": "https://www.rfa.org/arc/outboundfeeds/mandarin/rss/?outputType=xml"},
    {"source": "RFI 中文", "url": "https://www.rfi.fr/cn/rss"},
    {"source": "VOA 中文", "url": "https://www.voachinese.com/api/zm_yql-vomx-tpeybti"},
    # --- HK (zh) ---
    {"source": "Bastille Post", "url": "https://www.bastillepost.com/hongkong/feed"},
    {"source": "i-Cable", "url": "https://www.i-cable.com/feed/"},
    {"source": "RTHK 中文", "url": "https://rthk.hk/rthk/news/rss/c_expressnews_clocal.xml"},
    {"source": "Sing Tao", "url": "https://www.stheadline.com/rss"},
    {"source": "The Collective HK", "url": "https://thecollectivehk.com/feed/"},
    {"source": "Yahoo Hong Kong", "url": "https://hk.news.yahoo.com/rss/hong-kong"},
    # --- ID (en, id) ---
    {"source": "Antara English", "url": "https://en.antaranews.com/rss/news.xml"},
    {"source": "BBC Indonesia", "url": "https://feeds.bbci.co.uk/indonesia/rss.xml"},
    {"source": "Detik News", "url": "https://news.detik.com/rss"},
    {"source": "Kumparan", "url": "https://lapi.kumparan.com/v2.0/rss/"},
    # --- IL (ar, he) ---
    {"source": "Sonara", "url": "https://sonara.net/feed/"},
    {"source": "Behadrei Haredim", "url": "https://www.bhol.co.il/rss"},
    {"source": "Mekomit", "url": "https://www.mekomit.co.il/feed/"},
    {"source": "Srugim", "url": "https://www.srugim.co.il/feed"},
    {"source": "Zman Yisrael", "url": "https://www.zman.co.il/feed/"},
    # --- IN (bn, gu, hi, kn, ml, mr, pa, ta, te, ur) ---
    {"source": "ABP Ananda", "url": "https://bengali.abplive.com/home/feed"},
    {"source": "Sangbad Pratidin", "url": "https://www.sangbadpratidin.in/feed/"},
    {"source": "ABP Asmita", "url": "https://gujarati.abplive.com/home/feed"},
    {"source": "BBC Gujarati", "url": "https://feeds.bbci.co.uk/gujarati/rss.xml"},
    {"source": "TV9 Gujarati", "url": "https://tv9gujarati.com/feed"},
    {"source": "Aaj Tak", "url": "https://www.aajtak.in/rssfeeds/?id=home"},
    {"source": "ABP News", "url": "https://www.abplive.com/home/feed"},
    {"source": "Amar Ujala", "url": "https://www.amarujala.com/rss/breaking-news.xml"},
    {"source": "BBC Hindi", "url": "https://feeds.bbci.co.uk/hindi/rss.xml"},
    {"source": "Dainik Bhaskar", "url": "https://www.bhaskar.com/rss-v1--category-1061.xml"},
    {"source": "India TV Hindi", "url": "https://www.indiatv.in/rssnews/topstory.xml"},
    {"source": "NDTV Hindi", "url": "https://feeds.feedburner.com/ndtvkhabar-latest"},
    {"source": "News18 Hindi", "url": "https://hindi.news18.com/rss/khabar/nation/nation.xml"},
    {"source": "Prabhat Khabar", "url": "https://www.prabhatkhabar.com/feed"},
    {"source": "The Wire Hindi", "url": "https://thewirehindi.com/feed/"},
    {"source": "Zee News Hindi", "url": "https://zeenews.india.com/hindi/india.xml"},
    {"source": "Asianet Suvarna News", "url": "https://kannada.asianetnews.com/rss"},
    {"source": "Prajavani", "url": "https://www.prajavani.net/stories.rss"},
    {"source": "TV9 Kannada", "url": "https://tv9kannada.com/feed"},
    {"source": "Asianet News", "url": "https://www.asianetnews.com/rss"},
    {"source": "Mathrubhumi", "url": "https://www.mathrubhumi.com/rss/news"},
    {"source": "Twentyfour News", "url": "https://www.twentyfournews.com/feed"},
    {"source": "ABP Majha", "url": "https://marathi.abplive.com/home/feed"},
    {"source": "BBC Marathi", "url": "https://feeds.bbci.co.uk/marathi/rss.xml"},
    {"source": "eSakal", "url": "https://www.esakal.com/stories.rss"},
    {"source": "TV9 Marathi", "url": "https://www.tv9marathi.com/feed"},
    {"source": "ABP Sanjha", "url": "https://punjabi.abplive.com/home/feed"},
    {"source": "BBC Punjabi", "url": "https://feeds.bbci.co.uk/punjabi/rss.xml"},
    {"source": "ABP Nadu", "url": "https://tamil.abplive.com/home/feed"},
    {"source": "BBC Tamil", "url": "https://feeds.bbci.co.uk/tamil/rss.xml"},
    {"source": "Dinamani", "url": "https://www.dinamani.com/stories.rss"},
    {"source": "ABP Desam", "url": "https://telugu.abplive.com/home/feed"},
    {"source": "BBC Telugu", "url": "https://feeds.bbci.co.uk/telugu/rss.xml"},
    {"source": "Oneindia Telugu", "url": "https://telugu.oneindia.com/rss/telugu-news-fb.xml"},
    {"source": "Sakshi", "url": "https://www.sakshi.com/rss.xml"},
    {"source": "TV9 Telugu", "url": "https://tv9telugu.com/feed"},
    {"source": "Qaumi Awaz", "url": "https://www.qaumiawaz.com/stories.rss"},
    {"source": "Siasat Urdu", "url": "https://urdu.siasat.com/feed/"},
    # --- NZ (en) ---
    {"source": "Asia Pacific Report", "url": "https://asiapacificreport.nz/feed/"},
    {"source": "Farmers Weekly NZ", "url": "https://www.farmersweekly.co.nz/feed/"},
    {"source": "Newstalk ZB", "url": "https://www.newstalkzb.co.nz/news/rss"},
    {"source": "Te Ao Māori News", "url": "https://www.teaonews.co.nz/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "Waatea News", "url": "https://waateanews.com/feed/"},
    # --- PH (tl) ---
    {"source": "Hataw", "url": "https://hatawtabloid.com/feed/"},
    {"source": "Pilipino Mirror", "url": "https://mirror.pilipinomirror.com/feed/"},
    {"source": "Pinoy Weekly", "url": "https://pinoyweekly.org/feed/"},
    {"source": "Remate", "url": "https://remate.ph/sitemap.rss"},
    {"source": "Saksi Ngayon", "url": "https://saksingayon.com/feed/"},
    # --- PK (ur) ---
    {"source": "Aaj News", "url": "https://www.aaj.tv/feeds/latest-news"},
    {"source": "ARY Urdu", "url": "https://urdu.arynews.tv/feed/"},
    {"source": "BBC Urdu", "url": "https://feeds.bbci.co.uk/urdu/rss.xml"},
    {"source": "Bol News Urdu", "url": "https://www.bolnews.com/urdu/feed/"},
    {"source": "Daily Pakistan", "url": "https://dailypakistan.com.pk/rss/latest"},
    {"source": "DW Urdu", "url": "https://rss.dw.com/rdf/rss-urd-all"},
    {"source": "Express Urdu", "url": "https://www.express.pk/feed/"},
    {"source": "Independent Urdu", "url": "https://www.independenturdu.com/rss.xml"},
    {"source": "Jang", "url": "https://jang.com.pk/rss/1/1"},
    {"source": "Jang World", "url": "https://jang.com.pk/rss/1/2"},
    # --- QA (ar, en) ---
    {"source": "Al Arab Qatar", "url": "https://alarab.qa/rss/latestNews"},
    {"source": "Al Sharq", "url": "https://al-sharq.com/rss/latestNews"},
    {"source": "Lusail News", "url": "https://lusailnews.net/rss/latestNews"},
    {"source": "QNA", "url": "https://qna.org.qa/ar-QA/Pages/RSS-Feeds/General"},
    {"source": "QNA Qatar", "url": "https://qna.org.qa/ar-QA/Pages/RSS-Feeds/Qatar"},
    {"source": "QNA English", "url": "https://qna.org.qa/en/Pages/RSS-Feeds/General"},
    {"source": "QNA English Qatar", "url": "https://qna.org.qa/en/Pages/RSS-Feeds/Qatar"},
    {"source": "The Peninsula", "url": "https://thepeninsulaqatar.com/rss/latestNews"},
    # --- SG (en, ms, ta, zh) ---
    {"source": "e27", "url": "https://e27.co/feed/"},
    {"source": "Fintech News SG", "url": "https://fintechnews.sg/feed/"},
    {"source": "Singapore Business Review", "url": "https://sbr.com.sg/rss.xml"},
    {"source": "Berita Mediacorp", "url": "https://berita.mediacorp.sg/api/v1/rss-outbound-feed?_format=xml"},
    {"source": "Seithi", "url": "https://seithi.mediacorp.sg/api/v1/rss-outbound-feed?_format=xml"},
    # --- VN (en, vi) ---
    {"source": "Tuoi Tre News", "url": "https://tuoitrenews.vn/rss"},
    {"source": "BBC Tieng Viet", "url": "https://feeds.bbci.co.uk/vietnamese/rss.xml"},
    # --- Moved here from sitemaps (2026-10) ---
    {"source": "Diario Concepción", "url": "https://www.diarioconcepcion.cl/rss.xml"},
    {"source": "Vorwärts", "url": "https://www.vorwaerts.ch/feed/"},
]

# Descriptive UA + contact. Generic bot UAs get 403'd by these sites.
USER_AGENT = "AllNewsBot/0.1 (+https://github.com/yourname/all.news; POC)"
TIMEOUT = 15
SUMMARY_MAX = 200  # keep snippets short — legal caution
DAILY_PER_SOURCE = 150  # max articles kept per source per day (anti-spam cap)
WELTWOCHE_SITEMAP_INDEX = "https://weltwoche.ch/sitemap_index.xml"
WELTWOCHE_MAX = 50  # newest N stories from the latest weekly sitemap
NEBELSPALTER_SITEMAP = "https://nebelspalter.ch/sitemap.xml"
NEBELSPALTER_MAX = 50  # newest N /themen/YYYY/MM/slug articles
# Google-News sitemaps: real <news:title> + publication_date (no slug guessing).
NEWS_SITEMAPS = [
    {"source": "Watson",               "url": "https://www.watson.ch/api/2.0/feed/googlesitemap.xml",            "max": 50},
    {"source": "Freiburger Nachrichten","url": "https://www.freiburger-nachrichten.ch/sitemap_latest_news.xml", "max": 50},
    {"source": "Bote der Urschweiz",   "url": "https://www.bote.ch/googlenews.sitemap.xml",                    "max": 50},
    {"source": "Bild",                 "url": "https://www.bild.de/sitemap-news.xml", "max": 50},  # DE; see SOURCE_ORIGIN
    {"source": "Watson FR", "url": "https://www.watson.ch/fr/api/2.0/feed/googlesitemap.xml", "max": 50},
    # --- News sitemaps for RSS-poor countries (real <news:title>; see SOURCE_ORIGIN) ---
    {"source": "Excélsior",         "url": "https://www.excelsior.com.mx/sitemap-google-news.xml", "max": 50},
    {"source": "Milenio",           "url": "https://www.milenio.com/sitemap/google-news/sitemap-google-news-current-1.xml", "max": 50},
    {"source": "Al Jazeera Arabic", "url": "https://www.aljazeera.net/news-sitemap.xml", "max": 50},
    {"source": "El Espectador",     "url": "https://www.elespectador.com/arc/outboundfeeds/news-sitemap/?outputType=xml", "max": 50},
    {"source": "El Colombiano",     "url": "https://www.elcolombiano.com/sitemapforgoogle.xml", "max": 50},
    {"source": "Semana",            "url": "https://www.semana.com/arc/outboundfeeds/news-sitemap/?outputType=xml", "max": 50},
    {"source": "Chosun",            "url": "https://www.chosun.com/arc/outboundfeeds/news-sitemap/?outputType=xml", "max": 50},
    {"source": "The News",          "url": "https://www.thenews.com.pk/assets/uploads/google_news_latest.xml", "max": 50},
    {"source": "Geo News",          "url": "https://www.geo.tv/assets/uploads/google_news_latest.xml", "max": 50},
    {"source": "Business Recorder", "url": "https://www.brecorder.com/feeds/sitemap", "max": 50},
    {"source": "Globes",            "url": "https://www.globes.co.il/data/webservices/google-maps.ashx", "max": 50},
    {"source": "Sankei",            "url": "https://www.sankei.com/feeds/google-sitemap/?outputType=xml&from=0", "max": 50},
    {"source": "Stuff",             "url": "https://www.stuff.co.nz/sitemap/news/sitemap.xml", "max": 50},
    {"source": "1News",             "url": "https://www.1news.co.nz/arc/outboundfeeds/news-sitemap/?outputType=xml", "max": 50},
    {"source": "AsiaOne",           "url": "https://www.asiaone.com/googlenews.xml", "max": 50},
    {"source": "Business Times",    "url": "https://www.businesstimes.com.sg/googlenews.xml", "max": 50},
    {"source": "Kompas",            "url": "https://www.kompas.com/sitemap-news-tren.xml", "max": 50},
    {"source": "Liputan6",          "url": "https://www.liputan6.com/news/sitemap.xml", "max": 50},
    {"source": "The Standard",      "url": "https://www.thestandard.com.hk/sitemap.xml", "max": 50},
    {"source": "HK01",              "url": "https://www.hk01.com/sitemap.xml", "max": 50},
    {"source": "VietnamNet",        "url": "https://vietnamnet.vn/sitemap-news.xml", "max": 50},
    {"source": "VietnamPlus",       "url": "https://www.vietnamplus.vn/sitemaps/google-news.xml", "max": 50},
    {"source": "Zing",              "url": "https://znews.vn/sitemap/sitemap-news.xml", "max": 50},
    {"source": "Hromadske",         "url": "https://hromadske.ua/sitemap/news.xml", "max": 50},
    {"source": "Kyiv Independent",  "url": "https://kyivindependent.com/news-sitemap.xml", "max": 50},
    {"source": "Liga.net",          "url": "https://www.liga.net/sitemap-main/sitemap-news.xml", "max": 50},
    {"source": "Irish Examiner",    "url": "https://www.irishexaminer.com/news-sitemap.xml", "max": 50},
    # --- Latin America: outlets with no usable RSS, but a real Google News sitemap ---
    {"source": "El Mostrador",      "url": "https://www.elmostrador.cl/sitemap_news.xml", "max": 50},
    {"source": "Cooperativa",       "url": "https://www.cooperativa.cl/noticias/sitemap_news.xml", "max": 50},
    {"source": "Meganoticias",      "url": "https://www.meganoticias.cl/sitemaps/sitemap-news.xml", "max": 50},
    {"source": "El Dínamo",         "url": "https://www.eldinamo.cl/_files/sitemaps/sitemap_news.xml", "max": 50},
    {"source": "La República Perú",     "url": "https://larepublica.pe/sitemap/noticias.xml", "max": 50},
    {"source": "La República Política", "url": "https://larepublica.pe/sitemap/politica.xml", "max": 50},
    {"source": "La República Sociedad", "url": "https://larepublica.pe/sitemap/sociedad.xml", "max": 50},
    {"source": "El Informador",     "url": "https://www.informador.mx/sitemaps/googlenews.xml", "max": 50},
    # Arc XP "sitemap-news/latest" — the sitemap-news-index only points at this + yesterday.
    {"source": "Crónica",           "url": "https://www.cronica.com.mx/arc/outboundfeeds/sitemap-news/latest/", "max": 50},
    {"source": "El Heraldo CO",     "url": "https://www.elheraldo.co/arc/outboundfeeds/sitemap-news/latest/", "max": 50},
    {"source": "El Universal Cartagena", "url": "https://www.eluniversal.com.co/arc/outboundfeeds/sitemap-news/latest/", "max": 50},
    {"source": "Vanguardia CO",     "url": "https://www.vanguardia.com/arc/outboundfeeds/sitemap-news/latest/", "max": 50},
    # Japan regional dailies without RSS
    {"source": "Chunichi Shimbun", "url": "https://www.chunichi.co.jp/sitemap_news.xml", "max": 50},
    {"source": "Chugoku Shimbun",  "url": "https://www.chugoku-np.co.jp/list/feed/rss4googlenews", "max": 50},
    # --- Asia, Middle East & Pacific expansion (2026-10) ---
    {"source": "am730", "url": "https://www.am730.com.hk/sitemap.xml", "max": 50},  # HK
    {"source": "Commercial Radio HK", "url": "https://www.881903.com/sitemap-google-news.xml", "max": 50},  # HK
    {"source": "TVB News", "url": "https://news.tvb.com/sitemap.xml", "max": 50},  # HK
    {"source": "Jakarta Post", "url": "https://www.thejakartapost.com/sitemap_news.xml", "max": 50},  # ID
    {"source": "Bloomberg Technoz", "url": "https://www.bloombergtechnoz.com/sitemap-news.xml", "max": 50},  # ID
    {"source": "IDN Times", "url": "https://www.idntimes.com/news/sitemap-news.xml", "max": 50},  # ID
    {"source": "iNews.id", "url": "https://www.inews.id/news/sitemap.xml", "max": 50},  # ID
    {"source": "Jawa Pos", "url": "https://www.jawapos.com/sitemap-news.xml", "max": 50},  # ID
    {"source": "Merdeka", "url": "https://www.merdeka.com/peristiwa/sitemap.xml", "max": 50},  # ID
    {"source": "Metro TV News", "url": "https://www.metrotvnews.com/sitemap/sitemap-news.xml", "max": 50},  # ID
    {"source": "Pikiran Rakyat", "url": "https://www.pikiran-rakyat.com/news/sitemap_news.xml", "max": 50},  # ID
    {"source": "Suara", "url": "https://www.suara.com/news/sitemap-news.xml", "max": 50},  # ID
    {"source": "VOI", "url": "https://voi.id/sitemap_news.xml", "max": 50},  # ID
    {"source": "Al-Ittihad", "url": "https://alittihad44.com/gnews.xml", "max": 50},  # IL
    {"source": "Arab48", "url": "https://www.arab48.com/news_sitemap.xml", "max": 50},  # IL
    {"source": "i24NEWS Arabic", "url": "https://www.i24news.tv/ar/sitemapGoogleNews.xml", "max": 50},  # IL
    {"source": "Kul al-Arab", "url": "https://kul-alarab.com/sitemap-news.xml", "max": 50},  # IL
    {"source": "0404", "url": "https://0404.co.il/news-sitemap.xml", "max": 50},  # IL
    {"source": "i24NEWS Hebrew", "url": "https://www.i24news.tv/he/sitemapGoogleNews.xml", "max": 50},  # IL
    {"source": "Kikar HaShabbat", "url": "https://www.kikar.co.il/news-sitemap.xml", "max": 50},  # IL
    {"source": "N12", "url": "https://www.mako.co.il/SiteMap/mako_news/1.xml.gz", "max": 50},  # IL
    {"source": "Eisamay", "url": "https://eisamay.com/news_sitemap.xml", "max": 50},  # IN
    {"source": "News18 Bangla", "url": "https://bengali.news18.com/commonfeeds/v1/ben/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "Divya Bhaskar", "url": "https://www.divyabhaskar.co.in/sitemaps-v1--sitemap-google-news-1.xml", "max": 50},  # IN
    {"source": "Gujarat Samachar", "url": "https://www.gujaratsamachar.com/news-sitemap.xml", "max": 50},  # IN
    {"source": "News18 Gujarati", "url": "https://gujarati.news18.com/commonfeeds/v1/guj/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "Dainik Jagran", "url": "https://www.jagran.com/news-sitemap.xml", "max": 50},  # IN
    {"source": "Jansatta", "url": "https://www.jansatta.com/news-sitemap.xml", "max": 50},  # IN
    {"source": "Live Hindustan", "url": "https://www.livehindustan.com/news-sitemap.xml", "max": 50},  # IN
    {"source": "Navbharat Times", "url": "https://navbharattimes.indiatimes.com/staticsitemap/nbt/news/sitemap-48hours.xml", "max": 50},  # IN
    {"source": "Patrika", "url": "https://www.patrika.com/google-news-sitemap-v1.xml", "max": 50},  # IN
    {"source": "Kannada Prabha", "url": "https://www.kannadaprabha.in/news/sitemap.xml", "max": 50},  # IN
    {"source": "News18 Kannada", "url": "https://kannada.news18.com/commonfeeds/v1/kan/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "Madhyamam", "url": "https://www.madhyamam.com/news-sitemap-daily.xml", "max": 50},  # IN
    {"source": "Loksatta", "url": "https://www.loksatta.com/news-sitemap.xml", "max": 50},  # IN
    {"source": "Maharashtra Times", "url": "https://maharashtratimes.com/staticsitemap/mt/news/sitemap-48hours.xml", "max": 50},  # IN
    {"source": "Jagbani", "url": "https://jagbani.punjabkesari.in/newssitemap.xml", "max": 50},  # IN
    {"source": "News18 Punjab", "url": "https://punjab.news18.com/commonfeeds/v1/pan/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "Hindu Tamil Thisai", "url": "https://www.hindutamil.in/news_sitemap.xml", "max": 50},  # IN
    {"source": "News18 Tamil", "url": "https://tamil.news18.com/commonfeeds/v1/tam/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "ETV Bharat Urdu", "url": "https://www.etvbharat.com/ur/national/googlenewssitemap.xml", "max": 50},  # IN
    {"source": "News18 Urdu", "url": "https://urdu.news18.com/commonfeeds/v1/urd/sitemap/google-news.xml", "max": 50},  # IN
    {"source": "Bay of Plenty Times", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=bay-of-plenty-times", "max": 50},  # NZ
    {"source": "Gisborne Herald", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=gisborne-herald", "max": 50},  # NZ
    {"source": "Hawke's Bay Today", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=hawkes-bay-today", "max": 50},  # NZ
    {"source": "Northern Advocate", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=northern-advocate", "max": 50},  # NZ
    {"source": "Rotorua Daily Post", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=rotorua-daily-post", "max": 50},  # NZ
    {"source": "Whanganui Chronicle", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/sitemap-news/?outputType=xml&_website=whanganui-chronicle", "max": 50},  # NZ
    {"source": "Abante", "url": "https://www.abante.com.ph/news-sitemap.xml", "max": 50},  # PH
    {"source": "Abante Tonite", "url": "https://tonite.abante.com.ph/news-sitemap.xml", "max": 50},  # PH
    {"source": "Radyo Pilipinas", "url": "https://radyopilipinas.ph/news-sitemap.xml", "max": 50},  # PH
    {"source": "Nawa-i-Waqt", "url": "https://www.nawaiwaqt.com.pk/sitemap_news_google.xml", "max": 50},  # PK
    {"source": "Al Watan Qatar", "url": "https://www.al-watan.com/sitemaps/newsSitemap.xml", "max": 50},  # QA
    {"source": "Qatar Tribune", "url": "https://www.qatar-tribune.com/sitemaps/newsSitemap.xml", "max": 50},  # QA
    {"source": "Stomp", "url": "https://www.stomp.sg/googlenews.xml", "max": 50},  # SG
    {"source": "Berita Harian SG", "url": "https://www.beritaharian.sg/googlenews.xml", "max": 50},  # SG
    {"source": "Tamil Murasu", "url": "https://www.tamilmurasu.com.sg/googlenews.xml", "max": 50},  # SG
    {"source": "8world", "url": "https://www.8world.com/google-news-sitemap/171", "max": 50},  # SG
    {"source": "Lianhe Zaobao", "url": "https://www.zaobao.com.sg/googlenews.xml", "max": 50},  # SG
    {"source": "SGGP News", "url": "https://en.sggp.org.vn/sitemaps/google-news.xml", "max": 50},  # VN
    {"source": "Vietnam News", "url": "https://vietnamnews.vn/sitemap_news.xml", "max": 50},  # VN
    {"source": "VietnamPlus English", "url": "https://en.vietnamplus.vn/sitemaps/google-news.xml", "max": 50},  # VN
    {"source": "CafeF", "url": "https://cafef.vn/google-news-sitemap.xml", "max": 50},  # VN
    {"source": "Nhan Dan", "url": "https://nhandan.vn/sitemaps/google-news.xml", "max": 50},  # VN
    {"source": "Saigon Giai Phong", "url": "https://www.sggp.org.vn/sitemaps/google-news.xml", "max": 50},  # VN
    {"source": "VnEconomy", "url": "https://vneconomy.vn/sitemap/google-news.xml", "max": 50},  # VN
    {"source": "VOV", "url": "https://vov.vn/sitemaps/newsindex.xml", "max": 50},  # VN
    {"source": "VTC News", "url": "https://vtcnews.vn/news-sitemap.xml", "max": 50},  # VN
    # --- RSS gone or frozen; news sitemap instead (2026-10) ---
    {"source": "Bol News", "url": "https://www.bolnews.com/news-sitemap.xml", "max": 50},
    {"source": "Estadão", "url": "https://www.estadao.com.br/arc/outboundfeeds/news-sitemap/?outputType=xml", "max": 50},
    {"source": "Global Times", "url": "https://www.globaltimes.cn/sitemap.xml", "max": 50},
    {"source": "Oriental Daily", "url": "https://orientaldaily.on.cc/sitemap.xml", "max": 50},
    {"source": "Stuttgarter Zeitung", "url": "https://www.stuttgarter-zeitung.de/sitemap-news.xml", "max": 50},
    {"source": "The Texas Tribune", "url": "https://www.texastribune.org/news-sitemap.xml", "max": 50},
    {"source": "The Times of Israel", "url": "https://www.timesofisrael.com/news-sitemap.xml", "max": 50},
    {"source": "Yahoo Singapore", "url": "https://sg.news.yahoo.com/news-sitemap.xml", "max": 50},
]
# WordPress-core sitemap sources: (source, index_url, max). Newest = highest
# wp-sitemap-posts-post-N. Titles from URL slug (last path segment).
WP_SOURCES = [
    {"source": "Inside Paradeplatz", "index": "https://insideparadeplatz.ch/wp-sitemap.xml", "max": 50},
    {"source": "Infosperber",        "index": "https://www.infosperber.ch/wp-sitemap.xml",   "max": 50},
    {"source": "Rathuus",            "index": "https://rathuus.ch/sitemap.xml",              "max": 50},
]
BILANZ_MAX = 30      # https://www.bilanz.ch/sitemap-articles-time-limited-YYYY-MM.xml
REPUBLIK_SITEMAP = "https://www.republik.ch/sitemap.xml"  # index of per-year sitemaps
REPUBLIK_MAX = 50
SUEDOSTSCHWEIZ_MAX = 50
BAUERNZEITUNG_SITEMAP = "https://www.bauernzeitung.ch/sitemap/news.xml.gz"  # index of news-YYYY-MM.xml.gz
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

# ---- Topic sections ---------------------------------------------------------
# Besides the main news feed, sources can feed topic sections (/space/ …). Each
# section is its own daily feed — own seen-set, feed.json, archive record and
# SSR page — and is not split by country; visitors filter it by language.
# "news" is the main feed (home, archive pages, country shards, landing pages).
SECTIONS = {
    "space":    {"name": "Space",    "about": "space and astronomy"},
    "business": {"name": "Business", "about": "business, markets and economy"},
    "tech":     {"name": "Tech",     "about": "technology"},
    "gaming":   {"name": "Gaming",   "about": "video game"},
}
# Section-only sources (never in the news feed). Default kind is an RSS/Atom
# feed; "news_sitemap" = Google News sitemap. lang/country = the outlet's origin.
SECTION_SOURCES = [
    # --- Space (2026-10) ---
    {"source": "Ars Technica Space", "url": "https://arstechnica.com/space/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Astronomy Magazine", "url": "https://www.astronomy.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "EarthSky", "url": "https://earthsky.org/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "European Spaceflight", "url": "https://europeanspaceflight.com/feed/", "sections": ["space"], "lang": "en", "country": "IE"},
    {"source": "Gizmodo Space", "url": "https://gizmodo.com/science/space/feed", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Live Science Space", "url": "https://www.livescience.com/feeds/tag/space", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "NASA", "url": "https://www.nasa.gov/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "NASA Watch", "url": "https://nasawatch.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "NASASpaceflight", "url": "https://www.nasaspaceflight.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Payload", "url": "https://payloadspace.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Phys.org Space", "url": "https://phys.org/rss-feed/space-news/", "sections": ["space"], "lang": "en", "country": "GB"},
    {"source": "Satnews", "url": "https://news.satnews.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "ScienceDaily Space", "url": "https://www.sciencedaily.com/rss/space_time.xml", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Space.com", "url": "https://www.space.com/sitemap-news.xml", "kind": "news_sitemap", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Spaceflight Now", "url": "https://spaceflightnow.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "SpaceNews", "url": "https://spacenews.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "SpacePolicyOnline", "url": "https://spacepolicyonline.com/feed/", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "SpaceWatch.Global", "url": "https://spacewatch.global/feed/", "sections": ["space"], "lang": "en", "country": "AT"},
    {"source": "The Planetary Society", "url": "https://www.planetary.org/rss/articles", "sections": ["space"], "lang": "en", "country": "US"},
    {"source": "Universe Space Tech", "url": "https://universemagazine.com/en/feed/", "sections": ["space"], "lang": "en", "country": "UA"},
    {"source": "Universe Today", "url": "https://www.universetoday.com/feed/", "sections": ["space"], "lang": "en", "country": "CA"},
    {"source": "Kosmonautix", "url": "https://www.kosmonautix.cz/feed/", "sections": ["space"], "lang": "cs", "country": "CZ"},
    {"source": "Heise Raumfahrt", "url": "https://www.heise.de/thema/Raumfahrt.xml", "sections": ["space"], "lang": "de", "country": "DE"},
    {"source": "Watson Raumfahrt", "url": "https://www.watson.ch/api/2.0/rss/index.xml?tag=Raumfahrt", "sections": ["space"], "lang": "de", "country": "CH"},
    {"source": "Astrobitácora", "url": "https://astrobitacora.com/feed/", "sections": ["space"], "lang": "es", "country": "ES"},
    {"source": "Infoespacial", "url": "https://www.infoespacial.com/feed/all", "sections": ["space"], "lang": "es", "country": "ES"},
    {"source": "Xataka Espacio", "url": "https://www.xataka.com/categoria/espacio/rss2.xml", "sections": ["space"], "lang": "es", "country": "ES"},
    {"source": "Ciel & Espace", "url": "https://www.cieletespace.fr/rss", "sections": ["space"], "lang": "fr", "country": "FR"},
    {"source": "Futura Espace", "url": "https://www.futura-sciences.com/rss/espace/actualites.xml", "sections": ["space"], "lang": "fr", "country": "FR"},
    {"source": "Numerama Espace", "url": "https://www.numerama.com/sciences/espace/feed/", "sections": ["space"], "lang": "fr", "country": "FR"},
    {"source": "Sciences et Avenir Espace", "url": "https://www.sciencesetavenir.fr/espace/rss.xml", "sections": ["space"], "lang": "fr", "country": "FR"},
    {"source": "Csillagászat.hu", "url": "https://www.csillagaszat.hu/feed/", "sections": ["space"], "lang": "hu", "country": "HU"},
    {"source": "Astronautinews", "url": "https://www.astronautinews.it/feed/", "sections": ["space"], "lang": "it", "country": "IT"},
    {"source": "AstroSpace.it", "url": "https://www.astrospace.it/feed/", "sections": ["space"], "lang": "it", "country": "IT"},
    {"source": "Coelum", "url": "https://www.coelum.com/feed", "sections": ["space"], "lang": "it", "country": "IT"},
    {"source": "Passione Astronomia", "url": "https://www.passioneastronomia.it/feed/", "sections": ["space"], "lang": "it", "country": "IT"},
    {"source": "AstroArts", "url": "https://www.astroarts.co.jp/article/feed.rss", "sections": ["space"], "lang": "ja", "country": "JP"},
    {"source": "sorae", "url": "https://sorae.info/feed", "sections": ["space"], "lang": "ja", "country": "JP"},
    {"source": "Astroblogs", "url": "https://www.astroblogs.nl/feed/", "sections": ["space"], "lang": "nl", "country": "NL"},
    {"source": "Kosmonauta.net", "url": "https://kosmonauta.net/feed/", "sections": ["space"], "lang": "pl", "country": "PL"},
    {"source": "Space24", "url": "https://space24.pl/rss", "sections": ["space"], "lang": "pl", "country": "PL"},
    {"source": "Urania", "url": "https://www.urania.edu.pl/rss.xml", "sections": ["space"], "lang": "pl", "country": "PL"},
    {"source": "Space Today", "url": "https://spacetoday.com.br/feed/", "sections": ["space"], "lang": "pt", "country": "BR"},
    {"source": "Naked Science Space", "url": "https://naked-science.ru/article/category/astronomy/feed", "sections": ["space"], "lang": "ru", "country": "RU"},
    {"source": "Universe Space Tech RU", "url": "https://universemagazine.com/ru/feed/", "sections": ["space"], "lang": "ru", "country": "UA"},
    {"source": "Universe Space Tech UA", "url": "https://universemagazine.com/feed/", "sections": ["space"], "lang": "uk", "country": "UA"},
    {"source": "TechNews Space", "url": "https://technews.tw/category/%e5%a4%a9%e6%96%87/feed/", "sections": ["space"], "lang": "zh", "country": "TW"},
    # --- Tech (2026-10) ---
    {"source": "404 Media", "url": "https://www.404media.co/rss/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "9to5Google", "url": "https://9to5google.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "9to5Mac", "url": "https://9to5mac.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Android Authority", "url": "https://www.androidauthority.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Android Central", "url": "https://www.androidcentral.com/feeds.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Android Police", "url": "https://www.androidpolice.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "AppleInsider", "url": "https://appleinsider.com/rss/news/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "BBC Technology", "url": "https://feeds.bbci.co.uk/news/technology/rss.xml", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "BetaKit", "url": "https://betakit.com/feed/", "sections": ["tech"], "lang": "en", "country": "CA"},
    {"source": "BleepingComputer", "url": "https://www.bleepingcomputer.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "CNBC Tech", "url": "https://www.cnbc.com/id/19854910/device/rss/rss.html", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "CNET", "url": "https://www.cnet.com/rss/news/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "ComputerWeekly", "url": "https://www.computerweekly.com/rss/All-Computer-Weekly-content.xml", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "Dark Reading", "url": "https://www.darkreading.com/rss.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Data Center Dynamics", "url": "https://www.datacenterdynamics.com/en/rss/", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "Disrupt Africa", "url": "https://disruptafrica.com/feed/", "sections": ["tech"], "lang": "en", "country": "ZA"},
    {"source": "Engadget", "url": "https://www.engadget.com/rss.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Fast Company Tech", "url": "https://www.fastcompany.com/technology/rss", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "GeekWire", "url": "https://www.geekwire.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Ghacks", "url": "https://www.ghacks.net/feed/", "sections": ["tech"], "lang": "en", "country": "DE"},
    {"source": "Gizmodo", "url": "https://gizmodo.com/tech/feed", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "GSMArena", "url": "https://www.gsmarena.com/rss-news-reviews.php3", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "Guardian Technology", "url": "https://www.theguardian.com/uk/technology/rss", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "Hackaday", "url": "https://hackaday.com/blog/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "How-To Geek", "url": "https://www.howtogeek.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Inc42", "url": "https://inc42.com/feed/", "sections": ["tech"], "lang": "en", "country": "IN"},
    {"source": "iTnews", "url": "https://www.itnews.com.au/RSS/rss.ashx", "sections": ["tech"], "lang": "en", "country": "AU"},
    {"source": "Light Reading", "url": "https://www.lightreading.com/rss.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Liliputing", "url": "https://liliputing.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "MacRumors", "url": "https://feeds.macrumors.com/MacRumors-All", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "MIT Technology Review", "url": "https://www.technologyreview.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Neowin", "url": "https://www.neowin.net/news/rss/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "NYT Technology", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Technology.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Pandaily", "url": "https://pandaily.com/feed/", "sections": ["tech"], "lang": "en", "country": "CN"},
    {"source": "PCWorld", "url": "https://www.pcworld.com/feed", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Phoronix", "url": "https://www.phoronix.com/rss.php", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Rest of World", "url": "https://restofworld.org/feed/latest", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "SecurityWeek", "url": "https://www.securityweek.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Semiconductor Engineering", "url": "https://semiengineering.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Sifted", "url": "https://sifted.eu/feed", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "SiliconANGLE", "url": "https://siliconangle.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "TechCabal", "url": "https://techcabal.com/feed/", "sections": ["tech"], "lang": "en", "country": "NG"},
    {"source": "TechRadar", "url": "https://www.techradar.com/feeds.xml", "sections": ["tech"], "lang": "en", "country": "GB"},
    {"source": "TechSpot", "url": "https://www.techspot.com/backend.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "The Hacker News", "url": "https://feeds.feedburner.com/TheHackersNews", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "The Next Web", "url": "https://thenextweb.com/feed", "sections": ["tech"], "lang": "en", "country": "NL"},
    {"source": "The Record", "url": "https://therecord.media/feed", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Tom's Hardware", "url": "https://www.tomshardware.com/feeds.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "Windows Central", "url": "https://www.windowscentral.com/feeds.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "XDA", "url": "https://www.xda-developers.com/feed/", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "ZDNet", "url": "https://www.zdnet.com/news/rss.xml", "sections": ["tech"], "lang": "en", "country": "US"},
    {"source": "AIT News", "url": "https://aitnews.com/feed/", "sections": ["tech"], "lang": "ar", "country": "AE"},
    {"source": "Unlimit Tech", "url": "https://www.unlimit-tech.com/feed/", "sections": ["tech"], "lang": "ar", "country": "SA"},
    {"source": "Cnews.cz", "url": "https://www.cnews.cz/feed/", "sections": ["tech"], "lang": "cs", "country": "CZ"},
    {"source": "Lupa.cz", "url": "https://www.lupa.cz/rss/clanky/", "sections": ["tech"], "lang": "cs", "country": "CZ"},
    {"source": "Root.cz", "url": "https://www.root.cz/rss/clanky/", "sections": ["tech"], "lang": "cs", "country": "CZ"},
    {"source": "SMARTmania", "url": "https://smartmania.cz/feed/", "sections": ["tech"], "lang": "cs", "country": "CZ"},
    {"source": "Svět Androida", "url": "https://www.svetandroida.cz/feed/", "sections": ["tech"], "lang": "cs", "country": "CZ"},
    {"source": "Computerworld DK", "url": "https://www.computerworld.dk/rss/all", "sections": ["tech"], "lang": "da", "country": "DK"},
    {"source": "Mobilsiden", "url": "https://www.mobilsiden.dk/rss", "sections": ["tech"], "lang": "da", "country": "DK"},
    {"source": "Version2", "url": "https://www.version2.dk/rss", "sections": ["tech"], "lang": "da", "country": "DK"},
    {"source": "Apfelpage", "url": "https://www.apfelpage.de/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Basic Thinking", "url": "https://www.basicthinking.de/blog/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Caschys Blog", "url": "https://stadt-bremerhaven.de/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "ComputerBase", "url": "https://www.computerbase.de/rss/news.xml", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Hardwareluxx", "url": "https://www.hardwareluxx.de/hwl.feed", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "iphone-ticker", "url": "https://www.iphone-ticker.de/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Macerkopf", "url": "https://www.macerkopf.de/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Mobiflip", "url": "https://www.mobiflip.de/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "NZZ Technologie", "url": "https://www.nzz.ch/technologie.rss", "sections": ["tech"], "lang": "de", "country": "CH"},
    {"source": "Spiegel Netzwelt", "url": "https://www.spiegel.de/netzwelt/index.rss", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "t3n", "url": "https://t3n.de/rss.xml", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Tarnkappe", "url": "https://tarnkappe.info/feed/", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Watson Digital", "url": "https://www.watson.ch/api/2.0/rss/index.xml?tag=Digital", "sections": ["tech"], "lang": "de", "country": "CH"},
    {"source": "WinFuture", "url": "https://static.winfuture.de/feeds/WinFuture-News-rss2.0.xml", "sections": ["tech"], "lang": "de", "country": "DE"},
    {"source": "Techgear", "url": "https://www.techgear.gr/feed", "sections": ["tech"], "lang": "el", "country": "GR"},
    {"source": "ADSLZone", "url": "https://www.adslzone.net/feed/", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Applesfera", "url": "https://feeds.weblogssl.com/applesfera", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Clarín Tecnología", "url": "https://www.clarin.com/rss/tecnologia/", "sections": ["tech"], "lang": "es", "country": "AR"},
    {"source": "Computer Hoy", "url": "https://computerhoy.20minutos.es/rss", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "El Chapuzas", "url": "https://elchapuzasinformatico.com/feed/", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Europa Press Portaltic", "url": "https://www.europapress.es/rss/rss.aspx?ch=00564", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "FayerWayer", "url": "https://www.fayerwayer.com/arc/outboundfeeds/rss/?outputType=xml", "sections": ["tech"], "lang": "es", "country": "CL"},
    {"source": "Hardzone", "url": "https://hardzone.es/feed/", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Hipertextual", "url": "https://hipertextual.com/feed", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Infobae Tecno", "url": "https://www.infobae.com/arc/outboundfeeds/rss/category/tecno/?outputType=xml", "sections": ["tech"], "lang": "es", "country": "AR"},
    {"source": "Wwwhatsnew", "url": "https://wwwhatsnew.com/feed/", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Xataka", "url": "https://feeds.weblogssl.com/xataka2", "sections": ["tech"], "lang": "es", "country": "ES"},
    {"source": "Mobiili.fi", "url": "https://mobiili.fi/feed/", "sections": ["tech"], "lang": "fi", "country": "FI"},
    {"source": "Tivi", "url": "https://www.tivi.fi/rss.xml", "sections": ["tech"], "lang": "fi", "country": "FI"},
    {"source": "01net", "url": "https://www.01net.com/actualites/feed/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Clubic", "url": "https://www.clubic.com/feed/news.rss", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Frandroid", "url": "https://www.frandroid.com/feed", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "ICTjournal", "url": "https://www.ictjournal.ch/rss.xml", "sections": ["tech"], "lang": "fr", "country": "CH"},
    {"source": "Journal du Geek", "url": "https://www.journaldugeek.com/feed/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Korben", "url": "https://korben.info/feed", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Le Monde Informatique", "url": "https://www.lemondeinformatique.fr/flux-rss/thematique/toutes-les-actualites/rss.xml", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Le Monde Pixels", "url": "https://www.lemonde.fr/pixels/rss_full.xml", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Les Numériques", "url": "https://www.lesnumeriques.com/rss.xml", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "MacGeneration", "url": "https://rss.macg.co", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Next.ink", "url": "https://next.ink/feed/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Presse-citron", "url": "https://www.presse-citron.net/feed/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Siècle Digital", "url": "https://siecledigital.fr/feed/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "ZDNet France", "url": "https://www.zdnet.fr/feeds/rss/actualites/", "sections": ["tech"], "lang": "fr", "country": "FR"},
    {"source": "Ynet Digital", "url": "https://www.ynet.co.il/Integration/StoryRss544.xml", "sections": ["tech"], "lang": "he", "country": "IL"},
    {"source": "Bitport", "url": "https://bitport.hu/rss", "sections": ["tech"], "lang": "hu", "country": "HU"},
    {"source": "HWSW", "url": "https://www.hwsw.hu/xml/latest_news_rss.xml", "sections": ["tech"], "lang": "hu", "country": "HU"},
    {"source": "CNN Indonesia Tekno", "url": "https://www.cnnindonesia.com/teknologi/rss", "sections": ["tech"], "lang": "id", "country": "ID"},
    {"source": "Jagat Review", "url": "https://www.jagatreview.com/feed/", "sections": ["tech"], "lang": "id", "country": "ID"},
    {"source": "Liputan6 Tekno", "url": "https://feed.liputan6.com/rss/tekno", "sections": ["tech"], "lang": "id", "country": "ID"},
    {"source": "Agenda Digitale", "url": "https://www.agendadigitale.eu/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Corriere Tecnologia", "url": "https://www.corriere.it/dynamic-feed/rss/section/Tecnologia.xml", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "DDay.it", "url": "https://www.dday.it/rss", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Hardware Upgrade", "url": "https://www.hwupgrade.it/rss_hwup.xml", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "HDblog", "url": "https://www.hdblog.it/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Il Post Tecnologia", "url": "https://www.ilpost.it/tecnologia/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "iPhoneItalia", "url": "https://www.iphoneitalia.com/feed", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Macitynet", "url": "https://www.macitynet.it/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Punto Informatico", "url": "https://www.punto-informatico.it/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Repubblica Tecnologia", "url": "https://www.repubblica.it/rss/tecnologia/rss2.0.xml", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Smartworld", "url": "https://www.smartworld.it/feed", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "Tom's Hardware IT", "url": "https://www.tomshw.it/feed/", "sections": ["tech"], "lang": "it", "country": "IT"},
    {"source": "ASCII.jp", "url": "https://ascii.jp/rss.xml", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "CNET Japan", "url": "http://feed.japan.cnet.com/rss/index.rdf", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "Gigazine", "url": "https://gigazine.net/news/rss_2.0/", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "Gizmodo Japan", "url": "https://www.gizmodo.jp/index.xml", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "Impress Watch", "url": "https://www.watch.impress.co.jp/data/rss/1.0/ipw/feed.rdf", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "Publickey", "url": "https://www.publickey1.jp/atom.xml", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "Wired Japan", "url": "https://wired.jp/feed/rss", "sections": ["tech"], "lang": "ja", "country": "JP"},
    {"source": "AI Times", "url": "https://www.aitimes.com/rss/allArticle.xml", "sections": ["tech"], "lang": "ko", "country": "KR"},
    {"source": "Digital Today", "url": "https://www.digitaltoday.co.kr/rss/allArticle.xml", "sections": ["tech"], "lang": "ko", "country": "KR"},
    {"source": "inews24 IT", "url": "https://www.inews24.com/rss/news_it.xml", "sections": ["tech"], "lang": "ko", "country": "KR"},
    {"source": "IT Chosun", "url": "https://it.chosun.com/rss/allArticle.xml", "sections": ["tech"], "lang": "ko", "country": "KR"},
    {"source": "ZDNet Korea", "url": "https://zdnet.co.kr/feed", "sections": ["tech"], "lang": "ko", "country": "KR"},
    {"source": "Androidplanet", "url": "https://www.androidplanet.nl/feed/", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "Bright", "url": "https://www.bright.nl/rss", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "Computable", "url": "https://www.computable.nl/rss", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "Emerce", "url": "https://www.emerce.nl/rss", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "iCulture", "url": "https://www.iculture.nl/feed/", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "NOS Tech", "url": "https://feeds.nos.nl/nosnieuwstech", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "NU.nl Tech", "url": "https://www.nu.nl/rss/Tech", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "Security.nl", "url": "https://www.security.nl/rss/headlines.xml", "sections": ["tech"], "lang": "nl", "country": "NL"},
    {"source": "Digi.no", "url": "https://www.digi.no/rss", "sections": ["tech"], "lang": "no", "country": "NO"},
    {"source": "ITavisen", "url": "https://itavisen.no/feed/", "sections": ["tech"], "lang": "no", "country": "NO"},
    {"source": "Android.com.pl", "url": "https://android.com.pl/feed/", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Antyweb", "url": "https://antyweb.pl/feed", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Benchmark.pl", "url": "https://www.benchmark.pl/rss/aktualnosci-pliki.xml", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Chip.pl", "url": "https://www.chip.pl/feed", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Dobreprogramy", "url": "https://www.dobreprogramy.pl/rss/aktualnosci", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Komputer Świat", "url": "https://www.komputerswiat.pl/.feed", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "PurePC", "url": "https://www.purepc.pl/rss_all.xml", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Spider's Web", "url": "https://spidersweb.pl/feed", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "Telepolis.pl", "url": "https://www.telepolis.pl/rss", "sections": ["tech"], "lang": "pl", "country": "PL"},
    {"source": "4gnews", "url": "https://4gnews.pt/feed/", "sections": ["tech"], "lang": "pt", "country": "PT"},
    {"source": "Folha Tec", "url": "https://feeds.folha.uol.com.br/tec/rss091.xml", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "G1 Tecnologia", "url": "https://g1.globo.com/rss/g1/tecnologia/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Hardware.com.br", "url": "https://www.hardware.com.br/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "MacMagazine", "url": "https://macmagazine.com.br/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Mundo Conectado", "url": "https://mundoconectado.com.br/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Olhar Digital", "url": "https://olhardigital.com.br/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Pplware", "url": "https://pplware.sapo.pt/feed/", "sections": ["tech"], "lang": "pt", "country": "PT"},
    {"source": "Tecmundo", "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/tecmundo/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Tecnoblog", "url": "https://tecnoblog.net/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Tudocelular", "url": "https://www.tudocelular.com/feed/", "sections": ["tech"], "lang": "pt", "country": "BR"},
    {"source": "Go4IT", "url": "https://www.go4it.ro/feed/", "sections": ["tech"], "lang": "ro", "country": "RO"},
    {"source": "Start-up.ro", "url": "https://start-up.ro/feed/", "sections": ["tech"], "lang": "ro", "country": "RO"},
    {"source": "3DNews", "url": "https://3dnews.ru/news/rss/", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "4PDA", "url": "https://4pda.to/feed/", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "CNews.ru", "url": "https://www.cnews.ru/inc/rss/news.xml", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Ferra.ru", "url": "https://www.ferra.ru/exports/rss.xml", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Gagadget", "url": "https://gagadget.com/rss/", "sections": ["tech"], "lang": "ru", "country": "UA"},
    {"source": "Habr", "url": "https://habr.com/ru/rss/news/?fl=ru", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Hightech.fm", "url": "https://hightech.fm/feed.rss", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "iXBT", "url": "https://www.ixbt.com/export/news.rss", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Overclockers.ru", "url": "https://overclockers.ru/rss/all.rss", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Tproger", "url": "https://tproger.ru/feed/", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "vc.ru", "url": "https://vc.ru/rss/all", "sections": ["tech"], "lang": "ru", "country": "RU"},
    {"source": "Breakit", "url": "https://www.breakit.se/feed/artiklar", "sections": ["tech"], "lang": "sv", "country": "SE"},
    {"source": "M3", "url": "https://www.m3.se/feed", "sections": ["tech"], "lang": "sv", "country": "SE"},
    {"source": "SweClockers", "url": "https://www.sweclockers.com/feeds/nyheter", "sections": ["tech"], "lang": "sv", "country": "SE"},
    {"source": "Blognone", "url": "https://www.blognone.com/atom.xml", "sections": ["tech"], "lang": "th", "country": "TH"},
    {"source": "Chip Online TR", "url": "https://www.chip.com.tr/rss", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "DonanımHaber", "url": "https://www.donanimhaber.com/rss/tum/", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "Log.com.tr", "url": "https://www.log.com.tr/feed/", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "ShiftDelete", "url": "https://shiftdelete.net/feed", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "Teknoblog", "url": "https://www.teknoblog.com/feed/", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "Webtekno", "url": "https://www.webtekno.com/rss.xml", "sections": ["tech"], "lang": "tr", "country": "TR"},
    {"source": "dev.ua", "url": "https://dev.ua/rss", "sections": ["tech"], "lang": "uk", "country": "UA"},
    {"source": "DOU", "url": "https://dou.ua/feed/", "sections": ["tech"], "lang": "uk", "country": "UA"},
    {"source": "Mezha.media", "url": "https://mezha.media/feed/", "sections": ["tech"], "lang": "uk", "country": "UA"},
    {"source": "Root Nation", "url": "https://root-nation.com/ua/feed/", "sections": ["tech"], "lang": "uk", "country": "UA"},
    {"source": "Dan Tri Công nghệ", "url": "https://dantri.com.vn/rss/cong-nghe.rss", "sections": ["tech"], "lang": "vi", "country": "VN"},
    {"source": "Genk", "url": "https://genk.vn/rss/home.rss", "sections": ["tech"], "lang": "vi", "country": "VN"},
    {"source": "cnBeta", "url": "https://www.cnbeta.com.tw/backend.php", "sections": ["tech"], "lang": "zh", "country": "CN"},
    {"source": "GeekPark", "url": "https://www.geekpark.net/rss", "sections": ["tech"], "lang": "zh", "country": "CN"},
    {"source": "ifanr", "url": "https://www.ifanr.com/feed", "sections": ["tech"], "lang": "zh", "country": "CN"},
    {"source": "INSIDE", "url": "https://www.inside.com.tw/feed/rss", "sections": ["tech"], "lang": "zh", "country": "TW"},
    {"source": "iThome Taiwan", "url": "https://www.ithome.com.tw/rss.xml", "sections": ["tech"], "lang": "zh", "country": "TW"},
    {"source": "sspai", "url": "https://sspai.com/feed", "sections": ["tech"], "lang": "zh", "country": "CN"},
    {"source": "TechNews", "url": "https://technews.tw/feed/", "sections": ["tech"], "lang": "zh", "country": "TW"},
    {"source": "Unwire.hk", "url": "https://unwire.hk/feed/", "sections": ["tech"], "lang": "zh", "country": "HK"},
    # --- Business (2026-10) ---
    {"source": "Bangkok Post Business", "url": "https://www.bangkokpost.com/rss/data/business.xml", "sections": ["business"], "lang": "en", "country": "TH"},
    {"source": "BBC Business", "url": "https://feeds.bbci.co.uk/news/business/rss.xml", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "Bloomberg Markets", "url": "https://feeds.bloomberg.com/markets/news.rss", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "BNN Bloomberg", "url": "https://www.bnnbloomberg.ca/arc/outboundfeeds/rss/?outputType=xml", "sections": ["business"], "lang": "en", "country": "CA"},
    {"source": "Business Today IN", "url": "https://www.businesstoday.in/rss/home", "sections": ["business"], "lang": "en", "country": "IN"},
    {"source": "BusinessDay NG", "url": "https://businessday.ng/feed/", "sections": ["business"], "lang": "en", "country": "NG"},
    {"source": "DW Business", "url": "https://rss.dw.com/rdf/rss-en-bus", "sections": ["business"], "lang": "en", "country": "DE"},
    {"source": "Entrepreneur", "url": "https://www.entrepreneur.com/latest.rss", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Euronews Business", "url": "https://www.euronews.com/rss?level=vertical&name=business", "sections": ["business"], "lang": "en", "country": "FR"},
    {"source": "Fast Company", "url": "https://www.fastcompany.com/latest/rss", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Forbes", "url": "https://www.forbes.com/business/feed/", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Globe and Mail Business", "url": "https://www.theglobeandmail.com/arc/outboundfeeds/rss/category/business/", "sections": ["business"], "lang": "en", "country": "CA"},
    {"source": "Guardian Business", "url": "https://www.theguardian.com/uk/business/rss", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "Inc.", "url": "https://www.inc.com/rss", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Independent Business", "url": "https://www.independent.co.uk/news/business/rss", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "Investing.com", "url": "https://www.investing.com/rss/news.rss", "sections": ["business"], "lang": "en", "country": "IL"},
    {"source": "Irish Times Business", "url": "https://www.irishtimes.com/arc/outboundfeeds/rss/category/business/", "sections": ["business"], "lang": "en", "country": "IE"},
    {"source": "Kiplinger", "url": "https://www.kiplinger.com/feed/all", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Moneyweb", "url": "https://www.moneyweb.co.za/feed/", "sections": ["business"], "lang": "en", "country": "ZA"},
    {"source": "NYT Business", "url": "https://rss.nytimes.com/services/xml/rss/nyt/Business.xml", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Retail Dive", "url": "https://www.retaildive.com/feeds/news/", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Seeking Alpha", "url": "https://seekingalpha.com/market_currents.xml", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "Sky News Business", "url": "https://feeds.skynews.com/feeds/rss/business.xml", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "Supply Chain Dive", "url": "https://www.supplychaindive.com/feeds/news/", "sections": ["business"], "lang": "en", "country": "US"},
    {"source": "The Economist Business", "url": "https://www.economist.com/business/rss.xml", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "The National Business", "url": "https://www.thenationalnews.com/arc/outboundfeeds/rss/category/business/?outputType=xml", "sections": ["business"], "lang": "en", "country": "AE"},
    {"source": "This is Money", "url": "https://www.thisismoney.co.uk/money/index.rss", "sections": ["business"], "lang": "en", "country": "GB"},
    {"source": "VnExpress Intl Business", "url": "https://e.vnexpress.net/rss/business.rss", "sections": ["business"], "lang": "en", "country": "VN"},
    {"source": "Sky News Arabia Business", "url": "https://www.skynewsarabia.com/rss/business.xml", "sections": ["business"], "lang": "ar", "country": "AE"},
    {"source": "Capital.bg", "url": "https://www.capital.bg/rss/", "sections": ["business"], "lang": "bg", "country": "BG"},
    {"source": "CzechCrunch", "url": "https://cc.cz/feed/", "sections": ["business"], "lang": "cs", "country": "CZ"},
    {"source": "Euro.cz", "url": "https://www.euro.cz/rss/clanky/", "sections": ["business"], "lang": "cs", "country": "CZ"},
    {"source": "Peníze.cz", "url": "https://www.penize.cz/rss", "sections": ["business"], "lang": "cs", "country": "CZ"},
    {"source": "Finans.dk", "url": "https://feeds.finans.dk/seneste", "sections": ["business"], "lang": "da", "country": "DK"},
    {"source": "Capital.de", "url": "https://www.capital.de/rss", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "cash.ch", "url": "https://www.cash.ch/rss-article.xml", "sections": ["business"], "lang": "de", "country": "CH"},
    {"source": "Der Aktionär", "url": "https://www.deraktionaer.de/aktionaer-news.rss", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "FAZ Wirtschaft", "url": "https://www.faz.net/rss/aktuell/wirtschaft/", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Finanz und Wirtschaft", "url": "https://partner-feeds.publishing.tamedia.ch/rss/fuw/", "sections": ["business"], "lang": "de", "country": "CH"},
    {"source": "Gründerszene", "url": "https://www.businessinsider.de/gruenderszene/feed/", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "n-tv Wirtschaft", "url": "https://www.n-tv.de/wirtschaft/rss", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "NZZ Wirtschaft", "url": "https://www.nzz.ch/wirtschaft.rss", "sections": ["business"], "lang": "de", "country": "CH"},
    {"source": "Spiegel Wirtschaft", "url": "https://www.spiegel.de/wirtschaft/index.rss", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "SZ Wirtschaft", "url": "https://rss.sueddeutsche.de/rss/Wirtschaft", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Tagesschau Wirtschaft", "url": "https://www.tagesschau.de/wirtschaft/index~rss2.xml", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Tagesspiegel Wirtschaft", "url": "https://www.tagesspiegel.de/contentexport/feed/wirtschaft", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Welt Wirtschaft", "url": "https://www.welt.de/feeds/section/wirtschaft.rss", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Zeit Wirtschaft", "url": "https://newsfeed.zeit.de/wirtschaft/index", "sections": ["business"], "lang": "de", "country": "DE"},
    {"source": "Euro2day", "url": "https://www.euro2day.gr/rss.ashx?chiid=899001", "sections": ["business"], "lang": "el", "country": "GR"},
    {"source": "OT.gr", "url": "https://www.ot.gr/feed/", "sections": ["business"], "lang": "el", "country": "GR"},
    {"source": "ABC Economía", "url": "https://www.abc.es/rss/feeds/abc_Economia.xml", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "Bloomberg Línea", "url": "https://www.bloomberglinea.com/arc/outboundfeeds/rss/?outputType=xml", "sections": ["business"], "lang": "es", "country": "US"},
    {"source": "Business Insider España", "url": "https://www.businessinsider.es/rss", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "Cinco Días", "url": "https://feeds.elpais.com/mrss-s/pages/ep/site/cincodias.elpais.com/portada", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "El Confidencial Economía", "url": "https://rss.elconfidencial.com/economia/", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "El Mundo Economía", "url": "https://e00-elmundo.uecdn.es/elmundo/rss/economia.xml", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "Infobae Economía", "url": "https://www.infobae.com/arc/outboundfeeds/rss/category/economia/?outputType=xml", "sections": ["business"], "lang": "es", "country": "AR"},
    {"source": "Invertia", "url": "https://www.elespanol.com/rss/invertia/", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "La Tercera Pulso", "url": "https://www.latercera.com/arc/outboundfeeds/rss/category/pulso/?outputType=xml", "sections": ["business"], "lang": "es", "country": "CL"},
    {"source": "La Vanguardia Economía", "url": "https://www.lavanguardia.com/rss/economia.xml", "sections": ["business"], "lang": "es", "country": "ES"},
    {"source": "Portafolio", "url": "https://www.portafolio.co/rss/economia.xml", "sections": ["business"], "lang": "es", "country": "CO"},
    {"source": "Äripäev", "url": "https://www.aripaev.ee/rss", "sections": ["business"], "lang": "et", "country": "EE"},
    {"source": "Donya-e-Eqtesad", "url": "https://donya-e-eqtesad.com/fa/rss/allnews", "sections": ["business"], "lang": "fa", "country": "IR"},
    {"source": "Arvopaperi", "url": "https://www.arvopaperi.fi/rss.xml", "sections": ["business"], "lang": "fi", "country": "FI"},
    {"source": "Yle Talous", "url": "https://feeds.yle.fi/uutiset/v1/recent.rss?publisherIds=YLE_UUTISET&concepts=18-19274", "sections": ["business"], "lang": "fi", "country": "FI"},
    {"source": "Bilan", "url": "https://partner-feeds.publishing.tamedia.ch/rss/bilan/", "sections": ["business"], "lang": "fr", "country": "CH"},
    {"source": "Capital.fr", "url": "https://feed.prismamediadigital.com/v1/cap/rss?limit=20", "sections": ["business"], "lang": "fr", "country": "FR"},
    {"source": "Financial Afrik", "url": "https://www.financialafrik.com/feed/", "sections": ["business"], "lang": "fr", "country": "SN"},
    {"source": "Journal du Net", "url": "https://www.journaldunet.com/rss/", "sections": ["business"], "lang": "fr", "country": "FR"},
    {"source": "L'Echo Entreprises", "url": "https://www.lecho.be/rss/entreprises.xml", "sections": ["business"], "lang": "fr", "country": "BE"},
    {"source": "L'Usine Nouvelle", "url": "https://www.usinenouvelle.com/rss/", "sections": ["business"], "lang": "fr", "country": "FR"},
    {"source": "La Presse Affaires", "url": "https://www.lapresse.ca/affaires/rss", "sections": ["business"], "lang": "fr", "country": "CA"},
    {"source": "Le Temps Économie", "url": "https://www.letemps.ch/economie.rss", "sections": ["business"], "lang": "fr", "country": "CH"},
    {"source": "RFI Économie", "url": "https://www.rfi.fr/fr/economie/rss", "sections": ["business"], "lang": "fr", "country": "FR"},
    {"source": "Ynet Economy", "url": "https://www.ynet.co.il/Integration/StoryRss6.xml", "sections": ["business"], "lang": "he", "country": "IL"},
    {"source": "Amar Ujala Business", "url": "https://www.amarujala.com/rss/business.xml", "sections": ["business"], "lang": "hi", "country": "IN"},
    {"source": "Dainik Bhaskar Business", "url": "https://www.bhaskar.com/rss-v1--category-1051.xml", "sections": ["business"], "lang": "hi", "country": "IN"},
    {"source": "Poslovni dnevnik", "url": "https://www.poslovni.hr/feed", "sections": ["business"], "lang": "hr", "country": "HR"},
    {"source": "Privátbankár", "url": "https://privatbankar.hu/rss", "sections": ["business"], "lang": "hu", "country": "HU"},
    {"source": "CNBC Indonesia Market", "url": "https://www.cnbcindonesia.com/market/rss", "sections": ["business"], "lang": "id", "country": "ID"},
    {"source": "Liputan6 Bisnis", "url": "https://feed.liputan6.com/rss/bisnis", "sections": ["business"], "lang": "id", "country": "ID"},
    {"source": "Sindonews Ekbis", "url": "https://ekbis.sindonews.com/rss", "sections": ["business"], "lang": "id", "country": "ID"},
    {"source": "ANSA Economia", "url": "https://www.ansa.it/sito/notizie/economia/economia_rss.xml", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "Forbes Italia", "url": "https://forbes.it/feed/", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "Il Messaggero Economia", "url": "https://www.ilmessaggero.it/rss/economia.xml", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "Il Sole 24 Ore Economia", "url": "https://www.ilsole24ore.com/rss/economia.xml", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "La Repubblica Economia", "url": "https://www.repubblica.it/rss/economia/rss2.0.xml", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "Wall Street Italia", "url": "https://www.wallstreetitalia.com/feed/", "sections": ["business"], "lang": "it", "country": "IT"},
    {"source": "Asahi Business", "url": "https://www.asahi.com/rss/asahi/business.rdf", "sections": ["business"], "lang": "ja", "country": "JP"},
    {"source": "Business Insider Japan", "url": "https://www.businessinsider.jp/feed/index.xml", "sections": ["business"], "lang": "ja", "country": "JP"},
    {"source": "ITmedia Business", "url": "https://rss.itmedia.co.jp/rss/2.0/business.xml", "sections": ["business"], "lang": "ja", "country": "JP"},
    {"source": "Chosun Economy", "url": "https://www.chosun.com/arc/outboundfeeds/rss/category/economy/?outputType=xml", "sections": ["business"], "lang": "ko", "country": "KR"},
    {"source": "Edaily", "url": "http://rss.edaily.co.kr/edaily_news.xml", "sections": ["business"], "lang": "ko", "country": "KR"},
    {"source": "MK Economy", "url": "https://www.mk.co.kr/rss/30100041/", "sections": ["business"], "lang": "ko", "country": "KR"},
    {"source": "Verslo žinios", "url": "https://www.vz.lt/rss", "sections": ["business"], "lang": "lt", "country": "LT"},
    {"source": "Dienas Bizness", "url": "https://www.db.lv/rss", "sections": ["business"], "lang": "lv", "country": "LV"},
    {"source": "De Volkskrant Economie", "url": "https://www.volkskrant.nl/economie/rss.xml", "sections": ["business"], "lang": "nl", "country": "NL"},
    {"source": "NOS Economie", "url": "https://feeds.nos.nl/nosnieuwseconomie", "sections": ["business"], "lang": "nl", "country": "NL"},
    {"source": "Business Insider Polska", "url": "https://businessinsider.com.pl/.feed", "sections": ["business"], "lang": "pl", "country": "PL"},
    {"source": "Forbes Polska", "url": "https://forbes.pl/.feed", "sections": ["business"], "lang": "pl", "country": "PL"},
    {"source": "WNP.pl", "url": "https://www.wnp.pl/rss/serwis_rss.xml", "sections": ["business"], "lang": "pl", "country": "PL"},
    {"source": "Brazil Journal", "url": "https://braziljournal.com/feed/", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "Money Times", "url": "https://www.moneytimes.com.br/feed/", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "NeoFeed", "url": "https://neofeed.com.br/feed/", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "Seu Dinheiro", "url": "https://www.seudinheiro.com/feed/", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "Valor Econômico", "url": "https://pox.globo.com/rss/valor/", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "Época Negócios", "url": "https://epocanegocios.globo.com/rss/epocanegocios", "sections": ["business"], "lang": "pt", "country": "BR"},
    {"source": "Economedia", "url": "https://economedia.ro/feed", "sections": ["business"], "lang": "ro", "country": "RO"},
    {"source": "Forbes Russia", "url": "https://www.forbes.ru/newrss.xml", "sections": ["business"], "lang": "ru", "country": "RU"},
    {"source": "Frank Media", "url": "https://frankmedia.ru/feed", "sections": ["business"], "lang": "ru", "country": "RU"},
    {"source": "Kommersant Business", "url": "https://www.kommersant.ru/RSS/section-business.xml", "sections": ["business"], "lang": "ru", "country": "RU"},
    {"source": "PRIME", "url": "https://1prime.ru/export/rss2/index.xml", "sections": ["business"], "lang": "ru", "country": "RU"},
    {"source": "Vedomosti Business", "url": "https://www.vedomosti.ru/rss/rubric/business", "sections": ["business"], "lang": "ru", "country": "RU"},
    {"source": "Index SME", "url": "https://index.sme.sk/rss", "sections": ["business"], "lang": "sk", "country": "SK"},
    {"source": "Nova ekonomija", "url": "https://novaekonomija.rs/feed", "sections": ["business"], "lang": "sr", "country": "RS"},
    {"source": "Dagens PS", "url": "https://www.dagensps.se/feed/", "sections": ["business"], "lang": "sv", "country": "SE"},
    {"source": "Realtid", "url": "https://www.realtid.se/feed", "sections": ["business"], "lang": "sv", "country": "SE"},
    {"source": "Kaohoon", "url": "https://www.kaohoon.com/feed", "sections": ["business"], "lang": "th", "country": "TH"},
    {"source": "Prachachat Turakij", "url": "https://www.prachachat.net/feed", "sections": ["business"], "lang": "th", "country": "TH"},
    {"source": "Thairath Money", "url": "https://www.thairath.co.th/rss/money", "sections": ["business"], "lang": "th", "country": "TH"},
    {"source": "Anadolu Ekonomi", "url": "https://www.aa.com.tr/tr/rss/default?cat=ekonomi", "sections": ["business"], "lang": "tr", "country": "TR"},
    {"source": "Bloomberg HT", "url": "https://www.bloomberght.com/rss", "sections": ["business"], "lang": "tr", "country": "TR"},
    {"source": "Ekonomist", "url": "https://www.ekonomist.com.tr/rss", "sections": ["business"], "lang": "tr", "country": "TR"},
    {"source": "TRT Haber Ekonomi", "url": "https://www.trthaber.com/ekonomi_articles.rss", "sections": ["business"], "lang": "tr", "country": "TR"},
    {"source": "Mind.ua", "url": "https://s.mind.ua/rss/ua/all.xml", "sections": ["business"], "lang": "uk", "country": "UA"},
    {"source": "Thanh Nien Kinh te", "url": "https://thanhnien.vn/rss/kinh-te.rss", "sections": ["business"], "lang": "vi", "country": "VN"},
    {"source": "VietnamNet Kinh doanh", "url": "https://vietnamnet.vn/rss/kinh-doanh.rss", "sections": ["business"], "lang": "vi", "country": "VN"},
    {"source": "China News Finance", "url": "https://www.chinanews.com.cn/rss/finance.xml", "sections": ["business"], "lang": "zh", "country": "CN"},
    {"source": "CNA Taiwan Finance", "url": "https://feeds.feedburner.com/rsscna/finance", "sections": ["business"], "lang": "zh", "country": "TW"},
    {"source": "Liberty Times Business", "url": "https://news.ltn.com.tw/rss/business.xml", "sections": ["business"], "lang": "zh", "country": "TW"},
    {"source": "RTHK Finance", "url": "https://rthk.hk/rthk/news/rss/c_expressnews_cfinance.xml", "sections": ["business"], "lang": "zh", "country": "HK"},
    # --- Gaming (2026-10) ---
    {"source": "Automaton West", "url": "https://automaton-media.com/en/feed/", "sections": ["gaming"], "lang": "en", "country": "JP"},
    {"source": "Destructoid", "url": "https://www.destructoid.com/feed/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Dexerto Gaming", "url": "https://www.dexerto.com/gaming/feed/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Esports Insider", "url": "https://esportsinsider.com/feed", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Esports.gg", "url": "https://esports.gg/feed/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Eurogamer", "url": "https://www.eurogamer.net/feed", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Game Developer", "url": "https://www.gamedeveloper.com/rss.xml", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Game Informer", "url": "https://gameinformer.com/rss.xml", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "GamesIndustry.biz", "url": "https://www.gamesindustry.biz/feed", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "GameSpot", "url": "https://www.gamespot.com/feeds/mashup/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "GamesRadar+", "url": "https://www.gamesradar.com/rss/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Gematsu", "url": "https://www.gematsu.com/feed", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "HLTV", "url": "https://www.hltv.org/rss/news", "sections": ["gaming"], "lang": "en", "country": "DK"},
    {"source": "IGN", "url": "https://feeds.ign.com/ign/all", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Kotaku", "url": "https://kotaku.com/rss", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "My Nintendo News", "url": "https://mynintendonews.com/feed/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Nintendo Everything", "url": "https://nintendoeverything.com/feed/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Nintendo Life", "url": "https://www.nintendolife.com/feeds/latest", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Noisy Pixel", "url": "https://noisypixel.net/feed/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "PC Gamer", "url": "https://www.pcgamer.com/rss/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "PCGamesN", "url": "https://www.pcgamesn.com/mainrss.xml", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "PocketGamer.biz", "url": "https://www.pocketgamer.biz/rss/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Polygon", "url": "https://www.polygon.com/rss/index.xml", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "Pure Xbox", "url": "https://www.purexbox.com/feeds/latest", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Push Square", "url": "https://www.pushsquare.com/feeds/latest", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Rock Paper Shotgun", "url": "https://www.rockpapershotgun.com/feed", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "RPG Site", "url": "https://www.rpgsite.net/feed", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "Siliconera", "url": "https://www.siliconera.com/feed/", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "The Verge Games", "url": "https://www.theverge.com/rss/games/index.xml", "sections": ["gaming"], "lang": "en", "country": "US"},
    {"source": "VGC", "url": "https://www.videogameschronicle.com/feed/", "sections": ["gaming"], "lang": "en", "country": "GB"},
    {"source": "IGN Middle East", "url": "https://me.ign.com/ar/feed.xml", "sections": ["gaming"], "lang": "ar", "country": "AE"},
    {"source": "Bonusweb", "url": "https://servis.idnes.cz/rss.aspx?c=bonusweb", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "Doupě", "url": "https://doupe.zive.cz/rss", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "Games.cz", "url": "https://games.tiscali.cz/rss2.xml", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "Hrej.cz", "url": "https://www.hrej.cz/rss", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "IGN Czech", "url": "https://cz.ign.com/feed.xml", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "Zing.cz", "url": "https://zing.cz/rss/novinky", "sections": ["gaming"], "lang": "cs", "country": "CZ"},
    {"source": "4Players", "url": "https://www.4p.de/feed", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "Eurogamer.de", "url": "https://www.eurogamer.de/feed", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "GamePro", "url": "https://www.gamepro.de/rss/gpnews.rss", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "GamersGlobal", "url": "https://www.gamersglobal.de/news/feed", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "GameStar", "url": "https://www.gamestar.de/news/rss/news.rss", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "Gameswelt", "url": "https://www.gameswelt.ch/feeds/artikel/rss.xml", "sections": ["gaming"], "lang": "de", "country": "CH"},
    {"source": "GIGA Games", "url": "https://www.giga.de/games/feed/", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "MeinMMO", "url": "https://mein-mmo.de/feed/", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "PC Games", "url": "https://www.pcgames.de/feed.cfm?menu_alias=home", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "play3.de", "url": "https://www.play3.de/feed/", "sections": ["gaming"], "lang": "de", "country": "DE"},
    {"source": "3DJuegos", "url": "https://www.3djuegos.com/feedburner.xml", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "Areajugones", "url": "https://areajugones.sport.es/feed/", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "Generacion Xbox", "url": "https://www.generacionxbox.com/feed/", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "HobbyConsolas", "url": "https://www.hobbyconsolas.com/rss", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "IGN España", "url": "https://es.ign.com/feed.xml", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "IGN Latinoamérica", "url": "https://latam.ign.com/feed.xml", "sections": ["gaming"], "lang": "es", "country": "MX"},
    {"source": "Nintenderos", "url": "https://www.nintenderos.com/feed/", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "Vandal", "url": "https://vandal.elespanol.com/xml.cgi", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "Vida Extra", "url": "https://www.vidaextra.com/index.xml", "sections": ["gaming"], "lang": "es", "country": "ES"},
    {"source": "Pelaaja", "url": "https://www.pelaaja.fi/feed/", "sections": ["gaming"], "lang": "fi", "country": "FI"},
    {"source": "ActuGaming", "url": "https://www.actugaming.net/feed/", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Gameblog", "url": "https://www.gameblog.fr/rss", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Gamekult", "url": "https://www.gamekult.com/feed.xml", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Gamergen", "url": "https://gamergen.com/rss", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "IGN France", "url": "https://fr.ign.com/feed.xml", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Nintendo-Town", "url": "https://www.nintendo-town.fr/feed/", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Numerama Jeux Vidéo", "url": "https://www.numerama.com/pop-culture/jeux-video/feed/", "sections": ["gaming"], "lang": "fr", "country": "FR"},
    {"source": "Gamekapocs", "url": "https://www.gamekapocs.hu/rss", "sections": ["gaming"], "lang": "hu", "country": "HU"},
    {"source": "IGN Hungary", "url": "https://hu.ign.com/feed.xml", "sections": ["gaming"], "lang": "hu", "country": "HU"},
    {"source": "PlayDome", "url": "https://www.playdome.hu/rss/playdome.xml", "sections": ["gaming"], "lang": "hu", "country": "HU"},
    {"source": "Gamebrott", "url": "https://gamebrott.com/feed", "sections": ["gaming"], "lang": "id", "country": "ID"},
    {"source": "Jagat Play", "url": "https://jagatplay.com/feed/", "sections": ["gaming"], "lang": "id", "country": "ID"},
    {"source": "Everyeye", "url": "https://www.everyeye.it/feed", "sections": ["gaming"], "lang": "it", "country": "IT"},
    {"source": "IGN Italia", "url": "https://it.ign.com/feed.xml", "sections": ["gaming"], "lang": "it", "country": "IT"},
    {"source": "Multiplayer.it", "url": "https://multiplayer.it/feed/rss/homepage/", "sections": ["gaming"], "lang": "it", "country": "IT"},
    {"source": "Spaziogames", "url": "https://www.spaziogames.it/feed", "sections": ["gaming"], "lang": "it", "country": "IT"},
    {"source": "The Games Machine", "url": "https://www.thegamesmachine.it/feed/", "sections": ["gaming"], "lang": "it", "country": "IT"},
    {"source": "4Gamer.net", "url": "https://www.4gamer.net/rss/index.xml", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "Automaton", "url": "https://automaton-media.com/feed/", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "Game Watch", "url": "https://game.watch.impress.co.jp/data/rss/1.0/gmw/feed.rdf", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "Gamebiz", "url": "https://gamebiz.jp/feed.rss", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "GameSpark", "url": "https://www.gamespark.jp/rss/index.rdf", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "IGN Japan", "url": "https://jp.ign.com/feed.xml", "sections": ["gaming"], "lang": "ja", "country": "JP"},
    {"source": "GameMeca", "url": "https://www.gamemeca.com/rss.php", "sections": ["gaming"], "lang": "ko", "country": "KR"},
    {"source": "GameVu", "url": "https://www.gamevu.co.kr/rss/allArticle.xml", "sections": ["gaming"], "lang": "ko", "country": "KR"},
    {"source": "Inven", "url": "https://www.inven.co.kr/webzine/news/rss.php", "sections": ["gaming"], "lang": "ko", "country": "KR"},
    {"source": "Kyunghyang Game", "url": "https://www.khgames.co.kr/rss/allArticle.xml", "sections": ["gaming"], "lang": "ko", "country": "KR"},
    {"source": "Ruliweb", "url": "https://bbs.ruliweb.com/news/rss", "sections": ["gaming"], "lang": "ko", "country": "KR"},
    {"source": "Gamekings", "url": "https://www.gamekings.tv/feed/", "sections": ["gaming"], "lang": "nl", "country": "NL"},
    {"source": "IGN Benelux", "url": "https://nl.ign.com/feed.xml", "sections": ["gaming"], "lang": "nl", "country": "NL"},
    {"source": "Gamer.no", "url": "https://www.gamer.no/rss", "sections": ["gaming"], "lang": "no", "country": "NO"},
    {"source": "Pressfire", "url": "https://www.pressfire.no/rss.xml", "sections": ["gaming"], "lang": "no", "country": "NO"},
    {"source": "CD-Action", "url": "https://www.cdaction.pl/feed", "sections": ["gaming"], "lang": "pl", "country": "PL"},
    {"source": "Eurogamer.pl", "url": "https://www.eurogamer.pl/feed", "sections": ["gaming"], "lang": "pl", "country": "PL"},
    {"source": "Gry-Online", "url": "https://www.gry-online.pl/rss/news.xml", "sections": ["gaming"], "lang": "pl", "country": "PL"},
    {"source": "IGN Polska", "url": "https://pl.ign.com/feed.xml", "sections": ["gaming"], "lang": "pl", "country": "PL"},
    {"source": "PPE", "url": "https://www.ppe.pl/rss.html", "sections": ["gaming"], "lang": "pl", "country": "PL"},
    {"source": "Critical Hits", "url": "https://criticalhits.com.br/feed/", "sections": ["gaming"], "lang": "pt", "country": "BR"},
    {"source": "Eurogamer.pt", "url": "https://www.eurogamer.pt/feed", "sections": ["gaming"], "lang": "pt", "country": "PT"},
    {"source": "IGN Brasil", "url": "https://br.ign.com/feed.xml", "sections": ["gaming"], "lang": "pt", "country": "BR"},
    {"source": "IGN Portugal", "url": "https://pt.ign.com/feed.xml", "sections": ["gaming"], "lang": "pt", "country": "PT"},
    {"source": "Nintendo Blast", "url": "https://www.nintendoblast.com.br/feeds/posts/default?alt=rss", "sections": ["gaming"], "lang": "pt", "country": "BR"},
    {"source": "Voxel", "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/voxel/?body=%7B%22layout%22:%22google-news%22%7D", "sections": ["gaming"], "lang": "pt", "country": "BR"},
    {"source": "3DNews Games", "url": "https://3dnews.ru/games/rss/", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "Cybersport.ru", "url": "https://www.cybersport.ru/rss/materials", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "GoHa.ru", "url": "https://www.goha.ru/rss/news", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "Igromania", "url": "https://www.igromania.ru/rss/news.rss", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "PlayGround", "url": "https://www.playground.ru/rss/news.xml", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "StopGame", "url": "https://stopgame.ru/rss/rss_news.xml", "sections": ["gaming"], "lang": "ru", "country": "RU"},
    {"source": "FZ", "url": "https://www.fz.se/feeds/nyheter", "sections": ["gaming"], "lang": "sv", "country": "SE"},
    {"source": "GamingDose", "url": "https://www.gamingdose.com/feed/", "sections": ["gaming"], "lang": "th", "country": "TH"},
    {"source": "Merlin'in Kazanı", "url": "https://www.merlininkazani.com/rss", "sections": ["gaming"], "lang": "tr", "country": "TR"},
    {"source": "ShiftDelete Oyun", "url": "https://shiftdelete.net/oyun/feed", "sections": ["gaming"], "lang": "tr", "country": "TR"},
    {"source": "PlayUA", "url": "https://playua.net/feed/", "sections": ["gaming"], "lang": "uk", "country": "UA"},
    {"source": "GameK", "url": "https://gamek.vn/rss", "sections": ["gaming"], "lang": "vi", "country": "VN"},
    {"source": "4Gamers", "url": "https://www.4gamers.com.tw/rss/latest-news", "sections": ["gaming"], "lang": "zh", "country": "TW"},
    {"source": "Bahamut GNN", "url": "https://gnn.gamer.com.tw/rss.xml", "sections": ["gaming"], "lang": "zh", "country": "TW"},
    {"source": "Chuapp", "url": "https://www.chuapp.com/feed", "sections": ["gaming"], "lang": "zh", "country": "CN"},
]
# Existing news sources whose whole feed is on-topic also feed these sections
# (sections dedupe independently, so they appear in both).
SECTION_CROSSLIST = {
    "tech": [
        "Ars Technica", "Der Standard Web", "e27", "futurezone", "golem.de", "Heise",
        "Ingeniøren", "Inside IT", "ITmedia", "IT之家", "Netzwoche", "Numerama",
        "Silicon Republic", "TechCrunch", "The Verge", "Tweakers", "Wired",
    ],
    "business": [
        "ABC Business AU", "AFR", "Andina Economía", "Bankier.pl", "BFM Business",
        "Bilanz", "Breakit", "Business Insider", "Business Recorder", "Business Times",
        "Business World", "Børsen", "Challenges", "City AM", "Clarín Economía",
        "CNA Business SG", "CNBC", "Corriere Economia", "Cumhuriyet Ekonomi",
        "Dagens Industri", "Dan Tri Kinh doanh", "Dawn Business", "De Tijd Ondernemen",
        "Deník Ekonomika", "Der Standard Wirtschaft", "Detik Finance",
        "Diario Financiero", "Die Presse Wirtschaft", "Digi24 Economie", "DN Ekonomi",
        "Donga Economy", "DR Penge", "Dünya Gazetesi", "E15", "E24", "ECO",
        "Economic Times Markets", "Economica.net", "Ekonomim", "El Economista MX",
        "elDiario Economía", "Estadão Economia", "Exame", "Expansión",
        "Expansión Economía", "Financial Post News", "Financial Times", "Finews",
        "Folha Mercado", "Forbes Česko", "Fortune", "G1 Economia", "Gestión", "Globes",
        "GMA Money", "Habertürk Ekonomi", "Handelsblatt", "Hankyung",
        "Het Financieele Dagblad", "Hindustan Times Business", "Hong Kong Business",
        "Hospodářské noviny", "Hürriyet Ekonomi", "iDNES Ekonomika",
        "Ilta-Sanomat Taloussanomat", "Iltalehti Talous", "in.gr Oikonomia",
        "Index Gazdaság", "InfoMoney", "Inside Paradeplatz", "Interia Biznes",
        "iProfesional Economía", "Irish Independent Business", "Jornal de Negócios",
        "Jornal Económico", "Kleine Zeitung Wirtschaft", "Kontan Nasional",
        "Kurier Wirtschaft", "Kyunghyang Economy", "La Nación Economía",
        "La República CO", "La Tribune", "Le Figaro Éco", "Le Monde Éco",
        "Livemint Companies", "Manager Magazin", "MarketWatch", "Milliyet Ekonomi",
        "Money.it", "Money.pl", "Naftemporiki", "Newsis Economy", "NU.nl Economie",
        "NZ Herald Business", "O Globo Economia", "Observador Economia",
        "PhilStar Business", "Portfolio", "Profit.ro", "Público Economia",
        "Rappler Business", "RNZ Business", "RTÉ Business", "Rzeczpospolita Ekonomia",
        "Sabah Ekonomi", "SCMP Business", "Straits Times Business", "SVT Ekonomi",
        "Talouselämä", "TechCabal", "Telex Gazdaság", "The Bell",
        "The Express Tribune Business", "The Hindu Business Line",
        "Times of India Business", "Toyo Keizai", "Ukrainska Pravda Economy", "VG.hu",
        "VnExpress Kinh doanh", "WirtschaftsWoche", "Wprost Biznes", "Yonhap Economy",
        "Ziarul Financiar", "Ámbito Economía", "ČT24 Ekonomika",
    ],
}


# Origin labels stamped onto every article so they can be filtered by language
# and country later. Every source so far is a German-language Swiss outlet; as
# the scope widens beyond Switzerland, add per-source overrides to SOURCE_ORIGIN.
# Codes: lang = ISO 639-1 (e.g. "de", "fr"), country = ISO 3166-1 alpha-2 ("CH").
DEFAULT_LANG = "de"
DEFAULT_COUNTRY = "CH"
SOURCE_ORIGIN: dict = {  # source name -> {"lang": ..., "country": ...}
    # German-language outlets based in Germany (lang defaults to "de").
    "Tagesschau":   {"country": "DE"},
    "Süddeutsche":  {"country": "DE"},
    "FAZ":          {"country": "DE"},
    "Die Welt":     {"country": "DE"},
    "taz":          {"country": "DE"},
    "n-tv":         {"country": "DE"},
    "Der Spiegel":  {"country": "DE"},
    "Stern":        {"country": "DE"},
    "DW":           {"country": "DE"},
    "Bild":         {"country": "DE"},
    # French-language outlets based in France.
    "Le Monde":     {"lang": "fr", "country": "FR"},
    "Le Figaro":    {"lang": "fr", "country": "FR"},
    "Libération":   {"lang": "fr", "country": "FR"},
    "franceinfo":   {"lang": "fr", "country": "FR"},
    "France 24":    {"lang": "fr", "country": "FR"},
    "RFI":          {"lang": "fr", "country": "FR"},
    "L'Express":    {"lang": "fr", "country": "FR"},
    "L'Obs":        {"lang": "fr", "country": "FR"},
    "La Croix":     {"lang": "fr", "country": "FR"},
    "20 Minutes":   {"lang": "fr", "country": "FR"},
    "La Tribune":   {"lang": "fr", "country": "FR"},
    "BFM TV":       {"lang": "fr", "country": "FR"},
    "Mediapart":    {"lang": "fr", "country": "FR"},
    # French-language outlets based in Switzerland (country defaults to CH).
    "RTS":              {"lang": "fr"},
    "Le Temps":         {"lang": "fr"},
    "Tribune de Genève":{"lang": "fr"},
    "Heidi.news":       {"lang": "fr"},
    "Le Courrier":      {"lang": "fr"},
    "Watson FR":        {"lang": "fr"},
    # ===== Additional countries =====
    "NOS": {"lang":"nl","country":"NL"}, "NU.nl": {"lang":"nl","country":"NL"},
    "VRT NWS": {"lang":"nl","country":"BE"},
    "ORF": {"lang":"de","country":"AT"}, "Der Standard": {"lang":"de","country":"AT"},
    "RTP": {"lang":"pt","country":"PT"},
    "RTÉ": {"lang":"en","country":"IE"},
    "Onet": {"lang":"pl","country":"PL"}, "WP.pl": {"lang":"pl","country":"PL"},
    "SVT": {"lang":"sv","country":"SE"}, "Aftonbladet": {"lang":"sv","country":"SE"},
    "NRK": {"lang":"no","country":"NO"}, "VG": {"lang":"no","country":"NO"},
    "DR": {"lang":"da","country":"DK"},
    "YLE": {"lang":"fi","country":"FI"}, "Iltalehti": {"lang":"fi","country":"FI"},
    "To Vima": {"lang":"el","country":"GR"},
    "Novinky": {"lang":"cs","country":"CZ"}, "ČT24": {"lang":"cs","country":"CZ"},
    "Telex": {"lang":"hu","country":"HU"}, "HVG": {"lang":"hu","country":"HU"},
    "Digi24": {"lang":"ro","country":"RO"}, "HotNews": {"lang":"ro","country":"RO"},
    "Ukrainska Pravda": {"lang":"uk","country":"UA"},
    "Hürriyet": {"lang":"tr","country":"TR"},
    "Radio-Canada": {"lang":"fr","country":"CA"},
    "La Jornada": {"lang":"es","country":"MX"},
    "G1": {"lang":"pt","country":"BR"}, "Folha": {"lang":"pt","country":"BR"},
    "La Nación": {"lang":"es","country":"AR"},
    "El Tiempo": {"lang":"es","country":"CO"},
    "RPP": {"lang":"es","country":"PE"},
    "ABC News AU": {"lang":"en","country":"AU"}, "SMH": {"lang":"en","country":"AU"},
    "RNZ": {"lang":"en","country":"NZ"},
    "The Hindu": {"lang":"en","country":"IN"}, "NDTV": {"lang":"en","country":"IN"},
    "NHK": {"lang":"ja","country":"JP"},
    "Yonhap": {"lang":"en","country":"KR"},
    "Straits Times": {"lang":"en","country":"SG"}, "CNA": {"lang":"en","country":"SG"},
    "Rappler": {"lang":"en","country":"PH"}, "Inquirer": {"lang":"en","country":"PH"},
    "VnExpress": {"lang":"vi","country":"VN"},
    "Dawn": {"lang":"en","country":"PK"},
    "Jerusalem Post": {"lang":"en","country":"IL"},
    "Al Jazeera": {"lang":"en","country":"QA"},
    "SCMP": {"lang":"en","country":"HK"},
    # United Kingdom (English).
    "BBC News":         {"lang": "en", "country": "GB"},
    "The Guardian":     {"lang": "en", "country": "GB"},
    "The Independent":  {"lang": "en", "country": "GB"},
    "The Telegraph":    {"lang": "en", "country": "GB"},
    "Sky News":         {"lang": "en", "country": "GB"},
    "Daily Mail":       {"lang": "en", "country": "GB"},
    "Mirror":           {"lang": "en", "country": "GB"},
    "Metro":            {"lang": "en", "country": "GB"},
    "Evening Standard": {"lang": "en", "country": "GB"},
    "Financial Times":  {"lang": "en", "country": "GB"},
    # United States (English).
    "The New York Times":{"lang": "en", "country": "US"},
    "NPR":              {"lang": "en", "country": "US"},
    "ABC News":         {"lang": "en", "country": "US"},
    "NBC News":         {"lang": "en", "country": "US"},
    "Fox News":         {"lang": "en", "country": "US"},
    "The Hill":         {"lang": "en", "country": "US"},
    "Washington Post":  {"lang": "en", "country": "US"},
    "LA Times":         {"lang": "en", "country": "US"},
    # Italy (Italian).
    "la Repubblica":    {"lang": "it", "country": "IT"},
    "ANSA":             {"lang": "it", "country": "IT"},
    "Il Giornale":      {"lang": "it", "country": "IT"},
    "Il Sole 24 Ore":   {"lang": "it", "country": "IT"},
    # Spain (Spanish).
    "El Mundo":         {"lang": "es", "country": "ES"},
    "ABC":              {"lang": "es", "country": "ES"},
    "elDiario.es":      {"lang": "es", "country": "ES"},
    "20minutos":        {"lang": "es", "country": "ES"},
    "El Confidencial":  {"lang": "es", "country": "ES"},
    # ===== Core-country expansion =====
    # Germany
    "Handelsblatt": {"lang":"de","country":"DE"}, "Tagesspiegel": {"lang":"de","country":"DE"},
    "Frankfurter Rundschau": {"lang":"de","country":"DE"}, "Heise": {"lang":"de","country":"DE"},
    "WirtschaftsWoche": {"lang":"de","country":"DE"}, "Manager Magazin": {"lang":"de","country":"DE"},
    "RP Online": {"lang":"de","country":"DE"}, "Merkur": {"lang":"de","country":"DE"},
    "MDR": {"lang":"de","country":"DE"}, "Berliner Zeitung": {"lang":"de","country":"DE"},
    "t-online": {"lang":"de","country":"DE"}, "Stuttgarter Zeitung": {"lang":"de","country":"DE"},
    # France
    "Courrier International": {"lang":"fr","country":"FR"}, "La Dépêche": {"lang":"fr","country":"FR"},
    "France Inter": {"lang":"fr","country":"FR"}, "Europe 1": {"lang":"fr","country":"FR"},
    "Slate FR": {"lang":"fr","country":"FR"}, "Challenges": {"lang":"fr","country":"FR"},
    "France Bleu": {"lang":"fr","country":"FR"}, "Numerama": {"lang":"fr","country":"FR"},
    "Télérama": {"lang":"fr","country":"FR"}, "HuffPost FR": {"lang":"fr","country":"FR"},
    # United Kingdom
    "Daily Star": {"lang":"en","country":"GB"}, "iNews": {"lang":"en","country":"GB"},
    "City AM": {"lang":"en","country":"GB"}, "New Statesman": {"lang":"en","country":"GB"},
    "Wales Online": {"lang":"en","country":"GB"}, "The Scotsman": {"lang":"en","country":"GB"},
    "The Herald": {"lang":"en","country":"GB"}, "Manchester Evening News": {"lang":"en","country":"GB"},
    "Belfast Telegraph": {"lang":"en","country":"GB"}, "The Conversation": {"lang":"en","country":"GB"},
    # United States
    "CBS News": {"lang":"en","country":"US"}, "CNBC": {"lang":"en","country":"US"},
    "The Atlantic": {"lang":"en","country":"US"}, "Vox": {"lang":"en","country":"US"},
    "The Verge": {"lang":"en","country":"US"}, "TechCrunch": {"lang":"en","country":"US"},
    "Newsweek": {"lang":"en","country":"US"}, "PBS NewsHour": {"lang":"en","country":"US"},
    "NY Post": {"lang":"en","country":"US"}, "The Daily Beast": {"lang":"en","country":"US"},
    "Wired": {"lang":"en","country":"US"}, "ProPublica": {"lang":"en","country":"US"},
    # Italy
    "Rai News": {"lang":"it","country":"IT"}, "Adnkronos": {"lang":"it","country":"IT"},
    "TGcom24": {"lang":"it","country":"IT"}, "Open": {"lang":"it","country":"IT"},
    "Il Giorno": {"lang":"it","country":"IT"}, "Il Resto del Carlino": {"lang":"it","country":"IT"},
    "La Nazione": {"lang":"it","country":"IT"}, "AGI": {"lang":"it","country":"IT"},
    "Today": {"lang":"it","country":"IT"},
    "Il Mattino": {"lang":"it","country":"IT"}, "Il Messaggero": {"lang":"it","country":"IT"},
    "Il Gazzettino": {"lang":"it","country":"IT"}, "Quotidiano.net": {"lang":"it","country":"IT"},
    "askanews": {"lang":"it","country":"IT"}, "Domani": {"lang":"it","country":"IT"},
    # Spain
    "El Español": {"lang":"es","country":"ES"}, "COPE": {"lang":"es","country":"ES"},
    "Europa Press": {"lang":"es","country":"ES"}, "Marca": {"lang":"es","country":"ES"},
    "Expansión": {"lang":"es","country":"ES"}, "La Vanguardia": {"lang":"es","country":"ES"},
    "El Correo": {"lang":"es","country":"ES"}, "infoLibre": {"lang":"es","country":"ES"},
    "Mundo Deportivo": {"lang":"es","country":"ES"}, "El Salto": {"lang":"es","country":"ES"},
    "Las Provincias": {"lang":"es","country":"ES"}, "La Verdad": {"lang":"es","country":"ES"},
    "Ideal": {"lang":"es","country":"ES"}, "Diario Sur": {"lang":"es","country":"ES"},
    "El Diario Vasco": {"lang":"es","country":"ES"}, "Newtral": {"lang":"es","country":"ES"},
    "Maldita": {"lang":"es","country":"ES"}, "El Independiente": {"lang":"es","country":"ES"},
    # ===== Wider expansion: rest of the countries =====
    # Netherlands (nl/NL)
    "De Telegraaf": {"lang":"nl","country":"NL"}, "de Volkskrant": {"lang":"nl","country":"NL"},
    "NRC": {"lang":"nl","country":"NL"}, "Trouw": {"lang":"nl","country":"NL"},
    "Het Parool": {"lang":"nl","country":"NL"}, "AD": {"lang":"nl","country":"NL"},
    "Het Financieele Dagblad": {"lang":"nl","country":"NL"}, "De Limburger": {"lang":"nl","country":"NL"},
    "Nederlands Dagblad": {"lang":"nl","country":"NL"}, "De Gelderlander": {"lang":"nl","country":"NL"},
    "Brabants Dagblad": {"lang":"nl","country":"NL"}, "Tubantia": {"lang":"nl","country":"NL"},
    "BN DeStem": {"lang":"nl","country":"NL"}, "Eindhovens Dagblad": {"lang":"nl","country":"NL"},
    "PZC": {"lang":"nl","country":"NL"}, "De Stentor": {"lang":"nl","country":"NL"},
    # Belgium (nl/fr, BE)
    "Het Laatste Nieuws": {"lang":"nl","country":"BE"}, "7sur7": {"lang":"fr","country":"BE"},
    "La Libre": {"lang":"fr","country":"BE"}, "Le Vif": {"lang":"fr","country":"BE"},
    # Austria (de/AT)
    "Kurier": {"lang":"de","country":"AT"}, "Kleine Zeitung": {"lang":"de","country":"AT"},
    "futurezone": {"lang":"de","country":"AT"},
    # Portugal (pt/PT)
    "Observador": {"lang":"pt","country":"PT"}, "Expresso": {"lang":"pt","country":"PT"},
    "ECO": {"lang":"pt","country":"PT"}, "Notícias ao Minuto": {"lang":"pt","country":"PT"},
    "Jornal de Negócios": {"lang":"pt","country":"PT"}, "Sapo24": {"lang":"pt","country":"PT"},
    # Sweden (sv/SE)
    "Dagens Nyheter": {"lang":"sv","country":"SE"}, "Svenska Dagbladet": {"lang":"sv","country":"SE"},
    "Expressen": {"lang":"sv","country":"SE"}, "Dagens Industri": {"lang":"sv","country":"SE"},
    "Sydsvenskan": {"lang":"sv","country":"SE"}, "Göteborgs-Posten": {"lang":"sv","country":"SE"},
    # Norway (no/NO)
    "Aftenposten": {"lang":"no","country":"NO"}, "Bergens Tidende": {"lang":"no","country":"NO"},
    "Nettavisen": {"lang":"no","country":"NO"}, "E24": {"lang":"no","country":"NO"},
    # Denmark (da/DK)
    "Politiken": {"lang":"da","country":"DK"}, "Berlingske": {"lang":"da","country":"DK"},
    "BT": {"lang":"da","country":"DK"}, "Børsen": {"lang":"da","country":"DK"},
    # Finland (fi/FI)
    "Helsingin Sanomat": {"lang":"fi","country":"FI"}, "Ilta-Sanomat": {"lang":"fi","country":"FI"},
    "MTV Uutiset": {"lang":"fi","country":"FI"},
    # Poland (pl/PL)
    "Rzeczpospolita": {"lang":"pl","country":"PL"}, "TVN24": {"lang":"pl","country":"PL"},
    "Polsat News": {"lang":"pl","country":"PL"}, "Interia": {"lang":"pl","country":"PL"},
    "Gazeta.pl": {"lang":"pl","country":"PL"}, "Wprost": {"lang":"pl","country":"PL"},
    "Newsweek Polska": {"lang":"pl","country":"PL"},
    # Greece (el/GR)
    "Ta Nea": {"lang":"el","country":"GR"}, "Naftemporiki": {"lang":"el","country":"GR"},
    "iefimerida": {"lang":"el","country":"GR"}, "in.gr": {"lang":"el","country":"GR"},
    # Czechia (cs/CZ)
    "Seznam Zprávy": {"lang":"cs","country":"CZ"}, "Deník": {"lang":"cs","country":"CZ"},
    "České noviny": {"lang":"cs","country":"CZ"}, "iROZHLAS": {"lang":"cs","country":"CZ"},
    "Deník N": {"lang":"cs","country":"CZ"},
    # Hungary (hu/HU)
    "Index": {"lang":"hu","country":"HU"}, "444": {"lang":"hu","country":"HU"},
    "Portfolio": {"lang":"hu","country":"HU"}, "24.hu": {"lang":"hu","country":"HU"},
    "Qubit": {"lang":"hu","country":"HU"},
    # Romania (ro/RO)
    "Adevărul": {"lang":"ro","country":"RO"}, "Libertatea": {"lang":"ro","country":"RO"},
    "Gândul": {"lang":"ro","country":"RO"}, "ProTV Știrile": {"lang":"ro","country":"RO"},
    "G4Media": {"lang":"ro","country":"RO"},
    # Ukraine (uk/en, UA)
    "Unian": {"lang":"uk","country":"UA"}, "NV": {"lang":"ru","country":"UA"},
    "Ukrinform": {"lang":"en","country":"UA"},
    # Turkey (tr/TR)
    "Sabah": {"lang":"tr","country":"TR"}, "Milliyet": {"lang":"tr","country":"TR"},
    "Cumhuriyet": {"lang":"tr","country":"TR"}, "NTV": {"lang":"tr","country":"TR"},
    "TRT Haber": {"lang":"tr","country":"TR"},
    # Canada (en/fr, CA)
    "Global News": {"lang":"en","country":"CA"}, "National Post": {"lang":"en","country":"CA"},
    "Financial Post": {"lang":"en","country":"CA"}, "Toronto Sun": {"lang":"en","country":"CA"},
    "Le Devoir": {"lang":"fr","country":"CA"},
    # Brazil (pt/BR)
    "Veja": {"lang":"pt","country":"BR"}, "Metrópoles": {"lang":"pt","country":"BR"},
    "Poder360": {"lang":"pt","country":"BR"},
    # Argentina (es/AR)
    "Clarín": {"lang":"es","country":"AR"}, "Infobae": {"lang":"es","country":"AR"},
    "Ámbito": {"lang":"es","country":"AR"}, "Perfil": {"lang":"es","country":"AR"},
    "TN": {"lang":"es","country":"AR"},
    # Colombia (es/CO)
    "La República": {"lang":"es","country":"CO"},
    # Peru (es/PE)
    "Andina": {"lang":"es","country":"PE"},
    # Australia (en/AU)
    "The Age": {"lang":"en","country":"AU"}, "Guardian Australia": {"lang":"en","country":"AU"},
    "Brisbane Times": {"lang":"en","country":"AU"}, "AFR": {"lang":"en","country":"AU"},
    "Conversation AU": {"lang":"en","country":"AU"},
    # New Zealand (en/NZ)
    "The Spinoff": {"lang":"en","country":"NZ"}, "Newsroom": {"lang":"en","country":"NZ"},
    # India (en/IN)
    "Times of India": {"lang":"en","country":"IN"}, "Hindustan Times": {"lang":"en","country":"IN"},
    "Economic Times": {"lang":"en","country":"IN"}, "News18": {"lang":"en","country":"IN"},
    "India Today": {"lang":"en","country":"IN"}, "Livemint": {"lang":"en","country":"IN"},
    # Japan (ja/en, JP)
    "Mainichi": {"lang":"ja","country":"JP"}, "Japan Today": {"lang":"en","country":"JP"},
    # South Korea (en/KR)
    "Korea Times": {"lang":"en","country":"KR"},
    # Singapore (en/SG)
    "The Independent SG": {"lang":"en","country":"SG"},
    # Indonesia (id/ID)
    "CNN Indonesia": {"lang":"id","country":"ID"}, "Antara": {"lang":"id","country":"ID"},
    # Philippines (en/PH)
    "Philstar": {"lang":"en","country":"PH"}, "GMA News": {"lang":"en","country":"PH"},
    # Vietnam (vi/en, VN)
    "Thanh Nien": {"lang":"vi","country":"VN"}, "Dan Tri": {"lang":"vi","country":"VN"},
    "VnExpress Intl": {"lang":"en","country":"VN"},
    # Pakistan (en/PK)
    "ARY News": {"lang":"en","country":"PK"},
    # Israel (he/IL)
    "Ynet": {"lang":"he","country":"IL"},
    # Hong Kong (en/HK)
    "HKFP": {"lang":"en","country":"HK"}, "RTHK": {"lang":"en","country":"HK"},
    # Ireland (en/IE)
    "Irish Independent": {"lang":"en","country":"IE"}, "The Journal": {"lang":"en","country":"IE"},
    "Irish Mirror": {"lang":"en","country":"IE"},
    # China (en, CN) — state + independent/exile
    "CGTN": {"lang":"en","country":"CN"}, "China Digital Times": {"lang":"en","country":"CN"},
    # Russia (en/ru, RU) — state + independent/exile
    "TASS": {"lang":"en","country":"RU"}, "RT": {"lang":"en","country":"RU"},
    "RIA Novosti": {"lang":"ru","country":"RU"}, "Meduza": {"lang":"ru","country":"RU"},
    "The Moscow Times": {"lang":"en","country":"RU"},
    "Novaya Gazeta Europe": {"lang":"ru","country":"RU"}, "Mediazona": {"lang":"ru","country":"RU"},
    # News-sitemap sources for RSS-poor markets
    "Excélsior": {"lang":"es","country":"MX"}, "Milenio": {"lang":"es","country":"MX"},
    "Al Jazeera Arabic": {"lang":"ar","country":"QA"},
    "El Espectador": {"lang":"es","country":"CO"}, "El Colombiano": {"lang":"es","country":"CO"},
    "Semana": {"lang":"es","country":"CO"},
    "Chosun": {"lang":"en","country":"KR"},
    "The News": {"lang":"en","country":"PK"}, "Geo News": {"lang":"en","country":"PK"},
    "Business Recorder": {"lang":"en","country":"PK"},
    "Globes": {"lang":"he","country":"IL"},
    "Sankei": {"lang":"ja","country":"JP"},
    # Second news-sitemap wave
    "Stuff": {"lang":"en","country":"NZ"}, "1News": {"lang":"en","country":"NZ"},
    "AsiaOne": {"lang":"en","country":"SG"}, "Business Times": {"lang":"en","country":"SG"},
    "Kompas": {"lang":"id","country":"ID"}, "Liputan6": {"lang":"id","country":"ID"},
    "The Standard": {"lang":"en","country":"HK"}, "HK01": {"lang":"zh","country":"HK"},
    "VietnamNet": {"lang":"vi","country":"VN"}, "VietnamPlus": {"lang":"vi","country":"VN"},
    "Zing": {"lang":"vi","country":"VN"},
    "Hromadske": {"lang":"uk","country":"UA"}, "Kyiv Independent": {"lang":"en","country":"UA"},
    "Liga.net": {"lang":"uk","country":"UA"},
    "Irish Examiner": {"lang":"en","country":"IE"},
    # ===== Origins for the regional/national expansion (see FEEDS block). =====
    # AR
    "Cenital": {"lang":"es","country":"AR"},
    "Clarín Economía": {"lang":"es","country":"AR"}, "Clarín Mundo": {"lang":"es","country":"AR"},
    "Clarín Política": {"lang":"es","country":"AR"}, "Clarín Sociedad": {"lang":"es","country":"AR"},
    "Diario Uno": {"lang":"es","country":"AR"}, "El Cohete a la Luna": {"lang":"es","country":"AR"},
    "El Cronista": {"lang":"es","country":"AR"}, "iProfesional Economía": {"lang":"es","country":"AR"},
    "La Gaceta": {"lang":"es","country":"AR"}, "La Nación Economía": {"lang":"es","country":"AR"},
    "La Nación Mundo": {"lang":"es","country":"AR"}, "La Nación Política": {"lang":"es","country":"AR"},
    "Letra P": {"lang":"es","country":"AR"}, "Minuto Uno": {"lang":"es","country":"AR"},
    "Tiempo Argentino": {"lang":"es","country":"AR"}, "Ámbito Economía": {"lang":"es","country":"AR"},
    "Ámbito Finanzas": {"lang":"es","country":"AR"}, "Ámbito Política": {"lang":"es","country":"AR"},
    # AT
    "Der Standard Inland": {"lang":"de","country":"AT"},
    "Der Standard International": {"lang":"de","country":"AT"},
    "Der Standard Web": {"lang":"de","country":"AT"},
    "Der Standard Wirtschaft": {"lang":"de","country":"AT"},
    "Die Presse Wirtschaft": {"lang":"de","country":"AT"},
    "Kleine Zeitung Kärnten": {"lang":"de","country":"AT"},
    "Kleine Zeitung Politik": {"lang":"de","country":"AT"},
    "Kleine Zeitung Wirtschaft": {"lang":"de","country":"AT"},
    "Kurier Politik": {"lang":"de","country":"AT"}, "Kurier Wirtschaft": {"lang":"de","country":"AT"},
    "Meinbezirk": {"lang":"de","country":"AT"},
    "Oberösterreichische Nachrichten": {"lang":"de","country":"AT"},
    "ORF Oberösterreich": {"lang":"de","country":"AT"}, "ORF Salzburg": {"lang":"de","country":"AT"},
    "ORF Steiermark": {"lang":"de","country":"AT"}, "ORF Tirol": {"lang":"de","country":"AT"},
    "ORF Wien": {"lang":"de","country":"AT"}, "OÖ Nachrichten Politik": {"lang":"de","country":"AT"},
    # AU
    "ABC Business AU": {"lang":"en","country":"AU"}, "ABC News Just In": {"lang":"en","country":"AU"},
    "ABC Politics AU": {"lang":"en","country":"AU"}, "Brisbane Times National": {"lang":"en","country":"AU"},
    "Canberra Times": {"lang":"en","country":"AU"}, "Crikey": {"lang":"en","country":"AU"},
    "Newcastle Herald": {"lang":"en","country":"AU"}, "Pedestrian TV": {"lang":"en","country":"AU"},
    "Perth Now": {"lang":"en","country":"AU"}, "SBS News": {"lang":"en","country":"AU"},
    "SBS World News": {"lang":"en","country":"AU"}, "SMH National": {"lang":"en","country":"AU"},
    "The Age National": {"lang":"en","country":"AU"},
    "The Guardian AU Politics": {"lang":"en","country":"AU"},
    "The Guardian AU World": {"lang":"en","country":"AU"}, "The Mandarin": {"lang":"en","country":"AU"},
    "The West Australian": {"lang":"en","country":"AU"}, "WAtoday": {"lang":"en","country":"AU"},
    # BE
    "Bruzz": {"lang":"nl","country":"BE"}, "De Morgen": {"lang":"nl","country":"BE"},
    "De Morgen Politiek": {"lang":"nl","country":"BE"}, "De Tijd": {"lang":"nl","country":"BE"},
    "De Tijd Ondernemen": {"lang":"nl","country":"BE"}, "De Tijd Politiek": {"lang":"nl","country":"BE"},
    "Gazet van Antwerpen": {"lang":"nl","country":"BE"},
    "Het Belang van Limburg": {"lang":"nl","country":"BE"},
    "Het Laatste Nieuws Binnenland": {"lang":"nl","country":"BE"},
    "HLN Buitenland": {"lang":"nl","country":"BE"}, "Knack": {"lang":"nl","country":"BE"},
    "Knack Nieuws": {"lang":"nl","country":"BE"}, "L'Echo": {"lang":"fr","country":"BE"},
    "L'Echo Politique": {"lang":"fr","country":"BE"}, "La DH": {"lang":"fr","country":"BE"},
    "La DH Sports": {"lang":"fr","country":"BE"}, "Le Vif Belgique": {"lang":"fr","country":"BE"},
    "Trends": {"lang":"fr","country":"BE"}, "VRT NWS Politiek": {"lang":"nl","country":"BE"},
    # BR
    "Correio Braziliense": {"lang":"pt","country":"BR"},
    "A Gazeta ES": {"lang":"pt","country":"BR"}, "A Tarde": {"lang":"pt","country":"BR"},
    "Agência Brasil": {"lang":"pt","country":"BR"}, "BBC Brasil": {"lang":"pt","country":"BR"},
    "CartaCapital": {"lang":"pt","country":"BR"}, "CNN Brasil": {"lang":"pt","country":"BR"},
    "Congresso em Foco": {"lang":"pt","country":"BR"}, "Crusoé": {"lang":"pt","country":"BR"},
    "Estadão": {"lang":"pt","country":"BR"}, "Estadão Economia": {"lang":"pt","country":"BR"},
    "Estadão Política": {"lang":"pt","country":"BR"}, "Exame": {"lang":"pt","country":"BR"},
    "Folha Mercado": {"lang":"pt","country":"BR"}, "Folha Mundo": {"lang":"pt","country":"BR"},
    "Folha Poder": {"lang":"pt","country":"BR"}, "G1 Economia": {"lang":"pt","country":"BR"},
    "G1 Mundo": {"lang":"pt","country":"BR"}, "G1 Política": {"lang":"pt","country":"BR"},
    "Gazeta do Povo": {"lang":"pt","country":"BR"}, "Gazeta do Povo Mundo": {"lang":"pt","country":"BR"},
    "InfoMoney": {"lang":"pt","country":"BR"}, "IstoÉ": {"lang":"pt","country":"BR"},
    "Jota": {"lang":"pt","country":"BR"}, "Nexo Jornal": {"lang":"pt","country":"BR"},
    "O Antagonista": {"lang":"pt","country":"BR"}, "O Globo": {"lang":"pt","country":"BR"},
    "O Globo Economia": {"lang":"pt","country":"BR"}, "O Globo Política": {"lang":"pt","country":"BR"},
    "Terra Brasil": {"lang":"pt","country":"BR"}, "The Intercept Brasil": {"lang":"pt","country":"BR"},
    # CA
    "Calgary Herald": {"lang":"en","country":"CA"},
    "Edmonton Journal": {"lang":"en","country":"CA"}, "Financial Post News": {"lang":"en","country":"CA"},
    "Global News Money": {"lang":"en","country":"CA"}, "Global News Politics": {"lang":"en","country":"CA"},
    "iPolitics": {"lang":"en","country":"CA"}, "Journal de Montréal": {"lang":"fr","country":"CA"},
    "La Presse": {"lang":"fr","country":"CA"}, "Le Journal de Québec": {"lang":"fr","country":"CA"},
    "National Observer": {"lang":"en","country":"CA"},
    "National Post Politics": {"lang":"en","country":"CA"}, "Ottawa Citizen": {"lang":"en","country":"CA"},
    "Rabble.ca": {"lang":"en","country":"CA"}, "The Conversation CA": {"lang":"en","country":"CA"},
    "The Globe and Mail": {"lang":"en","country":"CA"},
    "The Globe and Mail Politics": {"lang":"en","country":"CA"},
    "The Globe and Mail World": {"lang":"en","country":"CA"}, "The Narwhal": {"lang":"en","country":"CA"},
    "The Tyee": {"lang":"en","country":"CA"}, "The Walrus": {"lang":"en","country":"CA"},
    "Vancouver Sun": {"lang":"en","country":"CA"}, "Winnipeg Free Press": {"lang":"en","country":"CA"},
    # CL
    "CIPER": {"lang":"es","country":"CL"}, "Diario Financiero": {"lang":"es","country":"CL"},
    "Ex-Ante": {"lang":"es","country":"CL"}, "Interferencia": {"lang":"es","country":"CL"},
    "La Nación Chile": {"lang":"es","country":"CL"},
    "Radio Universidad de Chile": {"lang":"es","country":"CL"}, "The Clinic": {"lang":"es","country":"CL"},
    "BioBioChile": {"lang":"es","country":"CL"}, "La Discusión": {"lang":"es","country":"CL"},
    "La Tercera": {"lang":"es","country":"CL"}, "Publimetro Chile": {"lang":"es","country":"CL"},
    "El Mostrador": {"lang":"es","country":"CL"}, "Cooperativa": {"lang":"es","country":"CL"},
    "Meganoticias": {"lang":"es","country":"CL"}, "El Dínamo": {"lang":"es","country":"CL"},
    "Diario Concepción": {"lang":"es","country":"CL"},
    # CN
    "Bitter Winter": {"lang":"en","country":"CN"},
    "CGTN China": {"lang":"en","country":"CN"},
    "China Media Project": {"lang":"en","country":"CN"},
    "Ecns.cn": {"lang":"en","country":"CN"}, "Global Times": {"lang":"en","country":"CN"},
    "Pekingnology": {"lang":"en","country":"CN"}, "Radio Free Asia": {"lang":"en","country":"CN"},
    "SCMP China": {"lang":"en","country":"CN"},
    "The Wire China": {"lang":"en","country":"CN"}, "Trivium China": {"lang":"en","country":"CN"},
    "What's on Weibo": {"lang":"en","country":"CN"},
    # CO
    "Cuestión Pública": {"lang":"es","country":"CO"},
    "El Colombiano Antioquia": {"lang":"es","country":"CO"},
    "El Colombiano Nacional": {"lang":"es","country":"CO"}, "El Tiempo Mundo": {"lang":"es","country":"CO"},
    "El Tiempo Política": {"lang":"es","country":"CO"}, "La República CO": {"lang":"es","country":"CO"},
    "La Silla Vacía": {"lang":"es","country":"CO"}, "Razón Pública": {"lang":"es","country":"CO"},
    "Semana Mundo": {"lang":"es","country":"CO"}, "Semana Nación": {"lang":"es","country":"CO"},
    "La Opinión": {"lang":"es","country":"CO"}, "El Heraldo CO": {"lang":"es","country":"CO"},
    "El Universal Cartagena": {"lang":"es","country":"CO"}, "Vanguardia CO": {"lang":"es","country":"CO"},
    # CZ
    "Aktuálně Domácí": {"lang":"cs","country":"CZ"}, "Aktuálně Zahraničí": {"lang":"cs","country":"CZ"},
    "Aktuálně.cz": {"lang":"cs","country":"CZ"}, "Blesk": {"lang":"cs","country":"CZ"},
    "Blesk Zprávy": {"lang":"cs","country":"CZ"}, "Deník Ekonomika": {"lang":"cs","country":"CZ"},
    "E15": {"lang":"cs","country":"CZ"}, "E15 Byznys": {"lang":"cs","country":"CZ"},
    "Forbes Česko": {"lang":"cs","country":"CZ"}, "Forum24": {"lang":"cs","country":"CZ"},
    "Hospodářské noviny": {"lang":"cs","country":"CZ"}, "iDNES": {"lang":"cs","country":"CZ"},
    "iDNES Ekonomika": {"lang":"cs","country":"CZ"}, "iDNES Zahraničí": {"lang":"cs","country":"CZ"},
    "Info.cz": {"lang":"cs","country":"CZ"}, "Lidovky": {"lang":"cs","country":"CZ"},
    "Reflex": {"lang":"cs","country":"CZ"}, "ČT24 Domácí": {"lang":"cs","country":"CZ"},
    "ČT24 Ekonomika": {"lang":"cs","country":"CZ"}, "ČT24 Svět": {"lang":"cs","country":"CZ"},
    # DE
    "Berliner Morgenpost": {"lang":"de","country":"DE"},
    "Braunschweiger Zeitung": {"lang":"de","country":"DE"}, "Cicero": {"lang":"de","country":"DE"},
    "Der Freitag": {"lang":"de","country":"DE"}, "Deutschlandfunk": {"lang":"de","country":"DE"},
    "General-Anzeiger Bonn": {"lang":"de","country":"DE"}, "golem.de": {"lang":"de","country":"DE"},
    "Hamburger Abendblatt": {"lang":"de","country":"DE"}, "hessenschau": {"lang":"de","country":"DE"},
    "Junge Welt": {"lang":"de","country":"DE"}, "Kieler Nachrichten": {"lang":"de","country":"DE"},
    "Kreiszeitung": {"lang":"de","country":"DE"}, "Lübecker Nachrichten": {"lang":"de","country":"DE"},
    "MDR Sachsen": {"lang":"de","country":"DE"}, "Netzpolitik": {"lang":"de","country":"DE"},
    "Neue Osnabrücker Zeitung": {"lang":"de","country":"DE"},
    "Ostthüringer Zeitung": {"lang":"de","country":"DE"}, "rbb24": {"lang":"de","country":"DE"},
    "Rheinische Post Politik": {"lang":"de","country":"DE"},
    "Ruhr Nachrichten": {"lang":"de","country":"DE"}, "Saarbrücker Zeitung": {"lang":"de","country":"DE"},
    "Tagesspiegel Politik": {"lang":"de","country":"DE"}, "Telepolis": {"lang":"de","country":"DE"},
    "Thüringer Allgemeine": {"lang":"de","country":"DE"},
    "Trierischer Volksfreund": {"lang":"de","country":"DE"}, "tz München": {"lang":"de","country":"DE"},
    "WAZ": {"lang":"de","country":"DE"}, "WDR": {"lang":"de","country":"DE"},
    "Wolfsburger Nachrichten": {"lang":"de","country":"DE"}, "Zeit Online": {"lang":"de","country":"DE"},
    # DK
    "Altinget": {"lang":"da","country":"DK"}, "Avisen.dk": {"lang":"da","country":"DK"},
    "DR Indland": {"lang":"da","country":"DK"}, "DR Kultur": {"lang":"da","country":"DK"},
    "DR Penge": {"lang":"da","country":"DK"}, "DR Politik": {"lang":"da","country":"DK"},
    "DR Udland": {"lang":"da","country":"DK"}, "Information": {"lang":"da","country":"DK"},
    "Ingeniøren": {"lang":"da","country":"DK"}, "Politiken Kultur": {"lang":"da","country":"DK"},
    "Politiken Udland": {"lang":"da","country":"DK"}, "TV2 Lorry": {"lang":"da","country":"DK"},
    # ES
    "ABC Internacional": {"lang":"es","country":"ES"}, "Ara": {"lang":"ca","country":"ES"},
    "Canarias7": {"lang":"es","country":"ES"}, "Diari de Tarragona": {"lang":"es","country":"ES"},
    "El Comercio": {"lang":"es","country":"ES"}, "El Confidencial Digital": {"lang":"es","country":"ES"},
    "El Confidencial Mundo": {"lang":"es","country":"ES"},
    "El Diario Montañés": {"lang":"es","country":"ES"}, "El Español Mundo": {"lang":"es","country":"ES"},
    "El Independiente España": {"lang":"es","country":"ES"}, "El Mundo España": {"lang":"es","country":"ES"},
    "El Mundo Internacional": {"lang":"es","country":"ES"},
    "El Norte de Castilla": {"lang":"es","country":"ES"}, "El País": {"lang":"es","country":"ES"},
    "El País España": {"lang":"es","country":"ES"}, "El País Internacional": {"lang":"es","country":"ES"},
    "El Salto Diario Política": {"lang":"es","country":"ES"},
    "elDiario Economía": {"lang":"es","country":"ES"}, "elDiario Política": {"lang":"es","country":"ES"},
    "Heraldo": {"lang":"es","country":"ES"}, "Hoy Extremadura": {"lang":"es","country":"ES"},
    "La Marea": {"lang":"es","country":"ES"}, "La Rioja": {"lang":"es","country":"ES"},
    "La Vanguardia Internacional": {"lang":"es","country":"ES"},
    "La Vanguardia Política": {"lang":"es","country":"ES"}, "Nació Digital": {"lang":"ca","country":"ES"},
    "Okdiario": {"lang":"es","country":"ES"}, "Sur in English": {"lang":"en","country":"ES"},
    # FI
    "Etelä-Suomen Sanomat": {"lang":"fi","country":"FI"},
    "Helsingin Sanomat Politiikka": {"lang":"fi","country":"FI"},
    "Hufvudstadsbladet": {"lang":"sv","country":"FI"}, "Ilta-Sanomat Kotimaa": {"lang":"fi","country":"FI"},
    "Ilta-Sanomat Taloussanomat": {"lang":"fi","country":"FI"},
    "Iltalehti Talous": {"lang":"fi","country":"FI"}, "Iltalehti Ulkomaat": {"lang":"fi","country":"FI"},
    "IS Ulkomaat": {"lang":"fi","country":"FI"}, "Karjalainen": {"lang":"fi","country":"FI"},
    "Keskisuomalainen": {"lang":"fi","country":"FI"}, "Maaseudun Tulevaisuus": {"lang":"fi","country":"FI"},
    "MTV Uutiset Kotimaa": {"lang":"fi","country":"FI"},
    "MTV Uutiset Ulkomaat": {"lang":"fi","country":"FI"}, "Savon Sanomat": {"lang":"fi","country":"FI"},
    "Suomenmaa": {"lang":"fi","country":"FI"}, "Talouselämä": {"lang":"fi","country":"FI"},
    "Verkkouutiset": {"lang":"fi","country":"FI"}, "Yle Politiikka": {"lang":"fi","country":"FI"},
    # FR
    "Basta!": {"lang":"fr","country":"FR"}, "BFM Business": {"lang":"fr","country":"FR"},
    "DNA": {"lang":"fr","country":"FR"}, "France Culture": {"lang":"fr","country":"FR"},
    "L'Est Républicain": {"lang":"fr","country":"FR"}, "La Croix Monde": {"lang":"fr","country":"FR"},
    "La Croix Régional": {"lang":"fr","country":"FR"}, "Le Bien Public": {"lang":"fr","country":"FR"},
    "Le Dauphiné Libéré": {"lang":"fr","country":"FR"}, "Le Figaro Éco": {"lang":"fr","country":"FR"},
    "Le Journal de Saône-et-Loire": {"lang":"fr","country":"FR"},
    "Le Monde Politique": {"lang":"fr","country":"FR"}, "Le Monde Éco": {"lang":"fr","country":"FR"},
    "Le Progrès": {"lang":"fr","country":"FR"}, "Le Républicain Lorrain": {"lang":"fr","country":"FR"},
    "Mediacités": {"lang":"fr","country":"FR"}, "Midi Libre": {"lang":"fr","country":"FR"},
    "Nice-Matin": {"lang":"fr","country":"FR"}, "Ouest-France": {"lang":"fr","country":"FR"},
    "Reporterre": {"lang":"fr","country":"FR"}, "RMC": {"lang":"fr","country":"FR"},
    "Sciences et Avenir": {"lang":"fr","country":"FR"}, "Var-Matin": {"lang":"fr","country":"FR"},
    "Vosges Matin": {"lang":"fr","country":"FR"},
    # GB
    "Birmingham Live": {"lang":"en","country":"GB"},
    "BBC UK": {"lang":"en","country":"GB"}, "Belfast Live": {"lang":"en","country":"GB"},
    "Birmingham Mail": {"lang":"en","country":"GB"}, "Bristol Post": {"lang":"en","country":"GB"},
    "Byline Times": {"lang":"en","country":"GB"}, "Cambridge News": {"lang":"en","country":"GB"},
    "Chronicle Live": {"lang":"en","country":"GB"}, "Coventry Telegraph": {"lang":"en","country":"GB"},
    "Daily Record": {"lang":"en","country":"GB"}, "Devon Live": {"lang":"en","country":"GB"},
    "Edinburgh Live": {"lang":"en","country":"GB"}, "Express": {"lang":"en","country":"GB"},
    "Glasgow Live": {"lang":"en","country":"GB"}, "Gloucestershire Live": {"lang":"en","country":"GB"},
    "Grimsby Live": {"lang":"en","country":"GB"}, "Hull Daily Mail": {"lang":"en","country":"GB"},
    "Leeds Live": {"lang":"en","country":"GB"}, "Liverpool Echo": {"lang":"en","country":"GB"},
    "Manchester Evening News UK": {"lang":"en","country":"GB"}, "Morning Star": {"lang":"en","country":"GB"},
    "MyLondon": {"lang":"en","country":"GB"}, "Nottingham Post": {"lang":"en","country":"GB"},
    "openDemocracy": {"lang":"en","country":"GB"}, "Oxford Mail": {"lang":"en","country":"GB"},
    "Reading Chronicle": {"lang":"en","country":"GB"}, "Sky News UK": {"lang":"en","country":"GB"},
    "The Big Issue": {"lang":"en","country":"GB"}, "The Canary": {"lang":"en","country":"GB"},
    "The National": {"lang":"en","country":"GB"}, "The Northern Echo": {"lang":"en","country":"GB"},
    "The Register": {"lang":"en","country":"GB"}, "The Sun": {"lang":"en","country":"GB"},
    "Wales Online News": {"lang":"en","country":"GB"}, "Yorkshire Post": {"lang":"en","country":"GB"},
    # GR
    "Alfavita": {"lang":"el","country":"GR"}, "Documento": {"lang":"el","country":"GR"},
    "Efimerida ton Syntakton": {"lang":"el","country":"GR"}, "Ethnos": {"lang":"el","country":"GR"},
    "in.gr Oikonomia": {"lang":"el","country":"GR"}, "In.gr Politiki": {"lang":"el","country":"GR"},
    "Lifo": {"lang":"el","country":"GR"}, "Newsbeast": {"lang":"el","country":"GR"},
    "Newsit": {"lang":"el","country":"GR"}, "Protagon": {"lang":"el","country":"GR"},
    "Protothema": {"lang":"el","country":"GR"}, "Real.gr": {"lang":"el","country":"GR"},
    "Star.gr": {"lang":"el","country":"GR"}, "ThePressProject": {"lang":"el","country":"GR"},
    "To Vima Politiki": {"lang":"el","country":"GR"},
    # HK
    "HKFP Politics": {"lang":"en","country":"HK"},
    "HKFP World": {"lang":"en","country":"HK"}, "Hong Kong Business": {"lang":"en","country":"HK"},
    "Ming Pao": {"lang":"zh","country":"HK"}, "Oriental Daily": {"lang":"zh","country":"HK"},
    "RTHK Greater China": {"lang":"en","country":"HK"}, "SCMP Asia": {"lang":"en","country":"HK"},
    "SCMP Business": {"lang":"en","country":"HK"}, "SCMP Hong Kong": {"lang":"en","country":"HK"},
    "SCMP World": {"lang":"en","country":"HK"}, "The Witness HK": {"lang":"zh","country":"HK"},
    # HU
    "Blikk": {"lang":"hu","country":"HU"}, "Daily News Hungary": {"lang":"en","country":"HU"},
    "Direkt36": {"lang":"hu","country":"HU"}, "HungaryToday": {"lang":"en","country":"HU"},
    "HVG Gazdaság": {"lang":"hu","country":"HU"}, "HVG Itthon": {"lang":"hu","country":"HU"},
    "HVG Világ": {"lang":"hu","country":"HU"}, "Index Belföld": {"lang":"hu","country":"HU"},
    "Index Gazdaság": {"lang":"hu","country":"HU"}, "Index Külföld": {"lang":"hu","country":"HU"},
    "Infostart": {"lang":"hu","country":"HU"}, "Magyar Hang": {"lang":"hu","country":"HU"},
    "Magyar Nemzet": {"lang":"hu","country":"HU"},
    "Média1": {"lang":"hu","country":"HU"}, "Népszava": {"lang":"hu","country":"HU"},
    "Portfolio Deviza": {"lang":"hu","country":"HU"}, "Portfolio Gazdaság": {"lang":"hu","country":"HU"},
    "Telex Belföld": {"lang":"hu","country":"HU"},
    "Telex Gazdaság": {"lang":"hu","country":"HU"}, "Telex Külföld": {"lang":"hu","country":"HU"},
    "VG.hu": {"lang":"hu","country":"HU"}, "Válasz Online": {"lang":"hu","country":"HU"},
    "Átlátszó": {"lang":"hu","country":"HU"},
    # ID
    "Antara Politik": {"lang":"id","country":"ID"}, "CNBC Indonesia": {"lang":"id","country":"ID"},
    "CNBC Indonesia News": {"lang":"id","country":"ID"},
    "CNN Indonesia Nasional": {"lang":"id","country":"ID"}, "Detik Finance": {"lang":"id","country":"ID"},
    "JPNN": {"lang":"id","country":"ID"}, "Katadata": {"lang":"id","country":"ID"},
    "Kontan Nasional": {"lang":"id","country":"ID"}, "Liputan6 News": {"lang":"id","country":"ID"},
    "Media Indonesia": {"lang":"id","country":"ID"}, "Okezone": {"lang":"id","country":"ID"},
    "Republika": {"lang":"id","country":"ID"}, "Sindonews": {"lang":"id","country":"ID"},
    "Viva": {"lang":"id","country":"ID"},
    # IE
    "Cork Beo": {"lang":"en","country":"IE"}, "Dublin Live": {"lang":"en","country":"IE"},
    "Extra.ie": {"lang":"en","country":"IE"}, "Gript": {"lang":"en","country":"IE"},
    "Hot Press": {"lang":"en","country":"IE"}, "Irish Independent Business": {"lang":"en","country":"IE"},
    "Irish Independent News": {"lang":"en","country":"IE"},
    "Irish Independent Sport": {"lang":"en","country":"IE"},
    "Irish Independent World": {"lang":"en","country":"IE"}, "Kilkenny People": {"lang":"en","country":"IE"},
    "Limerick Leader": {"lang":"en","country":"IE"}, "RTÉ Business": {"lang":"en","country":"IE"},
    "RTÉ News": {"lang":"en","country":"IE"}, "RTÉ World": {"lang":"en","country":"IE"},
    "Silicon Republic": {"lang":"en","country":"IE"}, "The Ditch": {"lang":"en","country":"IE"},
    "The Irish Sun": {"lang":"en","country":"IE"}, "The Irish Times": {"lang":"en","country":"IE"},
    "The42": {"lang":"en","country":"IE"},
    # IL
    "+972 Magazine": {"lang":"en","country":"IL"}, "Al-Monitor": {"lang":"en","country":"IL"},
    "Arutz Sheva": {"lang":"en","country":"IL"}, "Israel Hayom": {"lang":"en","country":"IL"},
    "Maariv": {"lang":"he","country":"IL"}, "The Jerusalem Post Israel News": {"lang":"en","country":"IL"},
    "The Media Line": {"lang":"en","country":"IL"},
    "The Times of Israel": {"lang":"en","country":"IL"}, "Walla": {"lang":"he","country":"IL"},
    "Ynetnews": {"lang":"en","country":"IL"}, "Ynetnews World": {"lang":"he","country":"IL"},
    "Arutz Sheva HE": {"lang":"he","country":"IL"}, "Davar": {"lang":"he","country":"IL"},
    "Israel Hayom HE": {"lang":"he","country":"IL"}, "Shakuf": {"lang":"he","country":"IL"},
    # IN
    "DNA India": {"lang":"en","country":"IN"}, "Economic Times Markets": {"lang":"en","country":"IN"},
    "Free Press Journal": {"lang":"en","country":"IN"},
    "Hindustan Times Business": {"lang":"en","country":"IN"},
    "Hindustan Times World": {"lang":"en","country":"IN"}, "India Today Feed": {"lang":"en","country":"IN"},
    "India Today India": {"lang":"en","country":"IN"}, "India Today World": {"lang":"en","country":"IN"},
    "Livemint Companies": {"lang":"en","country":"IN"},
    "Mint Politics": {"lang":"en","country":"IN"},
    "NDTV India News": {"lang":"en","country":"IN"}, "NDTV World News": {"lang":"en","country":"IN"},
    "News18 World": {"lang":"en","country":"IN"}, "Telangana Today": {"lang":"en","country":"IN"},
    "The Economic Times Politics": {"lang":"en","country":"IN"},
    "The Hindu Business Line": {"lang":"en","country":"IN"}, "The Hindu World": {"lang":"en","country":"IN"},
    "The Print India": {"lang":"en","country":"IN"}, "Times of India Business": {"lang":"en","country":"IN"},
    "Times of India India": {"lang":"en","country":"IN"},
    "Times of India World": {"lang":"en","country":"IN"}, "Zee News": {"lang":"en","country":"IN"},
    # IT
    "Bari Today": {"lang":"it","country":"IT"}, "Bologna Today": {"lang":"it","country":"IT"},
    "Corriere Cronache": {"lang":"it","country":"IT"}, "Corriere della Sera": {"lang":"it","country":"IT"},
    "Corriere Economia": {"lang":"it","country":"IT"}, "Formiche": {"lang":"it","country":"IT"},
    "Gazzetta dello Sport": {"lang":"it","country":"IT"}, "Genova Today": {"lang":"it","country":"IT"},
    "Il Fatto Quotidiano": {"lang":"it","country":"IT"}, "Il Manifesto": {"lang":"it","country":"IT"},
    "Il Messaggero Politica": {"lang":"it","country":"IT"},
    "Il Quotidiano del Sud": {"lang":"it","country":"IT"}, "Il Riformista": {"lang":"it","country":"IT"},
    "Il Sole 24 Ore Mondo": {"lang":"it","country":"IT"},
    "La Repubblica Cronaca": {"lang":"it","country":"IT"},
    "La Repubblica Esteri": {"lang":"it","country":"IT"}, "La Verità": {"lang":"it","country":"IT"},
    "Lettera43": {"lang":"it","country":"IT"}, "Linkiesta": {"lang":"it","country":"IT"},
    "Milano Today": {"lang":"it","country":"IT"}, "Money.it": {"lang":"it","country":"IT"},
    "Open Politica": {"lang":"it","country":"IT"}, "Palermo Today": {"lang":"it","country":"IT"},
    "Panorama": {"lang":"it","country":"IT"}, "Roma Today": {"lang":"it","country":"IT"},
    "Valigia Blu": {"lang":"it","country":"IT"},
    # JP
    "Asahi Politics": {"lang":"ja","country":"JP"}, "Asahi Shimbun": {"lang":"ja","country":"JP"},
    "Diamond": {"lang":"ja","country":"JP"}, "ITmedia": {"lang":"ja","country":"JP"},
    "J-CAST": {"lang":"ja","country":"JP"}, "Japan Forward": {"lang":"en","country":"JP"},
    "Jiji": {"lang":"ja","country":"JP"}, "NHK Politics": {"lang":"ja","country":"JP"},
    "SoraNews24": {"lang":"en","country":"JP"}, "The Japan Times": {"lang":"en","country":"JP"},
    "The Mainichi": {"lang":"en","country":"JP"}, "Yahoo Japan News": {"lang":"ja","country":"JP"},
    "Akita Sakigake": {"lang":"ja","country":"JP"}, "Bunshun": {"lang":"ja","country":"JP"},
    "Chiba Nippo": {"lang":"ja","country":"JP"}, "Fukui Shimbun": {"lang":"ja","country":"JP"},
    "Kumamoto Nichinichi": {"lang":"ja","country":"JP"}, "Kyoto Shimbun": {"lang":"ja","country":"JP"},
    "Okinawa Times": {"lang":"ja","country":"JP"}, "Saga Shimbun": {"lang":"ja","country":"JP"},
    "Shikoku Shimbun": {"lang":"ja","country":"JP"}, "Toyo Keizai": {"lang":"ja","country":"JP"},
    "Chunichi Shimbun": {"lang":"ja","country":"JP"}, "Chugoku Shimbun": {"lang":"ja","country":"JP"},
    # KR
    "KBS World": {"lang":"en","country":"KR"}, "Korea Pro": {"lang":"en","country":"KR"},
    "Maeil Business": {"lang":"ko","country":"KR"}, "MK Business": {"lang":"ko","country":"KR"},
    "NK News": {"lang":"en","country":"KR"}, "The Korea Times Business": {"lang":"en","country":"KR"},
    # KR, Korean-language
    "Chosun Ilbo": {"lang":"ko","country":"KR"}, "Donga Economy": {"lang":"ko","country":"KR"},
    "Donga Ilbo": {"lang":"ko","country":"KR"}, "Donga Politics": {"lang":"ko","country":"KR"},
    "ETNews": {"lang":"ko","country":"KR"}, "ETNews IT": {"lang":"ko","country":"KR"},
    "Hankyung": {"lang":"ko","country":"KR"}, "Hankyung Politics": {"lang":"ko","country":"KR"},
    "Kyunghyang": {"lang":"ko","country":"KR"}, "Kyunghyang Economy": {"lang":"ko","country":"KR"},
    "Kyunghyang Politics": {"lang":"ko","country":"KR"}, "Money Today": {"lang":"ko","country":"KR"},
    "Newsis Economy": {"lang":"ko","country":"KR"}, "Newsis Politics": {"lang":"ko","country":"KR"},
    "Newsis Society": {"lang":"ko","country":"KR"}, "Nocut News": {"lang":"ko","country":"KR"},
    "OhmyNews": {"lang":"ko","country":"KR"}, "Pressian": {"lang":"ko","country":"KR"},
    "Segye Ilbo": {"lang":"ko","country":"KR"}, "Seoul Shinmun": {"lang":"ko","country":"KR"},
    "Sisa Journal": {"lang":"ko","country":"KR"}, "Yonhap Economy": {"lang":"ko","country":"KR"},
    "Yonhap News": {"lang":"ko","country":"KR"}, "Yonhap Politics": {"lang":"ko","country":"KR"},
    "Chungcheong Today": {"lang":"ko","country":"KR"}, "Incheon Ilbo": {"lang":"ko","country":"KR"},
    "Jeju Sori": {"lang":"ko","country":"KR"}, "Jeonnam Ilbo": {"lang":"ko","country":"KR"},
    "Kangwon Domin Ilbo": {"lang":"ko","country":"KR"}, "Kyongbuk Ilbo": {"lang":"ko","country":"KR"},
    "Ulsan Jeil Ilbo": {"lang":"ko","country":"KR"},
    # MX
    "Contralínea": {"lang":"es","country":"MX"}, "El Economista MX": {"lang":"es","country":"MX"},
    "El Heraldo de México": {"lang":"es","country":"MX"}, "El Sol de México": {"lang":"es","country":"MX"},
    "Expansión Economía": {"lang":"es","country":"MX"}, "Expansión MX": {"lang":"es","country":"MX"},
    "La Jornada Política": {"lang":"es","country":"MX"}, "Pie de Página": {"lang":"es","country":"MX"},
    "Reforma": {"lang":"es","country":"MX"}, "Zeta Tijuana": {"lang":"es","country":"MX"},
    "Diario de Yucatán": {"lang":"es","country":"MX"}, "La Voz de Michoacán": {"lang":"es","country":"MX"},
    "Periódico AM": {"lang":"es","country":"MX"}, "Vanguardia MX": {"lang":"es","country":"MX"},
    "El Informador": {"lang":"es","country":"MX"}, "Crónica": {"lang":"es","country":"MX"},
    # NL
    "AD Binnenland": {"lang":"nl","country":"NL"}, "AD Politiek": {"lang":"nl","country":"NL"},
    "BN DeStem Regio": {"lang":"nl","country":"NL"}, "Brabant Dagblad Nieuws": {"lang":"nl","country":"NL"},
    "Brabants Dagblad Binnenland": {"lang":"nl","country":"NL"},
    "Dagblad van het Noorden": {"lang":"nl","country":"NL"},
    "De Gelderlander Binnenland": {"lang":"nl","country":"NL"},
    "De Gooi- en Eemlander": {"lang":"nl","country":"NL"}, "De Stentor Nieuws": {"lang":"nl","country":"NL"},
    "De Telegraaf Nieuws": {"lang":"nl","country":"NL"},
    "De Volkskrant Nieuws": {"lang":"nl","country":"NL"},
    "De Volkskrant Politiek": {"lang":"nl","country":"NL"},
    "Eindhovens Dagblad Regio": {"lang":"nl","country":"NL"}, "EW Magazine": {"lang":"nl","country":"NL"},
    "Follow the Money": {"lang":"nl","country":"NL"}, "Haarlems Dagblad": {"lang":"nl","country":"NL"},
    "Het Parool Amsterdam": {"lang":"nl","country":"NL"}, "Het Parool Nieuws": {"lang":"nl","country":"NL"},
    "IJmuider Courant": {"lang":"nl","country":"NL"}, "Leeuwarder Courant": {"lang":"nl","country":"NL"},
    "Leidsch Dagblad": {"lang":"nl","country":"NL"}, "Metro NL": {"lang":"nl","country":"NL"},
    "Nederlands Dagblad Nieuws": {"lang":"nl","country":"NL"},
    "Noordhollands Dagblad": {"lang":"nl","country":"NL"}, "NOS Politiek": {"lang":"nl","country":"NL"},
    "NU.nl Economie": {"lang":"nl","country":"NL"}, "Trouw Groen": {"lang":"nl","country":"NL"},
    "Trouw Politiek": {"lang":"nl","country":"NL"}, "Tweakers": {"lang":"nl","country":"NL"},
    # NO
    "Dagsavisen": {"lang":"no","country":"NO"},
    "Adresseavisen": {"lang":"no","country":"NO"}, "Aftenposten Nyheter": {"lang":"no","country":"NO"},
    "Fædrelandsvennen": {"lang":"no","country":"NO"}, "iTromsø": {"lang":"no","country":"NO"},
    "Morgenbladet": {"lang":"no","country":"NO"}, "NRK Norge": {"lang":"no","country":"NO"},
    "NRK Urix": {"lang":"no","country":"NO"}, "Stavanger Aftenblad": {"lang":"no","country":"NO"},
    "Sunnmørsposten": {"lang":"no","country":"NO"}, "TV 2": {"lang":"no","country":"NO"},
    "VG Nyheter": {"lang":"no","country":"NO"},
    # NZ
    "Newsroom NZ": {"lang":"en","country":"NZ"},
    "Kiwiblog": {"lang":"en","country":"NZ"}, "NZ Herald": {"lang":"en","country":"NZ"},
    "NZ Herald Business": {"lang":"en","country":"NZ"}, "Otago Daily Times": {"lang":"en","country":"NZ"},
    "RNZ Business": {"lang":"en","country":"NZ"}, "RNZ Political": {"lang":"en","country":"NZ"},
    "RNZ Te Ao Māori": {"lang":"en","country":"NZ"}, "RNZ World": {"lang":"en","country":"NZ"},
    "Stuff Politics": {"lang":"en","country":"NZ"}, "The Post": {"lang":"en","country":"NZ"},
    "The Press": {"lang":"en","country":"NZ"}, "Waikato Times": {"lang":"en","country":"NZ"},
    # PE
    "Andina Economía": {"lang":"es","country":"PE"}, "Andina Nacional": {"lang":"es","country":"PE"},
    "Andina Regional": {"lang":"es","country":"PE"}, "IDL-Reporteros": {"lang":"es","country":"PE"},
    "Wayka": {"lang":"es","country":"PE"},
    "Diario Correo": {"lang":"es","country":"PE"}, "El Comercio Perú": {"lang":"es","country":"PE"},
    "Gestión": {"lang":"es","country":"PE"}, "La República Perú": {"lang":"es","country":"PE"},
    "La República Política": {"lang":"es","country":"PE"},
    "La República Sociedad": {"lang":"es","country":"PE"},
    # QA
    "Doha News": {"lang":"en","country":"QA"},
    # PH
    "Business World": {"lang":"en","country":"PH"},
    "BusinessWorld Economy": {"lang":"en","country":"PH"}, "GMA Money": {"lang":"en","country":"PH"},
    "GMA News Nation": {"lang":"en","country":"PH"}, "GMA News World": {"lang":"en","country":"PH"},
    "Inquirer Nation": {"lang":"en","country":"PH"},
    "Interaksyon": {"lang":"en","country":"PH"}, "Manila Times News": {"lang":"en","country":"PH"},
    "PhilNews": {"lang":"en","country":"PH"}, "PhilStar Business": {"lang":"en","country":"PH"},
    "Philstar Nation": {"lang":"en","country":"PH"}, "Philstar World": {"lang":"en","country":"PH"},
    "Rappler Business": {"lang":"en","country":"PH"},
    "Rappler World": {"lang":"en","country":"PH"},
    # PK
    "ARY News Pakistan": {"lang":"en","country":"PK"}, "Bol News": {"lang":"en","country":"PK"},
    "Business Recorder Pakistan": {"lang":"en","country":"PK"}, "Daily Times": {"lang":"en","country":"PK"},
    "Dawn Business": {"lang":"en","country":"PK"}, "Dawn Pakistan": {"lang":"en","country":"PK"},
    "Dawn World": {"lang":"en","country":"PK"},
    "Minute Mirror": {"lang":"en","country":"PK"}, "Pakistan Observer": {"lang":"en","country":"PK"},
    "The Current": {"lang":"en","country":"PK"}, "The Express Tribune": {"lang":"en","country":"PK"},
    "The Express Tribune Business": {"lang":"en","country":"PK"},
    "The Express Tribune Pakistan": {"lang":"en","country":"PK"},
    "The Express Tribune World": {"lang":"en","country":"PK"},
    # PL
    "Gazeta Wyborcza": {"lang":"pl","country":"PL"},
    "Bankier.pl": {"lang":"pl","country":"PL"}, "Defence24": {"lang":"pl","country":"PL"},
    "Do Rzeczy": {"lang":"pl","country":"PL"}, "Dziennik Zachodni": {"lang":"pl","country":"PL"},
    "Fakt": {"lang":"pl","country":"PL"}, "Gazeta Krakowska": {"lang":"pl","country":"PL"},
    "Gazeta Pomorska": {"lang":"pl","country":"PL"}, "Interia Biznes": {"lang":"pl","country":"PL"},
    "Krytyka Polityczna": {"lang":"pl","country":"PL"}, "Money.pl": {"lang":"pl","country":"PL"},
    "Money.pl Gospodarka": {"lang":"pl","country":"PL"},
    "Newsweek Polska Polska": {"lang":"pl","country":"PL"},
    "Notes from Poland": {"lang":"en","country":"PL"}, "OKO.press": {"lang":"pl","country":"PL"},
    "Onet Kraj": {"lang":"pl","country":"PL"}, "Onet Świat": {"lang":"pl","country":"PL"},
    "Polsat News Polska": {"lang":"pl","country":"PL"}, "Polsat News Świat": {"lang":"pl","country":"PL"},
    "Press.pl": {"lang":"pl","country":"PL"}, "RMF FM": {"lang":"pl","country":"PL"},
    "Rmf24 Fakty": {"lang":"pl","country":"PL"}, "Rzeczpospolita Ekonomia": {"lang":"pl","country":"PL"},
    "Rzeczpospolita Polityka": {"lang":"pl","country":"PL"}, "TVN24 Świat": {"lang":"pl","country":"PL"},
    "Wprost Biznes": {"lang":"pl","country":"PL"}, "Wprost Polityka": {"lang":"pl","country":"PL"},
    "Wprost Wiadomości": {"lang":"pl","country":"PL"}, "Wprost Świat": {"lang":"pl","country":"PL"},
    "Wyborcza Kraj": {"lang":"pl","country":"PL"},
    # PT
    "Diário de Notícias da Madeira": {"lang":"pt","country":"PT"},
    "Dinheiro Vivo": {"lang":"pt","country":"PT"}, "Fumaça": {"lang":"pt","country":"PT"},
    "Jornal Económico": {"lang":"pt","country":"PT"}, "Mensagem de Lisboa": {"lang":"pt","country":"PT"},
    "Notícias ao Minuto Mundo": {"lang":"pt","country":"PT"},
    "Notícias ao Minuto País": {"lang":"pt","country":"PT"},
    "Observador Economia": {"lang":"pt","country":"PT"}, "Observador Política": {"lang":"pt","country":"PT"},
    "Público Economia": {"lang":"pt","country":"PT"}, "Público Mundo": {"lang":"pt","country":"PT"},
    "Público Política": {"lang":"pt","country":"PT"}, "Público PT": {"lang":"pt","country":"PT"},
    "RTP Mundo": {"lang":"pt","country":"PT"}, "Visão": {"lang":"pt","country":"PT"},
    # RO
    "Adevărul Internațional": {"lang":"ro","country":"RO"}, "Aktual24": {"lang":"ro","country":"RO"},
    "Antena 3 CNN": {"lang":"ro","country":"RO"}, "Cotidianul": {"lang":"ro","country":"RO"},
    "Digi Sport": {"lang":"ro","country":"RO"}, "Digi24 Economie": {"lang":"ro","country":"RO"},
    "Digi24 Externe": {"lang":"ro","country":"RO"}, "Digi24 Politică": {"lang":"ro","country":"RO"},
    "Economica.net": {"lang":"ro","country":"RO"}, "Europa FM": {"lang":"ro","country":"RO"},
    "Mediafax": {"lang":"ro","country":"RO"}, "Mediafax Externe": {"lang":"ro","country":"RO"},
    "News.ro": {"lang":"ro","country":"RO"}, "Newsweek România": {"lang":"ro","country":"RO"},
    "PressOne": {"lang":"ro","country":"RO"}, "Profit.ro": {"lang":"ro","country":"RO"},
    "Recorder": {"lang":"ro","country":"RO"}, "Republica": {"lang":"ro","country":"RO"},
    "Spotmedia": {"lang":"ro","country":"RO"}, "Stirile ProTV Feed": {"lang":"ro","country":"RO"},
    "Ziarul Financiar": {"lang":"ro","country":"RO"},
    "Ziarul Financiar Business": {"lang":"ro","country":"RO"},
    "Ziarul Financiar Companii": {"lang":"ro","country":"RO"},
    # RU
    "Agentstvo": {"lang":"ru","country":"RU"}, "Gazeta Politics": {"lang":"ru","country":"RU"},
    "Holod": {"lang":"ru","country":"RU"}, "Interfax": {"lang":"ru","country":"RU"},
    "It's My City": {"lang":"ru","country":"RU"}, "Kommersant": {"lang":"ru","country":"RU"},
    "Kommersant Politics": {"lang":"ru","country":"RU"}, "Kommersant World": {"lang":"ru","country":"RU"},
    "Lenta World": {"lang":"ru","country":"RU"}, "Lenta.ru": {"lang":"ru","country":"RU"},
    "Meduza English": {"lang":"en","country":"RU"}, "RBC": {"lang":"ru","country":"RU"},
    "TASS Russia": {"lang":"ru","country":"RU"}, "The Bell": {"lang":"ru","country":"RU"},
    "The Insider": {"lang":"ru","country":"RU"}, "Vedomosti": {"lang":"ru","country":"RU"},
    "Vedomosti Politics": {"lang":"ru","country":"RU"},
    # SE
    "Aftonbladet Nyheter": {"lang":"sv","country":"SE"}, "Aftonbladet Sport": {"lang":"sv","country":"SE"},
    "Arbetet": {"lang":"sv","country":"SE"}, "Barometern": {"lang":"sv","country":"SE"},
    "Blekinge Läns Tidning": {"lang":"sv","country":"SE"}, "Borås Tidning": {"lang":"sv","country":"SE"},
    "Dagens Arena": {"lang":"sv","country":"SE"}, "Dagens ETC": {"lang":"sv","country":"SE"},
    "Dagens Samhälle": {"lang":"sv","country":"SE"}, "Dala-Demokraten": {"lang":"sv","country":"SE"},
    "DN Ekonomi": {"lang":"sv","country":"SE"}, "Expressen Sport": {"lang":"sv","country":"SE"},
    "Gefle Dagblad": {"lang":"sv","country":"SE"}, "GT": {"lang":"sv","country":"SE"},
    "Helsingborgs Dagblad": {"lang":"sv","country":"SE"},
    "Kristianstadsbladet": {"lang":"sv","country":"SE"},
    "Länstidningen Östersund": {"lang":"sv","country":"SE"},
    "Nerikes Allehanda": {"lang":"sv","country":"SE"},
    "Nya Wermlands-Tidningen": {"lang":"sv","country":"SE"}, "Smålandsposten": {"lang":"sv","country":"SE"},
    "Sundsvalls Tidning": {"lang":"sv","country":"SE"}, "Sveriges Radio Ekot": {"lang":"sv","country":"SE"},
    "SVT Ekonomi": {"lang":"sv","country":"SE"}, "SVT Inrikes": {"lang":"sv","country":"SE"},
    "SVT Lokalt Skåne": {"lang":"sv","country":"SE"}, "SVT Lokalt Stockholm": {"lang":"sv","country":"SE"},
    "SVT Lokalt Väst": {"lang":"sv","country":"SE"}, "SVT Utrikes": {"lang":"sv","country":"SE"},
    "Sydsvenskan Malmö": {"lang":"sv","country":"SE"},
    "Vestmanlands Läns Tidning": {"lang":"sv","country":"SE"},
    "Ystads Allehanda": {"lang":"sv","country":"SE"},
    # SG
    "CNA Asia": {"lang":"en","country":"SG"}, "CNA Business SG": {"lang":"en","country":"SG"},
    "Rice Media": {"lang":"en","country":"SG"}, "Straits Times Business": {"lang":"en","country":"SG"},
    "The Business Times SG": {"lang":"en","country":"SG"},
    "The Business Times Singapore": {"lang":"en","country":"SG"},
    "The Business Times World": {"lang":"en","country":"SG"},
    "The Straits Times Asia": {"lang":"en","country":"SG"},
    "The Straits Times World": {"lang":"en","country":"SG"}, "Vulcan Post": {"lang":"en","country":"SG"},
    "Yahoo Singapore": {"lang":"en","country":"SG"},
    # TR
    "Anadolu Agency": {"lang":"tr","country":"TR"}, "BBC Türkçe": {"lang":"tr","country":"TR"},
    "CNN Türk": {"lang":"tr","country":"TR"}, "CNN Türk Dünya": {"lang":"tr","country":"TR"},
    "Cumhuriyet Dünya": {"lang":"tr","country":"TR"}, "Cumhuriyet Ekonomi": {"lang":"tr","country":"TR"},
    "Cumhuriyet Türkiye": {"lang":"tr","country":"TR"}, "Daily Sabah": {"lang":"en","country":"TR"},
    "Diken": {"lang":"tr","country":"TR"}, "Dünya Gazetesi": {"lang":"tr","country":"TR"},
    "Ekonomim": {"lang":"tr","country":"TR"}, "Euronews Türkçe": {"lang":"tr","country":"TR"},
    "Evrensel": {"lang":"tr","country":"TR"}, "HaberGlobal": {"lang":"tr","country":"TR"},
    "Habertürk": {"lang":"tr","country":"TR"}, "Habertürk Ekonomi": {"lang":"tr","country":"TR"},
    "Habertürk Gündem": {"lang":"tr","country":"TR"}, "Hürriyet Dünya": {"lang":"tr","country":"TR"},
    "Hürriyet Ekonomi": {"lang":"tr","country":"TR"}, "Hürriyet Gündem": {"lang":"tr","country":"TR"},
    "Independent Türkçe": {"lang":"tr","country":"TR"}, "Karar": {"lang":"tr","country":"TR"},
    "Milliyet Dünya": {"lang":"tr","country":"TR"}, "Milliyet Ekonomi": {"lang":"tr","country":"TR"},
    "Milliyet Gündem": {"lang":"tr","country":"TR"}, "NTV Dünya": {"lang":"tr","country":"TR"},
    "NTV Türkiye": {"lang":"tr","country":"TR"}, "Sabah Dünya": {"lang":"tr","country":"TR"},
    "Sabah Ekonomi": {"lang":"tr","country":"TR"}, "Sabah Gündem": {"lang":"tr","country":"TR"},
    "Star Gazete": {"lang":"tr","country":"TR"}, "Türkiye Gazetesi": {"lang":"tr","country":"TR"},
    "Yeni Şafak": {"lang":"tr","country":"TR"}, "Yeni Şafak Gündem": {"lang":"tr","country":"TR"},
    "Yeniçağ": {"lang":"tr","country":"TR"},
    # UA
    "Censor.NET": {"lang":"uk","country":"UA"}, "Espreso": {"lang":"uk","country":"UA"},
    "Interfax Ukraine": {"lang":"uk","country":"UA"}, "LB.ua": {"lang":"uk","country":"UA"},
    "Novoe Vremya Ukr": {"lang":"uk","country":"UA"}, "RBC Ukraine": {"lang":"uk","country":"UA"},
    "Suspilne": {"lang":"uk","country":"UA"}, "TSN": {"lang":"uk","country":"UA"},
    "Ukrainform Ukr": {"lang":"uk","country":"UA"}, "Ukrainska Pravda Economy": {"lang":"uk","country":"UA"},
    "Ukrainska Pravda Life": {"lang":"uk","country":"UA"},
    "Ukrainska Pravda Politics": {"lang":"uk","country":"UA"},
    # US
    "Ars Technica": {"lang":"en","country":"US"}, "Axios": {"lang":"en","country":"US"},
    "Bloomberg Politics": {"lang":"en","country":"US"}, "Business Insider": {"lang":"en","country":"US"},
    "Chicago Sun-Times": {"lang":"en","country":"US"}, "Cleveland.com": {"lang":"en","country":"US"},
    "Common Dreams": {"lang":"en","country":"US"}, "Fortune": {"lang":"en","country":"US"},
    "Grist": {"lang":"en","country":"US"}, "MarketWatch": {"lang":"en","country":"US"},
    "Mother Jones": {"lang":"en","country":"US"}, "National Review": {"lang":"en","country":"US"},
    "Politico": {"lang":"en","country":"US"}, "Reason": {"lang":"en","country":"US"},
    "Salon": {"lang":"en","country":"US"}, "Seattle Times": {"lang":"en","country":"US"},
    "Slate": {"lang":"en","country":"US"}, "Star Tribune": {"lang":"en","country":"US"},
    "STAT News": {"lang":"en","country":"US"}, "The American Conservative": {"lang":"en","country":"US"},
    "The Conversation US": {"lang":"en","country":"US"}, "The Guardian US": {"lang":"en","country":"US"},
    "The Hill Homenews": {"lang":"en","country":"US"}, "The Intercept": {"lang":"en","country":"US"},
    "The Nation": {"lang":"en","country":"US"}, "The New Yorker": {"lang":"en","country":"US"},
    "The Oregonian": {"lang":"en","country":"US"}, "The Texas Tribune": {"lang":"en","country":"US"},
    "The Verge US": {"lang":"en","country":"US"},
    # VN
    "Bao Giao Thong": {"lang":"vi","country":"VN"}, "Cong An Nhan Dan": {"lang":"vi","country":"VN"},
    "Dan Tri Kinh doanh": {"lang":"vi","country":"VN"}, "Dan Tri Su Kien": {"lang":"vi","country":"VN"},
    "Thanh Nien Chinh Tri": {"lang":"vi","country":"VN"}, "Thanh Nien Thoi su": {"lang":"vi","country":"VN"},
    "Tien Phong": {"lang":"vi","country":"VN"},
    "Vietnamnet Thoi su": {"lang":"vi","country":"VN"}, "VietnamPlus VN": {"lang":"vi","country":"VN"},
    "VnExpress Kinh doanh": {"lang":"vi","country":"VN"}, "VnExpress Thế giới": {"lang":"vi","country":"VN"},
    "VnExpress Thời sự": {"lang":"vi","country":"VN"},
    # ===== Asia, Middle East & Pacific expansion (2026-10) =====
    # CN
    "BBC 中文": {"lang":"zh","country":"CN"},
    "Chinanews": {"lang":"zh","country":"CN"},
    "DW 中文": {"lang":"zh","country":"CN"},
    "Initium Media": {"lang":"zh","country":"CN"},
    "IT之家": {"lang":"zh","country":"CN"},
    "RFA 中文": {"lang":"zh","country":"CN"},
    "RFI 中文": {"lang":"zh","country":"CN"},
    "VOA 中文": {"lang":"zh","country":"CN"},
    # HK
    "am730": {"lang":"zh","country":"HK"},
    "Bastille Post": {"lang":"zh","country":"HK"},
    "Commercial Radio HK": {"lang":"zh","country":"HK"},
    "i-Cable": {"lang":"zh","country":"HK"},
    "RTHK 中文": {"lang":"zh","country":"HK"},
    "Sing Tao": {"lang":"zh","country":"HK"},
    "The Collective HK": {"lang":"zh","country":"HK"},
    "TVB News": {"lang":"zh","country":"HK"},
    "Yahoo Hong Kong": {"lang":"zh","country":"HK"},
    # ID
    "Antara English": {"lang":"en","country":"ID"},
    "Jakarta Post": {"lang":"en","country":"ID"},
    "BBC Indonesia": {"lang":"id","country":"ID"},
    "Bloomberg Technoz": {"lang":"id","country":"ID"},
    "Detik News": {"lang":"id","country":"ID"},
    "IDN Times": {"lang":"id","country":"ID"},
    "iNews.id": {"lang":"id","country":"ID"},
    "Jawa Pos": {"lang":"id","country":"ID"},
    "Kumparan": {"lang":"id","country":"ID"},
    "Merdeka": {"lang":"id","country":"ID"},
    "Metro TV News": {"lang":"id","country":"ID"},
    "Pikiran Rakyat": {"lang":"id","country":"ID"},
    "Suara": {"lang":"id","country":"ID"},
    "VOI": {"lang":"id","country":"ID"},
    # IL
    "Al-Ittihad": {"lang":"ar","country":"IL"},
    "Arab48": {"lang":"ar","country":"IL"},
    "i24NEWS Arabic": {"lang":"ar","country":"IL"},
    "Kul al-Arab": {"lang":"ar","country":"IL"},
    "Sonara": {"lang":"ar","country":"IL"},
    "0404": {"lang":"he","country":"IL"},
    "Behadrei Haredim": {"lang":"he","country":"IL"},
    "i24NEWS Hebrew": {"lang":"he","country":"IL"},
    "Kikar HaShabbat": {"lang":"he","country":"IL"},
    "Mekomit": {"lang":"he","country":"IL"},
    "N12": {"lang":"he","country":"IL"},
    "Srugim": {"lang":"he","country":"IL"},
    "Zman Yisrael": {"lang":"he","country":"IL"},
    # IN
    "ABP Ananda": {"lang":"bn","country":"IN"},
    "Eisamay": {"lang":"bn","country":"IN"},
    "News18 Bangla": {"lang":"bn","country":"IN"},
    "Sangbad Pratidin": {"lang":"bn","country":"IN"},
    "ABP Asmita": {"lang":"gu","country":"IN"},
    "BBC Gujarati": {"lang":"gu","country":"IN"},
    "Divya Bhaskar": {"lang":"gu","country":"IN"},
    "Gujarat Samachar": {"lang":"gu","country":"IN"},
    "News18 Gujarati": {"lang":"gu","country":"IN"},
    "TV9 Gujarati": {"lang":"gu","country":"IN"},
    "Aaj Tak": {"lang":"hi","country":"IN"},
    "ABP News": {"lang":"hi","country":"IN"},
    "Amar Ujala": {"lang":"hi","country":"IN"},
    "BBC Hindi": {"lang":"hi","country":"IN"},
    "Dainik Bhaskar": {"lang":"hi","country":"IN"},
    "Dainik Jagran": {"lang":"hi","country":"IN"},
    "India TV Hindi": {"lang":"hi","country":"IN"},
    "Jansatta": {"lang":"hi","country":"IN"},
    "Live Hindustan": {"lang":"hi","country":"IN"},
    "Navbharat Times": {"lang":"hi","country":"IN"},
    "NDTV Hindi": {"lang":"hi","country":"IN"},
    "News18 Hindi": {"lang":"hi","country":"IN"},
    "Patrika": {"lang":"hi","country":"IN"},
    "Prabhat Khabar": {"lang":"hi","country":"IN"},
    "The Wire Hindi": {"lang":"hi","country":"IN"},
    "Zee News Hindi": {"lang":"hi","country":"IN"},
    "Asianet Suvarna News": {"lang":"kn","country":"IN"},
    "Kannada Prabha": {"lang":"kn","country":"IN"},
    "News18 Kannada": {"lang":"kn","country":"IN"},
    "Prajavani": {"lang":"kn","country":"IN"},
    "TV9 Kannada": {"lang":"kn","country":"IN"},
    "Asianet News": {"lang":"ml","country":"IN"},
    "Madhyamam": {"lang":"ml","country":"IN"},
    "Mathrubhumi": {"lang":"ml","country":"IN"},
    "Twentyfour News": {"lang":"ml","country":"IN"},
    "ABP Majha": {"lang":"mr","country":"IN"},
    "BBC Marathi": {"lang":"mr","country":"IN"},
    "eSakal": {"lang":"mr","country":"IN"},
    "Loksatta": {"lang":"mr","country":"IN"},
    "Maharashtra Times": {"lang":"mr","country":"IN"},
    "TV9 Marathi": {"lang":"mr","country":"IN"},
    "ABP Sanjha": {"lang":"pa","country":"IN"},
    "BBC Punjabi": {"lang":"pa","country":"IN"},
    "Jagbani": {"lang":"pa","country":"IN"},
    "News18 Punjab": {"lang":"pa","country":"IN"},
    "ABP Nadu": {"lang":"ta","country":"IN"},
    "BBC Tamil": {"lang":"ta","country":"IN"},
    "Dinamani": {"lang":"ta","country":"IN"},
    "Hindu Tamil Thisai": {"lang":"ta","country":"IN"},
    "News18 Tamil": {"lang":"ta","country":"IN"},
    "ABP Desam": {"lang":"te","country":"IN"},
    "BBC Telugu": {"lang":"te","country":"IN"},
    "Oneindia Telugu": {"lang":"te","country":"IN"},
    "Sakshi": {"lang":"te","country":"IN"},
    "TV9 Telugu": {"lang":"te","country":"IN"},
    "ETV Bharat Urdu": {"lang":"ur","country":"IN"},
    "News18 Urdu": {"lang":"ur","country":"IN"},
    "Qaumi Awaz": {"lang":"ur","country":"IN"},
    "Siasat Urdu": {"lang":"ur","country":"IN"},
    # NZ
    "Asia Pacific Report": {"lang":"en","country":"NZ"},
    "Bay of Plenty Times": {"lang":"en","country":"NZ"},
    "Farmers Weekly NZ": {"lang":"en","country":"NZ"},
    "Gisborne Herald": {"lang":"en","country":"NZ"},
    "Hawke's Bay Today": {"lang":"en","country":"NZ"},
    "Newstalk ZB": {"lang":"en","country":"NZ"},
    "Northern Advocate": {"lang":"en","country":"NZ"},
    "Rotorua Daily Post": {"lang":"en","country":"NZ"},
    "Te Ao Māori News": {"lang":"en","country":"NZ"},
    "Waatea News": {"lang":"en","country":"NZ"},
    "Whanganui Chronicle": {"lang":"en","country":"NZ"},
    # PH
    "Abante": {"lang":"tl","country":"PH"},
    "Abante Tonite": {"lang":"tl","country":"PH"},
    "Hataw": {"lang":"tl","country":"PH"},
    "Pilipino Mirror": {"lang":"tl","country":"PH"},
    "Pinoy Weekly": {"lang":"tl","country":"PH"},
    "Radyo Pilipinas": {"lang":"tl","country":"PH"},
    "Remate": {"lang":"tl","country":"PH"},
    "Saksi Ngayon": {"lang":"tl","country":"PH"},
    # PK
    "Aaj News": {"lang":"ur","country":"PK"},
    "ARY Urdu": {"lang":"ur","country":"PK"},
    "BBC Urdu": {"lang":"ur","country":"PK"},
    "Bol News Urdu": {"lang":"ur","country":"PK"},
    "Daily Pakistan": {"lang":"ur","country":"PK"},
    "DW Urdu": {"lang":"ur","country":"PK"},
    "Express Urdu": {"lang":"ur","country":"PK"},
    "Independent Urdu": {"lang":"ur","country":"PK"},
    "Jang": {"lang":"ur","country":"PK"},
    "Jang World": {"lang":"ur","country":"PK"},
    "Nawa-i-Waqt": {"lang":"ur","country":"PK"},
    # QA
    "Al Arab Qatar": {"lang":"ar","country":"QA"},
    "Al Sharq": {"lang":"ar","country":"QA"},
    "Al Watan Qatar": {"lang":"ar","country":"QA"},
    "Lusail News": {"lang":"ar","country":"QA"},
    "QNA": {"lang":"ar","country":"QA"},
    "QNA Qatar": {"lang":"ar","country":"QA"},
    "Qatar Tribune": {"lang":"en","country":"QA"},
    "QNA English": {"lang":"en","country":"QA"},
    "QNA English Qatar": {"lang":"en","country":"QA"},
    "The Peninsula": {"lang":"en","country":"QA"},
    # SG
    "e27": {"lang":"en","country":"SG"},
    "Fintech News SG": {"lang":"en","country":"SG"},
    "Singapore Business Review": {"lang":"en","country":"SG"},
    "Stomp": {"lang":"en","country":"SG"},
    "Berita Harian SG": {"lang":"ms","country":"SG"},
    "Berita Mediacorp": {"lang":"ms","country":"SG"},
    "Seithi": {"lang":"ta","country":"SG"},
    "Tamil Murasu": {"lang":"ta","country":"SG"},
    "8world": {"lang":"zh","country":"SG"},
    "Lianhe Zaobao": {"lang":"zh","country":"SG"},
    # VN
    "SGGP News": {"lang":"en","country":"VN"},
    "Tuoi Tre News": {"lang":"en","country":"VN"},
    "Vietnam News": {"lang":"en","country":"VN"},
    "VietnamPlus English": {"lang":"en","country":"VN"},
    "BBC Tieng Viet": {"lang":"vi","country":"VN"},
    "CafeF": {"lang":"vi","country":"VN"},
    "Nhan Dan": {"lang":"vi","country":"VN"},
    "Saigon Giai Phong": {"lang":"vi","country":"VN"},
    "VnEconomy": {"lang":"vi","country":"VN"},
    "VOV": {"lang":"vi","country":"VN"},
    "VTC News": {"lang":"vi","country":"VN"},
}


# Section-only sources carry their origin inline.
for _s in SECTION_SOURCES:
    SOURCE_ORIGIN.setdefault(_s["source"], {"lang": _s["lang"], "country": _s["country"]})

# source -> the feeds it contributes to; anything unlisted feeds only "news".
SOURCE_SECTIONS = {_s["source"]: set(_s["sections"]) for _s in SECTION_SOURCES}
for _sec, _names in SECTION_CROSSLIST.items():
    for _n in _names:
        SOURCE_SECTIONS.setdefault(_n, {"news"}).add(_sec)


def sections_of(source):
    return SOURCE_SECTIONS.get(source, {"news"})


def origin_of(source):
    o = SOURCE_ORIGIN.get(source, {})
    return {"lang": o.get("lang", DEFAULT_LANG),
            "country": o.get("country", DEFAULT_COUNTRY)}


class NotModified(Exception):
    pass


_http_cache: dict = {}
_fetched_urls: set = set()  # URLs fetched this run — used to emit the per-shard cache delta


# Sites that 404 any UA not starting "Mozilla/5.0" (VnExpress).
COMPAT_USER_AGENT = "Mozilla/5.0 (compatible; " + USER_AGENT.replace(" (", "; ", 1)


def stale_validator(last_modified, days=7):
    """True if a cached Last-Modified is over `days` old. Some servers keep a
    months-old Last-Modified on fresh content and answer every conditional
    request with 304, so such validators are not sent."""
    try:
        dt = parsedate_to_datetime(last_modified)
    except (TypeError, ValueError):
        return False
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - dt).days > days


def fetch(url, ua=None):
    _fetched_urls.add(url)
    headers = {"User-Agent": COMPAT_USER_AGENT if ua == "compat" else USER_AGENT}
    entry = _http_cache.get(url, {})
    if stale_validator(entry.get("last_modified")):
        entry = {}  # fetch unconditionally
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
            data = resp.read()
            # Some servers gzip even though we don't send Accept-Encoding.
            enc = (resp.headers.get("Content-Encoding") or "").lower()
            if enc == "gzip" or data[:2] == b"\x1f\x8b":
                data = gzip.decompress(data)
            elif enc == "deflate":
                try:
                    data = zlib.decompress(data)
                except zlib.error:
                    data = zlib.decompress(data, -zlib.MAX_WBITS)
            return data.lstrip(b"\xef\xbb\xbf \t\r\n")
    except urllib.error.HTTPError as e:
        if e.code == 304:
            raise NotModified(url)
        raise


def strip_html(text):
    return re.sub(r"<[^>]+>", "", text or "").strip()


def clean_title(text):
    """Normalize a headline for display (#31).

    Feeds hand us titles in two broken shapes: HTML entities that were never
    decoded (`&nbsp;`, `&#039;`, `&quot;`, `&agrave;`), and raw non-breaking /
    zero-width characters. Decode entities first, so an entity-encoded `&nbsp;`
    becomes U+00A0 and is then folded into an ordinary space by the same pass
    that handles literal ones. Titles are HTML-escaped again at render time
    (`escape()` in the page writers), so decoding here is display-only.

    Some feeds double-escape (Vietnamnet ships `&amp;apos;`), so one pass leaves a
    visible `&apos;`. Decode until stable, capped at two passes \u2014 enough for the
    double-escaping seen in the wild, while a headline that genuinely contains the
    literal text `&amp;` survives with at most one level stripped."""
    t = text or ""
    for _ in range(2):
        decoded = unescape(t)
        if decoded == t:
            break
        t = decoded
    t = t.replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
    return re.sub(r"\s+", " ", t).strip()


# Localized month names → English abbreviation, for RSS pubDates that aren't in
# English (e.g. Italian "mer, 17 giu 2026", Spanish "mié, 17 jun 2026"). The
# leading localized weekday is stripped before parsing.
_MONTH_ALIASES = {
    # Italian
    "gen": "Jan", "gennaio": "Jan", "febbraio": "Feb", "marzo": "Mar", "aprile": "Apr",
    "mag": "May", "maggio": "May", "giu": "Jun", "giugno": "Jun", "lug": "Jul", "luglio": "Jul",
    "ago": "Aug", "agosto": "Aug", "set": "Sep", "settembre": "Sep", "ott": "Oct", "ottobre": "Oct",
    "novembre": "Nov", "dic": "Dec", "dicembre": "Dec",
    # Spanish
    "ene": "Jan", "enero": "Jan", "febrero": "Feb", "abr": "Apr", "abril": "Apr",
    "mayo": "May", "jun": "Jun", "junio": "Jun", "jul": "Jul", "julio": "Jul",
    "septiembre": "Sep", "sept": "Sep", "octubre": "Oct", "noviembre": "Nov", "diciembre": "Dec",
}


# RFC 822 offsets written "+07:00" or "GMT+7", which email.utils ignores
# (reading the time as UTC); rewritten to "+0700".
_RFC_TZ = re.compile(r"(\d{1,2}:\d{2}(?::\d{2})?)\s*(?:GMT|UTC)?\s*([+-])(\d{1,2})(?::?(\d{2}))?\s*$")


def parse_date(s):
    """Parse RSS pubDate or ISO/sitemap lastmod into an aware datetime, or None."""
    if not s:
        return None
    s = s.strip()
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        pass
    s = _RFC_TZ.sub(lambda m: f"{m.group(1)} {m.group(2)}{int(m.group(3)):02d}{m.group(4) or '00'}", s)
    try:
        return parsedate_to_datetime(s)
    except (TypeError, ValueError):
        pass
    # Retry with a localized weekday dropped and month names mapped to English.
    s2 = re.sub(r"^[^\s,]+,\s*", "", s)
    s2 = re.sub(r"[A-Za-zÀ-ÿ]+", lambda m: _MONTH_ALIASES.get(m.group(0).lower(), m.group(0)), s2)
    try:
        return parsedate_to_datetime(s2)
    except (TypeError, ValueError):
        return None


def is_today(s):
    """True if the source date falls on today's date in Swiss local time."""
    dt = parse_date(s)
    if dt is None:
        return False
    today = datetime.now(ZURICH).date()
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    elif dt.time() == datetime.min.time():
        # Date-only stamp (local midnight): use the source's own calendar day,
        # or every outlet east of Zurich would always land on yesterday.
        return dt.date() == today
    return dt.astimezone(ZURICH).date() == today


def load_seen(path=SEEN_FILE):
    """All article URLs ever crawled — persists across days to block re-adds."""
    try:
        with open(path, encoding="utf-8") as f:
            return set(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError):
        return set()


def write_json(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(obj, f, ensure_ascii=False, indent=2)


def archive_dates():
    """Sorted (newest first) list of archived crawl dates."""
    names = os.listdir(ARCHIVE_DIR)
    dates = [n[:-5] for n in names if re.fullmatch(r"\d{4}-\d{2}-\d{2}\.json", n)]
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


_ENTITY = re.compile(rb"&(#\d+;|#x[0-9A-Fa-f]+;|[A-Za-z][A-Za-z0-9]*;)?")
_XML_ENTITIES = {b"amp;", b"lt;", b"gt;", b"quot;", b"apos;"}


def _fix_entity(m):
    ref = m.group(1)
    if not ref:  # bare "&", e.g. "AT&T"
        return b"&amp;"
    if ref.startswith(b"#") or ref in _XML_ENTITIES:
        return m.group(0)
    ch = HTML5_ENTITIES.get(ref.decode())  # HTML-only, e.g. &nbsp;
    return b"&#%d;" % ord(ch) if ch and len(ch) == 1 else b"&amp;" + ref


def parse_xml(xml_bytes):
    """Parse XML; retry once with broken entities repaired."""
    try:
        return ET.fromstring(xml_bytes)
    except ET.ParseError:
        return ET.fromstring(_ENTITY.sub(_fix_entity, xml_bytes))


def parse_feed(source, xml_bytes, allow_summary=True):
    root = parse_xml(xml_bytes)
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
            # "date" = dc:date, used by RDF/RSS-1.0 feeds (e.g. Deutsche Welle).
            "published": text_of(item, "pubdate", "published", "updated", "date"),
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
    article_re = re.compile(r"/\d+/[^/]+/([^/]+)/![A-Z0-9]+$")
    rows = drupal_newest_rows(pages, article_re)
    return crawl_sitemap_source("WOZ", rows, article_re, 50)


def drupal_newest_rows(pages, match):
    """Rows from the last two sitemap pages (new items straddle them)."""
    rows = []
    for _, url in sorted(pages)[-2:]:
        try:
            rows += sitemap_rows(fetch(url), match)
        except NotModified:
            pass  # unchanged page: handled on an earlier run
    rows.sort(reverse=True)
    return rows


def crawl_tachles():
    """Daily news only in the Drupal sitemap; /feed is the weekly issue."""
    index = ET.fromstring(fetch("https://www.tachles.ch/sitemap.xml"))
    pages = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text:
            m = re.search(r"[?&]page=(\d+)", loc.text)
            if m:
                pages.append((int(m.group(1)), loc.text))
    if not pages:
        raise ValueError("no sitemap pages found")
    news_re = re.compile(r"/artikel/news/([^/]+)$")
    return crawl_sitemap_source("Tachles", drupal_newest_rows(pages, news_re), news_re, 50)


def crawl_bauernzeitung():
    """sitemapindex of gzipped monthly sitemaps (news-YYYY-MM.xml.gz) — newest
    articles are in the highest YYYY-MM. Article URLs are /artikel/[category/]slug;
    older ones still carry a trailing numeric id (optionally prefixed -0), which is
    stripped for the title."""
    index = ET.fromstring(fetch(BAUERNZEITUNG_SITEMAP))
    months = []
    for loc in index.iter():
        if local(loc) == "loc" and loc.text:
            m = re.search(r"/news-(\d{4})-(\d{2})\.xml", loc.text)
            if m:
                months.append((m.group(1) + m.group(2), loc.text))
    if not months:
        raise ValueError("no monthly news sitemap entries found")
    newest = max(months)[1]
    rows = sitemap_rows(fetch(newest), re.compile(r"/artikel/"))
    return crawl_sitemap_source(
        "Bauernzeitung", rows,
        re.compile(r"/artikel/(?:[^/]+/)*([^/]+?)(?:-0)?(?:-\d{5,})?/?$"),
        BAUERNZEITUNG_MAX)


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
    "Tages-Anzeiger": "#e6e6e6",
    "NZZ": "#e6e6e6",
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
    "Republik": "#6f6f6f",
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
    "Schaffhauser Nachrichten": "#1a4f8b",
    "Schweizer Monat": "#8a6d3b",
    "Bote der Urschweiz": "#b8242a",
    "Tagesschau": "#0a3b75",
    "Süddeutsche": "#222a5c",
    "FAZ": "#2b2b2b",
    "Die Welt": "#1a6cb4",
    "taz": "#c0123c",
    "n-tv": "#c8102e",
    "Der Spiegel": "#e64415",
    "Stern": "#e3000f",
    "DW": "#00a8e1",
    "Bild": "#d00000",
    "Le Monde": "#0f0f0f",
    "Le Figaro": "#0b3d63",
    "Libération": "#cf0a2c",
    "franceinfo": "#0a4d8c",
    "France 24": "#142a6b",
    "RFI": "#e30613",
    "L'Express": "#b31217",
    "L'Obs": "#7a1fa2",
    "La Croix": "#2e5894",
    "20 Minutes": "#e01b22",
    "La Tribune": "#c0392b",
    "BFM TV": "#d81e2c",
    "Mediapart": "#a31515",
    "BBC News": "#bb1919",
    "The Guardian": "#052962",
    "The Independent": "#e0301e",
    "The Telegraph": "#0a4f73",
    "Sky News": "#0a4b9f",
    "Daily Mail": "#004db3",
    "Mirror": "#d70b29",
    "Metro": "#009b77",
    "Evening Standard": "#a01441",
    "Financial Times": "#990f3d",
    "The New York Times": "#333333",
    "NPR": "#2b6cb0",
    "ABC News": "#1b3a6b",
    "NBC News": "#6a5acd",
    "Fox News": "#003366",
    "The Hill": "#2e6e4e",
    "Washington Post": "#5d5d5d",
    "LA Times": "#252a5c",
    "la Repubblica": "#b01217",
    "ANSA": "#b22222",
    "Il Giornale": "#15406b",
    "Il Sole 24 Ore": "#cf7a3f",
    "El Mundo": "#163a6b",
    "ABC": "#d11a2a",
    "elDiario.es": "#0098c3",
    "20minutos": "#e8400c",
    "El Confidencial": "#c20e1a",
    "NOS": "#cd2129", "NU.nl": "#c81e1e",
    "VRT NWS": "#0084c6",
    "ORF": "#d12421", "Der Standard": "#a51d2d",
    "RTP": "#00a499",
    "RTÉ": "#00843d",
    "Onet": "#cc0000", "WP.pl": "#d6293e",
    "SVT": "#d72b2b", "Aftonbladet": "#ff5a00",
    "NRK": "#00457c", "VG": "#d0021b",
    "DR": "#00306b",
    "YLE": "#0091cd", "Iltalehti": "#d81e2c",
    "To Vima": "#0a3d8f",
    "Novinky": "#cc1122", "ČT24": "#0066b3",
    "Telex": "#e8870c", "HVG": "#c2122a",
    "Digi24": "#00529b", "HotNews": "#b22234",
    "Ukrainska Pravda": "#d52027",
    "Hürriyet": "#d40511",
    "Radio-Canada": "#c9252b",
    "La Jornada": "#8a0303",
    "G1": "#c4170c", "Folha": "#b9121b",
    "La Nación": "#0a2c6b",
    "El Tiempo": "#003da5",
    "RPP": "#d6001c",
    "ABC News AU": "#1a7fc4", "SMH": "#163a5e",
    "RNZ": "#006bb6",
    "The Hindu": "#b8242a", "NDTV": "#d11a1a",
    "NHK": "#0a5fa0",
    "Yonhap": "#0a47a0",
    "Straits Times": "#102a54", "CNA": "#e01a2b",
    "Rappler": "#ee7203", "Inquirer": "#14559e",
    "VnExpress": "#8f1d22",
    "Dawn": "#c8202a",
    "Jerusalem Post": "#1d4e8f",
    "Al Jazeera": "#f59e0b",
    "SCMP": "#d99e00",
    # ===== Core-country expansion =====
    "Handelsblatt": "#d2820a", "Tagesspiegel": "#c8102e", "Frankfurter Rundschau": "#d1101a",
    "Heise": "#cc3333", "WirtschaftsWoche": "#1a3c6e", "Manager Magazin": "#003a5d",
    "RP Online": "#c20012", "Merkur": "#006bb3", "MDR": "#0a64a0", "Berliner Zeitung": "#b01217",
    "t-online": "#e2001a", "Stuttgarter Zeitung": "#1f6cb0",
    "Courrier International": "#2b2b6b", "La Dépêche": "#d6001c", "France Inter": "#ab1f24",
    "Europe 1": "#d40d17", "Slate FR": "#6a1b9a", "Challenges": "#0a6b3a", "France Bleu": "#1d3f8f",
    "Numerama": "#6c3fc4", "Télérama": "#d63d6a", "HuffPost FR": "#2a8c4a",
    "Daily Star": "#e3001b", "iNews": "#d6293e", "City AM": "#d9008b", "New Statesman": "#b8242a",
    "Wales Online": "#c8344a", "The Scotsman": "#1a3a6b", "The Herald": "#0a4f73",
    "Manchester Evening News": "#d6001c", "Belfast Telegraph": "#0a3a6b", "The Conversation": "#d6601a",
    "CBS News": "#0073c8", "CNBC": "#005594", "The Atlantic": "#1d1d1d", "Vox": "#f7c948",
    "The Verge": "#5200ff", "TechCrunch": "#0a9e01", "Newsweek": "#c8102e", "PBS NewsHour": "#2638c4",
    "NY Post": "#cf1f2e", "The Daily Beast": "#e2001a", "Wired": "#2b2b2b", "ProPublica": "#d9382b",
    "Rai News": "#0a64a0", "Adnkronos": "#c8102e", "TGcom24": "#e2001a", "Open": "#1f1f1f",
    "Il Giorno": "#b01217", "Il Resto del Carlino": "#15406b", "La Nazione": "#1a6b3a",
    "AGI": "#0a4f9e", "Today": "#e2541b", "Il Mattino": "#c20012",
    "Il Messaggero": "#0a3a6b", "Il Gazzettino": "#1a5276", "Quotidiano.net": "#2e6da4",
    "askanews": "#b8242a", "Domani": "#cf1f2e",
    "El Español": "#c8102e", "COPE": "#003a8c", "Europa Press": "#0a6bb3", "Marca": "#e2001a",
    "Expansión": "#d6a400", "La Vanguardia": "#2b2b2b", "El Correo": "#b8242a", "infoLibre": "#1a6b9e",
    "Mundo Deportivo": "#cf1f2e", "El Salto": "#d6001c", "Las Provincias": "#1a6bb3",
    "La Verdad": "#c8344a", "Ideal": "#0a6b4a", "Diario Sur": "#1a8cc4", "El Diario Vasco": "#1a5276",
    "Newtral": "#00b3a4", "Maldita": "#1ab34a", "El Independiente": "#2b2b6b",
    # China & Russia
    "CGTN": "#c4161c", "China Digital Times": "#d35400",
    "TASS": "#0a4b9f", "RT": "#3d8b37", "RIA Novosti": "#1f5fa6",
    "Meduza": "#e0533f", "The Moscow Times": "#c8102e",
    "Novaya Gazeta Europe": "#9b1c1c", "Mediazona": "#cc2222",
    # South Korea (Korean-language)
    "Chosun Ilbo": "#1a4b8c", "Donga Ilbo": "#0d3b7a", "Donga Politics": "#265a99",
    "Donga Economy": "#3f77b8", "ETNews": "#0a7bc4", "ETNews IT": "#2f97dd",
    "Hankyung": "#1b4f9c", "Hankyung Politics": "#3a6cba", "Kyunghyang": "#1f7a4d",
    "Kyunghyang Politics": "#2f9663", "Kyunghyang Economy": "#47b07c",
    "Money Today": "#c8102e", "Newsis Politics": "#004a99", "Newsis Economy": "#1c68b3",
    "Newsis Society": "#3f86cc", "Nocut News": "#e2401a", "OhmyNews": "#00a04a",
    "Pressian": "#8c2f8c", "Segye Ilbo": "#0b5ea8", "Seoul Shinmun": "#14508c",
    "Sisa Journal": "#7a2f5e", "Yonhap News": "#0a4d9e", "Yonhap Politics": "#2469bc",
    "Yonhap Economy": "#4585d1",
    "Chungcheong Today": "#2b7a6b", "Incheon Ilbo": "#1a6fa8", "Jeju Sori": "#e07b1a",
    "Jeonnam Ilbo": "#1f8c5a", "Kangwon Domin Ilbo": "#2f6bb3", "Kyongbuk Ilbo": "#b8442a",
    "Ulsan Jeil Ilbo": "#0f7a8c",
    # Chile
    "La Tercera": "#d6001c", "BioBioChile": "#0a7ac4", "La Discusión": "#1a5276",
    "Publimetro Chile": "#e2541b", "El Mostrador": "#2b2b2b", "Cooperativa": "#004a99",
    "Meganoticias": "#c8102e", "El Dínamo": "#e0a800", "Diario Concepción": "#1f6b4a",
    # Peru
    "El Comercio Perú": "#b8942a", "Gestión": "#0a5c7a", "Diario Correo": "#c41230",
    "La República Perú": "#d6001c", "La República Política": "#e2402a",
    "La República Sociedad": "#ea6a4a",
    # Mexico (regional) & Qatar
    "El Informador": "#1a4f8b", "Diario de Yucatán": "#0a6b4a", "La Voz de Michoacán": "#8c2f2f",
    "Periódico AM": "#c8102e", "Vanguardia MX": "#1b5e9c", "Crónica": "#a8102e",
    "Doha News": "#6b2f8c",
    # Colombia (regional)
    "La Opinión": "#0a5ca8", "El Heraldo CO": "#e2001a", "El Universal Cartagena": "#1a6b8c",
    "Vanguardia CO": "#c41230",
    # Japan (regional + business) and Israel (Hebrew)
    "Toyo Keizai": "#1a4f8b", "Bunshun": "#8c2f2f", "Kyoto Shimbun": "#7a2f6b",
    "Okinawa Times": "#0a8ca8", "Fukui Shimbun": "#2f6bb3", "Saga Shimbun": "#1f7a5a",
    "Kumamoto Nichinichi": "#b8442a", "Akita Sakigake": "#1a5f9c", "Chiba Nippo": "#0f7a8c",
    "Shikoku Shimbun": "#2b7a4a", "Chunichi Shimbun": "#0d4b8c", "Chugoku Shimbun": "#c8102e",
    "Arutz Sheva HE": "#1a4b8c", "Davar": "#c8102e", "Israel Hayom HE": "#0a5ca8",
    "Shakuf": "#2f8c6b",
    # Assorted regional additions
    "Dagsavisen": "#d6001c", "Newsroom NZ": "#1a6b8c",
    "Diário de Notícias da Madeira": "#1f6b4a", "Correio Braziliense": "#0a4f9e",
    "Birmingham Live": "#c41230", "Gazeta Wyborcza": "#b8242a",
}


def color_for(source):
    """Badge color: the brand color if listed, else a stable hashed hue so every
    source gets a distinct color without hand-listing all of them (mirrors
    colorFor() in script.js)."""
    if source in SOURCE_COLORS:
        return SOURCE_COLORS[source]
    return f"hsl({djb2(source) % 360}, 65%, 45%)"


def text_color(bg):
    """Pill text colour: black on a light background, white on a dark one
    (mirrors textColor() in script.js)."""
    if not (bg.startswith("#") and len(bg) == 7):
        return "#fff"  # hashed hsl() colours are always dark enough
    r, g, b = int(bg[1:3], 16), int(bg[3:5], 16), int(bg[5:7], 16)
    return "#000" if (0.299 * r + 0.587 * g + 0.114 * b) / 255 > 0.6 else "#fff"


def fmt_datetime(iso_str):
    """Format ISO datetime as Swiss local time YYYY-MM-DD HH:MM:SS (mirrors fmtDateTime in script.js)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ZURICH).strftime("%Y-%m-%d %H:%M:%S")
    except (ValueError, TypeError):
        return ""


DE_MONTHS = ["", "Jan.", "Feb.", "März", "Apr.", "Mai", "Juni",
             "Juli", "Aug.", "Sept.", "Okt.", "Nov.", "Dez."]

# Icons reference a shared <symbol> sprite (defined once in template.html) via
# <use>, instead of inlining the full SVG on every row. Mirrors the
# OPEN_SVG/LINK_SVG/HIDE_BTN constants in script.js.


def fmt_time(iso_str):
    """Format ISO datetime as Swiss local HH:MM (mirrors fmtTime in script.js)."""
    if not iso_str:
        return ""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt.astimezone(ZURICH).strftime("%H:%M")
    except (ValueError, TypeError):
        return ""


def fmt_day_heading(date_iso):
    """'2026-08-08' -> '08. Aug. 2026'."""
    try:
        y, m, d = date_iso.split("-")
        return f"{d}. {DE_MONTHS[int(m)]} {y}"
    except (ValueError, IndexError):
        return date_iso


EN_MONTHS = ["", "January", "February", "March", "April", "May", "June",
             "July", "August", "September", "October", "November", "December"]


def fmt_day_en(date_iso):
    """'2026-08-08' -> '8 August 2026' (English, for SEO meta tags)."""
    try:
        y, m, d = date_iso.split("-")
        return f"{int(d)} {EN_MONTHS[int(m)]} {y}"
    except (ValueError, IndexError):
        return date_iso


# Action icons next to each row on hover (mirror OPEN_SVG / LINK_SVG in script.js).
OPEN_SVG = '<svg width="18" height="18"><use href="#ico-arrow"/></svg>'
LINK_SVG = '<svg width="18" height="18"><use href="#ico-link"/></svg>'
# Eye toggle next to the time: hides this source from the feed (mirrors HIDE_BTN
# in script.js). Replaces the old external-link arrow per #4.
HIDE_BTN = ('<button class="hide-src" type="button" aria-label="Hide source"'
            ' title="Hide source"><svg width="14" height="14"><use href="#ico-eye-off"/></svg></button>')


def portal_home(url):
    """Origin (scheme://host) of an article URL — the source portal's home (#4)."""
    try:
        parts = urlsplit(url)
        if parts.scheme and parts.netloc:
            return f"{parts.scheme}://{parts.netloc}"
    except ValueError:
        pass
    return url

_SLUG_TRANSLIT = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss"})


def slugify(s):
    """Lowercase ASCII slug (mirrors slugify() in script.js)."""
    s = (s or "").lower().translate(_SLUG_TRANSLIT)
    s = re.sub(r"[^a-z0-9]+", "-", s).strip("-")
    return s[:60].strip("-")


def djb2(s):
    """djb2 hash, 32-bit (mirrors djb2() in script.js)."""
    h = 5381
    for ch in s:
        h = ((h * 33) + ord(ch)) & 0xFFFFFFFF
    return h


def article_id(article):
    """Stable per-article anchor id (mirrors articleId() in script.js)."""
    return f"{slugify(article['title'])}-{djb2(article['url']):x}"


def render_article_html(article):
    color = color_for(article["source"])
    fg = text_color(color)
    url = escape(article["url"])
    home = escape(portal_home(article["url"]))
    return (
        f'      <li class="article" id="{escape(article_id(article))}"'
        f' data-lang="{escape(article.get("lang", DEFAULT_LANG))}"'
        f' data-country="{escape(article.get("country", DEFAULT_COUNTRY))}">'
        '<div class="meta-col">'
        f'<a class="source" href="{home}" target="_blank" rel="noopener nofollow ugc"'
        f' style="background:{color};color:{fg}">{escape(article["source"])}</a>'
        f'<span class="time">{escape(fmt_time(article.get("published", "")))} {HIDE_BTN}</span>'
        '</div>'
        f'<a class="title" href="{url}" target="_blank" rel="noopener nofollow ugc">{escape(article["title"])}</a>'
        '<div class="row-actions">'
        # Visible labels repeat on every row; the accessible name carries the headline
        # so a screen reader announces which article the action belongs to (#49).
        f'<a class="row-act open" href="{url}" target="_blank" rel="noopener nofollow ugc"'
        f' aria-label="Open article: {escape(article["title"])}"><span class="label">Open article</span> {OPEN_SVG}</a>'
        f'<button class="row-act share" type="button"'
        f' aria-label="Share article: {escape(article["title"])}"><span class="label">Share article</span> {LINK_SVG}</button>'
        '</div>'
        '</li>'
    )


def render_older_dates(dates):
    """The most recent days as date rows, then a link to the full archive."""
    chev = ('<svg class="chev" viewBox="0 0 24 24" width="22" height="22" fill="none" '
            'stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>')
    arrow = ('<svg class="chev" viewBox="0 0 24 24" width="22" height="22" fill="none" '
             'stroke="currentColor" stroke-width="2"><path d="M9 6l6 6-6 6"/></svg>')
    rows = [
        f'      <a class="day-row" href="/archive/{d}.html">'
        f'<span>{fmt_day_heading(d)}</span>{chev}</a>'
        for d in dates
    ]
    rows.append(
        # /archive, not /archive.html: Pages strips the extension and 308s, so the
        # .html form costs every visitor and crawler an extra hop.
        '      <a class="day-row day-row-all" href="/archive">'
        f'<span>Full archive</span>{arrow}</a>'
    )
    return "\n".join(rows)


def write_colors_js():
    pairs = ",\n  ".join(f'"{k}": "{v}"' for k, v in SOURCE_COLORS.items())
    with open("colors.js", "w", encoding="utf-8") as f:
        f.write(f"const SOURCE_COLORS = {{\n  {pairs}\n}};\n")


AD_EVERY = 25  # insert an ad slot after every N articles (mirrors AD_EVERY in script.js)
AD_SLOT = '      <li class="ad-slot">Werbung</li>'
# index.html server-renders only the newest SSR_LIMIT articles for a fast first
# paint; script.js lazy-renders the rest from crawled.json on scroll. Archive
# pages are not capped. Keep this >= a couple of screens of rows for SEO.
SSR_LIMIT = 120
# Archive days are split into static pages of this many articles, so a single
# page never balloons regardless of how many sources we add (more sources just
# mean more pages). Each page is fully server-rendered → crawlable for SEO.
ARCHIVE_PAGE_SIZE = 500


def write_rendered_html(articles, dest_path, *, title, description, canonical,
                        date_heading, older_dates=(), limit=None, count=None,
                        pager="", head_links="", html_lang="en", archive_link=True):
    """Render a page. `limit` caps the server-rendered rows (index.html lazy-loads
    the rest); `count` overrides the badge total (so a paginated archive page shows
    the whole day's count); `pager`/`head_links` add archive pagination chrome;
    `html_lang` sets <html lang>; landing pages pass their own language, everything
    else (home, archive index, archive days) is an English page listing many
    languages, so the default is "en". `archive_link=False` drops the bottom
    date/archive nav (sections have no archive pages)."""
    with open("template.html", encoding="utf-8") as f:
        tmpl = f.read()
    articles = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    total = count if count is not None else len(articles)
    head = articles[:limit] if limit else articles
    rows = []
    for i, a in enumerate(head):
        rows.append(render_article_html(a))
        if (i + 1) % AD_EVERY == 0 and i + 1 < len(head):
            rows.append(AD_SLOT)
    items = "\n".join(rows)
    os.makedirs(os.path.dirname(dest_path) or ".", exist_ok=True)
    html = (tmpl
            .replace("<!-- HTMLLANG -->", escape(html_lang))
            .replace("<!-- TITLE -->", escape(title))
            .replace("<!-- DESCRIPTION -->", escape(description))
            .replace("<!-- CANONICAL -->", escape(canonical))
            .replace("<!-- HEAD_LINKS -->", head_links)
            .replace("<!-- COUNT -->", str(total))
            .replace("<!-- DATE_HEADING -->", escape(date_heading))
            .replace("<!-- OLDER_DATES -->", render_older_dates(older_dates) if archive_link else "")
            .replace("<!-- PAGER -->", pager)
            .replace("<!-- ARTICLES -->", items))
    with open(dest_path, "w", encoding="utf-8") as f:
        f.write(html)


def archive_page_path(date, p):
    """Filesystem path for archive day `date`, page `p` (page 1 keeps the bare name)."""
    return os.path.join(ARCHIVE_DIR, f"{date}.html" if p == 1 else f"{date}-{p}.html")


def archive_page_url(date, p):
    return f"/archive/{date}.html" if p == 1 else f"/archive/{date}-{p}.html"


def render_pager(date, p, pages):
    """Numbered prev/next pager (windowed: first, last, current±2)."""
    if pages <= 1:
        return ""
    parts = []
    if p > 1:
        parts.append(f'<a class="pg" href="{archive_page_url(date, p-1)}" rel="prev">‹</a>')
    nums = sorted(set([1, pages] + list(range(max(1, p - 2), min(pages, p + 2) + 1))))
    prev = 0
    for n in nums:
        if n - prev > 1:
            parts.append('<span class="pg-gap">…</span>')
        if n == p:
            parts.append(f'<span class="pg pg-cur" aria-current="page">{n}</span>')
        else:
            parts.append(f'<a class="pg" href="{archive_page_url(date, n)}">{n}</a>')
        prev = n
    if p < pages:
        parts.append(f'<a class="pg" href="{archive_page_url(date, p+1)}" rel="next">›</a>')
    return '<nav class="pager" aria-label="Archive pages">' + "".join(parts) + "</nav>"


def write_archive_day(date, articles):
    """Write a day's archive as one or more paginated static pages. Returns the
    page count. Newest-first; each page fully SSR'd and self-canonical."""
    articles = sorted(articles, key=lambda a: a.get("published", ""), reverse=True)
    total = len(articles)
    pages = max(1, -(-total // ARCHIVE_PAGE_SIZE))  # ceil
    day_en, day_de = fmt_day_en(date), fmt_day_heading(date)
    for p in range(1, pages + 1):
        sl = articles[(p - 1) * ARCHIVE_PAGE_SIZE: p * ARCHIVE_PAGE_SIZE]
        url = archive_page_url(date, p)
        head = []
        if p > 1:
            head.append(f'<link rel="prev" href="{SITE_ORIGIN}{archive_page_url(date, p-1)}">')
        if p < pages:
            head.append(f'<link rel="next" href="{SITE_ORIGIN}{archive_page_url(date, p+1)}">')
        title = (f"News Archive for {day_en} – all.news" if p == 1
                 else f"News Archive for {day_en} (page {p}) – all.news")
        write_rendered_html(
            sl, archive_page_path(date, p),
            title=title,
            description=f"All world news headlines collected on {day_en} by all.news.",
            canonical=f"{SITE_ORIGIN}{url}",
            date_heading=day_de, older_dates=[], count=total,
            pager=render_pager(date, p, pages), head_links="".join(head))
    return pages


# Static pages (legal, about) — listed without lastmod.
STATIC_PAGES = ["/about", "/privacy", "/cookies", "/terms", "/imprint"]


def write_sitemap(dates, landing_urls=(), page_counts=None, now_iso=None, section_urls=()):
    """Write sitemap.xml. `page_counts` maps a date to its number of archive pages
    (from index.json) so days split across several pages list all of them, not just
    page 1 — pages 2+ are otherwise reachable only through the pager. `lastmod` is
    the crawl timestamp for the hourly pages and the day itself for settled archive
    days; it is the one hint here Google actually acts on (changefreq/priority are
    ignored), and it is what tells it this feed is worth recrawling."""
    page_counts = page_counts or {}
    now = datetime.now(ZURICH)
    today = now.date().isoformat()
    fresh = now_iso or now.isoformat(timespec="seconds")

    def url(loc, lastmod, changefreq, priority):
        mod = f"<lastmod>{lastmod}</lastmod>" if lastmod else ""
        return (f'  <url><loc>{SITE_ORIGIN}{loc}</loc>{mod}'
                f'<changefreq>{changefreq}</changefreq><priority>{priority}</priority></url>')

    urls = [
        url("/", fresh, "hourly", "1.0"),
        url("/news/", fresh, "daily", "0.6"),
        # /archive.html is served at /archive (Pages strips the extension); list the
        # URL that answers 200 so the sitemap doesn't hand Google a redirect.
        url("/archive", fresh, "daily", "0.5"),
    ]
    for u in section_urls:
        urls.append(url(u, fresh, "hourly", "0.8"))
    for u in landing_urls:
        urls.append(url(u, fresh, "hourly", "0.6"))
    for u in STATIC_PAGES:
        urls.append(url(u, None, "yearly", "0.2"))
    for d in dates:
        # Today's day page is still being appended to; past days are settled.
        current = d == today
        for p in range(1, page_counts.get(d, 1) + 1):
            urls.append(url(archive_page_url(d, p), fresh if current else d,
                            "hourly" if current else "never", "0.3"))
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.write("\n".join(urls))
        f.write("\n</urlset>\n")


# ---- Programmatic landing pages: /news/<country>/<lang>/ --------------------
# One server-rendered page per (country, language) we carry. The SPA renders an
# empty <ul> to search-engine bots (the article list is client-rendered), so these
# static pages give crawlers real headlines for each slice. They hydrate:
# script.js recognises the /news/<country>/<lang>/ path, loads the country shard
# and applies the filter, so the page is fully interactive for humans. A /news/
# hub links every page (internal linking so the pages get discovered + crawled).
# The (country, lang) matrix is derived from the source config (jobs_for), not
# from a single day's feed, so the URL set is stable across runs.
LANDING_DIR = "news"

# ISO 3166-1 alpha-2 -> English name (mirrors COUNTRY_NAMES in script.js).
COUNTRY_NAMES = {
    "CH": "Switzerland", "DE": "Germany", "FR": "France",
    "GB": "United Kingdom", "US": "United States", "IT": "Italy", "ES": "Spain",
    "NL": "Netherlands", "BE": "Belgium", "AT": "Austria", "PT": "Portugal", "IE": "Ireland",
    "PL": "Poland", "SE": "Sweden", "NO": "Norway", "DK": "Denmark", "FI": "Finland",
    "GR": "Greece", "CZ": "Czechia", "HU": "Hungary", "RO": "Romania", "UA": "Ukraine", "TR": "Turkey",
    "CA": "Canada", "MX": "Mexico", "BR": "Brazil", "AR": "Argentina", "CO": "Colombia", "PE": "Peru",
    "CL": "Chile",
    "AU": "Australia", "NZ": "New Zealand",
    "IN": "India", "JP": "Japan", "KR": "South Korea", "SG": "Singapore", "ID": "Indonesia",
    "PH": "Philippines", "VN": "Vietnam", "PK": "Pakistan", "IL": "Israel", "QA": "Qatar", "HK": "Hong Kong",
    "CN": "China", "RU": "Russia",
}
# ISO 639-1 -> English name (mirrors LANG_EN_NAMES in script.js). Kept in English
# (not the language's own name) so every slug is clean ASCII.
LANG_EN_NAMES = {
    "de": "German", "fr": "French", "en": "English", "it": "Italian", "es": "Spanish",
    "nl": "Dutch", "pt": "Portuguese", "pl": "Polish", "sv": "Swedish", "no": "Norwegian",
    "da": "Danish", "fi": "Finnish", "el": "Greek", "cs": "Czech", "hu": "Hungarian", "ro": "Romanian",
    "uk": "Ukrainian", "tr": "Turkish", "ja": "Japanese", "id": "Indonesian", "vi": "Vietnamese",
    "he": "Hebrew", "ar": "Arabic", "zh": "Chinese", "ru": "Russian", "ko": "Korean",
    "hi": "Hindi", "bn": "Bengali", "ta": "Tamil", "te": "Telugu", "mr": "Marathi",
    "ml": "Malayalam", "kn": "Kannada", "gu": "Gujarati", "pa": "Punjabi", "ur": "Urdu",
    "ms": "Malay", "tl": "Filipino", "th": "Thai", "ca": "Catalan",
    "bg": "Bulgarian", "et": "Estonian", "fa": "Persian", "hr": "Croatian", "lt": "Lithuanian",
    "lv": "Latvian", "sk": "Slovak", "sr": "Serbian",
}


def slugify(s):
    """Lowercase ASCII slug: 'United Kingdom' -> 'united-kingdom'. Mirrors slugify() in script.js."""
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")


def country_slug(cc):
    return slugify(COUNTRY_NAMES.get(cc.upper(), cc))


def lang_slug(lang):
    return slugify(LANG_EN_NAMES.get(lang.lower(), lang))


def landing_url(cc, lang):
    return f"/{LANDING_DIR}/{country_slug(cc)}/{lang_slug(lang)}/"


def landing_path(cc, lang):
    return os.path.join(LANDING_DIR, country_slug(cc), lang_slug(lang), "index.html")


def known_country_lang_pairs():
    """Every (country, lang) our sources can produce, derived from the source config
    so the landing URL set is stable regardless of what any single day carries."""
    pairs = set()
    for name, _ in jobs_for(None):
        if "news" not in sections_of(name):
            continue
        o = origin_of(name)
        pairs.add((o["country"].upper(), o["lang"].lower()))
    return sorted(pairs)


# ---- Landing-page localization ---------------------------------------------
# Titles/descriptions/headings are written in the page's own language (a localized
# <title> ranks far better in-country than an English one). The URL slugs stay
# English/ASCII (see country_slug/lang_slug), so localizing the copy needs no URL
# churn. Country names are given in the page language; the sentence templates use a
# "{c}: …" lead so the country name always stays nominative (no per-language
# declension). English falls back to COUNTRY_NAMES. Best-effort translations —
# easy to refine per language.
COUNTRY_I18N = {
    "de": {"AT": "Österreich", "CH": "Schweiz", "DE": "Deutschland"},
    "fr": {"BE": "Belgique", "CA": "Canada", "CH": "Suisse", "FR": "France"},
    "es": {"AR": "Argentina", "CL": "Chile", "CO": "Colombia", "ES": "España", "MX": "México", "PE": "Perú"},
    "pt": {"BR": "Brasil", "PT": "Portugal"},
    "nl": {"BE": "België", "NL": "Nederland"},
    "it": {"IT": "Italia"},
    "el": {"GR": "Ελλάδα"},
    "cs": {"CZ": "Česko"},
    "da": {"DK": "Danmark"},
    "fi": {"FI": "Suomi"},
    "no": {"NO": "Norge"},
    "sv": {"FI": "Finland", "SE": "Sverige"},
    "pl": {"PL": "Polska"},
    "hu": {"HU": "Magyarország"},
    "ro": {"RO": "România"},
    "uk": {"UA": "Україна"},
    "ru": {"RU": "Россия", "UA": "Украина"},
    "ko": {"KR": "대한민국"},
    "ca": {"ES": "Espanya"},
    "tr": {"TR": "Türkiye"},
    "id": {"ID": "Indonesia"},
    "vi": {"VN": "Việt Nam"},
    "ja": {"JP": "日本"},
    "zh": {"CN": "中国", "HK": "香港", "SG": "新加坡"},
    "ar": {"IL": "إسرائيل", "QA": "قطر"},
    "hi": {"IN": "भारत"},
    "bn": {"IN": "ভারত"},
    "ta": {"IN": "இந்தியா", "SG": "சிங்கப்பூர்"},
    "te": {"IN": "భారతదేశం"},
    "mr": {"IN": "भारत"},
    "ml": {"IN": "ഇന്ത്യ"},
    "kn": {"IN": "ಭಾರತ"},
    "gu": {"IN": "ભારત"},
    "pa": {"IN": "ਭਾਰਤ"},
    "ur": {"IN": "بھارت", "PK": "پاکستان"},
    "ms": {"SG": "Singapura"},
    "tl": {"PH": "Pilipinas"},
    "he": {"IL": "ישראל"},
}
# Per-language copy. "t" = title/heading phrase ({c} = localized country name),
# "d" = meta description. Brand + date are appended by the caller.
LANDING_STRINGS = {
    "en": {"t": "{c} News",
           "d": "{c}: today's top news headlines, updated hourly. all.news gathers the country's leading news sources in one place — every major story at a glance."},
    "de": {"t": "{c}: Nachrichten",
           "d": "{c}: aktuelle Nachrichten und Schlagzeilen, stündlich aktualisiert. all.news bündelt die führenden Nachrichtenquellen des Landes an einem Ort."},
    "fr": {"t": "{c} : actualités",
           "d": "{c} : l'actualité et les titres du jour, mis à jour chaque heure. all.news rassemble les principales sources d'information du pays en un seul endroit."},
    "it": {"t": "{c}: notizie",
           "d": "{c}: le notizie e i titoli di oggi, aggiornati ogni ora. all.news riunisce le principali fonti d'informazione del Paese in un unico posto."},
    "es": {"t": "{c}: noticias",
           "d": "{c}: las noticias y titulares de hoy, actualizados cada hora. all.news reúne las principales fuentes de información del país en un solo lugar."},
    "pt": {"t": "{c}: notícias",
           "d": "{c}: as notícias e manchetes de hoje, atualizadas a cada hora. all.news reúne as principais fontes de informação do país num só lugar."},
    "nl": {"t": "{c}: nieuws",
           "d": "{c}: het nieuws en de koppen van vandaag, elk uur bijgewerkt. all.news bundelt de belangrijkste nieuwsbronnen van het land op één plek."},
    "pl": {"t": "{c}: wiadomości",
           "d": "{c}: najważniejsze wiadomości i nagłówki dnia, aktualizowane co godzinę. all.news gromadzi czołowe źródła informacji z całego kraju w jednym miejscu."},
    "sv": {"t": "{c}: nyheter",
           "d": "{c}: dagens nyheter och rubriker, uppdateras varje timme. all.news samlar landets ledande nyhetskällor på ett ställe."},
    "no": {"t": "{c}: nyheter",
           "d": "{c}: dagens nyheter og overskrifter, oppdatert hver time. all.news samler landets ledende nyhetskilder på ett sted."},
    "da": {"t": "{c}: nyheder",
           "d": "{c}: dagens nyheder og overskrifter, opdateret hver time. all.news samler landets førende nyhedskilder ét sted."},
    "fi": {"t": "{c}: uutiset",
           "d": "{c}: päivän uutiset ja otsikot, päivittyy tunneittain. all.news kokoaa maan johtavat uutislähteet yhteen paikkaan."},
    "el": {"t": "{c}: ειδήσεις",
           "d": "{c}: οι ειδήσεις και οι τίτλοι της ημέρας, με ανανέωση κάθε ώρα. Το all.news συγκεντρώνει τις κορυφαίες πηγές ειδήσεων της χώρας σε ένα μέρος."},
    "cs": {"t": "{c}: zprávy",
           "d": "{c}: dnešní zprávy a titulky, aktualizováno každou hodinu. all.news shromažďuje přední zpravodajské zdroje země na jednom místě."},
    "hu": {"t": "{c}: hírek",
           "d": "{c}: a nap hírei és címlapsztorijai, óránként frissítve. Az all.news egy helyre gyűjti az ország vezető hírforrásait."},
    "ro": {"t": "{c}: știri",
           "d": "{c}: știrile și titlurile zilei, actualizate din oră în oră. all.news reunește principalele surse de știri din țară într-un singur loc."},
    "uk": {"t": "{c}: новини",
           "d": "{c}: головні новини та заголовки дня, оновлюється щогодини. all.news збирає провідні джерела новин країни в одному місці."},
    "ru": {"t": "{c}: новости",
           "d": "{c}: главные новости и заголовки дня, обновляется каждый час. all.news собирает ведущие источники новостей страны в одном месте."},
    "tr": {"t": "{c}: haberler",
           "d": "{c}: günün haberleri ve manşetleri, her saat güncellenir. all.news ülkenin önde gelen haber kaynaklarını tek bir yerde toplar."},
    "id": {"t": "{c}: berita",
           "d": "{c}: berita dan berita utama hari ini, diperbarui setiap jam. all.news mengumpulkan sumber berita terkemuka dari seluruh negeri dalam satu tempat."},
    "vi": {"t": "{c}: tin tức",
           "d": "{c}: tin tức và tiêu đề nổi bật hôm nay, cập nhật hằng giờ. all.news tập hợp các nguồn tin hàng đầu của quốc gia ở một nơi."},
    "ja": {"t": "{c}のニュース",
           "d": "{c}：今日の主要ニュースと見出しを毎時更新。all.news は国内の主要な報道機関のニュースを一つにまとめています。"},
    "zh": {"t": "{c}新聞",
           "d": "{c}：今日焦點新聞與頭條，每小時更新。all.news 匯集該地區主要新聞來源，一站掌握。"},
    # Simplified Chinese for mainland China and Singapore (keyed "<lang>-<CC>").
    "zh-CN": {"t": "{c}新闻",
              "d": "{c}：今日焦点新闻与头条，每小时更新。all.news 汇集该地区主要新闻来源，一站掌握。"},
    "zh-SG": {"t": "{c}新闻",
              "d": "{c}：今日焦点新闻与头条，每小时更新。all.news 汇集该地区主要新闻来源，一站掌握。"},
    "hi": {"t": "{c}: समाचार",
           "d": "{c}: आज की प्रमुख खबरें और सुर्खियाँ, हर घंटे अपडेट। all.news देश के प्रमुख समाचार स्रोतों को एक ही जगह पर लाता है।"},
    "bn": {"t": "{c}: সংবাদ",
           "d": "{c}: আজকের প্রধান খবর ও শিরোনাম, প্রতি ঘণ্টায় হালনাগাদ। all.news দেশের শীর্ষস্থানীয় সংবাদ উৎসগুলোকে এক জায়গায় নিয়ে আসে।"},
    "ta": {"t": "{c}: செய்திகள்",
           "d": "{c}: இன்றைய முக்கிய செய்திகள் மற்றும் தலைப்புகள், ஒவ்வொரு மணி நேரமும் புதுப்பிக்கப்படுகின்றன. all.news நாட்டின் முன்னணி செய்தி மூலங்களை ஒரே இடத்தில் தொகுக்கிறது."},
    "te": {"t": "{c}: వార్తలు",
           "d": "{c}: నేటి ముఖ్య వార్తలు మరియు శీర్షికలు, ప్రతి గంటకు నవీకరించబడతాయి. all.news దేశంలోని ప్రముఖ వార్తా మూలాలను ఒకే చోట అందిస్తుంది."},
    "mr": {"t": "{c}: बातम्या",
           "d": "{c}: आजच्या प्रमुख बातम्या आणि मथळे, दर तासाला अद्ययावत. all.news देशातील आघाडीचे वृत्तस्रोत एकाच ठिकाणी आणते."},
    "ml": {"t": "{c}: വാർത്തകൾ",
           "d": "{c}: ഇന്നത്തെ പ്രധാന വാർത്തകളും തലക്കെട്ടുകളും, ഓരോ മണിക്കൂറിലും പുതുക്കുന്നു. രാജ്യത്തെ മുൻനിര വാർത്താ സ്രോതസ്സുകളെ all.news ഒരിടത്ത് എത്തിക്കുന്നു."},
    "kn": {"t": "{c}: ಸುದ್ದಿ",
           "d": "{c}: ಇಂದಿನ ಪ್ರಮುಖ ಸುದ್ದಿಗಳು ಮತ್ತು ಶೀರ್ಷಿಕೆಗಳು, ಪ್ರತಿ ಗಂಟೆಗೆ ನವೀಕರಿಸಲಾಗುತ್ತದೆ. all.news ದೇಶದ ಪ್ರಮುಖ ಸುದ್ದಿ ಮೂಲಗಳನ್ನು ಒಂದೇ ಕಡೆ ಸಂಗ್ರಹಿಸುತ್ತದೆ."},
    "gu": {"t": "{c}: સમાચાર",
           "d": "{c}: આજના મુખ્ય સમાચાર અને હેડલાઇન્સ, દર કલાકે અપડેટ. all.news દેશના અગ્રણી સમાચાર સ્રોતોને એક જ જગ્યાએ લાવે છે."},
    "pa": {"t": "{c}: ਖ਼ਬਰਾਂ",
           "d": "{c}: ਅੱਜ ਦੀਆਂ ਮੁੱਖ ਖ਼ਬਰਾਂ ਅਤੇ ਸੁਰਖੀਆਂ, ਹਰ ਘੰਟੇ ਅੱਪਡੇਟ। all.news ਦੇਸ਼ ਦੇ ਪ੍ਰਮੁੱਖ ਖ਼ਬਰ ਸਰੋਤਾਂ ਨੂੰ ਇੱਕ ਥਾਂ ਇਕੱਠਾ ਕਰਦਾ ਹੈ।"},
    "ur": {"t": "{c}: خبریں",
           "d": "{c}: آج کی اہم خبریں اور سرخیاں، ہر گھنٹے اپ ڈیٹ۔ all.news ملک کے نمایاں خبر رساں ذرائع کو ایک جگہ جمع کرتا ہے۔"},
    "ms": {"t": "{c}: berita",
           "d": "{c}: berita dan tajuk utama hari ini, dikemas kini setiap jam. all.news menghimpunkan sumber berita terkemuka negara di satu tempat."},
    "ko": {"t": "{c} 뉴스",
           "d": "{c}: 오늘의 주요 뉴스와 헤드라인, 매시간 업데이트. all.news는 국내 주요 언론사의 뉴스를 한곳에 모았습니다."},
    "ca": {"t": "{c}: notícies",
           "d": "{c}: les notícies i els titulars d'avui, actualitzats cada hora. all.news reuneix les principals fonts d'informació del país en un sol lloc."},
    "tl": {"t": "{c}: balita",
           "d": "{c}: ang mga pangunahing balita at headline ngayon, ina-update bawat oras. Tinitipon ng all.news ang nangungunang mga pinagkukunan ng balita ng bansa sa iisang lugar."},
    "ar": {"t": "{c}: أخبار",
           "d": "{c}: أبرز أخبار وعناوين اليوم، تُحدَّث كل ساعة. يجمع all.news أهم مصادر الأخبار في البلد في مكان واحد."},
    "he": {"t": "{c}: חדשות",
           "d": "{c}: מבזקי החדשות והכותרות של היום, מתעדכן מדי שעה. all.news מרכז את מקורות החדשות המובילים במדינה במקום אחד."},
}


def country_name_i18n(cc, lang):
    """Country name in the page's language, falling back to the English name."""
    if lang == "en":
        return COUNTRY_NAMES.get(cc, cc)
    return COUNTRY_I18N.get(lang, {}).get(cc) or COUNTRY_NAMES.get(cc, cc)


def landing_date(lang, today):
    """Today's date formatted for the page's language (numeric, so no month-name
    tables): '16 July 2026' (en), '2026年7月16日' (ja/zh), '16.07.2026' (else)."""
    y, m, d = (int(x) for x in today.split("-"))
    if lang == "en":
        return fmt_day_en(today)
    if lang in ("ja", "zh"):
        return f"{y}年{m}月{d}日"
    return f"{d:02d}.{m:02d}.{y}"


HUB_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{title}</title>
  <meta name="description" content="{desc}">
  <link rel="canonical" href="{origin}/news/">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{desc}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="{origin}/news/">
  <meta property="og:image" content="{origin}/og-image.png">
  <meta name="robots" content="index, follow">
  <!-- Apply saved theme before first paint. -->
  <script>try{{var s=localStorage,t=s.getItem("allnews.theme"),r=document.documentElement;if(t==="light"||t==="system"&&matchMedia("(prefers-color-scheme: light)").matches)r.dataset.theme="light";if(s.getItem("allnews.density")==="compact")r.dataset.density="compact"}}catch(e){{}}</script>
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="icon" href="/favicon-192.png" type="image/png" sizes="192x192">
  <link rel="apple-touch-icon" href="/favicon-192.png">
  <link rel="stylesheet" href="/styles.css?v=6">
  <style>
    .hub-grid{{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:1.25rem;margin:1.5rem 0 3rem}}
    .hub-card h2{{font-size:1rem;margin:0 0 .4rem}}
    .hub-card ul{{list-style:none;padding:0;margin:0;display:flex;flex-wrap:wrap;gap:.35rem .6rem}}
    .hub-card a{{text-decoration:none}}
    .hub-card a:hover{{text-decoration:underline}}
    .hub-intro{{max-width:60ch}}
  </style>
  <script async src="https://www.googletagmanager.com/gtag/js?id=G-N83C506R65"></script>
  <script>
    window.dataLayer = window.dataLayer || [];
    function gtag(){{dataLayer.push(arguments);}}
    // Deny storage in EEA/UK/CH until the CMP grants consent.
    gtag('consent', 'default', {{
      ad_storage: 'denied', ad_user_data: 'denied', ad_personalization: 'denied', analytics_storage: 'denied',
      region: ['AT','BE','BG','HR','CY','CZ','DK','EE','FI','FR','DE','GR','HU','IS','IE','IT','LV','LI','LT','LU','MT','NL','NO','PL','PT','RO','SK','SI','ES','SE','GB','CH'],
      wait_for_update: 500
    }});
    gtag('js', new Date());
    gtag('config', 'G-N83C506R65');
  </script>
  <!-- Google AdSense (also loads the consent message) -->
  <script async src="https://pagead2.googlesyndication.com/pagead/js/adsbygoogle.js?client=ca-pub-3630076197785405"
     crossorigin="anonymous"></script>
</head>
<body>
  <div class="container">
    <header class="topbar">
      <a href="/" class="brand-link">all.news</a>
    </header>
    <main class="view">
      <div class="hero">
        <h1 class="wordmark">Browse by country &amp; language</h1>
      </div>
      <p class="hub-intro">Read world news by country and language. Each page collects
      today's headlines from that country's sources, updated hourly. Pick a country
      and a language to start.</p>
      <div class="hub-grid">
{cards}
      </div>
    </main>
    <footer class="site-footer">
      <nav class="footer-links" aria-label="Legal">
        <a href="/about">About</a>
        <a href="/privacy">Privacy</a>
        <a href="/cookies">Cookies</a>
        <a href="/terms">Terms</a>
        <a href="/imprint">Imprint</a>
        <a href="/settings">Settings</a>
        <a href="/cookies#manage" class="cookie-settings">Cookie settings</a>
      </nav>
      <div class="footer-meta">
        <span>© Copyright 2026 all.news</span>
        <span><a href="/">Home</a> · <a href="/archive">Archive</a></span>
      </div>
    </footer>
  </div>
  <script src="/site.js"></script>
</body>
</html>
"""


def write_news_hub(langs_by_country):
    """The /news/ hub: every country, linking each language landing page. Standalone
    (no feed JS) so it never gets hijacked by the SPA's article renderer."""
    countries = sorted(langs_by_country, key=lambda c: COUNTRY_NAMES.get(c, c))
    cards = []
    for cc in countries:
        cname = COUNTRY_NAMES.get(cc, cc)
        links = []
        for lang in sorted(set(langs_by_country[cc]), key=lambda l: LANG_EN_NAMES.get(l, l)):
            lname = LANG_EN_NAMES.get(lang, lang)
            links.append(f'<li><a href="{landing_url(cc, lang)}">{escape(lname)}</a></li>')
        cards.append(
            f'        <section class="hub-card"><h2>{escape(cname)}</h2>'
            f'<ul>{"".join(links)}</ul></section>')
    title = "Browse News by Country and Language – all.news"
    desc = ("Browse world news by country and language. all.news aggregates headlines "
            "from hundreds of sources across every country we cover, updated hourly.")
    html = HUB_TEMPLATE.format(origin=SITE_ORIGIN, title=escape(title),
                               desc=escape(desc), cards="\n".join(cards))
    os.makedirs(LANDING_DIR, exist_ok=True)
    with open(os.path.join(LANDING_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html)


def write_landing_pages(articles, today):
    """One SSR page per (country, language) at /news/<country>/<lang>/, plus the
    /news/ hub. Rendered from today's slice so bots get real headlines; the page
    hydrates client-side (country shard + filter). Returns the list of landing URLs
    for the sitemap."""
    pairs = known_country_lang_pairs()
    langs_by_country = {}
    for cc, lang in pairs:
        langs_by_country.setdefault(cc, []).append(lang)
    landing_urls = []
    for cc, lang in pairs:
        cname = country_name_i18n(cc, lang)      # country name in the page's language
        strings = (LANDING_STRINGS.get(f"{lang}-{cc}") or LANDING_STRINGS.get(lang)
                   or LANDING_STRINGS["en"])
        phrase = strings["t"].format(c=cname)    # e.g. "日本のニュース", "Suisse : actualités"
        sl = [a for a in articles
              if (a.get("country") or "").upper() == cc
              and (a.get("lang") or "").lower() == lang]
        url = landing_url(cc, lang)
        landing_urls.append(url)
        # hreflang alternates: sibling languages of the same country.
        alts = "".join(
            f'<link rel="alternate" hreflang="{l2}" href="{SITE_ORIGIN}{landing_url(cc, l2)}">'
            for l2 in sorted(set(langs_by_country[cc])))
        write_rendered_html(
            sl, landing_path(cc, lang),
            title=f"{phrase} – all.news",
            description=strings["d"].format(c=cname),
            canonical=f"{SITE_ORIGIN}{url}",
            date_heading=f"{phrase} · {landing_date(lang, today)}",
            older_dates=[], limit=SSR_LIMIT, head_links=alts, html_lang=lang)
    write_news_hub(langs_by_country)
    return landing_urls


# ---- Crawl jobs, grouped so the GitHub Actions matrix can run them in parallel.
# "vpn"  = CH Media papers + VPN_SOURCES (403 datacenter ASNs → must run behind the Swiss VPN).
# "main" = everything else (plain feeds/sitemaps, no VPN needed).
def feed_jobs():
    return [(f["source"], (lambda f: lambda: parse_feed(f["source"], fetch(f["url"], f.get("ua")), f.get("summary", True)))(f))
            for f in FEEDS]


def main_sitemap_jobs():
    jobs = [
        ("Weltwoche", crawl_weltwoche),
        ("Nebelspalter", crawl_nebelspalter),
        ("Bilanz", crawl_bilanz),
        ("Republik", crawl_republik),
        ("Südostschweiz", crawl_suedostschweiz),
        # Nau disabled: mostly reposts copied from other outlets, little original content
        # ("Nau", crawl_nau),
        ("WOZ", crawl_woz),
        ("Tachles", crawl_tachles),
        ("Bauernzeitung", crawl_bauernzeitung),
    ]
    jobs += [(n["source"], (lambda n: lambda: crawl_news_sitemap(n["source"], n["url"], n["max"]))(n))
             for n in NEWS_SITEMAPS]
    jobs += [(w["source"], (lambda w: lambda: crawl_wp(w["source"], w["index"], w["max"]))(w))
             for w in WP_SOURCES]
    return jobs


def section_jobs():
    """Section-only sources (feeds or Google News sitemaps)."""
    def job(s):
        if s.get("kind") == "news_sitemap":
            return lambda: crawl_news_sitemap(s["source"], s["url"], s.get("max", 50))
        return lambda: parse_feed(s["source"], fetch(s["url"]), s.get("summary", True))
    return [(s["source"], job(s)) for s in SECTION_SOURCES]


def ch_media_jobs():
    return [(s["source"], (lambda s: lambda: crawl_ch_media(s["source"], s["base"], s["max"]))(s))
            for s in CH_MEDIA_SOURCES]


# Feeds that 403 (or return empty bodies to) Azure IPs but work from a
# Swiss IP: crawled in the VPN job instead of the main one.
VPN_SOURCES = {
    "+972 Magazine", "CNBC Indonesia", "CNBC Indonesia News", "CNN Indonesia",
    "CNN Indonesia Nasional", "Davar", "Espreso", "Kontan Nasional", "Liga.net",
    "Pedestrian TV", "Razón Pública", "Seattle Times", "The Walrus",
    "Ukrainska Pravda Economy",
}


def jobs_for(group):
    every = feed_jobs() + main_sitemap_jobs() + section_jobs()
    if group == "vpn":
        return ch_media_jobs() + [j for j in every if j[0] in VPN_SOURCES]
    if group == "main":
        return [j for j in every if j[0] not in VPN_SOURCES]
    return every + ch_media_jobs()  # full run (local)


def run_jobs(jobs):
    """Run each crawl job, tolerating per-source failures (as before). Returns the
    combined raw rows; dedup/date-filtering/stamping happens later in write_outputs."""
    rows = []
    for name, fn in jobs:
        try:
            r = fn()
            rows += r
            print(f"  ok   {name}: {len(r)} rows", file=sys.stderr)
        except NotModified:
            print(f"  skip {name}: not modified", file=sys.stderr)
        except Exception as e:  # one bad feed (IncompleteRead, zlib…) must not abort the shard
            print(f"  fail {name}: {type(e).__name__}: {e}", file=sys.stderr)
    return rows


def load_http_cache():
    global _http_cache
    try:
        with open(HTTP_CACHE_FILE, encoding="utf-8") as f:
            _http_cache = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        _http_cache = {}


def run_map(group, out_path, shard=None, of=None):
    """Crawl one group (optionally a 1-of-N shard of it) and write a partial
    artifact (raw rows + the cache entries this shard touched) for reduce to merge.
    Sharding is round-robin (jobs[shard::of]) so the heavier sitemap jobs, which
    cluster at the end of the list, spread evenly across shards."""
    load_http_cache()
    jobs = jobs_for(group)
    if of and of > 1:
        jobs = jobs[shard::of]
    rows = run_jobs(jobs)
    cache_delta = {u: _http_cache[u] for u in _fetched_urls if u in _http_cache}
    write_json(out_path, {"rows": rows, "http_cache": cache_delta})
    tag = f"{group}{f' shard {shard}/{of}' if of and of > 1 else ''}"
    print(f"wrote {out_path}: {len(rows)} rows, {len(cache_delta)} cache entries ({tag})",
          file=sys.stderr)


def run_reduce(partial_paths):
    """Merge shard partials, then dedup/date-filter/stamp and write all outputs."""
    global _http_cache
    rows, merged_cache = [], {}
    for p in partial_paths:
        with open(p, encoding="utf-8") as f:
            part = json.load(f)
        rows += part.get("rows", [])
        merged_cache.update(part.get("http_cache", {}))  # groups crawl disjoint URLs
    _http_cache = merged_cache
    write_outputs(rows)


def write_country_shards(articles, now_iso, today):
    """Split today's feed into per-country files (data/<cc>.json) plus a manifest
    (data/manifest.json) that lists every country and the languages it publishes.
    The client fetches only the shards for the countries a visitor filters to, so
    the download scales with the selection instead of the whole world. The manifest
    lets the site render the full country/language picker before any shard loads."""
    os.makedirs(DATA_DIR, exist_ok=True)
    by_country = {}
    for a in articles:
        cc = (a.get("country") or "").upper()
        if cc:
            by_country.setdefault(cc, []).append(a)
    manifest = []
    for cc, arts in sorted(by_country.items()):
        write_json(os.path.join(DATA_DIR, f"{cc.lower()}.json"),
                   {"generated": now_iso, "date": today, "country": cc,
                    "count": len(arts), "articles": arts})
        langs = sorted({(a.get("lang") or "").lower() for a in arts if a.get("lang")})
        manifest.append({"code": cc, "count": len(arts), "langs": langs})
    write_json(os.path.join(DATA_DIR, "manifest.json"),
               {"generated": now_iso, "date": today, "countries": manifest})


def load_today(path, today):
    """Articles a feed file already holds for today (so reruns append)."""
    try:
        with open(path, encoding="utf-8") as f:
            prev = json.load(f)
        return prev["articles"] if prev.get("date") == today else []
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return []


def select_new(articles, existing_today, seen, now_iso):
    """Today's new articles from raw rows. Keeps only rows whose SOURCE date is
    today and whose URL was never crawled before (`seen`) — so a sitemap
    re-dating an old article never re-adds it — whose title isn't already kept
    today, and whose source is under its daily cap. Each kept article is stamped
    with the crawl time and its origin. Returns (new_articles, new_urls)."""
    seen_titles = {a["title"].lower() for a in existing_today}
    # Per-source daily cap: stop a single high-churn source (e.g. Infobae) from
    # dominating the day. Counts articles already kept today, then caps new ones.
    src_count = {}
    for a in existing_today:
        src_count[a["source"]] = src_count.get(a["source"], 0) + 1
    new, batch = [], set()
    for a in articles:
        u = a["url"]
        if u in seen or u in batch:
            continue
        if not is_today(a.get("published")):
            continue
        a = {**a, "title": clean_title(a.get("title"))}  # strip nbsp/zero-width from headlines (#31)
        t = a["title"].lower()
        if t in seen_titles:
            continue
        if src_count.get(a["source"], 0) >= DAILY_PER_SOURCE:
            continue  # this source hit its daily cap; keep earliest, drop overflow
        # date = crawl time; lang/country label the article's origin
        new.append({**a, "published": now_iso, **origin_of(a["source"])})
        batch.add(u)
        seen_titles.add(t)
        src_count[a["source"]] = src_count.get(a["source"], 0) + 1
    return new, batch


def section_feed_path(key):
    return os.path.join(key, "feed.json")


def section_seen_path(key):
    return os.path.join(ARCHIVE_DIR, f"seen-{key}.json")


def write_sections(rows, now_iso, today):
    """Each topic section from the rows of its sources: today's feed
    (<key>/feed.json), the day's record (archive/<key>/<date>.json), its own
    seen-set and its SSR page (<key>/index.html). Returns the page URLs."""
    urls = []
    for key, meta in SECTIONS.items():
        existing = load_today(section_feed_path(key), today)
        seen = load_seen(section_seen_path(key))
        sec_rows = [a for a in rows if key in sections_of(a["source"])]
        new, batch = select_new(sec_rows, existing, seen, now_iso)
        result = sorted(existing + new, key=lambda a: a.get("published", ""), reverse=True)
        data = {"generated": now_iso, "date": today, "section": key,
                "count": len(result), "articles": result}
        os.makedirs(key, exist_ok=True)
        os.makedirs(os.path.join(ARCHIVE_DIR, key), exist_ok=True)
        write_json(section_feed_path(key), data)
        write_json(os.path.join(ARCHIVE_DIR, key, f"{today}.json"), data)
        write_json(section_seen_path(key), sorted(seen | batch))
        name = meta["name"]
        write_rendered_html(
            result, os.path.join(key, "index.html"),
            title=f"{name} News From Every Source in One Place – all.news",
            description=(f"Today's {meta['about']} news from outlets around the world, "
                         "on one page and updated around the clock. Filter by language and source."),
            canonical=f"{SITE_ORIGIN}/{key}/",
            date_heading=f"{name} · {fmt_day_heading(today)}",
            limit=SSR_LIMIT, archive_link=False)
        urls.append(f"/{key}/")
        print(f"wrote {key}: +{len(new)} new, {len(result)} total today", file=sys.stderr)
    return urls


def write_outputs(articles):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    # One crawl per day; each kept article is stamped with the crawl time.
    now_iso = datetime.now(timezone.utc).isoformat()
    today = datetime.now(ZURICH).date().isoformat()

    # Preserve articles already saved for today (keep their first-seen crawl time)
    # so re-running the crawler on the same day appends rather than overwrites.
    existing_today = load_today("crawled.json", today)
    seen = load_seen()
    # The news feed takes only news sources; sections are written separately.
    news_rows = [a for a in articles if "news" in sections_of(a["source"])]
    new, batch = select_new(news_rows, existing_today, seen, now_iso)

    # Ship crawled.json already sorted newest-first (by crawl-stamped "published"),
    # so the client's default date-sort runs over near-sorted input (near-linear)
    # and the raw JSON order is sensible. The client still sorts, so ties it
    # resolves (prio) are unaffected.
    result = sorted(existing_today + new,
                    key=lambda a: a.get("published", ""), reverse=True)
    data = {"generated": now_iso, "date": today, "count": len(result), "articles": result}
    write_json("crawled.json", data)                       # newest crawl (all countries)
    write_json(os.path.join(ARCHIVE_DIR, f"{today}.json"), data)  # this date's crawl
    write_country_shards(result, now_iso, today)           # data/<cc>.json + manifest
    write_json(SEEN_FILE, sorted(seen | batch))
    # Date list = prior dates (from index.json, which the workflow pulls from R2)
    # plus today. Derived from index.json rather than listing the archive dir, so
    # the reduce step works without every day's files present locally.
    # `pages` rides along in the same file: past days' HTML isn't local here, so
    # their page counts can only come from what an earlier run recorded. Consumers
    # (archive.html, script.js, regen_archive.py) read `dates` and ignore it.
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            index = json.load(f)
        prior_dates, prior_pages = index.get("dates", []), index.get("pages", {})
    except (FileNotFoundError, json.JSONDecodeError):
        prior_dates, prior_pages = archive_dates(), {}  # local fallback (full runs)
    all_dates = sorted(set(prior_dates) | {today}, reverse=True)
    write_json(HTTP_CACHE_FILE, _http_cache)

    write_colors_js()
    # Bottom-of-feed nav shows the 5 most recent days, then a "Full archive" link.
    older = [d for d in all_dates if d != today][:5]
    write_rendered_html(
        result, "index.html",
        title="World News From Every Source in One Place – all.news",
        description="Read world news from hundreds of sources on one page. all.news aggregates global headlines and updates hourly — filter by source, language and more.",
        canonical=f"{SITE_ORIGIN}/",
        date_heading=fmt_day_heading(today),
        older_dates=older,
        limit=SSR_LIMIT,  # index head only; script.js lazy-loads the rest
    )
    pages_today = write_archive_day(today, result)  # paginated static pages
    page_counts = {d: n for d, n in {**prior_pages, today: pages_today}.items()
                   if d in set(all_dates)}
    write_json(INDEX_FILE, {"dates": all_dates, "pages": page_counts})
    landing_urls = write_landing_pages(result, today)  # /news/<country>/<lang>/ + hub
    section_urls = write_sections(articles, now_iso, today)  # /space/, /tech/ …
    write_sitemap(all_dates, landing_urls, page_counts, now_iso, section_urls)
    print(f"wrote crawled.json: +{len(new)} new, {len(result)} total today ({today})",
          file=sys.stderr)


def main():
    """Full local run: crawl every group in one process and write all outputs."""
    load_http_cache()
    write_outputs(run_jobs(jobs_for(None)))


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser(description="all.news crawler")
    ap.add_argument("--group", choices=["vpn", "main"],
                    help="crawl only this group and write a partial artifact (map step)")
    ap.add_argument("--shard", type=int, default=0, help="0-based shard index within the group")
    ap.add_argument("--of", type=int, default=1, help="total number of shards for the group")
    ap.add_argument("--out", help="partial artifact path (default: partial-<group>[-<shard>].json)")
    ap.add_argument("--reduce", nargs="+", metavar="PARTIAL",
                    help="merge partial artifacts and write all outputs (reduce step)")
    args = ap.parse_args()
    if args.reduce:
        run_reduce(args.reduce)
    elif args.group:
        suffix = f"-{args.shard}" if args.of > 1 else ""
        run_map(args.group, args.out or f"partial-{args.group}{suffix}.json", args.shard, args.of)
    else:
        main()
