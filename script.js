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

function ts(a) {
  const d = new Date(a.published);
  return isNaN(d) ? 0 : d.getTime();
}

function sortArticles(articles, mode) {
  const out = articles.slice();
  if (mode === "source") {
    out.sort((a, b) =>
      a.source.localeCompare(b.source) || ts(b) - ts(a));
  } else {
    out.sort((a, b) => ts(b) - ts(a)); // newest first
  }
  return out;
}

function render(articles, mode) {
  const list = document.getElementById("list");
  list.innerHTML = "";
  for (const a of sortArticles(articles, mode)) {
    const li = document.createElement("li");

    const link = document.createElement("a");
    link.href = a.url;
    link.textContent = a.title;
    link.target = "_blank";
    link.rel = "noopener";
    li.appendChild(link);

    const meta = document.createElement("div");
    meta.className = "info";

    const date = fmtDate(a.published);
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

fetch("crawled.json")
  .then((r) => r.json())
  .then((data) => {
    document.getElementById("meta").textContent =
      `${data.count} articles · updated ${fmtDate(data.generated)}`;

    const current = () =>
      document.querySelector('input[name="sort"]:checked').value;
    render(data.articles, current());

    for (const radio of document.querySelectorAll('input[name="sort"]')) {
      radio.addEventListener("change", () => render(data.articles, current()));
    }
  })
  .catch((e) => {
    document.getElementById("meta").textContent = "failed to load crawled.json: " + e;
  });
