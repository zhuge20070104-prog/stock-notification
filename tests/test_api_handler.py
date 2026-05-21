"""Tests for backend/api_handler.py routing — particularly the new /macro endpoint
and the legacy /watchlist routes after v2 schema simplification."""
import json
import sys

import pytest


@pytest.fixture
def api_handler(monkeypatch):
    """Import api_handler with env vars set + store mocked."""
    monkeypatch.setenv("WATCHLIST_TABLE", "wl")
    monkeypatch.setenv("STATE_TABLE", "st")
    monkeypatch.setenv("METRICS_CACHE_TABLE", "mc")
    monkeypatch.delenv("API_KEY", raising=False)

    # backend/ is not a package; import flat module via path injection
    import os
    backend_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "backend")
    if backend_dir not in sys.path:
        sys.path.insert(0, backend_dir)
    if "api_handler" in sys.modules:
        del sys.modules["api_handler"]
    import api_handler
    return api_handler


def _event(method, path, body=None, qs=None, headers=None):
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": method}},
        "queryStringParameters": qs,
        "headers": headers or {},
        "body": json.dumps(body) if body else None,
    }


class TestMacroEndpoint:
    def test_macro_returns_null_when_uncached(self, api_handler, monkeypatch):
        monkeypatch.setattr(api_handler, "get_macro_state", lambda: None)
        resp = api_handler.handler(_event("GET", "/macro"), None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body == {"state": None, "updated_at": 0}

    def test_macro_returns_cached_state(self, api_handler, monkeypatch):
        cached = {
            "updated_at": 1700000000,
            "state": {
                "scenario": 2,
                "scenario_name": "场景二 — 恐惧回调",
                "vix": 27.5,
                "spy_drop_pct": -8.0,
                "direction": "buy",
                "allocation_pct": 30,
            },
        }
        monkeypatch.setattr(api_handler, "get_macro_state", lambda: cached)
        resp = api_handler.handler(_event("GET", "/macro"), None)
        assert resp["statusCode"] == 200
        body = json.loads(resp["body"])
        assert body["updated_at"] == 1700000000
        assert body["state"]["scenario"] == 2
        assert body["state"]["scenario_name"] == "场景二 — 恐惧回调"

    def test_macro_strips_api_prefix(self, api_handler, monkeypatch):
        """CloudFront 转发的路径是 /api/macro，Lambda 看到后剥前缀"""
        monkeypatch.setattr(api_handler, "get_macro_state", lambda: None)
        resp = api_handler.handler(_event("GET", "/api/macro"), None)
        assert resp["statusCode"] == 200


class TestWatchlistRoutes:
    def test_post_without_threshold_succeeds(self, api_handler, monkeypatch):
        """v2 不再要求 threshold/direction"""
        captured = {}
        def fake_upsert(sym, strategy_horizon="short", strategy_notes=None):
            captured["sym"] = sym
            captured["horizon"] = strategy_horizon
            captured["notes"] = strategy_notes
            return {"symbol": sym, "strategy_horizon": strategy_horizon}
        monkeypatch.setattr(api_handler, "upsert_watchlist", fake_upsert)

        body = {"symbol": "orcl", "strategy_horizon": "short", "strategy_notes": "短线减仓"}
        resp = api_handler.handler(_event("POST", "/watchlist", body=body), None)
        assert resp["statusCode"] == 200
        assert captured["sym"] == "ORCL"
        assert captured["horizon"] == "short"
        assert captured["notes"] == "短线减仓"

    def test_post_rejects_bad_horizon(self, api_handler, monkeypatch):
        monkeypatch.setattr(api_handler, "upsert_watchlist", lambda *a, **kw: {})
        body = {"symbol": "ORCL", "strategy_horizon": "yolo"}
        resp = api_handler.handler(_event("POST", "/watchlist", body=body), None)
        assert resp["statusCode"] == 400

    def test_post_requires_symbol(self, api_handler):
        resp = api_handler.handler(_event("POST", "/watchlist", body={}), None)
        assert resp["statusCode"] == 400

    def test_notes_truncated_to_200_chars(self, api_handler, monkeypatch):
        captured = {}
        def fake_upsert(sym, **kw):
            captured.update(kw)
            return {"symbol": sym}
        monkeypatch.setattr(api_handler, "upsert_watchlist", fake_upsert)
        body = {"symbol": "ORCL", "strategy_notes": "x" * 500}
        resp = api_handler.handler(_event("POST", "/watchlist", body=body), None)
        assert resp["statusCode"] == 200
        assert len(captured["strategy_notes"]) == 200


class TestRouting:
    def test_unknown_path_404(self, api_handler):
        resp = api_handler.handler(_event("GET", "/nonexistent"), None)
        assert resp["statusCode"] == 404

    def test_options_returns_200(self, api_handler):
        resp = api_handler.handler(_event("OPTIONS", "/watchlist"), None)
        assert resp["statusCode"] == 200


class TestAuth:
    def test_requires_api_key_when_set(self, api_handler, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret123")
        resp = api_handler.handler(_event("GET", "/macro", headers={}), None)
        assert resp["statusCode"] == 401

    def test_accepts_matching_api_key(self, api_handler, monkeypatch):
        monkeypatch.setenv("API_KEY", "secret123")
        monkeypatch.setattr(api_handler, "get_macro_state", lambda: None)
        resp = api_handler.handler(
            _event("GET", "/macro", headers={"x-api-key": "secret123"}),
            None
        )
        assert resp["statusCode"] == 200
