"""Test the prompt builder in src/advisor.py — verify macro and analyst sections
are included when data is present, and gracefully omitted when absent."""
from dataclasses import dataclass
from typing import Optional

import pandas as pd
import pytest

from src import advisor
from src.macro import MacroState


@dataclass
class FakeQuote:
    symbol: str = "ORCL"
    name: str = "Oracle Corp."
    price: float = 191.35
    currency: str = "USD"
    day_change_pct: Optional[float] = -0.83
    williams_r: Optional[float] = 39.1
    williams_signal: Optional[str] = "中性"
    macd_hist: Optional[float] = 0.3
    macd_signal: Optional[str] = "死叉看跌"
    kst_signal: Optional[str] = "看涨"
    volume: Optional[float] = 25_000_000
    avg_volume: Optional[float] = 22_000_000
    week52_high: Optional[float] = 235.0
    week52_low: Optional[float] = 165.0
    # analyst
    target_mean_price: Optional[float] = None
    target_high_price: Optional[float] = None
    target_low_price: Optional[float] = None
    recommendation_key: Optional[str] = None
    recommendation_mean: Optional[float] = None
    num_analyst_opinions: Optional[int] = None
    forward_eps: Optional[float] = None
    forward_pe: Optional[float] = None


def _closes():
    return pd.Series([186.4, 189.1, 192.0, 192.95, 191.35])


def _mas():
    return {"MA5": 191.8, "MA20": 182.0, "MA60": 161.1, "MA120": 172.7, "MA250": 207.9}


class TestPromptBaseline:
    def test_base_prompt_has_required_blocks(self):
        p = advisor._build_prompt(FakeQuote(), _mas(), _closes(), [], "short", "")
        assert "[标的] ORCL Oracle Corp." in p
        assert "[趋势]" in p
        assert "[动量]" in p
        assert "[输出 schema]" in p

    def test_no_macro_no_analyst_omits_those_blocks(self):
        p = advisor._build_prompt(FakeQuote(), _mas(), _closes(), [], "short", "")
        assert "[宏观背景]" not in p
        assert "[分析师共识 / 前瞻]" not in p


class TestPromptWithMacro:
    def test_includes_macro_block_when_state_present(self):
        macro = MacroState(
            scenario=2,
            scenario_name="场景二 — 恐惧回调",
            action="第一笔 30% 加仓",
            vix=27.5,
            vix_status="恐慌",
            spy_drop_pct=-8.2,
        )
        p = advisor._build_prompt(FakeQuote(), _mas(), _closes(), [], "short", "", macro=macro)
        assert "[宏观背景]" in p
        assert "场景二" in p
        assert "27.5" in p  # VIX
        assert "+30%" not in p  # 不应有这种格式
        assert "30% 加仓" in p  # 应有 action 内容

    def test_scenario_0_does_not_inject_macro(self):
        """场景 0（未触发）不污染 prompt"""
        macro = MacroState(scenario=0, scenario_name="未触发")
        p = advisor._build_prompt(FakeQuote(), _mas(), _closes(), [], "short", "", macro=macro)
        assert "[宏观背景]" not in p


class TestPromptWithAnalyst:
    def test_includes_target_price(self):
        q = FakeQuote(
            target_mean_price=210.0,
            target_high_price=250.0,
            target_low_price=170.0,
        )
        p = advisor._build_prompt(q, _mas(), _closes(), [], "short", "")
        assert "[分析师共识" in p
        assert "210" in p
        assert "[$170" in p

    def test_includes_recommendation(self):
        q = FakeQuote(
            recommendation_key="buy",
            recommendation_mean=2.1,
            num_analyst_opinions=37,
        )
        p = advisor._build_prompt(q, _mas(), _closes(), [], "short", "")
        assert "评级: buy" in p
        assert "37 位分析师" in p

    def test_includes_forward_eps(self):
        q = FakeQuote(forward_eps=5.20, forward_pe=22.5)
        p = advisor._build_prompt(q, _mas(), _closes(), [], "short", "")
        assert "Forward EPS: 5.20" in p
        assert "Forward PE: 22.5" in p

    def test_partial_analyst_data_still_includes_block(self):
        q = FakeQuote(target_mean_price=210.0)
        p = advisor._build_prompt(q, _mas(), _closes(), [], "short", "")
        assert "[分析师共识" in p
        assert "210" in p


class TestPromptOrder:
    def test_macro_appears_before_trend(self):
        macro = MacroState(scenario=2, scenario_name="场景二", action="加仓", vix=27.0)
        p = advisor._build_prompt(FakeQuote(), _mas(), _closes(), [], "short", "", macro=macro)
        idx_macro = p.index("[宏观背景]")
        idx_trend = p.index("[趋势]")
        assert idx_macro < idx_trend

    def test_analyst_appears_before_trend(self):
        q = FakeQuote(target_mean_price=210.0)
        p = advisor._build_prompt(q, _mas(), _closes(), [], "short", "")
        idx_analyst = p.index("[分析师共识")
        idx_trend = p.index("[趋势]")
        assert idx_analyst < idx_trend


class TestShouldPush:
    def test_none_advice_silent(self):
        assert advisor.should_push(None) is False

    def test_hold_silent_by_default(self, monkeypatch):
        monkeypatch.delenv("ALWAYS_PUSH_WATCHLIST", raising=False)
        adv = advisor.Advice(action="hold", confidence=0.99)
        assert advisor.should_push(adv) is False

    def test_buy_low_confidence_silent(self, monkeypatch):
        monkeypatch.delenv("ALWAYS_PUSH_WATCHLIST", raising=False)
        monkeypatch.setenv("ADVISOR_PUSH_MIN_CONFIDENCE", "0.55")
        adv = advisor.Advice(action="buy", confidence=0.4)
        assert advisor.should_push(adv) is False

    def test_buy_high_confidence_pushes(self, monkeypatch):
        monkeypatch.delenv("ALWAYS_PUSH_WATCHLIST", raising=False)
        monkeypatch.setenv("ADVISOR_PUSH_MIN_CONFIDENCE", "0.55")
        adv = advisor.Advice(action="buy", confidence=0.7)
        assert advisor.should_push(adv) is True

    def test_always_push_watchlist_overrides_hold(self, monkeypatch):
        monkeypatch.setenv("ALWAYS_PUSH_WATCHLIST", "1")
        adv = advisor.Advice(action="hold", confidence=0.3)
        assert advisor.should_push(adv, source="watchlist") is True

    def test_always_push_watchlist_does_not_affect_movers(self, monkeypatch):
        """Mover (off-watchlist) 始终走置信度门槛"""
        monkeypatch.setenv("ALWAYS_PUSH_WATCHLIST", "1")
        monkeypatch.setenv("ADVISOR_PUSH_MIN_CONFIDENCE", "0.55")
        adv = advisor.Advice(action="hold", confidence=0.99)
        assert advisor.should_push(adv, source="mover") is False
        adv2 = advisor.Advice(action="buy", confidence=0.6)
        assert advisor.should_push(adv2, source="mover") is True


class TestSanityChecks:
    def test_sanity_ok_normal_legs(self):
        adv = advisor.Advice(
            action="buy", confidence=0.7,
            buy_legs=[{"price": 95, "shares_pct": 100}],
        )
        assert advisor._sanity_ok(adv, 100.0) is True

    def test_sanity_reject_far_leg(self):
        adv = advisor.Advice(
            action="buy", confidence=0.7,
            buy_legs=[{"price": 200, "shares_pct": 100}],
        )
        assert advisor._sanity_ok(adv, 100.0) is False


class TestParsing:
    def test_parse_valid_json(self):
        s = '{"action":"buy","confidence":0.7,"buy_legs":[],"sell_legs":[],"reference_ma":"MA20","rationale":"x","risk":"y"}'
        assert advisor._parse(s) is not None

    def test_parse_fenced_json(self):
        s = '```json\n{"action":"buy","confidence":0.7}\n```'
        assert advisor._parse(s) is not None

    def test_parse_invalid_action_rejected(self):
        s = '{"action":"YOLO","confidence":0.9}'
        assert advisor._parse(s) is None

    def test_clean_legs_filters_invalid(self):
        legs = [
            {"price": 91.0, "shares_pct": 50},
            {"price": "bad", "shares_pct": 25},
            {"price": -1, "shares_pct": 25},
            "not a dict",
        ]
        cleaned = advisor._clean_legs(legs)
        assert len(cleaned) == 1
        assert cleaned[0]["price"] == 91.0
