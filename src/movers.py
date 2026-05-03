"""Top movers among large-cap US tech stocks.

批量拉两日收盘算涨跌幅，按 |change_pct| / change_pct 排序返回 TopN。
进程内缓存 5 分钟，避免每次请求都打 Yahoo（盘中数据 5 分钟内够用，
冷启动会重算一次）。
"""
import time
from typing import Dict, List

import yfinance as yf

# SP500 大盘科技股清单。手动维护比动态拉 SP500 成分股稳。
TECH_TICKERS: List[str] = [
    # MAG7
    "AAPL", "MSFT", "GOOGL", "AMZN", "META", "NVDA", "TSLA",
    # 半导体
    "AVGO", "AMD", "QCOM", "INTC", "TXN", "MU", "AMAT", "LRCX", "KLAC", "MRVL", "ADI",
    # 软件 / SaaS
    "ORCL", "CRM", "ADBE", "NOW", "INTU", "PANW", "CRWD", "FTNT", "SNPS", "CDNS", "WDAY", "ANET",
    # 互联网 / 媒体
    "NFLX", "UBER", "ABNB", "PYPL",
    # 硬件 / 老牌
    "CSCO", "IBM", "DELL", "HPQ", "HPE",
]

_TTL_SECONDS = 300
_cache: Dict = {"ts": 0.0, "rows": []}


def _compute() -> List[Dict]:
    df = yf.download(
        tickers=" ".join(TECH_TICKERS),
        period="5d",
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    )
    rows: List[Dict] = []
    for sym in TECH_TICKERS:
        try:
            closes = df[sym]["Close"].dropna()
        except (KeyError, AttributeError):
            continue
        if len(closes) < 2:
            continue
        prev = float(closes.iloc[-2])
        curr = float(closes.iloc[-1])
        if prev <= 0:
            continue
        rows.append({
            "symbol": sym,
            "price": round(curr, 2),
            "prev_close": round(prev, 2),
            "change_pct": round((curr - prev) / prev * 100.0, 2),
        })
    return rows


def top_movers(limit: int = 20, direction: str = "both") -> List[Dict]:
    """direction: 'both' (按 |涨跌幅| 排), 'up' (只取涨), 'down' (只取跌)."""
    now = time.time()
    if not _cache["rows"] or now - _cache["ts"] > _TTL_SECONDS:
        _cache["rows"] = _compute()
        _cache["ts"] = now

    rows = _cache["rows"]
    if direction == "up":
        ranked = sorted(rows, key=lambda r: -r["change_pct"])
    elif direction == "down":
        ranked = sorted(rows, key=lambda r: r["change_pct"])
    else:
        ranked = sorted(rows, key=lambda r: -abs(r["change_pct"]))
    return ranked[: max(1, min(limit, len(rows)))]
