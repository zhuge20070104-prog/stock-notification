from dataclasses import dataclass
from typing import List

import yfinance as yf


@dataclass
class Quote:
    symbol: str
    price: float
    name: str
    currency: str


def _last_price(ticker: yf.Ticker) -> float:
    fi = ticker.fast_info
    for key in ("last_price", "lastPrice", "regularMarketPrice"):
        try:
            v = fi[key] if hasattr(fi, "__getitem__") else getattr(fi, key, None)
        except (KeyError, TypeError):
            v = None
        if v is not None:
            return float(v)
    hist = ticker.history(period="1d")
    if not hist.empty:
        return float(hist["Close"].iloc[-1])
    raise RuntimeError(f"no price available for {ticker.ticker}")


def _name(ticker: yf.Ticker, symbol: str) -> str:
    try:
        info = ticker.info or {}
        return info.get("shortName") or info.get("longName") or symbol
    except Exception:
        return symbol


def _currency(ticker: yf.Ticker) -> str:
    fi = ticker.fast_info
    try:
        v = fi["currency"] if hasattr(fi, "__getitem__") else getattr(fi, "currency", None)
        return v or "USD"
    except Exception:
        return "USD"


def get_quote(symbol: str) -> Quote:
    t = yf.Ticker(symbol)
    return Quote(
        symbol=symbol.upper(),
        price=_last_price(t),
        name=_name(t, symbol.upper()),
        currency=_currency(t),
    )


def get_quotes(symbols: List[str]) -> List[Quote]:
    return [get_quote(s) for s in symbols]
