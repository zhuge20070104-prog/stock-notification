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


def upsert_watchlist(
    symbol: str,
    strategy_horizon: str = "short",
    strategy_notes: Optional[str] = None,
) -> dict:
    """v2: only symbol + strategy fields. Legacy threshold/direction are ignored
    (rows with those fields still in DDB are read fine; new writes won't set them)."""
    item = {"symbol": symbol.upper(), "strategy_horizon": strategy_horizon}
    if strategy_notes:
        item["strategy_notes"] = strategy_notes
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


_MACRO_KEY = "__macro_latest"


def put_macro_state(state_dict: Dict) -> None:
    """Cache the latest MacroState (as dict) in the state table. Read by api_handler
    to serve the dashboard without recomputing yfinance fetches on every page load."""
    _state().put_item(Item={
        "symbol": _MACRO_KEY,
        "last_alert_ts": int(time.time()),
        "data": json.dumps(state_dict, default=str, ensure_ascii=False),
    })


def get_macro_state() -> Optional[Dict]:
    """Return {"updated_at": int, "state": dict} or None if not cached yet."""
    r = _state().get_item(Key={"symbol": _MACRO_KEY})
    item = r.get("Item")
    if not item:
        return None
    raw = item.get("data")
    if not isinstance(raw, str):
        return None
    try:
        state = json.loads(raw)
    except Exception:
        return None
    return {"updated_at": int(item.get("last_alert_ts") or 0), "state": state}


def count_advice_today(date_str: str) -> int:
    """Count advice records emitted today. Cheap-ish: scans state table once per call.
    Acceptable because daily volume is tiny (<200) and advisor only runs on alert."""
    items = _state().scan().get("Items", [])
    n = 0
    for it in items:
        sym = it.get("symbol", "")
        if not sym.endswith("#advice"):
            continue
        ts = int(it.get("last_alert_ts") or 0)
        if not ts:
            continue
        import datetime
        d = datetime.datetime.utcfromtimestamp(ts).strftime("%Y-%m-%d")
        if d == date_str:
            n += 1
    return n


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
