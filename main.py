"""
US stock watcher (yfinance).

Commands:
    python main.py query ORCL AMZN XLK GOOG
    python main.py search "Oracle"
    python main.py check          # 单次检查阈值并推送
    python main.py monitor        # 常驻轮询
    python main.py gainers        # 看一下当前 Top20 里涨幅 >5% 的
"""
import sys
import time

import yaml

from src.fetcher import get_quote
from src.lookup import search
from src.monitor import check_once
from src.movers import top_gainers_above
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
            change = f"({q.day_change_pct:+.2f}%)" if q.day_change_pct is not None else ""
            pe = f"PE {q.pe_ratio:.1f}" if q.pe_ratio is not None else "PE —"
            print(f"{q.symbol:<8} {q.name:<35} ${q.price:>9.2f} {change:>10}  {pe}")
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
        min_alert_interval_hours=float(cfg.get("min_alert_interval_hours", 1.5)),
        enable_gainer_alerts=cfg.get("enable_gainer_alerts", True),
        gainer_pct_threshold=float(cfg.get("gainer_pct_threshold", 5.0)),
        gainer_pool_size=int(cfg.get("gainer_pool_size", 20)),
    )


def cmd_monitor(_args):
    cfg = load_config()
    notifiers = build_notifiers(cfg.get("notifiers", {}))
    interval = int(cfg.get("poll_interval_seconds", 600))
    print(f"monitoring {len(cfg['watchlist'])} tickers, every {interval}s ...")
    while True:
        try:
            check_once(
                cfg["watchlist"],
                notifiers,
                min_alert_interval_hours=float(cfg.get("min_alert_interval_hours", 1.5)),
                enable_gainer_alerts=cfg.get("enable_gainer_alerts", True),
                gainer_pct_threshold=float(cfg.get("gainer_pct_threshold", 5.0)),
                gainer_pool_size=int(cfg.get("gainer_pool_size", 20)),
            )
        except Exception as e:
            print(f"[warn] iteration failed: {e}")
        time.sleep(interval)


def cmd_gainers(_args):
    cfg = load_config()
    pct = float(cfg.get("gainer_pct_threshold", 5.0))
    pool = int(cfg.get("gainer_pool_size", 20))
    hits = top_gainers_above(pct_threshold=pct, pool_size=pool)
    if not hits:
        print(f"no gainers >= {pct}% in top {pool}")
        return
    for q in hits:
        pe = f"PE {q.pe_ratio:.1f}" if q.pe_ratio is not None else "PE —"
        cap = f"${q.market_cap / 1e9:.1f}B" if q.market_cap else "—"
        print(f"{q.symbol:<6} {(q.day_change_pct or 0):+.2f}%  ${q.price:>8.2f}  cap {cap:<7} {pe}")


COMMANDS = {
    "query": cmd_query,
    "search": cmd_search,
    "check": cmd_check,
    "monitor": cmd_monitor,
    "gainers": cmd_gainers,
}


def main():
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(f"usage: python main.py [{'|'.join(COMMANDS)}] ...")
        sys.exit(1)
    COMMANDS[sys.argv[1]](sys.argv[2:])


if __name__ == "__main__":
    main()
