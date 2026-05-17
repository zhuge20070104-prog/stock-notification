"""技术指标：Williams %R / MACD / KST。

纯 pandas，无 yfinance 依赖，方便单测。每个函数返回 (值, 信号字符串)。
信号字符串：'看涨' / '看跌' / '中性' / '金叉看涨' / '死叉看跌'。
"""
from typing import Optional, Tuple

import pandas as pd


def _last(s: pd.Series) -> Optional[float]:
    if s is None or len(s) == 0:
        return None
    v = s.iloc[-1]
    if pd.isna(v):
        return None
    return float(v)


def williams_r(
    highs: pd.Series, lows: pd.Series, closes: pd.Series, period: int = 14
) -> Tuple[Optional[float], Optional[str]]:
    if len(closes) < period:
        return None, None
    hh = highs.rolling(period).max()
    ll = lows.rolling(period).min()
    rng = hh - ll
    r = -100 * (hh - closes) / rng.where(rng > 0)
    val = _last(r)
    if val is None:
        return None, None
    if val < -80:
        sig = "超卖看涨"
    elif val > -20:
        sig = "超买看跌"
    else:
        sig = "中性"
    return val, sig


def macd(
    closes: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> Tuple[Optional[float], Optional[str]]:
    if len(closes) < slow + signal:
        return None, None
    ema_fast = closes.ewm(span=fast, adjust=False).mean()
    ema_slow = closes.ewm(span=slow, adjust=False).mean()
    macd_line = ema_fast - ema_slow
    sig_line = macd_line.ewm(span=signal, adjust=False).mean()
    hist = macd_line - sig_line
    h_now = _last(hist)
    h_prev = _last(hist.iloc[:-1]) if len(hist) >= 2 else None
    if h_now is None:
        return None, None
    if h_prev is not None and h_prev <= 0 < h_now:
        return h_now, "金叉看涨"
    if h_prev is not None and h_prev >= 0 > h_now:
        return h_now, "死叉看跌"
    if h_now > 0:
        return h_now, "看涨"
    if h_now < 0:
        return h_now, "看跌"
    return h_now, "中性"


def kst(closes: pd.Series) -> Tuple[Optional[float], Optional[str]]:
    """Pring KST: ROC(10/15/20/30) 的 SMA(10/10/10/15)，加权 1/2/3/4，signal = SMA9。"""
    if len(closes) < 30 + 15:
        return None, None

    def _roc_sma(period: int, sma_period: int) -> pd.Series:
        roc = (closes / closes.shift(period) - 1) * 100
        return roc.rolling(sma_period).mean()

    rcma1 = _roc_sma(10, 10)
    rcma2 = _roc_sma(15, 10)
    rcma3 = _roc_sma(20, 10)
    rcma4 = _roc_sma(30, 15)
    kst_line = 1 * rcma1 + 2 * rcma2 + 3 * rcma3 + 4 * rcma4
    sig_line = kst_line.rolling(9).mean()

    k_now = _last(kst_line)
    s_now = _last(sig_line)
    if k_now is None or s_now is None:
        return None, None
    k_prev = _last(kst_line.iloc[:-1]) if len(kst_line) >= 2 else None
    s_prev = _last(sig_line.iloc[:-1]) if len(sig_line) >= 2 else None

    diff = k_now - s_now
    if k_prev is not None and s_prev is not None:
        prev_diff = k_prev - s_prev
        if prev_diff <= 0 < diff:
            return diff, "金叉看涨"
        if prev_diff >= 0 > diff:
            return diff, "死叉看跌"
    if diff > 0:
        return diff, "看涨"
    if diff < 0:
        return diff, "看跌"
    return diff, "中性"


def moving_averages(
    closes: pd.Series, windows=(5, 20, 60, 120, 250)
) -> dict:
    """Return {"MA5": float, "MA20": float, ...}. None when window > available bars."""
    out: dict = {}
    if closes is None or len(closes) == 0:
        return out
    for w in windows:
        key = f"MA{w}"
        if len(closes) < w:
            out[key] = None
            continue
        v = _last(closes.rolling(w).mean())
        out[key] = v
    return out


def compute_signals(df: pd.DataFrame) -> dict:
    """df: yfinance 风格的 OHLC DataFrame（columns: Open/High/Low/Close...）。"""
    if df is None or df.empty or "Close" not in df.columns:
        return {}
    closes = df["Close"].dropna()
    highs = df["High"].dropna() if "High" in df.columns else closes
    lows = df["Low"].dropna() if "Low" in df.columns else closes

    out: dict = {}
    wr_val, wr_sig = williams_r(highs, lows, closes)
    if wr_sig is not None:
        out["williams_r"] = wr_val
        out["williams_signal"] = wr_sig
    macd_val, macd_sig = macd(closes)
    if macd_sig is not None:
        out["macd_hist"] = macd_val
        out["macd_signal"] = macd_sig
    kst_val, kst_sig = kst(closes)
    if kst_sig is not None:
        out["kst_value"] = kst_val
        out["kst_signal"] = kst_sig
    return out


def apply_signals(quote, df: pd.DataFrame) -> None:
    """Mutate quote in place with signals computed from df. Duck-typed; no Quote import."""
    sigs = compute_signals(df)
    for k, v in sigs.items():
        if hasattr(quote, k):
            setattr(quote, k, v)
