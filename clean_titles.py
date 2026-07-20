#!/usr/bin/env python3
"""One-shot: re-run clean_title() over already-stored titles, then re-render (#31).

Only needed for days already archived. clean_title() runs at ingest, and at each
date rollover crawled.json starts empty, so every day from the fix onwards is clean
without help. But write_outputs() only ever rewrites *today's* archive JSON and
pages — once a date rolls over, its entity-mangled headlines are frozen in both the
JSON and the static HTML that Google indexes. This unfreezes them.

Archive days live in R2 and CI only pulls today's, so round-trip them:

    rclone copy "R2:$R2_BUCKET/archive" archive/ -v
    rclone copy "R2:$R2_BUCKET/crawled.json" .
    python3 clean_titles.py
    rclone copy archive "R2:$R2_BUCKET/archive" -v
    rclone copy crawled.json "R2:$R2_BUCKET/" -v

Re-rendering goes through write_archive_day(), the same paginated renderer the
crawler uses — not backfill.py, which predates pagination and skips days whose
HTML already exists.
"""
import glob, json, os
from crawler import clean_title, write_archive_day, write_colors_js, write_json

changed_files = changed_titles = rerendered = 0

for path in ["crawled.json"] + sorted(glob.glob(os.path.join("archive", "2*.json"))):
    if not os.path.exists(path):
        continue
    data = json.load(open(path, encoding="utf-8"))
    articles = data["articles"] if isinstance(data, dict) and "articles" in data else data
    if not isinstance(articles, list):
        continue
    n = 0
    for a in articles:
        cleaned = clean_title(a.get("title"))
        if cleaned != a.get("title"):
            a["title"] = cleaned
            n += 1
    if not n:
        continue
    write_json(path, data)
    changed_files += 1
    changed_titles += n
    print(f"  {path}: {n} titles cleaned")

    # Article ids are derived from the title (slugify), so a cleaned title changes
    # the row's anchor. Re-rendering the day keeps the pages' #hash links agreeing
    # with what script.js computes client-side.
    date = os.path.basename(path)[:-len(".json")]
    if path != "crawled.json":
        if not rerendered:
            write_colors_js()
        pages = write_archive_day(date, articles)
        rerendered += 1
        print(f"    re-rendered {date}: {pages} page(s)")

print(f"done: {changed_titles} titles across {changed_files} files, "
      f"{rerendered} archive day(s) re-rendered")
