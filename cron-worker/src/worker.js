// Cloudflare Worker — fires the GitHub Actions crawl workflow on a reliable
// schedule via workflow_dispatch (GitHub's own `schedule:` is best-effort).
// Also exposes GET /proxy?token=…&url=… so the GitHub Actions crawler can fetch
// sitemap URLs that block Azure datacenter IPs (e.g. CH Media papers).

async function handleProxy(request, env) {
  const params = new URL(request.url).searchParams;
  if (!env.PROXY_TOKEN || params.get("token") !== env.PROXY_TOKEN) {
    return new Response("Unauthorized", { status: 401 });
  }
  const target = params.get("url");
  if (!target || !target.startsWith("https://")) {
    return new Response("Missing or invalid url param", { status: 400 });
  }
  const resp = await fetch(target, {
    headers: { "User-Agent": "SwissNewsBot/0.1 (+https://github.com/yourname/swissnews; POC)" },
  });
  const body = await resp.arrayBuffer();
  return new Response(body, {
    status: resp.status,
    headers: { "Content-Type": resp.headers.get("Content-Type") || "text/xml" },
  });
}

async function dispatch(env) {
  const url = `https://api.github.com/repos/${env.OWNER}/${env.REPO}/actions/workflows/${env.WORKFLOW}/dispatches`;
  const res = await fetch(url, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "X-GitHub-Api-Version": "2022-11-28",
      "User-Agent": "swissnews-cron",
    },
    body: JSON.stringify({ ref: env.REF }),
  });
  // 204 = accepted. Anything else: surface the body for debugging.
  if (res.status !== 204) {
    throw new Error(`dispatch failed ${res.status}: ${await res.text()}`);
  }
}

export default {
  // Cron trigger
  async scheduled(event, env, ctx) {
    ctx.waitUntil(dispatch(env));
  },
  // Manual trigger: open the worker URL in a browser to fire on demand.
  // GET /proxy?token=…&url=… proxies sitemap fetches from Cloudflare IPs.
  async fetch(request, env) {
    if (new URL(request.url).pathname === "/proxy") {
      return handleProxy(request, env);
    }
    try {
      await dispatch(env);
      return new Response("dispatched\n");
    } catch (e) {
      return new Response(String(e), { status: 500 });
    }
  },
};
