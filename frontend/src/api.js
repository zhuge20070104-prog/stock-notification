const STORAGE = "stock-watcher-config";

export function getConfig() {
  try {
    return JSON.parse(localStorage.getItem(STORAGE)) || {};
  } catch {
    return {};
  }
}

export function setConfig(cfg) {
  localStorage.setItem(STORAGE, JSON.stringify(cfg));
}

export function defaultConfig() {
  return {
    apiUrl: import.meta.env.VITE_API_URL || "/api",
    apiKey: "",
  };
}

async function call(path, opts = {}) {
  const cfg = getConfig();
  const base = (cfg.apiUrl || import.meta.env.VITE_API_URL || "/api").replace(/\/$/, "");

  const res = await fetch(base + path, {
    ...opts,
    headers: {
      "content-type": "application/json",
      "x-api-key": cfg.apiKey || "",
      ...(opts.headers || {}),
    },
  });

  if (!res.ok) {
    let detail;
    try { detail = (await res.json()).error; } catch { detail = await res.text(); }
    throw new Error(`${res.status}: ${detail}`);
  }
  return res.json();
}

export const api = {
  list:   () => call("/watchlist"),
  upsert: (item) => call("/watchlist", { method: "POST", body: JSON.stringify(item) }),
  remove: (sym) => call(`/watchlist/${encodeURIComponent(sym)}`, { method: "DELETE" }),
  quote:  (syms) => call(`/quote?symbols=${syms.map(encodeURIComponent).join(",")}`),
  search: (q) => call(`/search?q=${encodeURIComponent(q)}`),
  movers: (limit = 20, dir = "both") => call(`/movers?limit=${limit}&dir=${encodeURIComponent(dir)}`),
};
