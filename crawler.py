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
import urllib.request
import urllib.error
import zlib
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
]

# Descriptive UA + contact. Generic bot UAs get 403'd by these sites.
USER_AGENT = "AllNewsBot/0.1 (+https://github.com/yourname/all.news; POC)"
TIMEOUT = 15
SUMMARY_MAX = 200  # keep snippets short — legal caution
DAILY_PER_SOURCE = 200  # max articles kept per source per day (anti-spam cap)
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
    "Tages-Anzeiger": "#565656",
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
    "Die Zeit": "#1c1c1c",
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
}


def color_for(source):
    """Badge color: the brand color if listed, else a stable hashed hue so every
    source gets a distinct color without hand-listing all of them (mirrors
    colorFor() in script.js)."""
    if source in SOURCE_COLORS:
        return SOURCE_COLORS[source]
    return f"hsl({djb2(source) % 360}, 65%, 45%)"


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
# <use>, instead of inlining the full SVG on every row — see ICON_SPRITE. Mirrors
# the EXT_SVG/OPEN_SVG/LINK_SVG constants in script.js.
EXT_SVG = '<svg class="ext" width="12" height="12"><use href="#ico-arrow"/></svg>'


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
OPEN_SVG = '<svg width="14" height="14"><use href="#ico-arrow"/></svg>'
LINK_SVG = '<svg width="14" height="14"><use href="#ico-link"/></svg>'

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
    url = escape(article["url"])
    return (
        f'      <li class="article" id="{escape(article_id(article))}"'
        f' data-lang="{escape(article.get("lang", DEFAULT_LANG))}"'
        f' data-country="{escape(article.get("country", DEFAULT_COUNTRY))}">'
        '<div class="meta-col">'
        f'<span class="source" style="background:{color}">{escape(article["source"])}</span>'
        f'<span class="time">{escape(fmt_time(article.get("published", "")))} {EXT_SVG}</span>'
        '</div>'
        f'<a class="title" href="{url}" target="_blank" rel="noopener">{escape(article["title"])}</a>'
        '<div class="row-actions">'
        f'<a class="row-act open" href="{url}" target="_blank" rel="noopener"><span class="label">Open</span> {OPEN_SVG}</a>'
        f'<button class="row-act share" type="button"><span class="label">Share</span> {LINK_SVG}</button>'
        '</div>'
        '</li>'
    )


def render_older_dates(dates):
    """Collapsible-looking date rows that link to each day's static archive page."""
    chev = ('<svg class="chev" viewBox="0 0 24 24" width="22" height="22" fill="none" '
            'stroke="currentColor" stroke-width="2"><path d="M6 9l6 6 6-6"/></svg>')
    return "\n".join(
        f'      <a class="day-row" href="/archive/{d}.html">'
        f'<span>{fmt_day_heading(d)}</span>{chev}</a>'
        for d in dates
    )


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
                        pager="", head_links=""):
    """Render a page. `limit` caps the server-rendered rows (index.html lazy-loads
    the rest); `count` overrides the badge total (so a paginated archive page shows
    the whole day's count); `pager`/`head_links` add archive pagination chrome."""
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
    html = (tmpl
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


def write_sitemap(dates):
    urls = [
        '  <url><loc>https://all.news/</loc><changefreq>hourly</changefreq><priority>1.0</priority></url>',
        '  <url><loc>https://all.news/archive.html</loc><changefreq>daily</changefreq><priority>0.5</priority></url>',
    ]
    for d in dates:
        urls.append(f'  <url><loc>https://all.news/archive/{d}.html</loc><changefreq>never</changefreq><priority>0.3</priority></url>')
    with open("sitemap.xml", "w", encoding="utf-8") as f:
        f.write('<?xml version="1.0" encoding="UTF-8"?>\n')
        f.write('<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n')
        f.write("\n".join(urls))
        f.write("\n</urlset>\n")


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

    result = existing_today + new
    data = {"generated": now_iso, "date": today, "count": len(result), "articles": result}
    write_json("crawled.json", data)                       # newest crawl
    write_json(os.path.join(ARCHIVE_DIR, f"{today}.json"), data)  # this date's crawl
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
    older = [d for d in all_dates if d != today]
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
    write_sitemap(all_dates)
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
