// Fill each i18n/<code>.json with CLDR data from Node's ICU:
// dates, month names, relative times, plural
// categories, country and language names. The "ui"
// strings are hand-written and kept; missing keys get
// the English text.
//
//   node i18n/build.mjs
//
// Rerun after adding a language file, a country or a
// language (COUNTRY_NAMES / LANG_EN_NAMES in script.js).
import fs from "node:fs";
import path from "node:path";

const DIR = path.dirname(new URL(import.meta.url).pathname);
const SCRIPT = fs.readFileSync(path.join(DIR, "..", "script.js"), "utf8");

// `const NAME = { XX: "Name", … };` from script.js.
function jsMap(name) {
  const body = SCRIPT.match(new RegExp(`const ${name} = \\{([\\s\\S]*?)\\};`))[1];
  return Object.fromEntries([...body.matchAll(/(\w+): "([^"]+)"/g)].map((m) => [m[1], m[2]]));
}
const COUNTRY_NAMES = jsMap("COUNTRY_NAMES");
const LANG_EN_NAMES = jsMap("LANG_EN_NAMES");

// UI code -> ICU locale, where they differ.
const LOCALES = {
  en: "en-GB", no: "nb", tl: "fil", sr: "sr-Latn",
  "zh-hans": "zh-Hans", "zh-hant": "zh-Hant-HK",
};
// Content language code -> ICU language for names.
const LANG_IDS = { tl: "fil" };
// Plural category -> one-letter code (server side).
const CAT = { zero: "z", one: "o", two: "t", few: "f", many: "m", other: "x" };

const en = JSON.parse(fs.readFileSync(path.join(DIR, "en.json"), "utf8")).ui;

function upperFirst(s, loc) {
  return s ? s[0].toLocaleUpperCase(loc) + s.slice(1) : s;
}

// Parts joined, with numbers as placeholders.
function template(parts, map) {
  return parts.map((p) => (p.type in map ? map[p.type] : p.value)).join("");
}

function build(code, loc) {
  const ext = `${loc}-u-ca-gregory-nu-latn`;
  const utc = (m) => new Date(Date.UTC(2026, m, 14));
  const full = new Intl.DateTimeFormat(ext, { day: "numeric", month: "long", year: "numeric", timeZone: "UTC" });
  const alone = new Intl.DateTimeFormat(ext, { month: "long", timeZone: "UTC" });
  const withYear = new Intl.DateTimeFormat(ext, { month: "long", year: "numeric", timeZone: "UTC" });
  const months = [...Array(12).keys()];
  const out = {
    dates: months.map((m) => template(full.formatToParts(utc(m)), { day: "{d}", year: "{y}" })),
    months: months.map((m) => upperFirst(alone.format(utc(m)), loc)),
    month_years: months.map((m) => upperFirst(template(withYear.formatToParts(utc(m)), { year: "{y}" }), loc)),
  };

  const rules = new Intl.PluralRules(ext);
  out.plurals = [...Array(100).keys()].map((n) => CAT[rules.select(n)]).join("");
  if (code === "en") {
    // Keep the site's own short English.
    out.ago = { m: { other: "{n} min ago" }, h: { other: "{n} h ago" }, d: { other: "{n} d ago" } };
  } else {
    const rtf = new Intl.RelativeTimeFormat(ext, { style: "short" });
    out.ago = {};
    for (const [key, unit] of [["m", "minute"], ["h", "hour"], ["d", "day"]]) {
      out.ago[key] = {};
      for (const cat of rules.resolvedOptions().pluralCategories) {
        let n = 1;
        while (n < 200 && rules.select(n) !== cat) n++;
        if (n < 200) out.ago[key][cat] = template(rtf.formatToParts(-n, unit), { integer: "{n}" });
      }
    }
  }

  const regions = new Intl.DisplayNames(loc, { type: "region", fallback: "none" });
  const regionsShort = new Intl.DisplayNames(loc, { type: "region", style: "short", fallback: "none" });
  out.countries = {};
  for (const [cc, name] of Object.entries(COUNTRY_NAMES)) {
    // "Hong Kong", not "Hong Kong SAR China".
    const local = cc === "HK" ? regionsShort.of(cc) : regions.of(cc);
    out.countries[cc] = code === "en" ? name : local || name;
  }
  const langs = new Intl.DisplayNames(loc, { type: "language", fallback: "none" });
  out.langs = {};
  for (const [l, name] of Object.entries(LANG_EN_NAMES)) {
    const local = langs.of(LANG_IDS[l] || l);
    out.langs[l] = code === "en" ? name : upperFirst(local || name, loc);
  }
  return out;
}

for (const file of fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).sort()) {
  const code = file.slice(0, -5);
  const loc = LOCALES[code] || code;
  const fp = path.join(DIR, file);
  const old = JSON.parse(fs.readFileSync(fp, "utf8"));
  const ui = {};
  const missing = [];
  for (const key of Object.keys(en)) {
    if (old.ui && old.ui[key]) ui[key] = old.ui[key];
    else { ui[key] = en[key]; missing.push(key); }
  }
  if (missing.length) console.warn(`${code}: English for ${missing.join(", ")}`);
  const data = { locale: loc, ui, ...build(code, loc) };
  fs.writeFileSync(fp, JSON.stringify(data, null, 1) + "\n");
}
console.log("built", fs.readdirSync(DIR).filter((f) => f.endsWith(".json")).length, "files");
