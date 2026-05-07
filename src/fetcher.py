import time
from dataclasses import dataclass, asdict
from typing import Callable, Dict, List, Optional

import yfinance as yf


@dataclass
class Quote:
    symbol: str
    price: float
    name: str
    currency: str
    previous_close: Optional[float] = None
    day_change_pct: Optional[float] = None
    day_high: Optional[float] = None
    day_low: Optional[float] = None
    volume: Optional[float] = None
    pre_market_price: Optional[float] = None
    post_market_price: Optional[float] = None
    pe_ratio: Optional[float] = None
    forward_pe: Optional[float] = None
    market_cap: Optional[float] = None
    avg_volume: Optional[float] = None
    week52_high: Optional[float] = None
    week52_low: Optional[float] = None
    dividend_yield: Optional[float] = None
    beta: Optional[float] = None
    williams_r: Optional[float] = None
    williams_signal: Optional[str] = None
    macd_hist: Optional[float] = None
    macd_signal: Optional[str] = None
    kst_value: Optional[float] = None
    kst_signal: Optional[str] = None

    def to_dict(self) -> Dict:
        return asdict(self)


def _retry(fn: Callable, attempts: int = 3, base: float = 0.4):
    last: Optional[BaseException] = None
    for i in range(attempts):
        try:
            return fn()
        except Exception as e:
            last = e
            if i < attempts - 1:
                time.sleep(base * (2 ** i))
    raise last  # type: ignore[misc]


def _f(v) -> Optional[float]:
    try:
        if v is None:
            return None
        f = float(v)
        if f != f:  # NaN
            return None
        return f
    except (TypeError, ValueError):
        return None


def _fast_get(fi, key: str):
    try:
        return fi[key] if hasattr(fi, "__getitem__") else getattr(fi, key, None)
    except (KeyError, TypeError, AttributeError):
        return None


def _last_price(t: yf.Ticker) -> float:
    fi = t.fast_info
    for key in ("last_price", "lastPrice", "regularMarketPrice"):
        v = _f(_fast_get(fi, key))
        if v is not None:
            return v
    hist = t.history(period="1d")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    raise RuntimeError(f"no price available for {t.ticker}")


def _info(t: yf.Ticker) -> Dict:
    """yfinance info 偶尔抛或返 None；外面 retry 再兜一层。"""
    try:
        return t.info or {}
    except Exception:
        return {}


_INFO_FIELDS_TO_CACHE = (
    "trailingPE", "forwardPE", "marketCap", "averageVolume",
    "fiftyTwoWeekHigh", "fiftyTwoWeekLow",
    "dividendYield", "beta",
    "shortName", "longName", "currency",
)


def _slim_info(info: Dict) -> Dict:
    return {k: info.get(k) for k in _INFO_FIELDS_TO_CACHE if info.get(k) is not None}


def get_quote(symbol: str, info_provider: Optional[Callable[[str], Dict]] = None) -> Quote:
    """Fetch a single quote.

    `info_provider(symbol) -> dict` 可选；如果提供，优先用它（便于注入缓存层）。
    """
    sym = symbol.upper()
    t = yf.Ticker(sym)
    fi = t.fast_info

    price = _retry(lambda: _last_price(t))
    prev = _f(_fast_get(fi, "previous_close")) or _f(_fast_get(fi, "previousClose"))
    day_high = _f(_fast_get(fi, "day_high")) or _f(_fast_get(fi, "dayHigh"))
    day_low = _f(_fast_get(fi, "day_low")) or _f(_fast_get(fi, "dayLow"))
    volume = _f(_fast_get(fi, "last_volume")) or _f(_fast_get(fi, "lastVolume"))
    currency = _fast_get(fi, "currency") or "USD"

    info = info_provider(sym) if info_provider else _retry(lambda: _slim_info(_info(t)), attempts=2)

    name = info.get("shortName") or info.get("longName") or sym
    change_pct = None
    if prev and prev > 0 and price is not None:
        change_pct = round((price - prev) / prev * 100.0, 2)

    div_y = _f(info.get("dividendYield"))
    if div_y is not None and div_y > 1:
        # yfinance 偶尔把股息率以百分点形式返回（如 2.5 = 2.5%），偶尔小数（0.025）。
        # >1 视为百分点形式，原样保留；<=1 视为小数，乘 100。
        pass
    elif div_y is not None:
        div_y = round(div_y * 100.0, 2)

    return Quote(
        symbol=sym,
        price=price,
        name=name,
        currency=currency,
        previous_close=prev,
        day_change_pct=change_pct,
        day_high=day_high,
        day_low=day_low,
        volume=volume,
        pre_market_price=_f(info.get("preMarketPrice")),
        post_market_price=_f(info.get("postMarketPrice")),
        pe_ratio=_f(info.get("trailingPE")),
        forward_pe=_f(info.get("forwardPE")),
        market_cap=_f(info.get("marketCap")),
        avg_volume=_f(info.get("averageVolume")),
        week52_high=_f(info.get("fiftyTwoWeekHigh")),
        week52_low=_f(info.get("fiftyTwoWeekLow")),
        dividend_yield=div_y,
        beta=_f(info.get("beta")),
    )


def get_quotes(symbols: List[str], info_provider: Optional[Callable[[str], Dict]] = None) -> List[Quote]:
    out: List[Quote] = []
    for s in symbols:
        try:
            out.append(get_quote(s, info_provider=info_provider))
        except Exception as e:
            print(f"[warn] {s}: {e}")
    return out


def fetch_info(symbol: str) -> Dict:
    """Fetch slim info (cacheable subset) for one symbol — used by the metrics cache."""
    sym = symbol.upper()
    t = yf.Ticker(sym)
    return _retry(lambda: _slim_info(_info(t)), attempts=2)


def batch_history(symbols: List[str], period: str = "3mo") -> Dict:
    """One yf.download call for many symbols. Returns {SYMBOL: DataFrame}.

    Used to compute technical indicators for symbols that just triggered an
    alert — single batched HTTP request, regardless of how many alerts.
    """
    if not symbols:
        return {}
    syms = [s.upper() for s in symbols]
    df = _retry(lambda: yf.download(
        tickers=" ".join(syms),
        period=period,
        interval="1d",
        group_by="ticker",
        progress=False,
        threads=True,
        auto_adjust=False,
    ), attempts=2)
    out: Dict = {}
    if df is None:
        return out
    if len(syms) == 1:
        out[syms[0]] = df
        return out
    for s in syms:
        try:
            sub = df[s]
            if sub is not None and not sub.empty:
                out[s] = sub
        except (KeyError, AttributeError):
            continue
    return out
