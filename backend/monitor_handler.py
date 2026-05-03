"""Scheduled Lambda: pull each watched ticker, check threshold, push alerts."""
import os
from datetime import date

from fetcher import get_quote
from notifier import build_notifiers
from store import get_alert_state, list_watchlist, set_alert_state


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


def handler(event, context):
    watchlist = list_watchlist()
    notifiers = build_notifiers(_notifier_cfg())
    today = date.today().isoformat()
    triggered = []

    for item in watchlist:
        sym = item["symbol"]
        threshold = item.get("threshold")
        direction = item.get("direction", "below")

        try:
            q = get_quote(sym)
        except Exception as e:
            print(f"[warn] {sym}: {e}")
            continue

        if threshold is None:
            continue

        hit = (q.price < threshold) if direction == "below" else (q.price > threshold)
        print(f"{sym}: ${q.price:.2f} threshold {direction} {threshold} -> {'HIT' if hit else 'ok'}")
        if not hit:
            continue

        st = get_alert_state(sym) or {}
        if st.get("last_alert_date") == today:
            continue

        title = f"📉 {sym} {direction} ${threshold}"
        msg = (
            f"**{q.name} ({sym})** 当前 ${q.price:.2f} {q.currency}\n"
            f"触发：{direction} ${threshold}"
        )
        for n in notifiers:
            try:
                n.send(title, msg)
            except Exception as e:
                print(f"[warn] {type(n).__name__} failed: {e}")

        set_alert_state(sym, today, q.price)
        triggered.append(sym)

    return {"checked": len(watchlist), "triggered": triggered}
