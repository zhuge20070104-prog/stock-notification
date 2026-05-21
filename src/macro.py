"""Macro market indicators + 5-scenario classifier (PLAN2.md).

Computes scenarios from yfinance data only:
  1. 正常回调 (normal pullback)
  2. 恐惧回调 (fear pullback) — 30/30/40 allocation
  3. 极端恐慌 (extreme panic) — contrarian buy
  4. 系统性风险 (systemic risk) — defense only
  5. 过度贪婪 (excessive greed) — reduce position
  0. 未触发 (neutral / wait)

F&G index (CNN) and AAII bearish % are deliberately omitted — they need
non-yfinance sources. Scenarios 1/2/4/5 are still decidable; scenario 3
will be flagged but with reduced confidence in the absence of F&G/AAII.
"""
import time
from dataclasses import asdict, dataclass, field
from typing import Callable, List, Optional

import pandas as pd
import yfinance as yf


@dataclass
class MacroState:
    timestamp_utc: int = 0
    # Raw indicators
    vix: Optional[float] = None
    spy_drop_pct: Optional[float] = None        # current vs 252d high (signed %, negative = down)
    spy_rsp_div_pct: Optional[float] = None     # 20d return diff (SPY - RSP); positive = SPY outpacing = concentration
    hyg_5d_pct: Optional[float] = None          # 5-day return of HYG (negative = stress)
    hyg_below_50ma_pct: Optional[float] = None  # HYG distance to 50d MA (negative = below)
    dxy: Optional[float] = None
    dxy_20d_pct: Optional[float] = None         # DXY 20d change
    fg_index: Optional[float] = None            # CNN Fear & Greed — not implemented this round
    aaii_bear_pct: Optional[float] = None       # AAII bearish % — not implemented this round
    # Status strings (Chinese)
    vix_status: str = "—"
    breadth_status: str = "—"
    hyg_status: str = "—"
    dxy_status: str = "—"
    # Scenario verdict
    scenario: int = 0
    scenario_name: str = "未判定"
    direction: str = "wait"             # buy / sell / hold / defend / wait
    allocation_pct: int = 0             # 0 if no action; 30/40 for partial buy/sell
    action: str = ""
    reasons: List[str] = field(default_factory=list)
    risks: List[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _last(s) -> Optional[float]:
    """Robust scalar extraction from a Series or 1-column DataFrame."""
    if s is None:
        return None
    try:
        if len(s) == 0:
            return None
    except TypeError:
        return None
    v = s.iloc[-1]
    # If v is itself a Series (e.g. when s is a 1-col DataFrame), squeeze to scalar
    if hasattr(v, "iloc"):
        if len(v) == 0:
            return None
        v = v.iloc[0]
    try:
        if pd.isna(v):
            return None
    except (TypeError, ValueError):
        pass
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _fetch_close_series(symbol: str, period: str = "1y") -> Optional[pd.Series]:
    """Use Ticker.history() (single-ticker API) — returns flat columns reliably.
    yfinance's yf.download() switched to MultiIndex columns by default in newer
    versions and breaks downstream pandas operations."""
    try:
        t = yf.Ticker(symbol)
        df = t.history(period=period, interval="1d", auto_adjust=False)
        if df is None or df.empty:
            return None
        # history() always returns flat columns; squeeze in case any plug returns DF.
        if "Close" not in df.columns:
            return None
        close = df["Close"]
        # Defensive: if somehow a DataFrame (1-col), squeeze to Series
        if hasattr(close, "columns"):
            if len(close.columns) == 0:
                return None
            close = close.iloc[:, 0]
        s = close.dropna()
        return s if len(s) > 0 else None
    except Exception:
        return None


def _fetch_vix(fetcher: Callable[[str, str], Optional[pd.Series]]) -> Optional[float]:
    return _last(fetcher("^VIX", "5d"))


def _fetch_spy_drop_pct(fetcher: Callable[[str, str], Optional[pd.Series]]) -> Optional[float]:
    s = fetcher("^GSPC", "1y")
    if s is None or len(s) < 30:
        return None
    high = float(s.tail(252).max())
    if high <= 0:
        return None
    current = float(s.iloc[-1])
    return round((current - high) / high * 100.0, 2)


def _fetch_spy_rsp_divergence(fetcher: Callable[[str, str], Optional[pd.Series]]) -> Optional[float]:
    spy = fetcher("SPY", "3mo")
    rsp = fetcher("RSP", "3mo")
    if spy is None or rsp is None or len(spy) < 21 or len(rsp) < 21:
        return None
    spy_ret = (float(spy.iloc[-1]) / float(spy.iloc[-21]) - 1) * 100
    rsp_ret = (float(rsp.iloc[-1]) / float(rsp.iloc[-21]) - 1) * 100
    return round(spy_ret - rsp_ret, 2)


def _fetch_hyg_state(fetcher: Callable[[str, str], Optional[pd.Series]]):
    s = fetcher("HYG", "6mo")
    if s is None or len(s) < 51:
        return None, None
    five_d = (float(s.iloc[-1]) / float(s.iloc[-6]) - 1) * 100
    ma50 = float(s.tail(50).mean())
    distance = (float(s.iloc[-1]) / ma50 - 1) * 100 if ma50 > 0 else 0.0
    return round(five_d, 2), round(distance, 2)


def _fetch_dxy_state(fetcher: Callable[[str, str], Optional[pd.Series]]):
    s = fetcher("DX-Y.NYB", "3mo")
    if s is None or len(s) < 21:
        return None, None
    current = float(s.iloc[-1])
    twenty_d_pct = (float(s.iloc[-1]) / float(s.iloc[-21]) - 1) * 100
    return round(current, 2), round(twenty_d_pct, 2)


def _set_status_strings(s: MacroState) -> None:
    # VIX
    if s.vix is None:
        s.vix_status = "—"
    elif s.vix < 15:
        s.vix_status = "极低（自满）"
    elif s.vix < 18:
        s.vix_status = "平静"
    elif s.vix < 25:
        s.vix_status = "升温"
    elif s.vix < 30:
        s.vix_status = "恐慌"
    elif s.vix < 35:
        s.vix_status = "高恐慌"
    else:
        s.vix_status = "极端恐慌"

    # SPY-RSP breadth
    if s.spy_rsp_div_pct is None:
        s.breadth_status = "—"
    elif s.spy_rsp_div_pct > 3:
        s.breadth_status = "集中度恶化（少数巨头拉升）"
    elif s.spy_rsp_div_pct > 1:
        s.breadth_status = "略微集中"
    elif s.spy_rsp_div_pct < -1:
        s.breadth_status = "广度健康"
    else:
        s.breadth_status = "同步"

    # HYG
    if s.hyg_5d_pct is None:
        s.hyg_status = "—"
    elif s.hyg_5d_pct < -3 or (s.hyg_below_50ma_pct is not None and s.hyg_below_50ma_pct < -2):
        s.hyg_status = "破位（系统性预警）"
    elif s.hyg_5d_pct < -1.5:
        s.hyg_status = "走弱"
    else:
        s.hyg_status = "稳定"

    # DXY
    if s.dxy is None:
        s.dxy_status = "—"
    elif s.dxy_20d_pct is not None and s.dxy_20d_pct > 3:
        s.dxy_status = "暴涨（流动性紧张）"
    elif s.dxy > 105:
        s.dxy_status = "偏强"
    else:
        s.dxy_status = "正常"


def classify(state: MacroState) -> None:
    """Set scenario / direction / allocation_pct / action / reasons in-place.

    Priority: 4 (systemic) > 3 (extreme panic) > 2 (fear pullback) > 5 (greed) > 1 (normal).
    """
    vix = state.vix
    drop = state.spy_drop_pct
    div = state.spy_rsp_div_pct
    hyg5 = state.hyg_5d_pct
    dxy20 = state.dxy_20d_pct

    # ── 场景四 ──
    if (vix is not None and vix > 30
            and hyg5 is not None and hyg5 < -3
            and dxy20 is not None and dxy20 > 3):
        state.scenario = 4
        state.scenario_name = "场景四 — 系统性风险"
        state.direction = "defend"
        state.allocation_pct = 0
        state.action = "防守！空仓或持有现金，禁止任何加仓/定投"
        state.reasons.append(f"VIX={vix:.1f} 持续高位")
        state.reasons.append(f"HYG 5日 {hyg5:+.2f}% 破位")
        state.reasons.append(f"DXY 20日 {dxy20:+.2f}% 失控")
        state.risks.append("信用市场冻结风险，等待企稳再恢复")
        return

    # ── 场景三 ──
    if vix is not None and vix > 35:
        state.scenario = 3
        state.scenario_name = "场景三 — 极端恐慌（逆向抄底）"
        state.direction = "buy"
        state.allocation_pct = 30
        state.action = "分段建仓，优先核心资产，必须保留部分现金"
        state.reasons.append(f"VIX={vix:.1f} > 35")
        if drop is not None:
            state.reasons.append(f"SPY 距高点 {drop:+.2f}%")
        state.risks.append("F&G 和 AAII 数据未集成，建议第一笔仅 30% 试探")
        if hyg5 is not None and hyg5 < -3:
            state.risks.append("HYG 已破位，警惕升级为场景四")
        return

    # ── 场景二 ──
    if vix is not None and 25 < vix <= 30 and drop is not None and -10 <= drop <= -7:
        safe = True
        if hyg5 is not None and hyg5 < -3:
            safe = False
        if dxy20 is not None and dxy20 > 3:
            safe = False
        if safe:
            state.scenario = 2
            state.scenario_name = "场景二 — 恐惧回调（分批加仓）"
            state.direction = "buy"
            state.allocation_pct = 30
            state.action = "第一笔 30% 加仓；VIX 至 30 补 30%；VIX 回落+RSP 改善 补 40%"
            state.reasons.append(f"VIX={vix:.1f} ∈ (25, 30]")
            state.reasons.append(f"SPY 距高点 {drop:+.2f}%")
            state.reasons.append("信用市场稳定")
            return

    # ── 场景五 ──
    if vix is not None and vix < 15 and div is not None and div > 3:
        state.scenario = 5
        state.scenario_name = "场景五 — 过度贪婪（考虑减仓）"
        state.direction = "sell"
        state.allocation_pct = 30
        state.action = "分批减仓 30%，高估值个股优先；现金留作未来机会"
        state.reasons.append(f"VIX={vix:.1f} < 15 长期低迷")
        state.reasons.append(f"SPY-RSP 背离 {div:+.2f}%（>3%）")
        state.risks.append("市场集中度恶化，少数巨头撑大盘")
        return

    # ── 场景一 ──
    if vix is not None and 18 <= vix <= 25 and drop is not None and -5 <= drop <= -3:
        state.scenario = 1
        state.scenario_name = "场景一 — 正常回调（保持定投）"
        state.direction = "hold"
        state.allocation_pct = 0
        state.action = "保持正常定投频率，不额外大额加仓"
        state.reasons.append(f"VIX={vix:.1f} ∈ [18, 25]")
        state.reasons.append(f"SPY 距高点 {drop:+.2f}%")
        return

    # ── 默认：未触发 ──
    state.scenario = 0
    state.scenario_name = "未触发场景（中性观望）"
    state.direction = "wait"
    state.allocation_pct = 0
    state.action = "无明确信号，按计划执行原有仓位策略"
    if vix is not None:
        state.reasons.append(f"VIX={vix:.1f}（{state.vix_status}）")
    if drop is not None:
        state.reasons.append(f"SPY 距高点 {drop:+.2f}%")


def compute_macro_state(
    fetcher: Optional[Callable[[str, str], Optional[pd.Series]]] = None,
) -> MacroState:
    """Fetch all macro indicators and classify scenario.

    `fetcher` is injectable for tests; defaults to a yfinance-backed function.
    Best-effort: any individual fetch failure → that indicator stays None.
    """
    fetcher = fetcher or _fetch_close_series
    state = MacroState(timestamp_utc=int(time.time()))
    try:
        state.vix = _fetch_vix(fetcher)
    except Exception as e:
        print(f"[macro] vix fetch failed: {e}")
    try:
        state.spy_drop_pct = _fetch_spy_drop_pct(fetcher)
    except Exception as e:
        print(f"[macro] spy fetch failed: {e}")
    try:
        state.spy_rsp_div_pct = _fetch_spy_rsp_divergence(fetcher)
    except Exception as e:
        print(f"[macro] spy/rsp fetch failed: {e}")
    try:
        state.hyg_5d_pct, state.hyg_below_50ma_pct = _fetch_hyg_state(fetcher)
    except Exception as e:
        print(f"[macro] hyg fetch failed: {e}")
    try:
        state.dxy, state.dxy_20d_pct = _fetch_dxy_state(fetcher)
    except Exception as e:
        print(f"[macro] dxy fetch failed: {e}")

    _set_status_strings(state)
    classify(state)
    return state
