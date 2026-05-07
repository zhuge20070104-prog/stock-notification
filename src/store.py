"""DynamoDB 存储：watchlist + alert state + metrics cache。仅在 AWS Lambda 中使用。"""
import json
import os
import time
from decimal import Decimal
from typing import Dict, List, Optional

import boto3

_dynamodb = boto3.resource("dynamodb")


def _watchlist():
    return _dynamodb.Table(os.environ["WATCHLIST_TABLE"])


def _state():
    return _dynamodb.Table(os.environ["STATE_TABLE"])


def _metrics_cache():
    return _dynamodb.Table(os.environ["METRICS_CACHE_TABLE"])


def _decode(item: dict) -> dict:
    out = dict(item)
    for k in ("threshold", "price"):
        if k in out and out[k] is not None:
            out[k] = float(out[k])
    if "last_alert_ts" in out and out["last_alert_ts"] is not None:
        out["last_alert_ts"] = int(out["last_alert_ts"])
    return out


def list_watchlist() -> List[dict]:
    items = _watchlist().scan().get("Items", [])
    return sorted([_decode(i) for i in items], key=lambda x: x["symbol"])


def upsert_watchlist(symbol: str, threshold: Optional[float], direction: str = "below") -> dict:
    item = {"symbol": symbol.upper(), "direction": direction}
    if threshold is not None:
        item["threshold"] = Decimal(str(threshold))
    _watchlist().put_item(Item=item)
    return _decode(item)


def delete_watchlist(symbol: str) -> None:
    _watchlist().delete_item(Key={"symbol": symbol.upper()})


def get_alert_state(symbol: str) -> Optional[dict]:
    r = _state().get_item(Key={"symbol": symbol.upper()})
    item = r.get("Item")
    return _decode(item) if item else None


def set_alert_state(symbol: str, last_alert_ts: int, price: float, kind: str = "threshold") -> None:
    """`kind` 区分告警类别（threshold/gainer），同一标的两类各自独立节流。"""
    key = f"{symbol.upper()}#{kind}"
    _state().put_item(Item={
        "symbol": key,
        "last_alert_ts": last_alert_ts,
        "price": Decimal(str(price)),
    })


def get_alert_ts(symbol: str, kind: str = "threshold") -> Optional[int]:
    key = f"{symbol.upper()}#{kind}"
    r = _state().get_item(Key={"symbol": key})
    item = r.get("Item")
    if not item:
        return None
    ts = item.get("last_alert_ts")
    return int(ts) if ts is not None else None


def get_cached_info(symbol: str) -> Optional[Dict]:
    r = _metrics_cache().get_item(Key={"symbol": symbol.upper()})
    item = r.get("Item")
    if not item:
        return None
    expires = int(item.get("expires_at", 0) or 0)
    if expires and expires < int(time.time()):
        return None
    raw = item.get("data")
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return None
    return None


def put_cached_info(symbol: str, data: Dict, ttl_seconds: int = 86400) -> None:
    _metrics_cache().put_item(Item={
        "symbol": symbol.upper(),
        "data": json.dumps(data, default=str),
        "expires_at": int(time.time()) + int(ttl_seconds),
    })
