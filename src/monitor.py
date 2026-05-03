import json
import os
from datetime import date
from typing import List

from .fetcher import get_quote
from .notifier import Notifier


def _load_state(path: str) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _save_state(state: dict, path: str) -> None:
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(state, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f"[warn] failed to write state: {e}")


def _hit(price: float, threshold: float, direction: str) -> bool:
    return price < threshold if direction == "below" else price > threshold


def check_once(
    watchlist: List[dict],
    notifiers: List[Notifier],
    state_path: str = "state.json",
    dedupe_per_day: bool = True,
) -> List[str]:
    state = _load_state(state_path)
    today = date.today().isoformat()
    triggered: List[str] = []

    for item in watchlist:
        sym = item["symbol"]
        try:
            q = get_quote(sym)
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

        if dedupe_per_day and state.get(sym, {}).get("last_alert_date") == today:
            continue

        title = f"📉 {sym} {direction} ${threshold}"
        msg = (
            f"**{q.name} ({sym})** 当前价格 ${q.price:.2f} {q.currency}\n"
            f"触发条件：{direction} ${threshold}"
        )
        for n in notifiers:
            try:
                n.send(title, msg)
            except Exception as e:
                print(f"[warn] notifier {type(n).__name__} failed: {e}")
        state[sym] = {"last_alert_date": today, "price": q.price}
        triggered.append(sym)

    _save_state(state, state_path)
    return triggered
