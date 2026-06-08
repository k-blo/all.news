#!/usr/bin/env python3
"""One-shot backfill: generate archive/YYYY-MM-DD.html for every existing JSON."""
import json, os
from crawler import write_rendered_html, write_colors_js, write_sitemap, archive_dates

write_colors_js()

dates = archive_dates()
for d in dates:
    src = os.path.join("archive", f"{d}.json")
    dest = os.path.join("archive", f"{d}.html")
    if os.path.exists(dest):
        continue
    data = json.load(open(src, encoding="utf-8"))
    write_rendered_html(
        data["articles"], dest,
        title=f"swissnews – {d}",
        description=f"Schweizer Nachrichtenlinks vom {d}.",
        canonical=f"https://www.swissnews.org/archive/{d}.html",
        day_link='<a id="dayLink" href="/archive.html">← archiv</a>',
    )
    print(f"  wrote {dest}")

write_sitemap(dates)
print(f"done: {len(dates)} dates, sitemap.xml updated")
