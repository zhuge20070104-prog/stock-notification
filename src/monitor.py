import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .fetcher import Quote, batch_history, fetch_info, get_quote
from .indicators import apply_signals
from .movers import top_gainers_above
from .notifier import (
    Notifier,
    fan_out,
    render_error_alert,
    render_gainer_alert,
    render_threshold_alert,
)


@dataclass
class _Alert:
    kind: str  # "threshold" | "gainer"
    quote: Quote
    threshold: Optional[float] = None
    direction: Optional[str] = None


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save_json(state: dict, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[warn] failed to write {path}: {e}")


def _hit(price: float, threshold: float, direction: str) -> bool:
    return price < threshold if direction == "below" else price > threshold


def _make_file_info_provider(cache_path: str, ttl_seconds: int = 86400) -> Callable[[str], Dict]:
    cache = _load_json(cache_path)
    dirty = {"v": False}

    def provider(symbol: str) -> Dict:
        sym = symbol.upper()
        entry = cache.get(sym)
        now = int(time.time())
        if entry and entry.get("expires_at", 0) > now:
            return entry.get("data") or {}
        try:
            data = fetch_info(sym)
        except Exception:
            data = (entry or {}).get("data") or {}
        cache[sym] = {"data": data, "expires_at": now + ttl_seconds}
        dirty["v"] = True
        return data

    def flush():
        if dirty["v"]:
            _save_json(cache, cache_path)

    provider.flush = flush  # type: ignore[attr-defined]
    return provider


def _enrich_with_indicators(alerts: List[_Alert]) -> None:
    """Batch-download 3mo history once for all alerted symbols, attach signals."""
    if not alerts:
        return
    syms = list({a.quote.symbol for a in alerts})
    try:
        hist = batch_history(syms, period="3mo")
    except Exception as e:
        print(f"[warn] batch_history failed: {e}")
        return
    for a in alerts:
        df = hist.get(a.quote.symbol)
        if df is not None:
            try:
                apply_signals(a.quote, df)
            except Exception as e:
                print(f"[warn] indicators {a.quote.symbol}: {e}")


def check_once(
    watchlist: List[dict],
    notifiers: List[Notifier],
    state_path: str = "state.json",
    metrics_cache_path: str = "metrics_cache.json",
    min_alert_interval_hours: float = 1.5,
    enable_gainer_alerts: bool = True,
    gainer_pct_threshold: float = 5.0,
    gainer_pool_size: int = 20,
) -> Dict:
    state = _load_json(state_path)
    info_provider = _make_file_info_provider(metrics_cache_path)
    interval_s = int(min_alert_interval_hours * 3600)
    now = int(time.time())
    alerts: List[_Alert] = []

    for item in watchlist:
        sym = item["symbol"].upper()
        try:
            q = get_quote(sym, info_provider=info_provider)
        except Exception as e:
            print(f"[warn] {sym}: {e}")
            continue

        threshold = item.get("threshold")
        direction = item.get("direction", "below")

        if threshold is None:
            print(f"{sym} {q.name}: ${q.price:.2f}")
            continue

        hit = _hit(q.price, threshold, direction)
        status = "HIT" if hit else "ok"
        print(f"{sym} {q.name}: ${q.price:.2f} (threshold {direction} {threshold}) -> {status}")
        if not hit:
            continue

        key = f"{sym}#threshold"
        last_ts = int((state.get(key) or {}).get("last_alert_ts", 0))
        if now - last_ts < interval_s:
            continue

        alerts.append(_Alert(kind="threshold", quote=q, threshold=float(threshold), direction=direction))

    if enable_gainer_alerts:
        try:
            hits: List[Quote] = top_gainers_above(
                pct_threshold=gainer_pct_threshold,
                pool_size=gainer_pool_size,
                info_provider=info_provider,
            )
        except Exception as e:
            print(f"[warn] gainers scan failed: {e}")
            hits = []

        for q in hits:
            key = f"{q.symbol}#gainer"
            last_ts = int((state.get(key) or {}).get("last_alert_ts", 0))
            if now - last_ts < interval_s:
                continue
            alerts.append(_Alert(kind="gainer", quote=q))

    _enrich_with_indicators(alerts)

    triggered: List[str] = []
    gainers: List[str] = []
    for a in alerts:
        q = a.quote
        if a.kind == "threshold":
            arrow = "📉" if a.direction == "below" else "📈"
            title = f"{arrow} {q.symbol} {a.direction} ${a.threshold}"
            msg = render_threshold_alert(q, float(a.threshold or 0), a.direction or "below")
            fan_out(notifiers, title, msg)
            state[f"{q.symbol}#threshold"] = {"last_alert_ts": now, "price": q.price}
            triggered.append(q.symbol)
        else:
            title = f"🚀 {q.symbol} {(q.day_change_pct or 0):+.2f}% (Top20)"
            msg = render_gainer_alert(q)
            fan_out(notifiers, title, msg)
            state[f"{q.symbol}#gainer"] = {"last_alert_ts": now, "price": q.price}
            gainers.append(q.symbol)

    flush = getattr(info_provider, "flush", None)
    if callable(flush):
        flush()
    _save_json(state, state_path)
    return {"triggered": triggered, "gainers": gainers}


def check_with_failure_alert(
    notifiers: List[Notifier],
    fn: Callable[[], Optional[Dict]],
    context: str = "monitor",
) -> Dict:
    try:
        result = fn()
        return result or {}
    except Exception as e:
        try:
            fan_out(notifiers, f"⚠ {context} 异常", render_error_alert(context, e))
        except Exception:
            pass
        raise
