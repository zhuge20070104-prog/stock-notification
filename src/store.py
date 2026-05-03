"""DynamoDB 存储：watchlist + alert state。仅在 AWS Lambda 中使用。"""
import os
from decimal import Decimal
from typing import List, Optional

import boto3

_dynamodb = boto3.resource("dynamodb")


def _watchlist():
    return _dynamodb.Table(os.environ["WATCHLIST_TABLE"])


def _state():
    return _dynamodb.Table(os.environ["STATE_TABLE"])


def _decode(item: dict) -> dict:
    out = dict(item)
    if "threshold" in out and out["threshold"] is not None:
        out["threshold"] = float(out["threshold"])
    if "price" in out and out["price"] is not None:
        out["price"] = float(out["price"])
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


def set_alert_state(symbol: str, last_alert_date: str, price: float) -> None:
    _state().put_item(Item={
        "symbol": symbol.upper(),
        "last_alert_date": last_alert_date,
        "price": Decimal(str(price)),
    })
