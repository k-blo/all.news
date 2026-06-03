// Cloudflare Worker — fires the GitHub Actions crawl workflow on a reliable
// schedule via workflow_dispatch (GitHub's own `schedule:` is best-effort).

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
  // Manual trigger: open the worker URL in a browser to fire on demand
  async fetch(request, env) {
    try {
      await dispatch(env);
      return new Response("dispatched\n");
    } catch (e) {
      return new Response(String(e), { status: 500 });
    }
  },
};
