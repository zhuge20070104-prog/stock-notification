"""Test that src/fetcher.py correctly extracts analyst fields from yfinance info dict."""
import types

import pandas as pd
import pytest

from src import fetcher


class FakeFastInfo:
    """Minimal fast_info mock."""
    def __init__(self, mapping):
        self._m = mapping
    def __getitem__(self, k):
        if k not in self._m:
            raise KeyError(k)
        return self._m[k]
    def __getattr__(self, k):
        if k.startswith("_"):
            raise AttributeError(k)
        return self._m.get(k)


class FakeTicker:
    def __init__(self, info_dict, fast_info_dict=None, price=100.0):
        self._info = info_dict
        self._fast = fast_info_dict or {"last_price": price, "previous_close": price}
        self.ticker = "TEST"
    @property
    def fast_info(self):
        return FakeFastInfo(self._fast)
    @property
    def info(self):
        return self._info
    def history(self, period="1d"):
        return pd.DataFrame({"Close": [100.0]})


@pytest.fixture
def patched_yfinance(monkeypatch):
    """Replace yf.Ticker so get_quote() builds a Quote from our fixture data."""
    calls = {}
    def _patch(info_dict, fast_info_dict=None, price=100.0):
        def _Ticker(symbol):
            calls["last_symbol"] = symbol
            return FakeTicker(info_dict, fast_info_dict, price)
        monkeypatch.setattr(fetcher.yf, "Ticker", _Ticker)
        return calls
    return _patch


class TestAnalystExtraction:
    def test_all_analyst_fields_populated(self, patched_yfinance):
        info = {
            "shortName": "Oracle",
            "currency": "USD",
            "trailingPE": 35.5,
            "forwardPE": 28.0,
            "marketCap": 500_000_000_000,
            "averageVolume": 22_000_000,
            "fiftyTwoWeekHigh": 235.0,
            "fiftyTwoWeekLow": 165.0,
            "targetMeanPrice": 210.0,
            "targetHighPrice": 250.0,
            "targetLowPrice": 170.0,
            "recommendationKey": "buy",
            "recommendationMean": 2.1,
            "numberOfAnalystOpinions": 37,
            "forwardEps": 5.20,
        }
        patched_yfinance(info, price=191.35)
        q = fetcher.get_quote("ORCL")
        assert q.target_mean_price == 210.0
        assert q.target_high_price == 250.0
        assert q.target_low_price == 170.0
        assert q.recommendation_key == "buy"
        assert q.recommendation_mean == 2.1
        assert q.num_analyst_opinions == 37
        assert q.forward_eps == 5.20
        assert q.forward_pe == 28.0

    def test_missing_analyst_fields_are_none(self, patched_yfinance):
        info = {"shortName": "Test", "currency": "USD"}
        patched_yfinance(info)
        q = fetcher.get_quote("TEST")
        assert q.target_mean_price is None
        assert q.recommendation_key is None
        assert q.num_analyst_opinions is None
        assert q.forward_eps is None

    def test_recommendation_key_empty_string_treated_as_none(self, patched_yfinance):
        info = {"shortName": "Test", "currency": "USD", "recommendationKey": ""}
        patched_yfinance(info)
        q = fetcher.get_quote("TEST")
        assert q.recommendation_key is None

    def test_num_analysts_string_value_handled(self, patched_yfinance):
        """yfinance has been known to return strings for some int fields."""
        info = {
            "shortName": "Test", "currency": "USD",
            "numberOfAnalystOpinions": "abc",  # garbage
        }
        patched_yfinance(info)
        q = fetcher.get_quote("TEST")
        assert q.num_analyst_opinions is None


class TestSlimInfo:
    def test_slim_keeps_analyst_fields(self):
        info = {
            "shortName": "Test",
            "currency": "USD",
            "targetMeanPrice": 200.0,
            "recommendationKey": "buy",
            "forwardEps": 5.0,
            "extra_garbage_field": "should be dropped",
        }
        slim = fetcher._slim_info(info)
        assert "targetMeanPrice" in slim
        assert "recommendationKey" in slim
        assert "forwardEps" in slim
        assert "extra_garbage_field" not in slim
