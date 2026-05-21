"""Tests for the macro-state DDB cache helpers in src/store.py.

The boto3 stub in conftest.py provides a no-op DynamoDB resource. We replace
its _state() table with a per-test in-memory dict so we can verify the
put → get roundtrip without a real DynamoDB connection.
"""
import json
import os

import pytest


@pytest.fixture
def store_with_memtable(monkeypatch):
    # Need WATCHLIST_TABLE etc env vars before importing store (it uses os.environ at access)
    monkeypatch.setenv("WATCHLIST_TABLE", "wl")
    monkeypatch.setenv("STATE_TABLE", "st")
    monkeypatch.setenv("METRICS_CACHE_TABLE", "mc")

    from src import store

    class _MemTable:
        def __init__(self):
            self.items = {}
        def put_item(self, Item):
            self.items[Item["symbol"]] = dict(Item)
        def get_item(self, Key):
            sym = Key["symbol"]
            return {"Item": self.items[sym]} if sym in self.items else {}
        def delete_item(self, Key):
            self.items.pop(Key["symbol"], None)
        def scan(self, **_):
            return {"Items": list(self.items.values())}

    mem = _MemTable()
    monkeypatch.setattr(store, "_state", lambda: mem)
    return store, mem


class TestMacroRoundtrip:
    def test_put_then_get_returns_state(self, store_with_memtable):
        store, mem = store_with_memtable
        state = {
            "scenario": 2,
            "scenario_name": "场景二 — 恐惧回调",
            "vix": 27.5,
            "spy_drop_pct": -8.0,
            "direction": "buy",
            "allocation_pct": 30,
        }
        store.put_macro_state(state)
        result = store.get_macro_state()
        assert result is not None
        assert result["state"]["scenario"] == 2
        assert result["state"]["scenario_name"] == "场景二 — 恐惧回调"
        assert result["state"]["vix"] == 27.5
        assert result["updated_at"] > 0

    def test_get_returns_none_when_no_cache(self, store_with_memtable):
        store, mem = store_with_memtable
        assert store.get_macro_state() is None

    def test_put_overwrites_previous(self, store_with_memtable):
        store, mem = store_with_memtable
        store.put_macro_state({"scenario": 1, "vix": 22.0})
        store.put_macro_state({"scenario": 4, "vix": 42.0})
        result = store.get_macro_state()
        assert result["state"]["scenario"] == 4
        assert result["state"]["vix"] == 42.0

    def test_get_handles_corrupt_data(self, store_with_memtable):
        store, mem = store_with_memtable
        mem.items["__macro_latest"] = {
            "symbol": "__macro_latest",
            "last_alert_ts": 12345,
            "data": "{not valid json",
        }
        assert store.get_macro_state() is None

    def test_get_handles_non_string_data(self, store_with_memtable):
        store, mem = store_with_memtable
        mem.items["__macro_latest"] = {
            "symbol": "__macro_latest",
            "last_alert_ts": 12345,
            "data": 12345,  # not a string
        }
        assert store.get_macro_state() is None

    def test_chinese_strings_preserved(self, store_with_memtable):
        store, mem = store_with_memtable
        state = {"scenario": 5, "action": "分批减仓 30%，高估值个股优先"}
        store.put_macro_state(state)
        result = store.get_macro_state()
        assert "减仓" in result["state"]["action"]


class TestMacroKeyDoesntCollideWithWatchlist:
    """Make sure __macro_latest 不会被当成普通 advice 记录污染统计"""
    def test_count_advice_today_ignores_macro_key(self, store_with_memtable):
        import datetime
        import time
        store, mem = store_with_memtable
        # 放 1 个真 advice 记录 + 1 个 macro
        today = datetime.datetime.utcfromtimestamp(int(time.time())).strftime("%Y-%m-%d")
        mem.items["AAPL#advice"] = {
            "symbol": "AAPL#advice",
            "last_alert_ts": int(time.time()),
        }
        store.put_macro_state({"scenario": 0})
        # count_advice_today 只数 #advice 后缀的
        assert store.count_advice_today(today) == 1
