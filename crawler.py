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
from html import escape
from zoneinfo import ZoneInfo

ZURICH = ZoneInfo("Europe/Zurich")
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
    {"source": "Tachles",       "url": "https://www.tachles.ch/feed"},
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
    {"source": "Il Giornale",   "url": "https://www.ilgiornale.it/feed.xml"},
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
    {"source": "Stuttgarter Zeitung","url": "https://www.stuttgarter-zeitung.de/news.rss.feed"},
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
    {"source": "Wired Italia",       "url": "https://www.wired.it/feed/rss"},
    {"source": "Il Mattino",         "url": "https://www.ilmattino.it/rss/home.xml"},
    {"source": "Il Messaggero",      "url": "https://www.ilmessaggero.it/rss/home.xml"},
    {"source": "Il Gazzettino",      "url": "https://www.ilgazzettino.it/rss/home.xml"},
    {"source": "Quotidiano.net",     "url": "https://www.quotidiano.net/rss"},
    {"source": "askanews",           "url": "https://www.askanews.it/feed/"},
    {"source": "Domani",             "url": "https://www.editorialedomani.it/rss"},
    {"source": "Il Secolo XIX",      "url": "https://www.ilsecoloxix.it/rss"},
    # --- Spain (ES) ---
    {"source": "El Español",         "url": "https://www.elespanol.com/rss/"},
    {"source": "COPE",               "url": "https://www.cope.es/api/es/news/rss.xml"},
    {"source": "Europa Press",       "url": "https://www.europapress.es/rss/rss.aspx"},
    {"source": "Marca",              "url": "https://e00-marca.uecdn.es/rss/portada.xml"},
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
    {"source": "Libertatea","url": "https://www.libertatea.ro/rss"},
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
    {"source": "Perú21","url": "https://peru21.pe/arc/outboundfeeds/rss/"},
    {"source": "Andina","url": "https://andina.pe/agencia/rss.aspx"},
    # Australia
    {"source": "The Age","url": "https://www.theage.com.au/rss/feed.xml"},
    {"source": "Guardian Australia","url": "https://www.theguardian.com/australia-news/rss"},
    {"source": "Brisbane Times","url": "https://www.brisbanetimes.com.au/rss/feed.xml"},
    {"source": "AFR","url": "https://www.afr.com/rss/feed.xml"},
    {"source": "Conversation AU","url": "https://theconversation.com/au/articles.atom"},
    # New Zealand
    {"source": "The Spinoff","url": "https://thespinoff.co.nz/feed"},
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
    {"source": "Tempo",         "url": "https://rss.tempo.co/nasional"},
    # --- Philippines (PH, en) ---
    {"source": "Rappler",       "url": "https://www.rappler.com/feed/"},
    {"source": "Inquirer",      "url": "https://www.inquirer.net/fullfeed"},
    # --- Vietnam (VN, vi) ---
    {"source": "VnExpress",     "url": "https://vnexpress.net/rss/tin-moi-nhat.rss"},
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
    {"source": "Chequeado", "url": "https://chequeado.com/feed/"},
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
    {"source": "Tiempo Argentino", "url": "https://www.tiempoar.com.ar/feed/"},
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
    {"source": "SBS World News", "url": "https://www.sbs.com.au/news/feed"},
    {"source": "SMH National", "url": "https://www.smh.com.au/rss/national.xml"},
    {"source": "The Age National", "url": "https://www.theage.com.au/rss/national.xml"},
    {"source": "The Conversation AU Politics", "url": "https://theconversation.com/au/topics/australian-politics-1445/articles.atom"},
    {"source": "The Guardian AU Politics", "url": "https://www.theguardian.com/australia-news/australian-politics/rss"},
    {"source": "The Guardian AU World", "url": "https://www.theguardian.com/world/rss"},
    {"source": "The Mandarin", "url": "https://www.themandarin.com.au/feed/"},
    {"source": "The West Australian", "url": "https://thewest.com.au/rss"},
    {"source": "WAtoday", "url": "https://www.watoday.com.au/rss/feed.xml"},
    # --- BE ---
    {"source": "Bruzz", "url": "https://www.bruzz.be/rss.xml"},
    {"source": "De Morgen", "url": "https://www.demorgen.be/rss.xml"},
    {"source": "De Morgen Politiek", "url": "https://www.demorgen.be/politiek/rss.xml"},
    {"source": "De Tijd", "url": "https://www.tijd.be/rss/top_stories.xml"},
    {"source": "De Tijd Ondernemen", "url": "https://www.tijd.be/rss/ondernemen.xml"},
    {"source": "De Tijd Politiek", "url": "https://www.tijd.be/rss/politiek_economie.xml"},
    {"source": "Gazet van Antwerpen", "url": "https://www.gva.be/rss"},
    {"source": "Het Belang van Limburg", "url": "https://www.hbvl.be/rss"},
    {"source": "Het Laatste Nieuws Binnenland", "url": "https://www.hln.be/binnenland/rss.xml"},
    {"source": "HLN Buitenland", "url": "https://www.hln.be/buitenland/rss.xml"},
    {"source": "Knack", "url": "https://www.knack.be/feed/"},
    {"source": "Knack Nieuws", "url": "https://www.knack.be/nieuws/feed/"},
    {"source": "L'Echo", "url": "https://www.lecho.be/rss/top_stories.xml"},
    {"source": "L'Echo Politique", "url": "https://www.lecho.be/rss/politique_economie.xml"},
    {"source": "La DH", "url": "https://www.dhnet.be/arc/outboundfeeds/rss/?outputType=xml"},
    {"source": "La DH Sports", "url": "https://www.dhnet.be/arc/outboundfeeds/rss/category/sports/?outputType=xml"},
    {"source": "Le Vif Belgique", "url": "https://www.levif.be/belgique/feed/"},
    {"source": "Trends", "url": "https://trends.levif.be/feed/"},
    {"source": "VRT NWS Politiek", "url": "https://www.vrt.be/vrtnws/nl.rss.headlines.xml"},
    # --- BR ---
    {"source": "A Gazeta ES", "url": "https://www.agazeta.com.br/rss"},
    {"source": "A Tarde", "url": "https://www.atarde.com.br/rss"},
    {"source": "Agência Brasil", "url": "https://agenciabrasil.ebc.com.br/rss/ultimasnoticias/feed.xml"},
    {"source": "BBC Brasil", "url": "https://www.bbc.com/portuguese/index.xml"},
    {"source": "CartaCapital", "url": "https://www.cartacapital.com.br/feed/"},
    {"source": "CNN Brasil", "url": "https://www.cnnbrasil.com.br/feed/"},
    {"source": "Congresso em Foco", "url": "https://www.congressoemfoco.com.br/feed/"},
    {"source": "Crusoé", "url": "https://crusoe.com.br/feed/"},
    {"source": "Estadão", "url": "https://www.estadao.com.br/arc/outboundfeeds/feeds/rss/sections/ultimas/?outputType=xml"},
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
    {"source": "Canadaland", "url": "https://www.canadaland.com/feed/"},
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
    {"source": "CIPER", "url": "https://www.ciperchile.cl/feed/"},
    {"source": "Diario Financiero", "url": "https://www.df.cl/noticias/site/list/port/rss.xml"},
    {"source": "Ex-Ante", "url": "https://www.ex-ante.cl/feed/"},
    {"source": "Interferencia", "url": "https://interferencia.cl/rss.xml"},
    {"source": "La Nación Chile", "url": "https://www.lanacion.cl/feed/"},
    {"source": "Radio Universidad de Chile", "url": "https://radio.uchile.cl/feed/"},
    {"source": "The Clinic", "url": "https://www.theclinic.cl/feed/"},
    # --- CN ---
    {"source": "Bitter Winter", "url": "https://bitterwinter.org/feed/"},
    {"source": "CGTN Business", "url": "https://www.cgtn.com/subscribe/rss/section/business.xml"},
    {"source": "CGTN China", "url": "https://www.cgtn.com/subscribe/rss/section/china.xml"},
    {"source": "China Daily", "url": "https://www.chinadaily.com.cn/rss/china_rss.xml"},
    {"source": "China Daily World", "url": "https://www.chinadaily.com.cn/rss/world_rss.xml"},
    {"source": "China Media Project", "url": "https://chinamediaproject.org/feed/"},
    {"source": "Ecns.cn", "url": "https://www.ecns.cn/rss/rss.xml"},
    {"source": "Global Times", "url": "https://www.globaltimes.cn/rss/outbrain.xml"},
    {"source": "Pekingnology", "url": "https://www.pekingnology.com/feed"},
    {"source": "Radio Free Asia", "url": "https://www.rfa.org/english/rss2.xml"},
    {"source": "SCMP China", "url": "https://www.scmp.com/rss/4/feed"},
    {"source": "SupChina / The China Project", "url": "https://thechinaproject.com/feed/"},
    {"source": "The Wire China", "url": "https://www.thewirechina.com/feed/"},
    {"source": "Trivium China", "url": "https://triviumchina.com/feed/"},
    {"source": "What's on Weibo", "url": "https://www.whatsonweibo.com/feed/"},
    # --- CO ---
    {"source": "Cuestión Pública", "url": "https://cuestionpublica.com/feed/"},
    {"source": "El Colombiano Antioquia", "url": "https://www.elcolombiano.com/rss/antioquia.xml"},
    {"source": "El Colombiano Nacional", "url": "https://www.elcolombiano.com/rss/colombia.xml"},
    {"source": "El Tiempo Mundo", "url": "https://www.eltiempo.com/rss/mundo.xml"},
    {"source": "El Tiempo Política", "url": "https://www.eltiempo.com/rss/politica.xml"},
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
    {"source": "ČT24 Domácí", "url": "https://ct24.ceskatelevize.cz/rss/tema/domaci-960"},
    {"source": "ČT24 Ekonomika", "url": "https://ct24.ceskatelevize.cz/rss/tema/ekonomika-961"},
    {"source": "ČT24 Svět", "url": "https://ct24.ceskatelevize.cz/rss/tema/svet-959"},
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
    {"source": "El Periódico de Catalunya", "url": "https://www.elperiodico.com/es/rss/politica/rss.xml"},
    {"source": "El Periódico Internacional", "url": "https://www.elperiodico.com/es/rss/internacional/rss.xml"},
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
    {"source": "Var-Matin", "url": "https://www.varmatin.com/rss"},
    {"source": "Vosges Matin", "url": "https://www.vosgesmatin.fr/rss"},
    # --- GB ---
    {"source": "BBC UK", "url": "https://feeds.bbci.co.uk/news/uk/rss.xml"},
    {"source": "Belfast Live", "url": "https://www.belfastlive.co.uk/?service=rss"},
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
    {"source": "openDemocracy", "url": "https://www.opendemocracy.net/en/rss/"},
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
    {"source": "Harbour Times", "url": "https://harbourtimes.com/feed/"},
    {"source": "HKFP Politics", "url": "https://hongkongfp.com/category/hong-kong/feed/"},
    {"source": "HKFP World", "url": "https://hongkongfp.com/category/world/feed/"},
    {"source": "Hong Kong Business", "url": "https://hongkongbusiness.hk/rss.xml"},
    {"source": "Ming Pao", "url": "https://news.mingpao.com/rss/pns/s00001.xml"},
    {"source": "Oriental Daily", "url": "https://orientaldaily.on.cc/rss/news.xml"},
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
    {"source": "Mandiner", "url": "https://mandiner.hu/rss"},
    {"source": "Média1", "url": "https://media1.hu/feed/"},
    {"source": "Népszava", "url": "https://nepszava.hu/feed"},
    {"source": "Portfolio Deviza", "url": "https://www.portfolio.hu/rss/deviza.xml"},
    {"source": "Portfolio Gazdaság", "url": "https://www.portfolio.hu/rss/gazdasag.xml"},
    {"source": "Sportal", "url": "https://sportal.blog.hu/rss"},
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
    {"source": "Tempo Bisnis", "url": "https://rss.tempo.co/bisnis"},
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
    {"source": "The Jerusalem Post News", "url": "https://www.jpost.com/rss/rssfeedsheadlines.aspx"},
    {"source": "The Media Line", "url": "https://themedialine.org/feed/"},
    {"source": "The Times of Israel", "url": "https://www.timesofisrael.com/feed/"},
    {"source": "Walla", "url": "https://rss.walla.co.il/feed/1?type=main"},
    {"source": "Ynetnews", "url": "https://www.ynetnews.com/Integration/StoryRss3082.xml"},
    {"source": "Ynetnews World", "url": "https://www.ynetnews.com/Integration/StoryRss1854.xml"},
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
    {"source": "Livemint Markets", "url": "https://www.livemint.com/rss/market"},
    {"source": "Mint Politics", "url": "https://www.livemint.com/rss/politics"},
    {"source": "Moneycontrol", "url": "https://www.moneycontrol.com/rss/latestnews.xml"},
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
    {"source": "Corriere della Sera", "url": "https://www.corriere.it/rss/homepage.xml"},
    {"source": "Corriere Economia", "url": "https://www.corriere.it/dynamic-feed/rss/section/Economia.xml"},
    {"source": "Formiche", "url": "https://formiche.net/feed/"},
    {"source": "Gazzetta dello Sport", "url": "https://www.gazzetta.it/rss/home.xml"},
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
    # --- KR ---
    {"source": "KBS World", "url": "https://world.kbs.co.kr/rss/rss_news.htm?lang=e"},
    {"source": "Korea Pro", "url": "https://koreapro.org/feed/"},
    {"source": "Maeil Business", "url": "https://www.mk.co.kr/rss/30000001/"},
    {"source": "MK Business", "url": "https://www.mk.co.kr/rss/40300001/"},
    {"source": "NK News", "url": "https://www.nknews.org/feed/"},
    {"source": "The Korea Times Business", "url": "https://www.koreatimes.co.kr/www/rss/biz.xml"},
    # --- MX ---
    {"source": "Contralínea", "url": "https://contralinea.com.mx/feed/"},
    {"source": "El Economista MX", "url": "https://www.eleconomista.com.mx/rss/ultimas-noticias"},
    {"source": "El Heraldo de México", "url": "https://heraldodemexico.com.mx/rss/feed.html"},
    {"source": "El Sol de México", "url": "https://www.elsoldemexico.com.mx/rss.xml"},
    {"source": "Expansión Economía", "url": "https://expansion.mx/rss/economia"},
    {"source": "Expansión MX", "url": "https://expansion.mx/rss"},
    {"source": "La Jornada Política", "url": "https://www.jornada.com.mx/rss/politica.xml"},
    {"source": "Pie de Página", "url": "https://piedepagina.mx/feed/"},
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
    {"source": "De Stentor Regio", "url": "https://www.destentor.nl/regio/rss.xml"},
    {"source": "De Telegraaf Nieuws", "url": "https://www.telegraaf.nl/nieuws/rss"},
    {"source": "De Volkskrant Nieuws", "url": "https://www.volkskrant.nl/nieuws-achtergrond/rss.xml"},
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
    {"source": "NRC Binnenland", "url": "https://www.nrc.nl/rss/binnenland/"},
    {"source": "Nrc Economie", "url": "https://www.nrc.nl/rss/economie/"},
    {"source": "NU.nl Economie", "url": "https://www.nu.nl/rss/Economie"},
    {"source": "Trouw Groen", "url": "https://www.trouw.nl/groen/rss.xml"},
    {"source": "Trouw Politiek", "url": "https://www.trouw.nl/politiek/rss.xml"},
    {"source": "Tweakers", "url": "https://feeds.feedburner.com/tweakers/mixed"},
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
    {"source": "VG Sport", "url": "https://www.vg.no/rss/feed/?categories=1069"},
    # --- NZ ---
    {"source": "Kiwiblog", "url": "https://www.kiwiblog.co.nz/feed"},
    {"source": "NZ Herald", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/rss/curated/78/?outputType=xml"},
    {"source": "NZ Herald Business", "url": "https://www.nzherald.co.nz/arc/outboundfeeds/rss/section/business/?outputType=xml"},
    {"source": "Otago Daily Times", "url": "https://www.odt.co.nz/rss.xml"},
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
    {"source": "IDL-Reporteros", "url": "https://www.idl-reporteros.pe/feed/"},
    {"source": "Wayka", "url": "https://wayka.pe/feed/"},
    # --- PH ---
    {"source": "Bandera", "url": "https://bandera.inquirer.net/feed"},
    {"source": "Business World", "url": "https://www.bworldonline.com/feed/"},
    {"source": "BusinessWorld Economy", "url": "https://www.bworldonline.com/economy/feed/"},
    {"source": "GMA Money", "url": "https://data.gmanetwork.com/gno/rss/money/feed.xml"},
    {"source": "GMA News Nation", "url": "https://data.gmanetwork.com/gno/rss/news/nation/feed.xml"},
    {"source": "GMA News World", "url": "https://data.gmanetwork.com/gno/rss/news/world/feed.xml"},
    {"source": "Inquirer Global", "url": "https://globalnation.inquirer.net/feed"},
    {"source": "Inquirer Nation", "url": "https://newsinfo.inquirer.net/feed"},
    {"source": "Interaksyon", "url": "https://interaksyon.philstar.com/feed/"},
    {"source": "Manila Times News", "url": "https://www.manilatimes.net/news/feed"},
    {"source": "PhilNews", "url": "https://philnews.ph/feed/"},
    {"source": "PhilStar Business", "url": "https://www.philstar.com/rss/business"},
    {"source": "Philstar Nation", "url": "https://www.philstar.com/rss/nation"},
    {"source": "Philstar World", "url": "https://www.philstar.com/rss/world"},
    {"source": "Rappler Business", "url": "https://www.rappler.com/business/feed/"},
    {"source": "Rappler Nation", "url": "https://www.rappler.com/nation/feed/"},
    {"source": "Rappler World", "url": "https://www.rappler.com/world/feed/"},
    # --- PK ---
    {"source": "ARY News Pakistan", "url": "https://arynews.tv/category/pakistan/feed/"},
    {"source": "Bol News", "url": "https://www.bolnews.com/feed/"},
    {"source": "Business Recorder Pakistan", "url": "https://www.brecorder.com/feeds/latest-news"},
    {"source": "Daily Times", "url": "https://dailytimes.com.pk/feed/"},
    {"source": "Dawn Business", "url": "https://www.dawn.com/feeds/business"},
    {"source": "Dawn Pakistan", "url": "https://www.dawn.com/feeds/home"},
    {"source": "Dawn World", "url": "https://www.dawn.com/feeds/world"},
    {"source": "Geo News Pakistan", "url": "https://www.geo.tv/rss/1/53"},
    {"source": "Minute Mirror", "url": "https://minutemirror.com.pk/feed/"},
    {"source": "Pakistan Observer", "url": "https://pakobserver.net/feed/"},
    {"source": "The Current", "url": "https://thecurrent.pk/feed/"},
    {"source": "The Express Tribune", "url": "https://tribune.com.pk/feed/home"},
    {"source": "The Express Tribune Business", "url": "https://tribune.com.pk/feed/business"},
    {"source": "The Express Tribune Pakistan", "url": "https://tribune.com.pk/feed/pakistan"},
    {"source": "The Express Tribune World", "url": "https://tribune.com.pk/feed/world"},
    {"source": "The News International Pakistan", "url": "https://www.thenews.com.pk/rss/1/1"},
    # --- PL ---
    {"source": "Bankier.pl", "url": "https://www.bankier.pl/rss/wiadomosci.xml"},
    {"source": "Defence24", "url": "https://www.defence24.pl/rss"},
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
    {"source": "Yahoo SG World", "url": "https://sg.news.yahoo.com/rss/world"},
    {"source": "Yahoo Singapore", "url": "https://sg.news.yahoo.com/rss/"},
    {"source": "Yahoo Singapore Feed", "url": "https://sg.news.yahoo.com/rss/singapore"},
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
    {"source": "Yeni Şafak", "url": "https://www.yenisafak.com/rss?xml=anasayfa"},
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
    {"source": "The Texas Tribune", "url": "https://www.texastribune.org/feeds/main/"},
    {"source": "The Verge US", "url": "https://www.theverge.com/rss/full.xml"},
    # --- VN ---
    {"source": "Bao Giao Thong", "url": "https://www.baogiaothong.vn/rss/home.rss"},
    {"source": "Cong An Nhan Dan", "url": "https://cand.com.vn/rss/home.rss"},
    {"source": "Dan Tri Kinh doanh", "url": "https://dantri.com.vn/rss/kinh-doanh.rss"},
    {"source": "Dan Tri Su Kien", "url": "https://dantri.com.vn/rss/su-kien.rss"},
    {"source": "Nguoi Lao Dong", "url": "https://nld.com.vn/rss/home.rss"},
    {"source": "Nguoi Lao Dong Thoi su", "url": "https://nld.com.vn/rss/thoi-su.rss"},
    {"source": "Thanh Nien Chinh Tri", "url": "https://thanhnien.vn/rss/chinh-tri.rss"},
    {"source": "Thanh Nien Thoi su", "url": "https://thanhnien.vn/rss/thoi-su.rss"},
    {"source": "Tien Phong", "url": "https://tienphong.vn/rss/home.rss"},
    {"source": "Tien Phong Kinh te", "url": "https://tienphong.vn/rss/kinh-te-6.rss"},
    {"source": "Vietnamnet Thoi su", "url": "https://vietnamnet.vn/rss/thoi-su.rss"},
    {"source": "VietnamPlus VN", "url": "https://www.vietnamplus.vn/rss/tin-moi.rss"},
    {"source": "VnExpress Kinh doanh", "url": "https://vnexpress.net/rss/kinh-doanh.rss"},
    {"source": "VnExpress Thế giới", "url": "https://vnexpress.net/rss/the-gioi.rss"},
    {"source": "VnExpress Thời sự", "url": "https://vnexpress.net/rss/thoi-su.rss"},
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
ZEIT_MAX = 50  # https://www.zeit.de/gsitemaps/index.xml?date=YYYY-MM-01&unit=months&period=1
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


# Origin labels stamped onto every article so they can be filtered by language
# and country later. Every source so far is a German-language Swiss outlet; as
# the scope widens beyond Switzerland, add per-source overrides to SOURCE_ORIGIN.
# Codes: lang = ISO 639-1 (e.g. "de", "fr"), country = ISO 3166-1 alpha-2 ("CH").
DEFAULT_LANG = "de"
DEFAULT_COUNTRY = "CH"
SOURCE_ORIGIN: dict = {  # source name -> {"lang": ..., "country": ...}
    # German-language outlets based in Germany (lang defaults to "de").
    "Die Zeit":     {"country": "DE"},
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
    "Tempo": {"lang":"id","country":"ID"},
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
    "Today": {"lang":"it","country":"IT"}, "Wired Italia": {"lang":"it","country":"IT"},
    "Il Mattino": {"lang":"it","country":"IT"}, "Il Messaggero": {"lang":"it","country":"IT"},
    "Il Gazzettino": {"lang":"it","country":"IT"}, "Quotidiano.net": {"lang":"it","country":"IT"},
    "askanews": {"lang":"it","country":"IT"}, "Domani": {"lang":"it","country":"IT"},
    "Il Secolo XIX": {"lang":"it","country":"IT"},
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
    "Het Laatste Nieuws": {"lang":"nl","country":"BE"}, "7sur7": {"lang":"nl","country":"BE"},
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
    "Unian": {"lang":"uk","country":"UA"}, "NV": {"lang":"uk","country":"UA"},
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
    "Perú21": {"lang":"es","country":"PE"}, "Andina": {"lang":"es","country":"PE"},
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
    "Cenital": {"lang":"es","country":"AR"}, "Chequeado": {"lang":"es","country":"AR"},
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
    "The Conversation AU Politics": {"lang":"en","country":"AU"},
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
    "Calgary Herald": {"lang":"en","country":"CA"}, "Canadaland": {"lang":"en","country":"CA"},
    "Edmonton Journal": {"lang":"en","country":"CA"}, "Financial Post News": {"lang":"en","country":"CA"},
    "Global News Money": {"lang":"en","country":"CA"}, "Global News Politics": {"lang":"en","country":"CA"},
    "iPolitics": {"lang":"en","country":"CA"}, "Journal de Montréal": {"lang":"en","country":"CA"},
    "La Presse": {"lang":"en","country":"CA"}, "Le Journal de Québec": {"lang":"en","country":"CA"},
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
    # CN
    "Bitter Winter": {"lang":"en","country":"CN"}, "CGTN Business": {"lang":"en","country":"CN"},
    "CGTN China": {"lang":"en","country":"CN"}, "China Daily": {"lang":"en","country":"CN"},
    "China Daily World": {"lang":"en","country":"CN"}, "China Media Project": {"lang":"en","country":"CN"},
    "Ecns.cn": {"lang":"en","country":"CN"}, "Global Times": {"lang":"en","country":"CN"},
    "Pekingnology": {"lang":"en","country":"CN"}, "Radio Free Asia": {"lang":"en","country":"CN"},
    "SCMP China": {"lang":"en","country":"CN"}, "SupChina / The China Project": {"lang":"en","country":"CN"},
    "The Wire China": {"lang":"en","country":"CN"}, "Trivium China": {"lang":"en","country":"CN"},
    "What's on Weibo": {"lang":"en","country":"CN"},
    # CO
    "Cuestión Pública": {"lang":"es","country":"CO"},
    "El Colombiano Antioquia": {"lang":"es","country":"CO"},
    "El Colombiano Nacional": {"lang":"es","country":"CO"}, "El Tiempo Mundo": {"lang":"es","country":"CO"},
    "El Tiempo Política": {"lang":"es","country":"CO"}, "La República CO": {"lang":"es","country":"CO"},
    "La Silla Vacía": {"lang":"es","country":"CO"}, "Razón Pública": {"lang":"es","country":"CO"},
    "Semana Mundo": {"lang":"es","country":"CO"}, "Semana Nación": {"lang":"es","country":"CO"},
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
    "ABC Internacional": {"lang":"es","country":"ES"}, "Ara": {"lang":"es","country":"ES"},
    "Canarias7": {"lang":"es","country":"ES"}, "Diari de Tarragona": {"lang":"es","country":"ES"},
    "El Comercio": {"lang":"es","country":"ES"}, "El Confidencial Digital": {"lang":"es","country":"ES"},
    "El Confidencial Mundo": {"lang":"es","country":"ES"},
    "El Diario Montañés": {"lang":"es","country":"ES"}, "El Español Mundo": {"lang":"es","country":"ES"},
    "El Independiente España": {"lang":"es","country":"ES"}, "El Mundo España": {"lang":"es","country":"ES"},
    "El Mundo Internacional": {"lang":"es","country":"ES"},
    "El Norte de Castilla": {"lang":"es","country":"ES"}, "El País": {"lang":"es","country":"ES"},
    "El País España": {"lang":"es","country":"ES"}, "El País Internacional": {"lang":"es","country":"ES"},
    "El Periódico de Catalunya": {"lang":"es","country":"ES"},
    "El Periódico Internacional": {"lang":"es","country":"ES"},
    "El Salto Diario Política": {"lang":"es","country":"ES"},
    "elDiario Economía": {"lang":"es","country":"ES"}, "elDiario Política": {"lang":"es","country":"ES"},
    "Heraldo": {"lang":"es","country":"ES"}, "Hoy Extremadura": {"lang":"es","country":"ES"},
    "La Marea": {"lang":"es","country":"ES"}, "La Rioja": {"lang":"es","country":"ES"},
    "La Vanguardia Internacional": {"lang":"es","country":"ES"},
    "La Vanguardia Política": {"lang":"es","country":"ES"}, "Nació Digital": {"lang":"es","country":"ES"},
    "Okdiario": {"lang":"es","country":"ES"}, "Sur in English": {"lang":"es","country":"ES"},
    # FI
    "Etelä-Suomen Sanomat": {"lang":"fi","country":"FI"},
    "Helsingin Sanomat Politiikka": {"lang":"fi","country":"FI"},
    "Hufvudstadsbladet": {"lang":"fi","country":"FI"}, "Ilta-Sanomat Kotimaa": {"lang":"fi","country":"FI"},
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
    "Harbour Times": {"lang":"en","country":"HK"}, "HKFP Politics": {"lang":"en","country":"HK"},
    "HKFP World": {"lang":"en","country":"HK"}, "Hong Kong Business": {"lang":"en","country":"HK"},
    "Ming Pao": {"lang":"en","country":"HK"}, "Oriental Daily": {"lang":"en","country":"HK"},
    "RTHK Greater China": {"lang":"en","country":"HK"}, "SCMP Asia": {"lang":"en","country":"HK"},
    "SCMP Business": {"lang":"en","country":"HK"}, "SCMP Hong Kong": {"lang":"en","country":"HK"},
    "SCMP World": {"lang":"en","country":"HK"}, "The Witness HK": {"lang":"en","country":"HK"},
    # HU
    "Blikk": {"lang":"hu","country":"HU"}, "Daily News Hungary": {"lang":"hu","country":"HU"},
    "Direkt36": {"lang":"hu","country":"HU"}, "HungaryToday": {"lang":"hu","country":"HU"},
    "HVG Gazdaság": {"lang":"hu","country":"HU"}, "HVG Itthon": {"lang":"hu","country":"HU"},
    "HVG Világ": {"lang":"hu","country":"HU"}, "Index Belföld": {"lang":"hu","country":"HU"},
    "Index Gazdaság": {"lang":"hu","country":"HU"}, "Index Külföld": {"lang":"hu","country":"HU"},
    "Infostart": {"lang":"hu","country":"HU"}, "Magyar Hang": {"lang":"hu","country":"HU"},
    "Magyar Nemzet": {"lang":"hu","country":"HU"}, "Mandiner": {"lang":"hu","country":"HU"},
    "Média1": {"lang":"hu","country":"HU"}, "Népszava": {"lang":"hu","country":"HU"},
    "Portfolio Deviza": {"lang":"hu","country":"HU"}, "Portfolio Gazdaság": {"lang":"hu","country":"HU"},
    "Sportal": {"lang":"hu","country":"HU"}, "Telex Belföld": {"lang":"hu","country":"HU"},
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
    "Tempo Bisnis": {"lang":"id","country":"ID"}, "Viva": {"lang":"id","country":"ID"},
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
    "The Jerusalem Post News": {"lang":"en","country":"IL"}, "The Media Line": {"lang":"en","country":"IL"},
    "The Times of Israel": {"lang":"en","country":"IL"}, "Walla": {"lang":"he","country":"IL"},
    "Ynetnews": {"lang":"en","country":"IL"}, "Ynetnews World": {"lang":"en","country":"IL"},
    # IN
    "DNA India": {"lang":"en","country":"IN"}, "Economic Times Markets": {"lang":"en","country":"IN"},
    "Free Press Journal": {"lang":"en","country":"IN"},
    "Hindustan Times Business": {"lang":"en","country":"IN"},
    "Hindustan Times World": {"lang":"en","country":"IN"}, "India Today Feed": {"lang":"en","country":"IN"},
    "India Today India": {"lang":"en","country":"IN"}, "India Today World": {"lang":"en","country":"IN"},
    "Livemint Companies": {"lang":"en","country":"IN"}, "Livemint Markets": {"lang":"en","country":"IN"},
    "Mint Politics": {"lang":"en","country":"IN"}, "Moneycontrol": {"lang":"en","country":"IN"},
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
    # KR
    "KBS World": {"lang":"en","country":"KR"}, "Korea Pro": {"lang":"en","country":"KR"},
    "Maeil Business": {"lang":"en","country":"KR"}, "MK Business": {"lang":"en","country":"KR"},
    "NK News": {"lang":"en","country":"KR"}, "The Korea Times Business": {"lang":"en","country":"KR"},
    # MX
    "Contralínea": {"lang":"es","country":"MX"}, "El Economista MX": {"lang":"es","country":"MX"},
    "El Heraldo de México": {"lang":"es","country":"MX"}, "El Sol de México": {"lang":"es","country":"MX"},
    "Expansión Economía": {"lang":"es","country":"MX"}, "Expansión MX": {"lang":"es","country":"MX"},
    "La Jornada Política": {"lang":"es","country":"MX"}, "Pie de Página": {"lang":"es","country":"MX"},
    "Reforma": {"lang":"es","country":"MX"}, "Zeta Tijuana": {"lang":"es","country":"MX"},
    # NL
    "AD Binnenland": {"lang":"nl","country":"NL"}, "AD Politiek": {"lang":"nl","country":"NL"},
    "BN DeStem Regio": {"lang":"nl","country":"NL"}, "Brabant Dagblad Nieuws": {"lang":"nl","country":"NL"},
    "Brabants Dagblad Binnenland": {"lang":"nl","country":"NL"},
    "Dagblad van het Noorden": {"lang":"nl","country":"NL"},
    "De Gelderlander Binnenland": {"lang":"nl","country":"NL"},
    "De Gooi- en Eemlander": {"lang":"nl","country":"NL"}, "De Stentor Nieuws": {"lang":"nl","country":"NL"},
    "De Stentor Regio": {"lang":"nl","country":"NL"}, "De Telegraaf Nieuws": {"lang":"nl","country":"NL"},
    "De Volkskrant Nieuws": {"lang":"nl","country":"NL"},
    "De Volkskrant Politiek": {"lang":"nl","country":"NL"},
    "Eindhovens Dagblad Regio": {"lang":"nl","country":"NL"}, "EW Magazine": {"lang":"nl","country":"NL"},
    "Follow the Money": {"lang":"nl","country":"NL"}, "Haarlems Dagblad": {"lang":"nl","country":"NL"},
    "Het Parool Amsterdam": {"lang":"nl","country":"NL"}, "Het Parool Nieuws": {"lang":"nl","country":"NL"},
    "IJmuider Courant": {"lang":"nl","country":"NL"}, "Leeuwarder Courant": {"lang":"nl","country":"NL"},
    "Leidsch Dagblad": {"lang":"nl","country":"NL"}, "Metro NL": {"lang":"nl","country":"NL"},
    "Nederlands Dagblad Nieuws": {"lang":"nl","country":"NL"},
    "Noordhollands Dagblad": {"lang":"nl","country":"NL"}, "NOS Politiek": {"lang":"nl","country":"NL"},
    "NRC Binnenland": {"lang":"nl","country":"NL"}, "Nrc Economie": {"lang":"nl","country":"NL"},
    "NU.nl Economie": {"lang":"nl","country":"NL"}, "Trouw Groen": {"lang":"nl","country":"NL"},
    "Trouw Politiek": {"lang":"nl","country":"NL"}, "Tweakers": {"lang":"nl","country":"NL"},
    # NO
    "Adresseavisen": {"lang":"no","country":"NO"}, "Aftenposten Nyheter": {"lang":"no","country":"NO"},
    "Fædrelandsvennen": {"lang":"no","country":"NO"}, "iTromsø": {"lang":"no","country":"NO"},
    "Morgenbladet": {"lang":"no","country":"NO"}, "NRK Norge": {"lang":"no","country":"NO"},
    "NRK Urix": {"lang":"no","country":"NO"}, "Stavanger Aftenblad": {"lang":"no","country":"NO"},
    "Sunnmørsposten": {"lang":"no","country":"NO"}, "TV 2": {"lang":"no","country":"NO"},
    "VG Nyheter": {"lang":"no","country":"NO"}, "VG Sport": {"lang":"no","country":"NO"},
    # NZ
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
    # PH
    "Bandera": {"lang":"en","country":"PH"}, "Business World": {"lang":"en","country":"PH"},
    "BusinessWorld Economy": {"lang":"en","country":"PH"}, "GMA Money": {"lang":"en","country":"PH"},
    "GMA News Nation": {"lang":"en","country":"PH"}, "GMA News World": {"lang":"en","country":"PH"},
    "Inquirer Global": {"lang":"en","country":"PH"}, "Inquirer Nation": {"lang":"en","country":"PH"},
    "Interaksyon": {"lang":"en","country":"PH"}, "Manila Times News": {"lang":"en","country":"PH"},
    "PhilNews": {"lang":"en","country":"PH"}, "PhilStar Business": {"lang":"en","country":"PH"},
    "Philstar Nation": {"lang":"en","country":"PH"}, "Philstar World": {"lang":"en","country":"PH"},
    "Rappler Business": {"lang":"en","country":"PH"}, "Rappler Nation": {"lang":"en","country":"PH"},
    "Rappler World": {"lang":"en","country":"PH"},
    # PK
    "ARY News Pakistan": {"lang":"en","country":"PK"}, "Bol News": {"lang":"en","country":"PK"},
    "Business Recorder Pakistan": {"lang":"en","country":"PK"}, "Daily Times": {"lang":"en","country":"PK"},
    "Dawn Business": {"lang":"en","country":"PK"}, "Dawn Pakistan": {"lang":"en","country":"PK"},
    "Dawn World": {"lang":"en","country":"PK"}, "Geo News Pakistan": {"lang":"en","country":"PK"},
    "Minute Mirror": {"lang":"en","country":"PK"}, "Pakistan Observer": {"lang":"en","country":"PK"},
    "The Current": {"lang":"en","country":"PK"}, "The Express Tribune": {"lang":"en","country":"PK"},
    "The Express Tribune Business": {"lang":"en","country":"PK"},
    "The Express Tribune Pakistan": {"lang":"en","country":"PK"},
    "The Express Tribune World": {"lang":"en","country":"PK"},
    "The News International Pakistan": {"lang":"en","country":"PK"},
    # PL
    "Bankier.pl": {"lang":"pl","country":"PL"}, "Defence24": {"lang":"pl","country":"PL"},
    "Do Rzeczy": {"lang":"pl","country":"PL"}, "Dziennik Zachodni": {"lang":"pl","country":"PL"},
    "Fakt": {"lang":"pl","country":"PL"}, "Gazeta Krakowska": {"lang":"pl","country":"PL"},
    "Gazeta Pomorska": {"lang":"pl","country":"PL"}, "Interia Biznes": {"lang":"pl","country":"PL"},
    "Krytyka Polityczna": {"lang":"pl","country":"PL"}, "Money.pl": {"lang":"pl","country":"PL"},
    "Money.pl Gospodarka": {"lang":"pl","country":"PL"},
    "Newsweek Polska Polska": {"lang":"pl","country":"PL"},
    "Notes from Poland": {"lang":"pl","country":"PL"}, "OKO.press": {"lang":"pl","country":"PL"},
    "Onet Kraj": {"lang":"pl","country":"PL"}, "Onet Świat": {"lang":"pl","country":"PL"},
    "Polsat News Polska": {"lang":"pl","country":"PL"}, "Polsat News Świat": {"lang":"pl","country":"PL"},
    "Press.pl": {"lang":"pl","country":"PL"}, "RMF FM": {"lang":"pl","country":"PL"},
    "Rmf24 Fakty": {"lang":"pl","country":"PL"}, "Rzeczpospolita Ekonomia": {"lang":"pl","country":"PL"},
    "Rzeczpospolita Polityka": {"lang":"pl","country":"PL"}, "TVN24 Świat": {"lang":"pl","country":"PL"},
    "Wprost Biznes": {"lang":"pl","country":"PL"}, "Wprost Polityka": {"lang":"pl","country":"PL"},
    "Wprost Wiadomości": {"lang":"pl","country":"PL"}, "Wprost Świat": {"lang":"pl","country":"PL"},
    "Wyborcza Kraj": {"lang":"pl","country":"PL"},
    # PT
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
    "Meduza English": {"lang":"ru","country":"RU"}, "RBC": {"lang":"ru","country":"RU"},
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
    "Yahoo SG World": {"lang":"en","country":"SG"}, "Yahoo Singapore": {"lang":"en","country":"SG"},
    "Yahoo Singapore Feed": {"lang":"en","country":"SG"},
    # TR
    "Anadolu Agency": {"lang":"tr","country":"TR"}, "BBC Türkçe": {"lang":"tr","country":"TR"},
    "CNN Türk": {"lang":"tr","country":"TR"}, "CNN Türk Dünya": {"lang":"tr","country":"TR"},
    "Cumhuriyet Dünya": {"lang":"tr","country":"TR"}, "Cumhuriyet Ekonomi": {"lang":"tr","country":"TR"},
    "Cumhuriyet Türkiye": {"lang":"tr","country":"TR"}, "Daily Sabah": {"lang":"tr","country":"TR"},
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
    "Nguoi Lao Dong": {"lang":"vi","country":"VN"}, "Nguoi Lao Dong Thoi su": {"lang":"vi","country":"VN"},
    "Thanh Nien Chinh Tri": {"lang":"vi","country":"VN"}, "Thanh Nien Thoi su": {"lang":"vi","country":"VN"},
    "Tien Phong": {"lang":"vi","country":"VN"}, "Tien Phong Kinh te": {"lang":"vi","country":"VN"},
    "Vietnamnet Thoi su": {"lang":"vi","country":"VN"}, "VietnamPlus VN": {"lang":"vi","country":"VN"},
    "VnExpress Kinh doanh": {"lang":"vi","country":"VN"}, "VnExpress Thế giới": {"lang":"vi","country":"VN"},
    "VnExpress Thời sự": {"lang":"vi","country":"VN"},
}


def origin_of(source):
    o = SOURCE_ORIGIN.get(source, {})
    return {"lang": o.get("lang", DEFAULT_LANG),
            "country": o.get("country", DEFAULT_COUNTRY)}


class NotModified(Exception):
    pass


_http_cache: dict = {}
_fetched_urls: set = set()  # URLs fetched this run — used to emit the per-shard cache delta


def fetch(url):
    _fetched_urls.add(url)
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
    """Normalize whitespace in a headline. Some feeds embed non-breaking spaces
    (U+00A0) and zero-width characters that render as stray gaps or literal boxes;
    fold them into ordinary spaces, drop zero-width ones, and collapse runs (#31)."""
    t = (text or "").replace("\u00a0", " ").replace("\u200b", "").replace("\ufeff", "")
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


def crawl_zeit():
    """Die Zeit (Germany) — monthly Google sitemap at /gsitemaps/index.xml?
    date=YYYY-MM-01&unit=months&period=1. urlset has <loc>+<lastmod> but no
    news:title, so the title comes from the URL's last path segment. Article
    URLs are /section/YYYY-MM/slug or /section/YYYY-MM/DD/slug."""
    ym = datetime.now(timezone.utc).strftime("%Y-%m")
    url = f"https://www.zeit.de/gsitemaps/index.xml?date={ym}-01&unit=months&period=1"
    rows = sitemap_rows(fetch(url), re.compile(r"/\d{4}-\d{2}/.*[^/]$"))
    return crawl_sitemap_source(
        "Die Zeit", rows, re.compile(r"/([^/?]+)/?$"), ZEIT_MAX)


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
    "Die Zeit": "#e6e6e6",
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
    "Tempo": "#c41e1e",
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
    "AGI": "#0a4f9e", "Today": "#e2541b", "Wired Italia": "#2b2b2b", "Il Mattino": "#c20012",
    "Il Messaggero": "#0a3a6b", "Il Gazzettino": "#1a5276", "Quotidiano.net": "#2e6da4",
    "askanews": "#b8242a", "Domani": "#cf1f2e", "Il Secolo XIX": "#1a4f8b",
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
        f'<a class="row-act open" href="{url}" target="_blank" rel="noopener nofollow ugc"><span class="label">Open article</span> {OPEN_SVG}</a>'
        f'<button class="row-act share" type="button"><span class="label">Share article</span> {LINK_SVG}</button>'
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
        '      <a class="day-row day-row-all" href="/archive.html">'
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
                        pager="", head_links="", html_lang="de"):
    """Render a page. `limit` caps the server-rendered rows (index.html lazy-loads
    the rest); `count` overrides the badge total (so a paginated archive page shows
    the whole day's count); `pager`/`head_links` add archive pagination chrome;
    `html_lang` sets <html lang> (per-language for landing pages)."""
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
            .replace("<!-- OLDER_DATES -->", render_older_dates(older_dates))
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
            head.append(f'<link rel="prev" href="https://all.news{archive_page_url(date, p-1)}">')
        if p < pages:
            head.append(f'<link rel="next" href="https://all.news{archive_page_url(date, p+1)}">')
        title = (f"News Archive for {day_en} – all.news" if p == 1
                 else f"News Archive for {day_en} (page {p}) – all.news")
        write_rendered_html(
            sl, archive_page_path(date, p),
            title=title,
            description=f"All world news headlines collected on {day_en} by all.news.",
            canonical=f"https://all.news{url}",
            date_heading=day_de, older_dates=[], count=total,
            pager=render_pager(date, p, pages), head_links="".join(head))
    return pages


def write_sitemap(dates, landing_urls=()):
    urls = [
        '  <url><loc>https://all.news/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>',
        '  <url><loc>https://all.news/news/</loc><changefreq>daily</changefreq><priority>0.6</priority></url>',
        '  <url><loc>https://all.news/archive.html</loc><changefreq>daily</changefreq><priority>0.5</priority></url>',
    ]
    for u in landing_urls:
        urls.append(f'  <url><loc>https://all.news{u}</loc><changefreq>hourly</changefreq><priority>0.6</priority></url>')
    for d in dates:
        urls.append(f'  <url><loc>https://all.news/archive/{d}.html</loc><changefreq>never</changefreq><priority>0.3</priority></url>')
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.write("\n".join(urls))
        f.write("\n</urlset>\n")


# ---- Programmatic landing pages: /news/<country>/<lang>/ --------------------
# One server-rendered page per (country, language) we carry. The SPA's filtered
# views (?country=&lang=) render an empty <ul> to search-engine bots, so these
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
    "he": "Hebrew", "ar": "Arabic", "zh": "Chinese", "ru": "Russian",
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
    "sv": {"SE": "Sverige"},
    "pl": {"PL": "Polska"},
    "hu": {"HU": "Magyarország"},
    "ro": {"RO": "România"},
    "uk": {"UA": "Україна"},
    "ru": {"RU": "Россия"},
    "tr": {"TR": "Türkiye"},
    "id": {"ID": "Indonesia"},
    "vi": {"VN": "Việt Nam"},
    "ja": {"JP": "日本"},
    "zh": {"HK": "香港"},
    "ar": {"QA": "قطر"},
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
  <link rel="canonical" href="https://all.news/news/">
  <meta property="og:title" content="{title}">
  <meta property="og:description" content="{desc}">
  <meta property="og:type" content="website">
  <meta property="og:url" content="https://all.news/news/">
  <meta property="og:image" content="https://all.news/og-image.png">
  <meta name="robots" content="index, follow">
  <link rel="icon" href="/favicon.svg" type="image/svg+xml">
  <link rel="icon" href="/favicon-192.png" type="image/png" sizes="192x192">
  <link rel="apple-touch-icon" href="/favicon-192.png">
  <link rel="stylesheet" href="/styles.css?v=5">
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
    gtag('js', new Date());
    gtag('config', 'G-N83C506R65');
  </script>
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
      <span>© Copyright 2026 all.news</span>
      <span><a href="/">Home</a> · <a href="/archive.html">Archive</a></span>
    </footer>
  </div>
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
    html = HUB_TEMPLATE.format(title=escape(title), desc=escape(desc), cards="\n".join(cards))
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
        strings = LANDING_STRINGS.get(lang, LANDING_STRINGS["en"])
        phrase = strings["t"].format(c=cname)    # e.g. "日本のニュース", "Suisse : actualités"
        sl = [a for a in articles
              if (a.get("country") or "").upper() == cc
              and (a.get("lang") or "").lower() == lang]
        url = landing_url(cc, lang)
        landing_urls.append(url)
        # hreflang alternates: sibling languages of the same country.
        alts = "".join(
            f'<link rel="alternate" hreflang="{l2}" href="https://all.news{landing_url(cc, l2)}">'
            for l2 in sorted(set(langs_by_country[cc])))
        write_rendered_html(
            sl, landing_path(cc, lang),
            title=f"{phrase} – all.news",
            description=strings["d"].format(c=cname),
            canonical=f"https://all.news{url}",
            date_heading=f"{phrase} · {landing_date(lang, today)}",
            older_dates=[], limit=SSR_LIMIT, head_links=alts, html_lang=lang)
    write_news_hub(langs_by_country)
    return landing_urls


# ---- Crawl jobs, grouped so the GitHub Actions matrix can run them in parallel.
# "vpn"  = CH Media papers (403 datacenter ASNs → must run behind the Swiss VPN).
# "main" = everything else (plain feeds/sitemaps, no VPN needed).
def feed_jobs():
    return [(f["source"], (lambda f: lambda: parse_feed(f["source"], fetch(f["url"]), f.get("summary", True)))(f))
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
        ("Bauernzeitung", crawl_bauernzeitung),
        ("Die Zeit", crawl_zeit),
    ]
    jobs += [(n["source"], (lambda n: lambda: crawl_news_sitemap(n["source"], n["url"], n["max"]))(n))
             for n in NEWS_SITEMAPS]
    jobs += [(w["source"], (lambda w: lambda: crawl_wp(w["source"], w["index"], w["max"]))(w))
             for w in WP_SOURCES]
    return jobs


def ch_media_jobs():
    return [(s["source"], (lambda s: lambda: crawl_ch_media(s["source"], s["base"], s["max"]))(s))
            for s in CH_MEDIA_SOURCES]


def jobs_for(group):
    if group == "vpn":
        return ch_media_jobs()
    if group == "main":
        return feed_jobs() + main_sitemap_jobs()
    return feed_jobs() + main_sitemap_jobs() + ch_media_jobs()  # full run (local)


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
        except (urllib.error.URLError, urllib.error.HTTPError, ET.ParseError, OSError, ValueError) as e:
            print(f"  fail {name}: {e}", file=sys.stderr)
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


def write_outputs(articles):
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
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
    try:
        with open(INDEX_FILE, encoding="utf-8") as f:
            prior_dates = json.load(f).get("dates", [])
    except (FileNotFoundError, json.JSONDecodeError):
        prior_dates = archive_dates()  # local fallback (full runs)
    all_dates = sorted(set(prior_dates) | {today}, reverse=True)
    write_json(INDEX_FILE, {"dates": all_dates})
    write_json(HTTP_CACHE_FILE, _http_cache)

    write_colors_js()
    # Bottom-of-feed nav shows the 5 most recent days, then a "Full archive" link.
    older = [d for d in all_dates if d != today][:5]
    write_rendered_html(
        result, "index.html",
        title="World News From Every Source in One Place – all.news",
        description="Read world news from hundreds of sources on one page. all.news aggregates global headlines and updates hourly — filter by source, language and more.",
        canonical="https://all.news/",
        date_heading=fmt_day_heading(today),
        older_dates=older,
        limit=SSR_LIMIT,  # index head only; script.js lazy-loads the rest
    )
    write_archive_day(today, result)  # paginated static pages
    landing_urls = write_landing_pages(result, today)  # /news/<country>/<lang>/ + hub
    write_sitemap(all_dates, landing_urls)
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
