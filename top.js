// Most reported (/top/): lists from /top/<window>.json.
// Rows mirror top_item() in crawler.py.
(() => {
  const list = document.getElementById("topList");
  const sel = document.getElementById("topCc");
  const wins = document.getElementById("topWin");
  if (!list || !sel || !wins) return;
  const STORE = "allnews.top"; // {win, cc} of the last visit
  const EXT = ' target="_blank" rel="noopener nofollow ugc"';
  const esc = (s) => String(s).replace(/[&<>"']/g, (c) =>
    ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
  const color = (s) => (typeof SOURCE_COLORS !== "undefined" && SOURCE_COLORS[s]) || "#888";
  const kicker = (t) => t.replace(/^[A-ZÄÖÜ]{4,}\s*[-–:]\s*/, ""); // TOP_KICKER
  // articleId() of script.js: slug + URL hash.
  const slug = (s) => (s || "").normalize("NFKD").replace(/[\u0300-\u036f]/g, "")
    .toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "");
  function djb2(s) {
    let h = 5381;
    for (let i = 0; i < s.length; i++) h = ((h * 33) + s.charCodeAt(i)) >>> 0;
    return h;
  }
  const loaded = {}; // window -> promise of its JSON
  const load = (w) => loaded[w] || (loaded[w] = fetch(`/top/${w}.json`).then((r) => {
    if (!r.ok) throw new Error(r.status);
    return r.json();
  }));
  let data = null; // the shown window
  let state = { win: "24h", cc: "" };
  try { state = { ...state, ...JSON.parse(localStorage.getItem(STORE) || "{}") }; } catch (e) { /* ignore */ }

  // Headline in the visitor's language, else the biggest edition.
  function pick(st, cc) {
    const ui = (I18N.lang || "en").split("-")[0];
    const inCc = cc ? st.ed.filter((e) => e[0] === cc) : st.ed;
    return inCc.find((e) => e[1] === ui) || st.ed.find((e) => e[1] === ui) || inCc[0] || st.ed[0];
  }

  // Live editions open their landing page.
  function edition(e) {
    const [cc, lang, n, title, url, live] = e;
    const path = live && data.paths[`${cc}/${lang}`];
    const href = path ? `${path}#${slug(title)}-${djb2(url).toString(16)}` : url;
    return `<li><a href="${esc(href)}"${path ? "" : EXT}>` +
      `<span class="ed-where">${esc(I18N.country(cc))} · ${esc(I18N.langName(lang))}</span>` +
      `<span class="ed-title" dir="auto">${esc(kicker(title))}</span>` +
      `<span class="ed-n">${esc(I18N.count("event_count", n))}</span></a></li>`;
  }

  // Recent: "3 h ago"; older: the date.
  function when(t) {
    return Date.now() - new Date(t) < 7 * 864e5 ? I18N.ago(t) : I18N.date(t.slice(0, 10));
  }

  function item(st, row, rank, cc) {
    const ed = pick(st, cc);
    const dots = st.dots.map((s) => `<i style="background:${esc(color(s))}" title="${esc(s)}"></i>`).join("");
    return `<li class="top-item"><span class="top-rank">${rank}</span><div class="top-body">` +
      `<a class="top-title" dir="auto" href="${esc(ed[4])}"${EXT}>${esc(kicker(ed[3]))}</a>` +
      `<div class="top-meta"><span class="top-n">${esc(I18N.count("outlet_count", row[1]))}</span>` +
      (cc ? "" : `<span>${esc(I18N.count("country_count", st.cc.length))}</span>`) +
      `<time datetime="${esc(st.t1)}">${esc(when(st.t1))}</time>` +
      `<span class="top-dots" aria-hidden="true">${dots}</span></div>` +
      `<details class="top-eds"><summary>${esc(I18N.count("edition_count", st.ne))}</summary>` +
      `<ul>${st.ed.map(edition).join("")}</ul></details></div></li>`;
  }

  function render() {
    const rows = data.lists[state.cc || "all"] || [];
    list.innerHTML = rows.length
      ? rows.map((r, i) => item(data.stories[r[0]], r, i + 1, state.cc)).join("")
      : `<li class="top-empty">${esc(I18N.t("no_stories"))}</li>`;
    for (const b of wins.querySelectorAll(".choice")) {
      b.setAttribute("aria-pressed", String(b.dataset.win === state.win));
    }
  }

  function save() {
    try { localStorage.setItem(STORE, JSON.stringify(state)); } catch (e) { /* ignore */ }
  }

  // Countries with a list in this window.
  function fillCountries() {
    const ccs = new Set(Object.keys(data.lists).filter((k) => k !== "all"));
    const sorted = [...ccs].sort((a, b) => I18N.compare(I18N.country(a), I18N.country(b)));
    sel.innerHTML = `<option value="">${esc(I18N.t("all_countries"))}</option>` +
      sorted.map((c) => `<option value="${esc(c)}">${esc(I18N.country(c))}</option>`).join("");
    if (!ccs.has(state.cc)) state.cc = "";
    sel.value = state.cc;
  }

  // Load a window, then draw it.
  function show(win) {
    return Promise.all([load(win), I18N.ready]).then(([d]) => {
      if (state.win !== win) return;
      data = d;
      fillCountries();
      render();
    });
  }

  wins.addEventListener("click", (e) => {
    const b = e.target.closest(".choice");
    if (!b || b.dataset.win === state.win) return;
    state.win = b.dataset.win;
    save();
    show(state.win).catch(() => {});
  });
  sel.addEventListener("change", () => {
    if (!data) return;
    state.cc = sel.value;
    save();
    render();
  });

  const known = [...wins.querySelectorAll(".choice")].map((b) => b.dataset.win);
  if (!known.includes(state.win)) state.win = "24h";
  show(state.win).catch(() => { /* keep the server-rendered list */ });
})();
