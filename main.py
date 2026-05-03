"""
US stock watcher (yfinance).

Commands:
    python main.py query ORCL AMZN XLK GOOG
    python main.py search "Oracle"
    python main.py check          # 单次检查阈值并推送
    python main.py monitor        # 常驻轮询
"""
import sys
import time

import yaml

from src.fetcher import get_quote
from src.lookup import search
from src.monitor import check_once
from src.notifier import build_notifiers


def load_config(path: str = "config.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def cmd_query(args):
    if not args:
        print("usage: query SYMBOL [SYMBOL ...]")
        return
    for s in args:
        try:
            q = get_quote(s)
            print(f"{q.symbol:<8} {q.name:<40} ${q.price:>10.2f} {q.currency}")
        except Exception as e:
            print(f"{s}: error {e}")


def cmd_search(args):
    if not args:
        print('usage: search "QUERY"')
        return
    query = " ".join(args)
    results = search(query)
    if not results:
        print("no matches")
        return
    for r in results:
        print(f"{r['symbol']:<10} {r['name']:<45} {r['exchange']:<8} {r['type']}")


def cmd_check(_args):
    cfg = load_config()
    notifiers = build_notifiers(cfg.get("notifiers", {}))
    check_once(
        cfg["watchlist"],
        notifiers,
        dedupe_per_day=cfg.get("dedupe_per_day", True),
    )


def cmd_monitor(_args):
    cfg = load_config()
    notifiers = build_notifiers(cfg.get("notifiers", {}))
    interval = int(cfg.get("poll_interval_seconds", 600))
    dedupe = cfg.get("dedupe_per_day", True)
    print(f"monitoring {len(cfg['watchlist'])} tickers, every {interval}s ...")
    while True:
        try:
            check_once(cfg["watchlist"], notifiers, dedupe_per_day=dedupe)
        except Exception as e:
            print(f"[warn] iteration failed: {e}")
        time.sleep(interval)


COMMANDS = {
    "query": cmd_query,
    "search": cmd_search,
    "check": cmd_check,
    "monitor": cmd_monitor,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python main.py [{'|'.join(COMMANDS)}] ...")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
