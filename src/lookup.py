from typing import List, Dict

import yfinance as yf


def search(query: str, max_results: int = 8) -> List[Dict]:
    """按公司名或代码模糊搜索，返回候选 [{symbol, name, exchange, type}, ...]。"""
    s = yf.Search(query, max_results=max_results)
    out: List[Dict] = []
    for q in getattr(s, "quotes", []) or []:
        out.append({
            "symbol": q.get("symbol"),
            "name": q.get("shortname") or q.get("longname") or "",
            "exchange": q.get("exchange") or "",
            "type": q.get("quoteType") or "",
        })
    return out
