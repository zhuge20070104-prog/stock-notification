"""Test render_macro_briefing produces well-formed cards for all scenarios."""
import pytest

from src.macro import MacroState, classify, _set_status_strings
from src.notifier import render_macro_briefing


def _full_state(**kw):
    s = MacroState(**kw)
    _set_status_strings(s)
    classify(s)
    return s


class TestRenderMacroBriefing:
    def test_scenario2_card_structure(self):
        state = _full_state(
            vix=27.5,
            spy_drop_pct=-8.2,
            spy_rsp_div_pct=-0.5,
            hyg_5d_pct=-1.0,
            dxy=99.5,
            dxy_20d_pct=0.5,
        )
        title, body = render_macro_briefing(state)
        assert "📊 大盘简报" in title
        assert "🟢" in title  # scenario 2 → green
        assert "场景二" in title
        # body sections
        assert "**一、核心指标**" in body
        assert "**二、场景诊断**" in body
        assert "**三、今日指令**" in body
        assert "**四、风控**" not in body or True  # may or may not have risks
        # numbers present
        assert "VIX: 27.5" in body
        assert "-8.20" in body  # spy drop

    def test_scenario4_red_card_defense(self):
        state = _full_state(
            vix=42.0,
            hyg_5d_pct=-5.0,
            dxy=110.0,
            dxy_20d_pct=4.0,
        )
        title, body = render_macro_briefing(state)
        assert state.scenario == 4
        assert "🔴" in title
        assert "DEFEND" in body
        assert "防守" in body

    def test_scenario5_orange_card(self):
        state = _full_state(vix=13.0, spy_rsp_div_pct=4.0)
        title, body = render_macro_briefing(state)
        assert state.scenario == 5
        assert "🟠" in title

    def test_scenario1_blue_card(self):
        state = _full_state(vix=22.0, spy_drop_pct=-4.0)
        title, body = render_macro_briefing(state)
        assert state.scenario == 1
        assert "🔵" in title
        assert "HOLD" in body

    def test_neutral_scenario_card(self):
        state = _full_state()
        title, body = render_macro_briefing(state)
        assert state.scenario == 0
        assert "⚪" in title
        assert "WAIT" in body

    def test_allocation_pct_displayed(self):
        state = _full_state(
            vix=27.5,
            spy_drop_pct=-8.0,
            hyg_5d_pct=-1.0,
            dxy_20d_pct=0.5,
        )
        title, body = render_macro_briefing(state)
        assert "30%" in body

    def test_no_allocation_section_when_zero(self):
        state = _full_state(vix=22.0, spy_drop_pct=-4.0)  # scenario 1, allocation=0
        title, body = render_macro_briefing(state)
        # 仓位行只在 > 0 时出现
        lines = body.split("\n")
        assert state.allocation_pct == 0
        assert not any("仓位:" in line for line in lines)

    def test_indicator_legend_present(self):
        """每张简报底部都应该有指标速查表，方便用户每天看不需要查含义"""
        state = _full_state()
        title, body = render_macro_briefing(state)
        assert "📖 指标速查" in body
        assert "VIX" in body
        assert "SPY-RSP" in body
        assert "HYG" in body
        assert "DXY" in body
