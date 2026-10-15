// Shared by every page (feed, archive, hub, legal, settings).

// "Cookie settings" reopens Google's consent message (the AdSense CMP, set up
// under AdSense → Privacy & messaging). Until that CMP has loaded, the link
// simply navigates to /cookies#manage, which explains the other options.
window.googlefc = window.googlefc || {};
window.googlefc.callbackQueue = window.googlefc.callbackQueue || [];
document.addEventListener("click", (e) => {
  const link = e.target.closest(".cookie-settings");
  const fc = window.googlefc;
  if (!link || typeof fc.showRevocationMessage !== "function") return;
  e.preventDefault();
  fc.callbackQueue.push(fc.showRevocationMessage);
});

// ---------- UI language (#53) ----------
// Strings live in /i18n/<code>.json (see i18n/build.mjs). Pages are served in
// English, or in their own language (landing and section language pages carry
// <html data-ui>); this swaps the [data-i18n…] markers to the visitor's
// language. Priority: the Settings choice, then the page's own language, then
// the browser's languages, then English. The <head> script hides the marked text
// (.i18n-wait) until it's translated, so English never flashes.
window.I18N = (() => {
  // Native names; one i18n/<code>.json each.
  const NAMES = {
    en: "English", de: "Deutsch", fr: "Français", it: "Italiano", es: "Español",
    nl: "Nederlands", pt: "Português", pl: "Polski", sv: "Svenska", no: "Norsk",
    da: "Dansk", fi: "Suomi", el: "Ελληνικά", cs: "Čeština", hu: "Magyar", ro: "Română",
    uk: "Українська", tr: "Türkçe", ja: "日本語", id: "Bahasa Indonesia", vi: "Tiếng Việt",
    he: "עברית", ar: "العربية", "zh-hans": "简体中文", "zh-hant": "繁體中文", ru: "Русский",
    ko: "한국어", hi: "हिन्दी", bn: "বাংলা", ta: "தமிழ்", te: "తెలుగు", mr: "मराठी",
    ml: "മലയാളം", kn: "ಕನ್ನಡ", gu: "ગુજરાતી", pa: "ਪੰਜਾਬੀ", ur: "اردو",
    ms: "Bahasa Melayu", tl: "Filipino", th: "ไทย", ca: "Català",
    bg: "Български", et: "Eesti", fa: "فارسی", hr: "Hrvatski", lt: "Lietuvių",
    lv: "Latviešu", sk: "Slovenčina", sr: "Srpski", sl: "Slovenščina",
    eu: "Euskara", gl: "Galego", ga: "Gaeilge", cy: "Cymraeg", gd: "Gàidhlig",
  };
  const STORE = "allnews.uilang"; // a code, or "auto"
  const VERSION = 1;              // bump when a dictionary changes
  const ALIASES = { nb: "no", nn: "no", fil: "tl" };
  const RTL = ["ar", "he", "fa", "ur"];
  const CATS = { z: "zero", o: "one", t: "two", f: "few", m: "many", x: "other" };
  const root = document.documentElement;
  const served = root.dataset.ui || "en"; // the language the HTML is in

  function saved() { try { return localStorage.getItem(STORE); } catch (e) { return null; } }

  // Browser tag -> our code, or null.
  function match(tag) {
    tag = (tag || "").toLowerCase();
    if (tag.startsWith("zh")) return /-(hant|tw|hk|mo)\b/.test(tag) ? "zh-hant" : "zh-hans";
    const code = tag.split("-")[0];
    return (ALIASES[code] || code) in NAMES ? ALIASES[code] || code : null;
  }

  function pick() {
    const s = saved();
    if (s in NAMES) return s;
    if (root.dataset.ui) return root.dataset.ui;
    for (const tag of navigator.languages || [navigator.language]) {
      const code = match(tag);
      if (code) return code;
    }
    return "en";
  }

  let lang = pick();
  let dict = null;
  let rules = null;
  if (lang === served) root.classList.remove("i18n-wait");

  function load(code) {
    return fetch(`/i18n/${code}.json?v=${VERSION}`)
      .then((r) => { if (!r.ok) throw new Error(r.status); return r.json(); })
      .then((d) => {
        dict = d; lang = code;
        // RTL text keeps its punctuation in place.
        if (RTL.includes(code)) root.dataset.uidir = "rtl"; else delete root.dataset.uidir;
        try { rules = new Intl.PluralRules(d.locale); } catch (e) { rules = null; }
        return d;
      });
  }

  function t(key, vars) {
    let s = (dict && dict.ui[key]) || key;
    for (const k in vars || {}) s = s.replace(`{${k}}`, vars[k]);
    return s;
  }

  // "2026-10-14" -> "14. Oktober 2026".
  function date(iso) {
    const [y, m, d] = (iso || "").split("-");
    const tpl = dict && dict.dates[m - 1];
    return tpl ? tpl.replace("{d}", +d).replace("{y}", y) : iso;
  }
  function month(m) { return dict ? dict.months[m - 1] : String(m); }
  function monthYear(m, y) { return dict ? dict.month_years[m - 1].replace("{y}", y) : `${m}/${y}`; }

  // "vor 3 Std." (mirrors fmt_ago in crawler.py).
  function ago(s) {
    const when = new Date(s);
    if (!s || isNaN(when) || !dict) return "";
    const mins = Math.floor((Date.now() - when.getTime()) / 60000);
    if (mins < 1) return t("just_now");
    const [unit, n] = mins < 60 ? ["m", mins]
      : mins < 24 * 60 ? ["h", Math.floor(mins / 60)] : ["d", Math.floor(mins / (24 * 60))];
    const cat = n < dict.plurals.length ? CATS[dict.plurals[n]] : rules ? rules.select(n) : "other";
    const forms = dict.ago[unit];
    return (forms[cat] || forms.other).replace("{n}", n);
  }

  function country(cc) {
    cc = (cc || "").toUpperCase();
    return (dict && dict.countries[cc]) || cc;
  }
  function langName(code) { return (dict && dict.langs[code]) || code; }
  function compare(a, b) { return a.localeCompare(b, dict ? dict.locale : undefined); }

  // Hub: re-sort by the translated names.
  function sortHub() {
    const grid = document.querySelector(".hub-grid");
    if (!grid) return;
    const name = (el) => el.firstElementChild.textContent;
    for (const ul of grid.querySelectorAll("ul")) {
      ul.append(...[...ul.children].sort((a, b) => compare(name(a), name(b))));
    }
    grid.append(...[...grid.children].sort((a, b) => compare(name(a), name(b))));
  }

  // Translate the markers under `el`.
  function apply(el) {
    const each = (sel, fn) => { for (const n of el.querySelectorAll(sel)) fn(n); };
    each("[data-i18n]", (n) => { n.textContent = t(n.dataset.i18n); });
    each("[data-i18n-ph]", (n) => { n.placeholder = t(n.dataset.i18nPh); });
    each("[data-i18n-aria]", (n) => { n.setAttribute("aria-label", t(n.dataset.i18nAria)); });
    each("[data-i18n-title]", (n) => { n.title = t(n.dataset.i18nTitle); });
    each("[data-date]", (n) => { n.textContent = date(n.dataset.date); });
    each("[data-cc]", (n) => { n.textContent = country(n.dataset.cc); });
    each("[data-lang-name]", (n) => { n.textContent = langName(n.dataset.langName); });
    sortHub();
  }

  // Resolves once the strings are in.
  const ready = load(lang)
    .catch(() => (lang !== "en" ? load("en") : null))
    .catch(() => null)
    .then(() => {
      if (dict && lang !== served) apply(document);
      root.classList.remove("i18n-wait");
      return dict;
    });

  // Settings: switch now and remember.
  function set(code) {
    try { localStorage.setItem(STORE, code); } catch (e) { /* ignore */ }
    return load(pick()).then(() => apply(document), () => {});
  }

  return {
    NAMES, ready, t, date, month, monthYear, ago, country, langName, compare, apply, set,
    get lang() { return lang; },
    served,
    choice: () => (saved() in NAMES ? saved() : "auto"),
  };
})();
