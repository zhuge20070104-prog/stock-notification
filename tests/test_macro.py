"""Unit tests for src/macro.py 5-scenario classifier.

Each test builds a MacroState with the indicators that should trigger a
specific scenario, calls classify(), and asserts the scenario id / direction /
allocation_pct match PLAN2.md.
"""
import pandas as pd
import pytest

from src.macro import (
    MacroState,
    _set_status_strings,
    classify,
    compute_macro_state,
)


def _state(**kw):
    s = MacroState(**kw)
    _set_status_strings(s)
    return s


# ── scenario 1: 正常回调 ──
class TestScenario1:
    def test_typical_normal_pullback(self):
        s = _state(vix=22.0, spy_drop_pct=-4.0)
        classify(s)
        assert s.scenario == 1
        assert s.direction == "hold"
        assert s.allocation_pct == 0

    def test_vix_at_lower_bound_18(self):
        s = _state(vix=18.0, spy_drop_pct=-3.0)
        classify(s)
        assert s.scenario == 1

    def test_vix_at_upper_bound_25(self):
        s = _state(vix=25.0, spy_drop_pct=-5.0)
        classify(s)
        assert s.scenario == 1

    def test_vix_below_18_drops_to_neutral(self):
        s = _state(vix=16.0, spy_drop_pct=-4.0)
        classify(s)
        assert s.scenario == 0  # not normal because VIX < 18


# ── scenario 2: 恐惧回调 ──
class TestScenario2:
    def test_typical_fear_pullback(self):
        s = _state(
            vix=27.0,
            spy_drop_pct=-8.0,
            hyg_5d_pct=-1.0,
            dxy_20d_pct=1.0,
        )
        classify(s)
        assert s.scenario == 2
        assert s.direction == "buy"
        assert s.allocation_pct == 30

    def test_at_vix_30_upper_bound(self):
        s = _state(vix=30.0, spy_drop_pct=-9.0, hyg_5d_pct=-1.0, dxy_20d_pct=0.5)
        classify(s)
        assert s.scenario == 2

    def test_credit_breakdown_blocks_scenario2(self):
        """HYG 破位时不进 2 而是中性（除非升级 4）"""
        s = _state(
            vix=27.0,
            spy_drop_pct=-8.0,
            hyg_5d_pct=-4.0,  # 破位
            dxy_20d_pct=0.5,
        )
        classify(s)
        assert s.scenario != 2  # 安全确认失败

    def test_dxy_spike_blocks_scenario2(self):
        s = _state(
            vix=27.0,
            spy_drop_pct=-8.0,
            hyg_5d_pct=-1.0,
            dxy_20d_pct=4.0,  # 暴涨
        )
        classify(s)
        assert s.scenario != 2

    def test_drop_too_shallow_not_scenario2(self):
        s = _state(vix=27.0, spy_drop_pct=-5.0, hyg_5d_pct=-1.0, dxy_20d_pct=0.5)
        classify(s)
        assert s.scenario != 2


# ── scenario 3: 极端恐慌 ──
class TestScenario3:
    def test_typical_extreme_panic(self):
        s = _state(vix=38.0, spy_drop_pct=-15.0)
        classify(s)
        assert s.scenario == 3
        assert s.direction == "buy"
        assert s.allocation_pct == 30

    def test_vix_just_above_35(self):
        s = _state(vix=35.5, spy_drop_pct=-12.0)
        classify(s)
        assert s.scenario == 3

    def test_hyg_breakdown_keeps_scenario3_with_warning(self):
        """VIX>35 but HYG also broke — should still be 3 (unless DXY also spikes → 4)"""
        s = _state(vix=38.0, spy_drop_pct=-15.0, hyg_5d_pct=-4.0)
        classify(s)
        assert s.scenario == 3
        assert any("HYG 已破位" in r for r in s.risks)


# ── scenario 4: 系统性风险 ──
class TestScenario4:
    def test_full_systemic_signal(self):
        s = _state(
            vix=38.0,
            hyg_5d_pct=-5.0,
            dxy_20d_pct=4.0,
        )
        classify(s)
        assert s.scenario == 4
        assert s.direction == "defend"
        assert s.allocation_pct == 0
        assert "防守" in s.action

    def test_high_vix_alone_does_not_trigger_scenario4(self):
        s = _state(vix=40.0, hyg_5d_pct=-1.0, dxy_20d_pct=1.0)
        classify(s)
        assert s.scenario != 4  # → 3 instead


# ── scenario 5: 过度贪婪 ──
class TestScenario5:
    def test_typical_greed(self):
        s = _state(vix=13.0, spy_rsp_div_pct=4.0)
        classify(s)
        assert s.scenario == 5
        assert s.direction == "sell"
        assert s.allocation_pct == 30

    def test_low_vix_no_divergence_drops_to_neutral(self):
        s = _state(vix=13.0, spy_rsp_div_pct=0.5)
        classify(s)
        assert s.scenario == 0  # 没集中度恶化

    def test_divergence_with_high_vix_not_scenario5(self):
        s = _state(vix=20.0, spy_rsp_div_pct=5.0)
        classify(s)
        assert s.scenario != 5  # VIX 不够低


# ── scenario 0: 中性观望 ──
class TestScenarioNeutral:
    def test_all_none(self):
        s = _state()
        classify(s)
        assert s.scenario == 0
        assert s.direction == "wait"

    def test_partial_data_doesnt_crash(self):
        s = _state(vix=20.0)
        classify(s)
        # 缺 drop_pct，无法判 scenario 1
        assert s.scenario == 0


# ── priority test: 严重场景优先 ──
class TestPriority:
    def test_scenario4_beats_scenario3(self):
        # VIX>35 alone is 3; add HYG breakdown + DXY spike → should become 4
        s = _state(vix=40.0, hyg_5d_pct=-5.0, dxy_20d_pct=4.0)
        classify(s)
        assert s.scenario == 4


# ── _set_status_strings ──
class TestStatusStrings:
    def test_vix_status_calm(self):
        s = MacroState(vix=12.0)
        _set_status_strings(s)
        assert "自满" in s.vix_status

    def test_vix_status_panic(self):
        s = MacroState(vix=28.0)
        _set_status_strings(s)
        assert s.vix_status == "恐慌"

    def test_breadth_concentration(self):
        s = MacroState(spy_rsp_div_pct=4.5)
        _set_status_strings(s)
        assert "集中度恶化" in s.breadth_status

    def test_hyg_breakdown(self):
        s = MacroState(hyg_5d_pct=-4.0)
        _set_status_strings(s)
        assert "破位" in s.hyg_status


# ── compute_macro_state with injected fetcher ──
class TestComputeMacroState:
    def _make_fetcher(self, data):
        """data: dict mapping symbol → list[close prices]"""
        def fetch(symbol, period):
            prices = data.get(symbol)
            if prices is None:
                return None
            return pd.Series(prices, dtype=float)
        return fetch

    def test_full_pipeline_scenario1(self):
        # VIX 22, SPY -4% from high
        spy_closes = [105.0] * 220 + [104, 103, 102, 101.0, 100.8] * 1
        # ensure tail(252).max() is ~105, current 100.8 → drop ~-4%
        spy_full = [105.0] * 250 + [100.8]
        fetcher = self._make_fetcher({
            "^VIX": [22.0],
            "^GSPC": spy_full,
            "SPY": [100.0] * 30,
            "RSP": [100.0] * 30,
            "HYG": [80.0] * 60,
            "DX-Y.NYB": [100.0] * 30,
        })
        s = compute_macro_state(fetcher)
        assert s.vix == 22.0
        assert s.spy_drop_pct is not None
        assert s.scenario == 1

    def test_fetcher_failure_doesnt_crash(self):
        def broken_fetcher(symbol, period):
            return None
        s = compute_macro_state(broken_fetcher)
        # All indicators None, classifier falls into scenario 0
        assert s.scenario == 0
        assert s.vix is None


class TestLastRobust:
    """Regression: yfinance v0.2.50+ returns MultiIndex column DataFrames for
    yf.download(). If our _fetch_close_series leaks a DataFrame through,
    `_last()` used to crash with "truth value of a Series is ambiguous".
    These tests pin behaviour so the bug can't return."""
    def test_last_on_plain_series(self):
        from src.macro import _last
        s = pd.Series([1.0, 2.0, 3.5])
        assert _last(s) == 3.5

    def test_last_on_empty_series(self):
        from src.macro import _last
        assert _last(pd.Series([], dtype=float)) is None

    def test_last_on_one_col_dataframe(self):
        """Simulate the MultiIndex squeeze case — iloc[-1] returns a Series."""
        from src.macro import _last
        df = pd.DataFrame({"^VIX": [10.0, 12.5, 15.2]})
        # iloc[-1] of a DataFrame is a Series → _last must squeeze it
        assert _last(df) == 15.2

    def test_last_on_none(self):
        from src.macro import _last
        assert _last(None) is None

    def test_last_on_nan_tail(self):
        from src.macro import _last
        import numpy as np
        assert _last(pd.Series([1.0, float("nan")])) is None
