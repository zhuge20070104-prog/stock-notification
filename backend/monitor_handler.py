"""Scheduled Lambda: pull each watched ticker, check threshold, push alerts.

Phase-based:
  1. Quote each watchlist symbol; collect threshold hits that pass 1.5h dedupe.
  2. Scan Top20 movers; collect gainers >= 5% that pass 1.5h dedupe.
  3. ONE batched yf.download(period='3mo') for every alerted symbol → attach
     Williams %R / MACD / KST signals to each Quote.
  4. Render rich card and fan out to feishu / Server酱 / console.

Failures bubble up as a notification so silent failures don't go unseen.
"""
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from fetcher import Quote, batch_history, fetch_info, get_quote
from indicators import apply_signals
from movers import top_gainers_above
from notifier import (
    build_notifiers,
    fan_out,
    render_error_alert,
    render_gainer_alert,
    render_threshold_alert,
)
from store import (
    get_alert_ts,
    get_cached_info,
    list_watchlist,
    put_cached_info,
    set_alert_state,
)


@dataclass
class _Alert:
    kind: str  # "threshold" | "gainer"
    quote: Quote
    threshold: Optional[float] = None
    direction: Optional[str] = None


def _notifier_cfg() -> dict:
    return {
        "console": {"enabled": True},
        "feishu": {
            "enabled": bool(os.environ.get("FEISHU_WEBHOOK")),
            "webhook_url": os.environ.get("FEISHU_WEBHOOK", ""),
        },
        "serverchan": {
            "enabled": bool(os.environ.get("SERVERCHAN_SENDKEY")),
            "sendkey": os.environ.get("SERVERCHAN_SENDKEY", ""),
        },
    }


def _make_ddb_info_provider(ttl_seconds: int = 86400):
    def provider(symbol: str) -> Dict:
        cached = get_cached_info(symbol)
        if cached is not None:
            return cached
        try:
            data = fetch_info(symbol)
        except Exception:
            data = {}
        if data:
            try:
                put_cached_info(symbol, data, ttl_seconds=ttl_seconds)
            except Exception as e:
                print(f"[warn] cache write {symbol}: {e}")
        return data
    return provider


def _hit(price: float, threshold: float, direction: str) -> bool:
    return price < threshold if direction == "below" else price > threshold


def _enrich_with_indicators(alerts: List[_Alert]) -> None:
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


def _run(notifiers) -> Dict:
    interval_h = float(os.environ.get("MIN_ALERT_INTERVAL_HOURS", "1.5"))
    interval_s = int(interval_h * 3600)
    gainer_pct = float(os.environ.get("GAINER_PCT_THRESHOLD", "5"))
    gainer_pool = int(os.environ.get("GAINER_POOL_SIZE", "20"))
    enable_gainers = os.environ.get("ENABLE_GAINER_ALERTS", "1") not in ("0", "false", "False")

    info_provider = _make_ddb_info_provider()
    watchlist = list_watchlist()
    now = int(time.time())
    alerts: List[_Alert] = []

    for item in watchlist:
        sym = item["symbol"]
        threshold = item.get("threshold")
        direction = item.get("direction", "below")

        try:
            q = get_quote(sym, info_provider=info_provider)
        except Exception as e:
            print(f"[warn] {sym}: {e}")
            continue

        if threshold is None:
            continue

        hit = _hit(q.price, threshold, direction)
        print(f"{sym}: ${q.price:.2f} threshold {direction} {threshold} -> {'HIT' if hit else 'ok'}")
        if not hit:
            continue

        last_ts = get_alert_ts(sym, kind="threshold") or 0
        if now - last_ts < interval_s:
            continue

        alerts.append(_Alert(kind="threshold", quote=q, threshold=float(threshold), direction=direction))

    if enable_gainers:
        try:
            hits: List[Quote] = top_gainers_above(
                pct_threshold=gainer_pct,
                pool_size=gainer_pool,
                info_provider=info_provider,
            )
        except Exception as e:
            print(f"[warn] gainers scan: {e}")
            hits = []

        for q in hits:
            last_ts = get_alert_ts(q.symbol, kind="gainer") or 0
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
            set_alert_state(q.symbol, now, q.price, kind="threshold")
            triggered.append(q.symbol)
        else:
            title = f"🚀 {q.symbol} {(q.day_change_pct or 0):+.2f}% (Top20)"
            msg = render_gainer_alert(q)
            fan_out(notifiers, title, msg)
            set_alert_state(q.symbol, now, q.price, kind="gainer")
            gainers.append(q.symbol)

    return {"checked": len(watchlist), "triggered": triggered, "gainers": gainers}


def handler(event, context):
    notifiers = build_notifiers(_notifier_cfg())
    try:
        return _run(notifiers)
    except Exception as e:
        try:
            fan_out(notifiers, "⚠ monitor 执行失败", render_error_alert("monitor", e))
        except Exception:
            pass
        raise
