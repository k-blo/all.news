#!/usr/bin/env python3
"""Re-render past archive day pages in R2 with the current template.

Archive day pages are written once, on the day they happen, and then never
touched again — so every change to template.html (nav, analytics, ad slots)
leaves the older days frozen in whatever layout was current back then. This
re-renders them from the day's JSON, which is the durable record. It also
(re)writes each day's country shards (archive/<date>/<cc>.json, which infinite
scroll loads) and deletes the old archive/<date>-N.html pages: days are one
page now.

    python3 regen_archive.py --all              # every day except today
    python3 regen_archive.py 2026-06-05 …       # specific days
    python3 regen_archive.py --all --dry-run    # list what would change

Today is skipped: the hourly crawl rewrites it from the same template anyway.

Needs CLOUDFLARE_TOKEN in .env (an API token with R2 read+write) and boto3 —
this is a maintenance tool, not part of the stdlib-only crawl path. Past-day
pages are edge-cached for a day (functions/[[path]].js), so already cached
copies keep serving the old layout until then, or until the cache is purged.
"""
import argparse, concurrent.futures, hashlib, json, os, re, sys, urllib.request
from datetime import datetime, timezone

import crawler

BUCKET = os.environ.get("R2_BUCKET", "allnews")
API = "https://api.cloudflare.com/client/v4"


def cf_get(path, token):
    req = urllib.request.Request(f"{API}/{path}", headers={"Authorization": f"Bearer {token}"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def r2_client():
    """S3 client for R2. Prefers an R2 access key pair (R2_ACCESS_KEY_ID /
    R2_SECRET_ACCESS_KEY — the same pair the crawl workflow uses); otherwise
    falls back to CLOUDFLARE_TOKEN, which authenticates as access key = token id,
    secret = SHA-256 of the token value. Either way the credential needs R2
    *write* access: a read-only token gets through listing and rendering and
    then fails on the first upload."""
    try:
        import boto3
        from botocore.config import Config
    except ImportError:
        sys.exit("boto3 required: pip install boto3")

    key = os.environ.get("R2_ACCESS_KEY_ID")
    secret = os.environ.get("R2_SECRET_ACCESS_KEY")
    endpoint = os.environ.get("R2_ENDPOINT")
    account = os.environ.get("R2_ACCOUNT_ID")
    if not (key and secret):
        token = os.environ.get("CLOUDFLARE_TOKEN")
        if not token:
            sys.exit("no R2 credentials — set R2_ACCESS_KEY_ID/R2_SECRET_ACCESS_KEY or "
                     "CLOUDFLARE_TOKEN in .env, then: set -a; . ./.env; set +a")
        key = cf_get("user/tokens/verify", token)["result"]["id"]
        secret = hashlib.sha256(token.encode()).hexdigest()
        account = account or cf_get("accounts", token)["result"][0]["id"]
    if not endpoint:
        if not account:
            sys.exit("set R2_ENDPOINT or R2_ACCOUNT_ID alongside the R2 access keys")
        endpoint = f"https://{account}.r2.cloudflarestorage.com"
    return boto3.client("s3", endpoint_url=endpoint, aws_access_key_id=key,
                        aws_secret_access_key=secret, region_name="auto",
                        config=Config(signature_version="s3v4"))


def check_writable(s3):
    """Fail before rendering anything if the credential can't write."""
    probe = "archive/.regen-write-probe"
    try:
        s3.put_object(Bucket=BUCKET, Key=probe, Body=b"ok", ContentType="text/plain")
        s3.delete_object(Bucket=BUCKET, Key=probe)
    except Exception as e:
        sys.exit(f"R2 credential cannot write to {BUCKET} ({type(e).__name__}). Grant "
                 "'Workers R2 Storage: Edit' to the API token, or use an R2 access key "
                 "pair with Object Read & Write.")


def day_keys(s3, date):
    """Existing archive/<date>[-N].html keys in the bucket."""
    keys = []
    for page in s3.get_paginator("list_objects_v2").paginate(Bucket=BUCKET, Prefix=f"archive/{date}"):
        for o in page.get("Contents", []):
            if re.fullmatch(rf"archive/{date}(?:-\d+)?\.html", o["Key"]):
                keys.append(o["Key"])
    return keys


def regen_day(s3, date, out_dir, dry_run=False):
    """Re-render one day; returns (shards, old pages removed)."""
    page = f"archive/{date}.html"
    orphans = [k for k in day_keys(s3, date) if k != page]  # old page N
    if dry_run:
        return 0, len(orphans)

    body = s3.get_object(Bucket=BUCKET, Key=f"archive/{date}.json")["Body"].read()
    articles = json.loads(body)["articles"]
    crawler.ARCHIVE_DIR = out_dir          # write_archive_day joins onto this
    shard_dir = os.path.join(out_dir, date)
    os.makedirs(out_dir, exist_ok=True)
    crawler.write_archive_day(date, articles)
    crawler.write_country_shards(articles, datetime.now(timezone.utc).isoformat(),
                                 date, shard_dir)
    uploads = [(crawler.archive_page_path(date), page, "text/html; charset=utf-8")]
    uploads += [(os.path.join(shard_dir, n), f"archive/{date}/{n}",
                 "application/json; charset=utf-8") for n in os.listdir(shard_dir)]

    def put(u):
        path, key, ctype = u
        with open(path, "rb") as f:
            s3.put_object(Bucket=BUCKET, Key=key, Body=f.read(), ContentType=ctype)
        os.remove(path)

    with concurrent.futures.ThreadPoolExecutor(8) as ex:
        list(ex.map(put, uploads))
    for i in range(0, len(orphans), 1000):  # after the new page is up
        s3.delete_objects(Bucket=BUCKET,
                          Delete={"Objects": [{"Key": k} for k in orphans[i:i + 1000]]})
    return len(uploads) - 1, len(orphans)


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("dates", nargs="*", help="days to re-render (YYYY-MM-DD)")
    ap.add_argument("--all", action="store_true", help="every day in archive/index.json")
    ap.add_argument("--dry-run", action="store_true", help="count old pages, write nothing")
    ap.add_argument("--out", default="/tmp/regen-archive", help="scratch dir for rendered pages")
    args = ap.parse_args()

    s3 = r2_client()
    if not args.dry_run:
        check_writable(s3)
    today = datetime.now(crawler.ZURICH).date().isoformat()  # the crawler's "today"
    dates = args.dates
    if args.all:
        idx = s3.get_object(Bucket=BUCKET, Key="archive/index.json")["Body"].read()
        dates = sorted(json.loads(idx)["dates"], reverse=True)
    if not dates:
        ap.error("pass dates or --all")
    dates = [d for d in dates if d != today]

    total_shards = total_orphans = 0
    for i, d in enumerate(dates, 1):
        shards, orphans = regen_day(s3, d, args.out, args.dry_run)
        total_shards += shards
        total_orphans += orphans
        print(f"[{i}/{len(dates)}] {d}: {shards} country shards"
              + (f", {orphans} old pages removed" if orphans else ""), flush=True)
    verb = "would remove" if args.dry_run else "removed"
    print(f"{len(dates)} days, {total_shards} country shards; "
          f"{verb} {total_orphans} old pages")


if __name__ == "__main__":
    main()
