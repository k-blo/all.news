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
from datetime import datetime

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
    save_cache({a["url"]: cache[a["url"]] for a in rows if a["url"] in cache})
    print(f"clustering: embedded {done}/{len(todo)} new, {len(events)} events, "
          f"{tagged}/{len(rows)} articles grouped, {len(stories)} cross-edition stories "
          f"({sum(map(len, stories))} events)")


if __name__ == "__main__":
    main()
