"""Local CLI mirror of backend/monitor_handler.py (v2).

State lives in state.json + metrics_cache.json instead of DynamoDB; otherwise
the pipeline is identical: candidate pool = watchlist + movers, LLM evaluates
each, only buy/sell with confidence ≥ threshold get pushed.
"""
import datetime
import json
import os
import time
from dataclasses import dataclass
from typing import Callable, Dict, List, Optional

from .advisor import advise, advisor_enabled, should_push
from .fetcher import Quote, batch_history, fetch_info, get_quote
from .indicators import apply_signals
from .movers import top_movers
from .news import fetch_news
from .notifier import (
    Notifier,
    fan_out,
    render_advice,
    render_error_alert,
)


@dataclass
class _Candidate:
    symbol: str
    source: str          # "watchlist" | "mover"
    horizon: str = "short"
    notes: str = ""
    change_pct: float = 0.0


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


def _build_candidates(watchlist_items: List[dict]) -> List[_Candidate]:
    max_n = int(float(os.environ.get("ADVISOR_MAX_CANDIDATES", "30")))
    mover_pct = float(os.environ.get("MOVER_CHANGE_PCT_THRESHOLD", "3.0"))

    seen = set()
    wl: List[_Candidate] = []
    for it in watchlist_items:
        sym = (it.get("symbol") or "").upper()
        if not sym:
            continue
        horizon = str(it.get("strategy_horizon") or "short")
        if horizon == "skip":
            continue
        wl.append(_Candidate(
            symbol=sym, source="watchlist", horizon=horizon,
            notes=str(it.get("strategy_notes") or ""),
        ))
        seen.add(sym)

    mv: List[_Candidate] = []
    try:
        movers = top_movers(limit=40, direction="both")
    except Exception as e:
        print(f"[warn] top_movers failed: {e}")
        movers = []
    for r in movers:
        sym = (r.get("symbol") or "").upper()
        if not sym or sym in seen:
            continue
        chg = float(r.get("change_pct") or 0.0)
        if abs(chg) < mover_pct:
            continue
        mv.append(_Candidate(symbol=sym, source="mover", change_pct=chg))
        seen.add(sym)

    mv.sort(key=lambda c: -abs(c.change_pct))
    quota = max(0, max_n - len(wl))
    return wl + mv[:quota]


def _evaluate_one(c: _Candidate, df_3mo, info_provider,
                  notifiers: List[Notifier], state: dict, now: int) -> Optional[str]:
    cooldown_h = float(os.environ.get("ADVISOR_COOLDOWN_HOURS", "6.0"))
    last = int((state.get(f"{c.symbol}#advice") or {}).get("last_alert_ts", 0))
    if now - last < int(cooldown_h * 3600):
        print(f"[advisor] {c.symbol} cooldown, skip")
        return None

    try:
        q = get_quote(c.symbol, info_provider=info_provider)
    except Exception as e:
        print(f"[warn] quote {c.symbol}: {e}")
        return None

    if df_3mo is not None:
        try:
            apply_signals(q, df_3mo)
        except Exception as e:
            print(f"[warn] indicators {c.symbol}: {e}")

    try:
        df_1y = batch_history([c.symbol], period="1y").get(c.symbol)
    except Exception:
        df_1y = None
    try:
        news = fetch_news(c.symbol)
    except Exception:
        news = []

    try:
        adv = advise(q, df_3mo, df_1y, news, horizon=c.horizon, notes=c.notes)
    except Exception as e:
        print(f"[warn] advisor {c.symbol}: {e}")
        return None

    if not should_push(adv, source=c.source):
        verdict = "—" if adv is None else f"{adv.action} ({adv.confidence:.0%})"
        print(f"[advisor] {c.symbol} silent: {verdict}")
        return None

    title, body = render_advice(q, adv, source=c.source)
    fan_out(notifiers, title, body)
    state[f"{c.symbol}#advice"] = {"last_alert_ts": now, "price": q.price}
    return c.symbol


def check_once(
    watchlist: List[dict],
    notifiers: List[Notifier],
    state_path: str = "state.json",
    metrics_cache_path: str = "metrics_cache.json",
    **_legacy_kwargs,
) -> Dict:
    """v2 entry. legacy kwargs (min_alert_interval_hours, gainer_*) accepted but ignored."""
    if not advisor_enabled():
        print("[advisor] disabled (no DASHSCOPE_API_KEY or ADVISOR_ENABLED=0)")
        return {"checked": 0, "pushed": []}

    state = _load_json(state_path)
    info_provider = _make_file_info_provider(metrics_cache_path)
    now = int(time.time())

    candidates = _build_candidates(watchlist)
    print(f"[run] candidates: {len(candidates)} "
          f"(watchlist={sum(1 for c in candidates if c.source=='watchlist')}, "
          f"movers={sum(1 for c in candidates if c.source=='mover')})")
    if not candidates:
        return {"checked": 0, "pushed": []}

    budget = int(float(os.environ.get("ADVISOR_DAILY_BUDGET", "200")))
    today = datetime.datetime.utcfromtimestamp(now).strftime("%Y-%m-%d")
    used = 0
    for k, v in state.items():
        if not k.endswith("#advice"):
            continue
        ts = int((v or {}).get("last_alert_ts") or 0)
        if not ts:
            continue
        if datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d") == today:
            used += 1
    if used >= budget:
        print(f"[advisor] daily budget exhausted ({used}/{budget})")
        return {"checked": 0, "pushed": [], "skipped_budget": True}

    syms = [c.symbol for c in candidates]
    try:
        hist = batch_history(syms, period="3mo")
    except Exception as e:
        print(f"[warn] batch_history failed: {e}")
        hist = {}

    pushed: List[str] = []
    for c in candidates:
        if used >= budget:
            print(f"[advisor] budget hit mid-run, stop at {c.symbol}")
            break
        r = _evaluate_one(c, hist.get(c.symbol), info_provider, notifiers, state, now)
        used += 1
        if r:
            pushed.append(r)

    flush = getattr(info_provider, "flush", None)
    if callable(flush):
        flush()
    _save_json(state, state_path)
    return {"checked": len(candidates), "pushed": pushed}


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
