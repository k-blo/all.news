"""Cluster the live feed into events (#58)."""
# Runs after the crawl's reduce step.
# Needs numpy, onnxruntime, tokenizers (requirements-clustering.txt).
import hashlib
import html
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone

import numpy as np
import onnxruntime as ort
from tokenizers import Tokenizer

import crawler

MODEL_DIR = os.environ.get("CLUSTERING_MODEL", "models/embeddinggemma")
MODEL_ID = "onnx-community/embeddinggemma-300m-ONNX"  # cache key
CACHE = os.path.join("state", "embeddings.npz")  # not served publicly
BUDGET = float(os.environ.get("CLUSTERING_BUDGET", "720"))  # embedding seconds
PROMPT = "task: sentence similarity | query: "
EMBED_SUMMARY = 200  # summary chars used, as calibrated
MAX_TOKENS, BATCH, CHUNK = 256, 32, 256

TAU = 0.80                    # same-event cutoff (with fetched descriptions)
XTAU = 0.85                   # cross-edition cutoff, event averages
MAX_SX = 12                   # other editions listed per event
BONUS, BONUS_FROM = 0.08, 0.72  # shared rare word lifts near-misses
RARE_DF = 5                   # rare: in at most 5 articles
MIN_SOURCES, MAX_SHARE = 2, 0.5

# Most reported (/top/): stories ranked by outlets.
TOP_MEMORY = os.path.join("state", "top.npz")  # 7-day stories
TOP_DAYS = 7
TOP_MATCH = 0.85              # same story in an earlier run
TOP_LINK = 0.3                # shared articles: same story
TOP_MAX_U = 5000              # article keys kept per story
TOP_ALL, TOP_CC = 20, 10      # list lengths shown
TOP_KEEP = 5                  # outlets to be remembered
TOP_MAX_ED = 40               # editions listed per story

# Horoscopes, roundups, tickers: never grouped.
HARD = re.compile(
    r"\+\+|News-?[Tt]icker|im Ticker|Newsblog|Taza Khabar|politics live|news live|"
    r"[Hh]oroscope|[Hh]oroskop|[Oo]roscopo|[Hh]or[oó]scopo|[Rr]ashifal|राशिफल|राशि –|"
    r"मूलांक|पंचांग|[Pp]anchang|[Tt]arot|гороскоп|Гороскоп|星座|占い|運勢|运势|운세|"
    r"\b[Ll]otto\b|[Ll]oter[ií]a|Primitiva|Chispazo|Euro[Mm]illion|Powerball|ロト|ナンバーズ|宝くじ")
# Live blogs: may join, never lead.
LIVE = re.compile(
    r"\bLIVE\b|\bLive:|live updates|live blog|Liveticker|Live-Ticker|EN DIRECT|^En direct|"
    r"IN DIRETTA|^In diretta|EN VIVO|^En vivo|AO VIVO|^Ao vivo|"
    r"(?<![ऀ-ॿ])लाइव(?![ऀ-ॿ])|速報予定|ライブ中継|as it happened")
# Ticker-style titles make poor leads.
NOISY = re.compile(r"^([A-ZÄÖÜ]{3,}\s+)+[A-ZÄÖÜ]{3,}\s*[-–]|\bLIVE\b|Liveticker|^【|\+\+\+")
WORD = re.compile(r"\w+")
CJK = re.compile(r"[぀-ヿ㐀-鿿]+")


def text_of(a):
    """Title plus summary, as tested."""
    title, summary = a["title"].strip(), (a.get("summary") or "")[:EMBED_SUMMARY].strip()
    return f"{title}. {summary}" if summary else title


def load_cache():
    """URL -> float16 vector."""
    try:
        with np.load(CACHE) as z:
            if str(z["model"]) != MODEL_ID:
                return {}
            urls = bytes(z["urls"]).decode().split("\n")
            return dict(zip(urls, z["vecs"]))
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return {}


def save_cache(cache):
    os.makedirs(os.path.dirname(CACHE), exist_ok=True)
    urls = list(cache)
    if not urls:
        return
    np.savez(CACHE, model=np.array(MODEL_ID),
             urls=np.frombuffer("\n".join(urls).encode(), dtype=np.uint8),
             vecs=np.stack([cache[u] for u in urls]))


def load_model():
    tok = Tokenizer.from_file(os.path.join(MODEL_DIR, "tokenizer.json"))
    tok.enable_truncation(MAX_TOKENS)
    tok.enable_padding(pad_id=0, pad_token="<pad>")
    opts = ort.SessionOptions()
    opts.intra_op_num_threads = os.cpu_count() or 4
    sess = ort.InferenceSession(os.path.join(MODEL_DIR, "onnx", "model.onnx"), opts,
                                providers=["CPUExecutionProvider"])
    return tok, sess


def embed_new(rows, cache):
    """Newest first, until BUDGET runs out."""
    tok, sess = load_model()
    start = time.monotonic()
    for i in range(0, len(rows), CHUNK):
        chunk = sorted(rows[i:i + CHUNK], key=lambda a: len(text_of(a)))  # less padding
        for j in range(0, len(chunk), BATCH):
            part = chunk[j:j + BATCH]
            enc = tok.encode_batch([PROMPT + text_of(a) for a in part])
            feed = {"input_ids": np.array([e.ids for e in enc], dtype=np.int64),
                    "attention_mask": np.array([e.attention_mask for e in enc], dtype=np.int64)}
            vecs = sess.run(["sentence_embedding"], feed)[0]
            vecs = vecs / np.linalg.norm(vecs, axis=1, keepdims=True)
            for a, v in zip(part, vecs):
                cache[a["url"]] = v.astype(np.float16)
        if time.monotonic() - start > BUDGET:
            return i + len(chunk)
    return len(rows)


def vague(a):
    """Short title, no summary."""
    title = a["title"].strip()
    cjk = sum(ch > "⺀" for ch in title)
    short = len(title) < (10 if cjk > len(title) / 2 else 30)
    return short and not (a.get("summary") or "").strip()


def words(a):
    """4+ letter words; CJK as bigrams."""
    text = (a["title"] + " " + html.unescape((a.get("summary") or "")[:EMBED_SUMMARY])).lower()
    out = {w for w in WORD.findall(CJK.sub(" ", text)) if len(w) >= 4}
    for run in CJK.findall(text):
        out |= {run[k:k + 2] for k in range(len(run) - 1)}
    return out


def rare_pairs(rows):
    """Pairs sharing a rare word."""
    docs = [words(a) for a in rows]
    df = Counter(w for d in docs for w in d)
    holders = defaultdict(list)
    for i, d in enumerate(docs):
        for w in d:
            if 2 <= df[w] <= RARE_DF:
                holders[w].append(i)
    return {(ids[x], ids[y]) for ids in holders.values()
            for x in range(len(ids)) for y in range(x + 1, len(ids))}


def star(sim, sources, skip, no_lead):
    """Best-connected article leads its neighbors."""
    adj = sim >= TAU
    adj[skip, :] = False
    adj[:, skip] = False
    np.fill_diagonal(adj, False)
    free, can_lead = ~skip, ~skip & ~no_lead
    deg = adj.sum(1)
    events = []
    while True:
        cand = np.where(free & can_lead)[0]
        if not len(cand):
            break
        seed = int(cand[np.argmax(deg[cand])])
        if deg[seed] < MIN_SOURCES - 1:
            break
        mem = [seed] + [int(j) for j in np.where(adj[seed] & free)[0]]
        srcs = [sources[m] for m in mem]
        if len(set(srcs)) < MIN_SOURCES or max(map(srcs.count, set(srcs))) / len(mem) > MAX_SHARE:
            can_lead[seed] = False
            continue
        events.append(mem)
        free[mem] = False
        deg -= adj[:, mem].sum(1)
    return events


def lead_first(mem, sim, rows):
    """Most central clean headline first."""
    sub = sim[np.ix_(mem, mem)].copy()
    np.fill_diagonal(sub, 1.0)
    score = sub.mean(1) - np.array([0.05 if NOISY.search(rows[m]["title"]) else 0 for m in mem])
    lead = mem[int(np.argmax(score))]
    return sorted(mem, key=lambda m: -2 if m == lead else -sim[lead, m])


def cluster(rows, vecs):
    """Events of one country+language."""
    v = np.stack(vecs).astype(np.float32)
    v /= np.linalg.norm(v, axis=1, keepdims=True)
    sim = v @ v.T
    np.fill_diagonal(sim, -1.0)
    boosted = sim.copy()
    for i, j in rare_pairs(rows):
        if sim[i, j] >= BONUS_FROM:
            boosted[i, j] = boosted[j, i] = sim[i, j] + BONUS
    skip = np.array([vague(a) or bool(HARD.search(a["title"])) for a in rows])
    live = np.array([bool(LIVE.search(a["title"])) for a in rows])
    return [lead_first(e, sim, rows) for e in star(boosted, [a["source"] for a in rows], skip, live)]


def centroid(vecs):
    """Unit-length average vector."""
    c = np.mean(np.stack(vecs).astype(np.float32), axis=0)
    return c / np.linalg.norm(c)


def link_events(events):
    """Stories: events across editions."""
    if len(events) < 2:
        return []
    m = np.stack([e["c"] for e in events])
    sim = m @ m.T
    keys = {}
    kid = np.array([keys.setdefault(e["key"], len(keys)) for e in events])
    sim[kid[:, None] == kid[None, :]] = -1.0  # same edition: no link
    adj = sim >= XTAU
    free = np.ones(len(events), bool)
    deg = adj.sum(1)
    stories = []
    while True:
        cand = np.where(free)[0]
        if not len(cand):
            break
        seed = int(cand[np.argmax(deg[cand])])
        if deg[seed] < 1:
            break
        mem = [seed] + [int(j) for j in np.where(adj[seed] & free)[0]]
        stories.append(mem)
        free[mem] = False
        deg -= adj[:, mem].sum(1)
    return stories


def tag_stories(events):
    """Lead rows list other editions (sx)."""
    stories = link_events(events)
    for story in stories:
        for i in story:
            best = {}  # one entry per edition: its largest event
            for j in story:
                e = events[j]
                if e["key"] != events[i]["key"] and len(e["rows"]) > len(best.get(e["key"], {"rows": []})["rows"]):
                    best[e["key"]] = e
            others = sorted(best.values(), key=lambda e: -len(e["rows"]))[:MAX_SX]
            if others:
                events[i]["rows"][0]["sx"] = [
                    [e["key"][0], e["key"][1], len(e["rows"]), e["rows"][0]["title"], e["rows"][0]["url"]]
                    for e in others]
    return stories


def utc(s):
    """ISO time as UTC, seconds."""
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat(timespec="seconds")


def edition_of(evs):
    """[cc, lang, articles, headline, url, live]."""
    big = max(evs, key=lambda e: len(e["rows"]))
    lead = next((a for a in big["rows"] if not NOISY.search(a["title"])), big["rows"][0])
    return [*big["key"], sum(len(e["rows"]) for e in evs), lead["title"], lead["url"], 1]


def url_key(url):
    """64-bit article key."""
    return int.from_bytes(hashlib.blake2b(url.encode(), digest_size=8).digest(), "little")


def story_of(evs, now):
    """Outlets, articles, editions of one story."""
    src, n, per_src, by_ed = defaultdict(dict), Counter(), Counter(), defaultdict(list)
    times, keys = [], {}
    for e in evs:
        by_ed[e["key"]].append(e)
        for a in e["rows"]:
            t = min(utc(a["published"]), now)
            times.append(t)
            seen = src[e["key"][0]]  # outlet -> last article
            seen[a["source"]] = max(seen.get(a["source"], t), t)
            n[e["key"][0]] += 1
            per_src[a["source"]] += 1
            if not LIVE.search(a["title"]):  # live blogs span stories
                keys[url_key(a["url"])] = None
    eds = sorted((edition_of(v) for v in by_ed.values()), key=lambda x: -x[2])
    return {"v": centroid([e["c"] for e in evs]), "src": dict(src), "u": keys,
            "n": dict(n), "ed": eds[:TOP_MAX_ED], "ne": len(eds), "t0": min(times),
            "t1": max(times), "dots": [s for s, _ in per_src.most_common(5)]}


def outlets(s, cc=None):
    return len(s["src"].get(cc, ())) if cc else sum(map(len, s["src"].values()))


def load_memory():
    """Stories of the last 7 days."""
    try:
        with np.load(TOP_MEMORY) as z:
            meta = json.loads(bytes(z["meta"]).decode())
            keys = (np.split(z["urls"], np.cumsum(z["nu"])[:-1]) if "urls" in z.files
                    else [None] * len(meta))
            for m, v, u in zip(meta, z["vecs"], keys):
                m["v"] = v.astype(np.float32)
                # older state: lead URLs, outlet lists
                m["u"] = (dict.fromkeys(u.tolist()) if u is not None
                          else {url_key(e[4]): None for e in m["ed"]})
                m["src"] = {cc: o if isinstance(o, dict) else dict.fromkeys(o, m["t1"])
                            for cc, o in m["src"].items()}
            return meta
    except (FileNotFoundError, KeyError, ValueError, OSError):
        return []


def save_memory(mem):
    os.makedirs(os.path.dirname(TOP_MEMORY), exist_ok=True)
    meta = [{k: v for k, v in m.items() if k not in ("v", "u")} for m in mem]
    vecs = np.stack([m["v"] for m in mem]).astype(np.float16) if mem else np.zeros((0, 1), np.float16)
    keys = [np.fromiter(m["u"], np.uint64, len(m["u"])) for m in mem]
    np.savez(TOP_MEMORY, meta=np.frombuffer(json.dumps(meta, ensure_ascii=False).encode(), np.uint8),
             vecs=vecs, urls=np.concatenate(keys) if keys else np.zeros(0, np.uint64),
             nu=np.array([len(k) for k in keys], np.int64))


def merge_into(m, s):
    """Add story s to m."""
    for cc, seen in s["src"].items():
        mine = m["src"].setdefault(cc, {})
        for k, t in seen.items():
            mine[k] = max(mine.get(k, t), t)
    for cc, k in s["n"].items():
        m["n"][cc] = max(m["n"].get(cc, 0), k)
    eds = {(e[0], e[1]): e for e in m["ed"]}
    for e in s["ed"]:
        old = eds.get((e[0], e[1]))
        if not old or not old[5] or e[2] > old[2]:
            eds[(e[0], e[1])] = e
    m["ed"] = sorted(eds.values(), key=lambda e: -e[2])[:TOP_MAX_ED]
    m["ne"] = max(m["ne"], s["ne"], len(eds))
    m["t0"], m["t1"] = min(m["t0"], s["t0"]), max(m["t1"], s["t1"])
    keys = [k for k in m["u"] if k not in s["u"]] + list(s["u"])  # newest last
    m["u"] = dict.fromkeys(keys[-TOP_MAX_U:])
    m["v"], m["dots"] = s["v"], s["dots"]


def same_story(items, old):
    """Groups of items telling one story."""
    parent = list(range(len(items)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owners = defaultdict(list)  # article -> items, ascending
    for i, s in enumerate(items):
        for k in s["u"]:
            owners[k].append(i)
    shared = Counter()
    for ids in owners.values():
        for a, i in enumerate(ids):
            for j in ids[a + 1:]:
                if i < old:  # new stories never pair up
                    shared[i, j] += 1
    linked = set()
    for (i, j), k in shared.items():
        if k >= 2 and k >= TOP_LINK * min(len(items[i]["u"]), len(items[j]["u"])):
            parent[find(i)] = find(j)
            linked.add(j)
    rest = [j for j in range(old, len(items)) if j not in linked]
    if old and rest:  # no shared articles: closest vector
        sim = np.stack([items[j]["v"] for j in rest]) @ np.stack([m["v"] for m in items[:old]]).T
        for j, row in zip(rest, sim):
            if row.max() >= TOP_MATCH:
                parent[find(j)] = find(int(row.argmax()))
    groups = defaultdict(list)
    for i in range(len(items)):
        groups[find(i)].append(i)
    return list(groups.values())


def remember(stories, now):
    """7-day memory, updated with this run."""
    cut = (datetime.fromisoformat(now) - timedelta(days=TOP_DAYS)).isoformat(timespec="seconds")
    mem = []
    for m in load_memory():
        m["src"] = {cc: kept for cc, o in m["src"].items()
                    if (kept := {k: t for k, t in o.items() if t >= cut})}
        m["n"] = {cc: k for cc, k in m["n"].items() if cc in m["src"]}
        if m["src"]:  # outlets seen in 7 days
            m["ed"] = [e[:5] + [0] for e in m["ed"]]  # live: in this run's feed
            mem.append(m)
    shown = {i for ids in top_ids(stories).values() for i in ids}
    items = mem + [s for i, s in enumerate(stories) if i in shown or outlets(s) >= TOP_KEEP]
    out = []
    for g in same_story(items, len(mem)):
        # newest, biggest last: it sets vector and dots
        g.sort(key=lambda i: (i >= len(mem), outlets(items[i]), items[i]["t1"]))
        s = items[g[0]]
        m = {**s, "src": {cc: dict(o) for cc, o in s["src"].items()}, "n": dict(s["n"]),
             "ed": list(s["ed"]), "u": dict(s["u"])}
        for i in g[1:]:
            merge_into(m, items[i])
        out.append(m)
    return out


def top_ids(items):
    """Scope -> story indices, most outlets first."""
    lists = {"all": sorted(range(len(items)), key=lambda i: (
        -outlets(items[i]), -len(items[i]["src"]), -sum(items[i]["n"].values())))[:TOP_ALL]}
    by_cc = defaultdict(list)
    for i, s in enumerate(items):
        for cc in s["src"]:
            if outlets(s, cc) >= 2:
                by_cc[cc].append(i)
    for cc, ids in by_cc.items():
        lists[cc] = sorted(ids, key=lambda i: (-outlets(items[i], cc), -items[i]["n"][cc]))[:TOP_CC]
    return lists


def rank(items):
    """Stories, lists per scope, landing paths."""
    lists = top_ids(items)
    table, sid, paths = [], {}, {}
    for ids in lists.values():
        for i in ids:
            if i not in sid:
                sid[i] = len(table)
                s = items[i]
                table.append({"cc": sorted(s["src"]), "t1": s["t1"], "dots": s["dots"],
                              "ed": s["ed"], "ne": s["ne"]})
                for e in s["ed"]:
                    paths[f"{e[0]}/{e[1]}"] = crawler.landing_url(e[0], e[1])
    out = {}
    for scope, ids in lists.items():
        cc = None if scope == "all" else scope
        out[scope] = [[sid[i], outlets(items[i], cc),
                       items[i]["n"][cc] if cc else sum(items[i]["n"].values())] for i in ids]
    return {"stories": table, "lists": out, "paths": paths}


def most_reported(events, stories, gen):
    """Write /top/: 24 hours and 7 days."""
    now = utc(gen)
    linked = {i for s in stories for i in s}
    groups = stories + [[i] for i in range(len(events)) if i not in linked]
    day = [story_of([events[i] for i in g], now) for g in groups]
    mem = remember(day, now)
    crawler.write_top({"24h": rank(day), "7d": rank(mem)}, gen)
    save_memory(mem)
    return len(mem)


def main():
    with open("crawled.json", encoding="utf-8") as f:
        feed = json.load(f)
    rows = feed["articles"]
    cache = load_cache()
    todo = [a for a in rows if a["url"] not in cache]
    done = embed_new(todo, cache) if todo else 0
    groups = defaultdict(list)
    for a in rows:
        for k in ("ev", "evr", "sx"):
            a.pop(k, None)
        if a["url"] in cache:
            groups[(a.get("country"), a.get("lang"))].append(a)
    events, tagged = [], 0
    for key, grp in groups.items():
        if len({a["source"] for a in grp}) < MIN_SOURCES:
            continue
        for ev in cluster(grp, [cache[a["url"]] for a in grp]):
            eid = "e" + hashlib.sha1(grp[ev[0]]["url"].encode()).hexdigest()[:10]
            for rank, m in enumerate(ev):
                grp[m]["ev"], grp[m]["evr"] = eid, rank
            members = [grp[m] for m in ev]
            events.append({"key": key, "rows": members,
                           "c": centroid([cache[a["url"]] for a in members])})
            tagged += len(ev)
    stories = tag_stories(events)
    gen = feed["generated"]
    today = datetime.fromisoformat(gen).astimezone(crawler.ZURICH).date().isoformat()
    crawler.write_feed(rows, gen, today, feed.get("since", gen))
    remembered = most_reported(events, stories, gen)
    save_cache({a["url"]: cache[a["url"]] for a in rows if a["url"] in cache})
    print(f"clustering: embedded {done}/{len(todo)} new, {len(events)} events, "
          f"{tagged}/{len(rows)} articles grouped, {len(stories)} cross-edition stories "
          f"({sum(map(len, stories))} events), {remembered} stories in 7-day memory")


if __name__ == "__main__":
    main()
