import { useEffect, useState } from "react";
import { api, defaultConfig, getConfig, setConfig } from "./api.js";

export default function App() {
  const [cfg, setCfg] = useState({ ...defaultConfig(), ...getConfig() });
  const [showSetup, setShowSetup] = useState(!cfg.apiKey);
  const [items, setItems] = useState([]);
  const [quotes, setQuotes] = useState({});
  const [search, setSearch] = useState("");
  const [results, setResults] = useState([]);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(false);
  const [moversOpen, setMoversOpen] = useState(false);
  const [moversDir, setMoversDir] = useState("both");
  const [movers, setMovers] = useState([]);
  const [moversLoading, setMoversLoading] = useState(false);

  const refresh = async () => {
    setError(""); setLoading(true);
    try {
      const list = await api.list();
      setItems(list);
      if (list.length) {
        const qs = await api.quote(list.map((i) => i.symbol));
        const map = {};
        qs.forEach((q) => { map[q.symbol] = q; });
        setQuotes(map);
      } else {
        setQuotes({});
      }
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    if (!showSetup) refresh();
  }, [showSetup]);

  const onSearch = async () => {
    if (!search.trim()) return;
    setError("");
    try {
      const r = await api.search(search.trim());
      setResults(r);
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const upsert = async (symbol, threshold, direction) => {
    setError("");
    try {
      const t = threshold === "" || threshold == null ? null : Number(threshold);
      await api.upsert({ symbol, threshold: t, direction });
      setResults([]); setSearch("");
      await refresh();
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  const loadMovers = async (dir = moversDir) => {
    setError(""); setMoversLoading(true);
    try {
      const m = await api.movers(20, dir);
      setMovers(m);
    } catch (e) {
      setError(e.message || String(e));
    } finally {
      setMoversLoading(false);
    }
  };

  const toggleMovers = () => {
    const next = !moversOpen;
    setMoversOpen(next);
    if (next && movers.length === 0) loadMovers();
  };

  const switchMoversDir = (d) => {
    setMoversDir(d);
    loadMovers(d);
  };

  const remove = async (symbol) => {
    if (!confirm(`移除 ${symbol}?`)) return;
    setError("");
    try {
      await api.remove(symbol);
      await refresh();
    } catch (e) {
      setError(e.message || String(e));
    }
  };

  if (showSetup) {
    return <Setup cfg={cfg} setCfg={(c) => { setConfig(c); setCfg(c); }} onDone={() => setShowSetup(false)} />;
  }

  return (
    <div className="app">
      <header>
        <h1>📈 美股监控</h1>
        <button className="ghost" onClick={() => setShowSetup(true)} title="设置">⚙</button>
      </header>

      {error && <div className="error">{error}</div>}

      <div className="search">
        <input
          placeholder="搜公司或代码（如 Oracle、ORCL）"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
          onKeyDown={(e) => e.key === "Enter" && onSearch()}
          autoComplete="off"
        />
        <button onClick={onSearch}>搜索</button>
      </div>

      {results.length > 0 && (
        <ul>
          {results.map((r) => (
            <SearchRow key={r.symbol} item={r} onAdd={upsert} />
          ))}
        </ul>
      )}

      <div className="row-between section-title">
        <h2>
          <button className="ghost link" onClick={toggleMovers}>
            {moversOpen ? "▾" : "▸"} 热门异动 (TOP20 大盘科技股)
          </button>
        </h2>
        {moversOpen && (
          <button className="ghost" onClick={() => loadMovers()} disabled={moversLoading}>
            {moversLoading ? "..." : "刷新"}
          </button>
        )}
      </div>
      {moversOpen && (
        <>
          <div className="row gap mt">
            <button
              className={`ghost ${moversDir === "both" ? "active" : ""}`}
              onClick={() => switchMoversDir("both")}
            >全部</button>
            <button
              className={`ghost ${moversDir === "up" ? "active" : ""}`}
              onClick={() => switchMoversDir("up")}
            >涨幅</button>
            <button
              className={`ghost ${moversDir === "down" ? "active" : ""}`}
              onClick={() => switchMoversDir("down")}
            >跌幅</button>
          </div>
          <ul className="mt">
            {movers.map((m) => (
              <MoverRow key={m.symbol} item={m} onAdd={upsert} />
            ))}
            {!moversLoading && movers.length === 0 && (
              <p className="muted">暂无数据</p>
            )}
          </ul>
        </>
      )}

      <div className="row-between section-title">
        <h2>关注列表 ({items.length})</h2>
        <button className="ghost" onClick={refresh} disabled={loading}>
          {loading ? "..." : "刷新"}
        </button>
      </div>
      {items.length === 0 && <p className="muted">还没有关注的标的，搜索后添加</p>}
      <ul>
        {items.map((it) => (
          <WatchCard
            key={it.symbol}
            item={it}
            quote={quotes[it.symbol]}
            onSave={upsert}
            onRemove={remove}
          />
        ))}
      </ul>
    </div>
  );
}

function MoverRow({ item, onAdd }) {
  const up = item.change_pct >= 0;
  return (
    <li className="card alt">
      <div className="row-between">
        <div className="grow">
          <div className="symbol">{item.symbol}</div>
          <div className="muted small">
            ${item.price?.toFixed(2)} · 昨收 ${item.prev_close?.toFixed(2)}
          </div>
        </div>
        <div className={`price ${up ? "up" : "down"}`}>
          {up ? "+" : ""}{item.change_pct.toFixed(2)}%
        </div>
      </div>
      <div className="row gap mt">
        <button className="ghost" onClick={() => onAdd(item.symbol, "", "below")}>
          加入关注
        </button>
      </div>
    </li>
  );
}

function SearchRow({ item, onAdd }) {
  const [threshold, setThreshold] = useState("");
  const [direction, setDirection] = useState("below");
  return (
    <li className="card alt">
      <div className="row-between">
        <div className="grow">
          <div><strong>{item.symbol}</strong> <span className="muted small">{item.exchange}</span></div>
          <div className="muted small">{item.name}</div>
        </div>
      </div>
      <div className="row gap mt">
        <select value={direction} onChange={(e) => setDirection(e.target.value)}>
          <option value="below">低于</option>
          <option value="above">高于</option>
        </select>
        <input
          type="number"
          inputMode="decimal"
          placeholder="阈值"
          value={threshold}
          onChange={(e) => setThreshold(e.target.value)}
        />
        <button onClick={() => onAdd(item.symbol, threshold, direction)}>添加</button>
      </div>
    </li>
  );
}

function fmtMoney(v) {
  if (v == null) return "—";
  if (v >= 1e12) return `$${(v / 1e12).toFixed(2)}T`;
  if (v >= 1e9) return `$${(v / 1e9).toFixed(2)}B`;
  if (v >= 1e6) return `$${(v / 1e6).toFixed(2)}M`;
  return `$${v.toFixed(2)}`;
}

function WatchCard({ item, quote, onSave, onRemove }) {
  const [editing, setEditing] = useState(false);
  const [threshold, setThreshold] = useState(item.threshold ?? "");
  const [direction, setDirection] = useState(item.direction || "below");

  const price = quote?.price;
  const hit =
    price != null &&
    item.threshold != null &&
    (item.direction === "below" ? price < item.threshold : price > item.threshold);

  const chg = quote?.day_change_pct;
  const up = chg != null && chg >= 0;

  return (
    <li className={`card ${hit ? "hit" : ""}`}>
      <div className="row-between">
        <div>
          <div className="symbol">{item.symbol}</div>
          <div className="muted small">{quote?.name || ""}</div>
        </div>
        <div className={`price ${chg != null ? (up ? "up" : "down") : ""}`}>
          {price != null ? `$${price.toFixed(2)}` : "—"}
          {chg != null && (
            <span className="small" style={{ marginLeft: 6 }}>
              {up ? "+" : ""}{chg.toFixed(2)}%
            </span>
          )}
        </div>
      </div>

      {quote && (quote.pe_ratio != null || quote.market_cap != null || quote.week52_high != null) && (
        <div className="muted small mt">
          {quote.pe_ratio != null && <>PE {quote.pe_ratio.toFixed(1)} · </>}
          {quote.market_cap != null && <>市值 {fmtMoney(quote.market_cap)} · </>}
          {quote.week52_low != null && quote.week52_high != null && (
            <>52周 ${quote.week52_low.toFixed(0)}–${quote.week52_high.toFixed(0)}</>
          )}
        </div>
      )}

      {editing ? (
        <div className="row gap mt">
          <select value={direction} onChange={(e) => setDirection(e.target.value)}>
            <option value="below">低于</option>
            <option value="above">高于</option>
          </select>
          <input
            type="number"
            inputMode="decimal"
            value={threshold}
            onChange={(e) => setThreshold(e.target.value)}
          />
          <button onClick={async () => { await onSave(item.symbol, threshold, direction); setEditing(false); }}>保存</button>
          <button className="ghost" onClick={() => setEditing(false)}>取消</button>
        </div>
      ) : (
        <div className="row-between mt">
          <div className="muted small">
            {item.threshold != null
              ? `${item.direction === "below" ? "低于" : "高于"} $${item.threshold}`
              : "仅追踪价格"}
          </div>
          <div className="row gap">
            <button className="ghost" onClick={() => setEditing(true)}>编辑</button>
            <button className="ghost danger" onClick={() => onRemove(item.symbol)}>删除</button>
          </div>
        </div>
      )}
    </li>
  );
}

function Setup({ cfg, setCfg, onDone }) {
  const [apiKey, setApiKey] = useState(cfg.apiKey || "");
  const [apiUrl, setApiUrl] = useState(cfg.apiUrl || "/api");
  const [advanced, setAdvanced] = useState(false);
  const save = () => {
    const next = { apiUrl: (apiUrl.replace(/\/$/, "") || "/api"), apiKey };
    setCfg(next);
    onDone();
  };
  return (
    <div className="app">
      <h1>⚙ 配置</h1>
      <p className="muted small">
        填 API Key（terraform 部署时设的 <code>TF_VAR_api_key</code>）。保存在浏览器 localStorage，不上传服务器。
      </p>
      <div className="form">
        <label>API Key</label>
        <input
          value={apiKey}
          onChange={(e) => setApiKey(e.target.value)}
          type="password"
          autoComplete="off"
        />

        {!advanced ? (
          <button className="ghost" onClick={() => setAdvanced(true)}>高级 (自定义 API URL)</button>
        ) : (
          <>
            <label>API URL</label>
            <input
              value={apiUrl}
              onChange={(e) => setApiUrl(e.target.value)}
              placeholder="/api"
              autoComplete="off"
            />
            <p className="muted small">默认 "/api"（CloudFront 同源）。直连 API Gateway URL 调试时才需要改。</p>
          </>
        )}

        <button onClick={save} disabled={!apiKey}>保存</button>
      </div>
    </div>
  );
}
