const SOURCE_COLORS = {
  "SRF": "#d52b1e",
  "RTS": "#e2001a",
  "Le Temps": "#1a3c5e",
  "Blick": "#e2001a",
  "20 Minuten": "#0055aa",
  "Tages-Anzeiger": "#1c1c1c",
  "NZZ": "#444444",
  "Weltwoche": "#7a0019",
  "Nebelspalter": "#c8102e",
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
};

function fmtDate(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d)) return "";
  return d.toISOString().slice(0, 10); // YYYY-MM-DD
}

function fmtDateTime(s) {
  if (!s) return "";
  const d = new Date(s);
  if (isNaN(d)) return "";
  // Swiss local date + exact crawl time
  return d.toLocaleString("sv-SE", { timeZone: "Europe/Zurich" }); // YYYY-MM-DD HH:MM:SS
}

function ts(a) {
  const d = new Date(a.published);
  return isNaN(d) ? 0 : d.getTime();
}

// Preferred source order; listed sources rank first (in this order), rest after. Prioritize sources with fewer updates for fairer visibility.
const SOURCE_PRIORITY = [
  "Inside Paradeplatz",
  "Infosperber",
  "Inside IT",
  "Bilanz",
  "Republik",
  "Weltwoche",
  "NZZ",
  "Tages-Anzeiger",
];
function prio(a) {
  const i = SOURCE_PRIORITY.indexOf(a.source);
  return i === -1 ? SOURCE_PRIORITY.length : i;
}

function sortArticles(articles, mode) {
  const out = articles.slice();
  if (mode === "source") {
    out.sort((a, b) =>
      a.source.localeCompare(b.source) || ts(b) - ts(a));
  } else {
    // newest first, then by custom source preference within the same timestamp
    out.sort((a, b) => ts(b) - ts(a) || prio(a) - prio(b));
  }
  return out;
}

// Excluded sources (lowercased), persisted in the URL: ?exclude=blick,watson
const excluded = new Set(
  (new URLSearchParams(location.search).get("exclude") || "")
    .split(",").map((s) => s.trim().toLowerCase()).filter(Boolean)
);

function isExcluded(a) {
  return excluded.has(a.source.toLowerCase());
}

function syncUrl() {
  const params = new URLSearchParams(location.search);
  if (excluded.size) params.set("exclude", [...excluded].join(","));
  else params.delete("exclude");
  const qs = params.toString();
  history.replaceState(null, "", location.pathname + (qs ? "?" + qs : ""));
}

function render(articles, mode) {
  const list = document.getElementById("list");
  list.innerHTML = "";
  for (const a of sortArticles(articles, mode).filter((a) => !isExcluded(a))) {
    const li = document.createElement("li");

    const link = document.createElement("a");
    link.href = a.url;
    link.textContent = a.title;
    link.target = "_blank";
    link.rel = "noopener";
    li.appendChild(link);

    const meta = document.createElement("div");
    meta.className = "info";

    const date = fmtDateTime(a.published);
    if (date) {
      const d = document.createElement("span");
      d.className = "date";
      d.textContent = date;
      meta.appendChild(d);
    }

    const src = document.createElement("span");
    src.className = "source";
    src.textContent = a.source;
    src.style.background = SOURCE_COLORS[a.source] || "#888";
    meta.appendChild(src);

    li.appendChild(meta);
    list.appendChild(li);
  }
}

const meta = document.getElementById("meta");
const daySel = document.getElementById("day");
function sortMode() {
  const el = document.querySelector('input[name="sort"]:checked');
  return el ? el.value : "date";
}

let current = [];

function buildFilters(articles) {
  const box = document.getElementById("filters");
  box.innerHTML = "";
  const sources = [...new Set(articles.map((a) => a.source))].sort();
  for (const s of sources) {
    const label = document.createElement("label");
    const cb = document.createElement("input");
    cb.type = "checkbox";
    cb.checked = excluded.has(s.toLowerCase());
    cb.addEventListener("change", () => {
      if (cb.checked) excluded.add(s.toLowerCase());
      else excluded.delete(s.toLowerCase());
      syncUrl();
      render(current, sortMode());
    });
    label.appendChild(cb);
    label.append(" " + s);
    box.appendChild(label);
  }
}

function load(url) {
  meta.textContent = "loading…";
  fetch(url)
    .then((r) => r.json())
    .then((data) => {
      current = data.articles;
      meta.textContent =
        `${data.count} articles · ${data.date || ""} · updated ${fmtDate(data.generated)}`;
      buildFilters(current);
      render(current, sortMode());
    })
    .catch((e) => { meta.textContent = "failed to load " + url + ": " + e; });
}

// Populate day picker from the archive index; "latest" = newest crawl.
fetch("archive/index.json")
  .then((r) => r.json())
  .then((idx) => {
    const opts = ['<option value="crawled.json">latest</option>'];
    for (const d of idx.dates || []) {
      opts.push(`<option value="archive/${d}.json">${d}</option>`);
    }
    daySel.innerHTML = opts.join("");
  })
  .catch(() => { daySel.innerHTML = '<option value="crawled.json">latest</option>'; })
  .finally(() => load(daySel.value));

daySel.addEventListener("change", () => load(daySel.value));
for (const radio of document.querySelectorAll('input[name="sort"]')) {
  radio.addEventListener("change", () => render(current, sortMode()));
}
