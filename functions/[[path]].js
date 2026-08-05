// Cloudflare Pages Function: serve the generated HTML + data (index, archive
// pages, JSON, sitemap) from the R2 bucket, and everything else (script.js,
// styles.css, colors.js, favicon, robots.txt, archive.html shell…) from the
// static assets. Edge-cached so R2 read ops stay minimal.
//
// Requires an R2 binding named BUCKET on the Pages project.

const TYPES = {
  html: "text/html; charset=utf-8",
  json: "application/json; charset=utf-8",
  xml: "application/xml; charset=utf-8",
  txt: "text/plain; charset=utf-8",
};

// Today's date in Zurich (YYYY-MM-DD) — matches the crawler's "today".
function zurichToday() {
  return new Intl.DateTimeFormat("en-CA", { timeZone: "Europe/Zurich" }).format(new Date());
}

// Map a request path to its R2 object key ("/" → index.html; no leading slash).
function r2Key(pathname) {
  let p = decodeURIComponent(pathname);
  if (p.endsWith("/")) p += "index.html"; // "/" or "/news/ch/de/" -> …/index.html
  return p.replace(/^\/+/, "");
}

// Which keys come from R2 (everything else is a static asset).
function servesFromR2(key) {
  return key === "index.html"
    || key === "crawled.json"
    || key === "sitemap.xml"
    || key.startsWith("archive/")
    || key.startsWith("data/")  // per-country shards + manifest (today's feed)
    || key.startsWith("news/"); // programmatic landing pages + /news/ hub
}

// A finalized past archive day never changes again, so it — and only it — is
// immutable. Everything else gets a short TTL: the live feed, today's archive
// files, and crucially `archive/index.json`, which gains a date every day. An
// immutable index froze the visitor's archive list at whatever day they first
// loaded it, so recent days silently went missing from the list even though
// their pages existed.
function cacheControl(key) {
  if (key === "sitemap.xml") return "public, max-age=3600";
  const day = key.match(/^archive\/(\d{4}-\d{2}-\d{2})/); // archive/2026-08-02[-3].html|json
  const frozen = day && day[1] < zurichToday();
  return frozen ? "public, max-age=31536000, immutable" : "public, max-age=300";
}

export async function onRequest(context) {
  const { request, env } = context;
  if (request.method !== "GET" && request.method !== "HEAD") {
    return new Response("Method not allowed", { status: 405 });
  }

  const url = new URL(request.url);
  // Landing pages are directory URLs (…/news/ch/de/). Enforce the trailing slash so
  // there's a single canonical form; the last segment carries no file extension.
  if (/^\/news\/.+[^/]$/.test(url.pathname) && !url.pathname.slice(1).includes(".")) {
    return Response.redirect(url.origin + url.pathname + "/" + url.search, 301);
  }
  const key = r2Key(url.pathname);
  if (!servesFromR2(key)) return env.ASSETS.fetch(request); // static asset

  // Edge cache first.
  const cache = caches.default;
  const cached = await cache.match(request);
  if (cached) return cached;

  const obj = await env.BUCKET.get(key);
  if (!obj) return new Response("Not found", { status: 404 });

  const headers = new Headers();
  obj.writeHttpMetadata(headers); // content-type from stored metadata, if any
  headers.set("etag", obj.httpEtag);
  headers.set("cache-control", cacheControl(key));
  if (!headers.has("content-type")) {
    const type = TYPES[key.split(".").pop()];
    if (type) headers.set("content-type", type);
  }

  if (request.headers.get("if-none-match") === obj.httpEtag) {
    return new Response(null, { status: 304, headers });
  }

  const resp = new Response(request.method === "HEAD" ? null : obj.body, { headers });
  if (request.method === "GET") context.waitUntil(cache.put(request, resp.clone()));
  return resp;
}
