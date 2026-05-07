"""HTTP API behind a Lambda Function URL.

Routes:
    GET    /watchlist
    POST   /watchlist           {symbol, threshold?, direction?}
    DELETE /watchlist/{symbol}
    GET    /quote?symbols=A,B,C
    GET    /search?q=...
"""
import json
import os

from fetcher import fetch_info, get_quote
from lookup import search as do_search
from movers import top_movers
from store import (
    delete_watchlist,
    get_cached_info,
    list_watchlist,
    put_cached_info,
    upsert_watchlist,
)


def _make_info_provider(ttl_seconds: int = 86400):
    def provider(symbol: str):
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


def _resp(status: int, body):
    # CORS 由 API Gateway HTTP API 处理（cors_configuration），这里只管业务响应。
    return {
        "statusCode": status,
        "headers": {"content-type": "application/json"},
        "body": json.dumps(body, ensure_ascii=False),
    }


def _strip_api_prefix(path: str) -> str:
    """CloudFront 把 /api/* 整段转给 API Gateway，Lambda 看到的 rawPath 是 /api/foo。
    剥掉前缀，路由表只关心业务路径。直连 API Gateway URL 时路径没前缀，原样返回。"""
    if path.startswith("/api/"):
        return path[4:]
    if path == "/api":
        return "/"
    return path


def _authorized(event) -> bool:
    expected = os.environ.get("API_KEY")
    if not expected:
        return True
    headers = {k.lower(): v for k, v in (event.get("headers") or {}).items()}
    return headers.get("x-api-key") == expected


def _route(event):
    method = event.get("requestContext", {}).get("http", {}).get("method", "GET")
    path = _strip_api_prefix(event.get("rawPath", "/"))
    qs = event.get("queryStringParameters") or {}

    if method == "OPTIONS":
        return _resp(200, {})

    if not _authorized(event):
        return _resp(401, {"error": "unauthorized"})

    if path == "/watchlist" and method == "GET":
        return _resp(200, list_watchlist())

    if path == "/watchlist" and method == "POST":
        body = json.loads(event.get("body") or "{}")
        symbol = (body.get("symbol") or "").strip().upper()
        if not symbol:
            return _resp(400, {"error": "symbol required"})
        threshold = body.get("threshold")
        direction = body.get("direction") or "below"
        if direction not in ("below", "above"):
            return _resp(400, {"error": "direction must be 'below' or 'above'"})
        item = upsert_watchlist(symbol, threshold, direction)
        return _resp(200, item)

    if path.startswith("/watchlist/") and method == "DELETE":
        sym = path.split("/", 2)[2].upper()
        delete_watchlist(sym)
        return _resp(200, {"deleted": sym})

    if path == "/quote" and method == "GET":
        symbols = [s.strip().upper() for s in (qs.get("symbols") or "").split(",") if s.strip()]
        out = []
        info_provider = _make_info_provider()
        for s in symbols:
            try:
                q = get_quote(s, info_provider=info_provider)
                out.append({
                    "symbol": q.symbol,
                    "price": q.price,
                    "name": q.name,
                    "currency": q.currency,
                    "previous_close": q.previous_close,
                    "day_change_pct": q.day_change_pct,
                    "day_high": q.day_high,
                    "day_low": q.day_low,
                    "volume": q.volume,
                    "pe_ratio": q.pe_ratio,
                    "forward_pe": q.forward_pe,
                    "market_cap": q.market_cap,
                    "avg_volume": q.avg_volume,
                    "week52_high": q.week52_high,
                    "week52_low": q.week52_low,
                    "dividend_yield": q.dividend_yield,
                    "beta": q.beta,
                })
            except Exception as e:
                out.append({"symbol": s, "error": str(e)})
        return _resp(200, out)

    if path == "/search" and method == "GET":
        q = (qs.get("q") or "").strip()
        if not q:
            return _resp(400, {"error": "q required"})
        return _resp(200, do_search(q))

    if path == "/movers" and method == "GET":
        try:
            limit = int(qs.get("limit") or 20)
        except ValueError:
            limit = 20
        direction = qs.get("dir") or "both"
        if direction not in ("both", "up", "down"):
            direction = "both"
        return _resp(200, top_movers(limit=limit, direction=direction))

    return _resp(404, {"error": "not found", "path": path, "method": method})


def handler(event, context):
    try:
        return _route(event)
    except Exception as e:
        return _resp(500, {"error": str(e)})
