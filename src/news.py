"""Per-symbol news fetcher via yfinance. Free, no extra deps.

Used by advisor.py to give the LLM recent headlines.
"""
import time
from typing import Dict, List

import yfinance as yf


def fetch_news(symbol: str, limit: int = 5, days: int = 7) -> List[Dict]:
    sym = symbol.upper()
    try:
        raw = yf.Ticker(sym).news or []
    except Exception as e:
        print(f"[warn] news {sym}: {e}")
        return []

    cutoff = int(time.time()) - days * 86400
    out: List[Dict] = []
    for n in raw:
        content = n.get("content") if isinstance(n.get("content"), dict) else None
        title = (content or n).get("title") or ""
        publisher = ""
        if content:
            prov = content.get("provider") or {}
            publisher = prov.get("displayName") or ""
        publisher = publisher or n.get("publisher") or ""
        ts = n.get("providerPublishTime") or 0
        if not ts and content:
            pub_date = content.get("pubDate") or ""
            ts = _parse_iso(pub_date)
        if ts and ts < cutoff:
            continue
        summary = ""
        if content:
            summary = content.get("summary") or content.get("description") or ""
        if not title:
            continue
        out.append({
            "title": title.strip(),
            "publisher": publisher.strip(),
            "ts": int(ts) if ts else 0,
            "summary": summary.strip()[:280],
        })
        if len(out) >= limit:
            break
    return out


def _parse_iso(s: str) -> int:
    if not s:
        return 0
    try:
        from datetime import datetime
        s2 = s.replace("Z", "+00:00")
        return int(datetime.fromisoformat(s2).timestamp())
    except Exception:
        return 0
