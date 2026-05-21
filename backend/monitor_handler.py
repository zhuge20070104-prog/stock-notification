"""Scheduled Lambda: LLM-driven trading suggestion fan-out (v2).

Pipeline:
  1. Build candidate pool = watchlist (all, unless horizon='skip')
     + TECH_TICKERS movers with |change_pct| >= MOVER_CHANGE_PCT_THRESHOLD.
  2. Cap to ADVISOR_MAX_CANDIDATES; watchlist always wins, movers ranked by |change|.
  3. Quote each candidate, batch-download 3mo history, attach indicators.
  4. For each, fetch 1y history (for MA250) + news, call LLM.
  5. Push only if action ∈ {buy, sell} AND confidence >= ADVISOR_PUSH_MIN_CONFIDENCE.
     Otherwise silent. Per-symbol 6h cooldown via state table (kind='advice').

There are no threshold/gainer alerts in v2 — LLM is the sole gate.
"""
import datetime
import os
import time
from dataclasses import dataclass
from typing import Dict, List, Optional

from advisor import advise, advisor_enabled, should_push
from fetcher import Quote, batch_history, fetch_info, get_quote
from indicators import apply_signals
from macro import compute_macro_state
from movers import top_movers
from news import fetch_news
from notifier import (
    build_notifiers,
    fan_out,
    render_advice,
    render_error_alert,
    render_macro_briefing,
)
from store import (
    count_advice_today,
    get_alert_ts,
    get_cached_info,
    list_watchlist,
    put_cached_info,
    put_macro_state,
    set_alert_state,
)


@dataclass
class _Candidate:
    symbol: str
    source: str         # "watchlist" | "mover"
    horizon: str = "short"
    notes: str = ""
    change_pct: float = 0.0   # 仅 mover 用于排序


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


def _evaluate_one(c: _Candidate, df_3mo, info_provider, notifiers, now: int,
                  macro=None) -> Optional[str]:
    """Quote + LLM + push for one candidate. Returns symbol if pushed else None."""
    cooldown_h = float(os.environ.get("ADVISOR_COOLDOWN_HOURS", "6.0"))
    last = get_alert_ts(c.symbol, kind="advice") or 0
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
        adv = advise(q, df_3mo, df_1y, news, horizon=c.horizon, notes=c.notes, macro=macro)
    except Exception as e:
        print(f"[warn] advisor {c.symbol}: {e}")
        return None

    if not should_push(adv, source=c.source):
        verdict = "—" if adv is None else f"{adv.action} ({adv.confidence:.0%})"
        print(f"[advisor] {c.symbol} silent: {verdict}")
        return None

    title, body = render_advice(q, adv, source=c.source)
    fan_out(notifiers, title, body)
    try:
        set_alert_state(c.symbol, now, q.price, kind="advice")
    except Exception as e:
        print(f"[warn] state {c.symbol}: {e}")
    return c.symbol


def _run(notifiers) -> Dict:
    if not advisor_enabled():
        print("[advisor] disabled (no DASHSCOPE_API_KEY or ADVISOR_ENABLED=0)")
        return {"checked": 0, "pushed": [], "skipped_budget": False}

    info_provider = _make_ddb_info_provider()
    watchlist = list_watchlist()
    candidates = _build_candidates(watchlist)
    print(f"[run] candidates: {len(candidates)} "
          f"(watchlist={sum(1 for c in candidates if c.source=='watchlist')}, "
          f"movers={sum(1 for c in candidates if c.source=='mover')})")

    # ── 算宏观状态（run 开头算一次，单股 prompt + 结尾简报共用）──
    try:
        macro = compute_macro_state()
        print(f"[macro] scenario={macro.scenario} ({macro.scenario_name}) "
              f"VIX={macro.vix} drop={macro.spy_drop_pct}")
        # 缓存到 DDB，供 web UI 顶部展示（避免每次打开页面都重算 5 个 yfinance 调用）
        try:
            put_macro_state(macro.to_dict())
        except Exception as e:
            print(f"[warn] macro cache write failed: {e}")
    except Exception as e:
        print(f"[warn] macro compute failed: {e}")
        macro = None

    if not candidates:
        # 没有候选也推一张简报
        if macro is not None:
            try:
                t, b = render_macro_briefing(macro)
                fan_out(notifiers, t, b)
            except Exception as e:
                print(f"[warn] macro briefing render failed: {e}")
        return {"checked": 0, "pushed": [], "macro_scenario": getattr(macro, "scenario", None)}

    # Daily budget guard (counts today's advice records in state table)
    budget = int(float(os.environ.get("ADVISOR_DAILY_BUDGET", "200")))
    now = int(time.time())
    today = datetime.datetime.utcfromtimestamp(now).strftime("%Y-%m-%d")
    try:
        used = count_advice_today(today)
    except Exception as e:
        print(f"[warn] budget check failed: {e}")
        used = 0
    if used >= budget:
        print(f"[advisor] daily budget exhausted ({used}/{budget})")
        return {"checked": 0, "pushed": [], "skipped_budget": True}

    # ONE batched 3mo history call covering all candidates
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
        result = _evaluate_one(c, hist.get(c.symbol), info_provider, notifiers, now, macro=macro)
        used += 1
        if result:
            pushed.append(result)

    # ── 单股都推完后，推一张大盘简报作为 TL;DR ──
    if macro is not None:
        try:
            t, b = render_macro_briefing(macro)
            fan_out(notifiers, t, b)
        except Exception as e:
            print(f"[warn] macro briefing render failed: {e}")

    return {
        "checked": len(candidates),
        "pushed": pushed,
        "macro_scenario": getattr(macro, "scenario", None),
    }


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
