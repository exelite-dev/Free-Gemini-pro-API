/**
 * OmniBridge Edge Proxy – Cloudflare Worker Edition
 * Provides 0-cost, 0-latency edge routing for Gemini, Qwen, and other models.
 * Completely bypasses regional IP geoblocks and WAF restrictions.
 */

const UPSTREAM_TARGETS = {
  gemini: "https://gemini-fastapi-latest-kzs9.onrender.com/v1",
  qwen: "https://dashscope.aliyuncs.com/compatible-mode/v1"
};

export default {
  async fetch(request, env, ctx) {
    // Handle CORS preflight
    if (request.method === "OPTIONS") {
      return new Response(null, {
        headers: {
          "Access-Control-Allow-Origin": "*",
          "Access-Control-Allow-Methods": "GET, POST, OPTIONS",
          "Access-Control-Allow-Headers": "*"
        }
      });
    }

    const url = new URL(request.url);
    const path = url.pathname;

    // Direct health check
    if (path === "/" || path === "/health") {
      return new Response(JSON.stringify({ status: "ok", service: "OmniBridge Edge Proxy" }), {
        headers: { "Content-Type": "application/json" }
      });
    }

    // Determine provider from requested model or header
    let targetBase = UPSTREAM_TARGETS.gemini;
    
    if (request.method === "POST" && path.includes("/chat/completions")) {
      try {
        const bodyText = await request.clone().text();
        const body = JSON.parse(bodyText);
        const model = (body.model || "").toLowerCase();
        
        if (model.includes("qwen")) {
          targetBase = UPSTREAM_TARGETS.qwen;
        }
      } catch (e) {}
    }

    const targetUrl = `${targetBase}${path.replace(/^\/v1/, "")}${url.search}`;

    const newHeaders = new Headers(request.headers);
    newHeaders.set("Host", new URL(targetBase).hostname);

    const init = {
      method: request.method,
      headers: newHeaders,
      body: request.method !== "GET" && request.method !== "HEAD" ? request.body : null,
      redirect: "follow"
    };

    const response = await fetch(targetUrl, init);
    const resHeaders = new Headers(response.headers);
    resHeaders.set("Access-Control-Allow-Origin", "*");

    return new Response(response.body, {
      status: response.status,
      statusText: response.statusText,
      headers: resHeaders
    });
  }
};
