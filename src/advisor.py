"""LLM advisor: turns a Quote + OHLC history + recent news into a JSON
trading suggestion (action + split-entry/exit prices).

Provider: Qwen via DashScope's OpenAI-compatible endpoint
(https://dashscope.aliyuncs.com/compatible-mode/v1).

Designed to fail soft: any exception → return None; caller logs and
continues. Original alerts are never blocked.
"""
import json
import os
from dataclasses import asdict, dataclass, field
from typing import Dict, List, Optional

import pandas as pd
import requests

try:
    from .indicators import moving_averages
except ImportError:  # Lambda zip flattens src/* to root
    from indicators import moving_averages  # type: ignore[no-redef]


# 默认公共 DashScope endpoint。
# 私有 MaaS workspace 用户改 DASHSCOPE_BASE_URL（如
# https://ws-xxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1）。
_DEFAULT_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@dataclass
class Advice:
    action: str               # "buy" | "sell" | "hold"
    confidence: float         # 0.0–1.0
    buy_legs: List[Dict] = field(default_factory=list)   # [{"price":..,"shares_pct":..}]
    sell_legs: List[Dict] = field(default_factory=list)
    reference_ma: str = ""
    rationale: str = ""
    risk: str = ""
    raw: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def advisor_enabled() -> bool:
    if os.environ.get("ADVISOR_ENABLED", "1") in ("0", "false", "False"):
        return False
    return bool(os.environ.get("DASHSCOPE_API_KEY"))


def should_push(adv: Optional["Advice"], source: str = "watchlist") -> bool:
    """Push policy:
    - watchlist + ALWAYS_PUSH_WATCHLIST=1: 任何 Advice（含 hold/低置信）都推。用于"每天 10:00/18:00 固定简报"。
    - 否则: 仅当 action ∈ {buy, sell} 且 confidence ≥ ADVISOR_PUSH_MIN_CONFIDENCE 时推。
    """
    if adv is None:
        return False
    always = os.environ.get("ALWAYS_PUSH_WATCHLIST", "0") in ("1", "true", "True")
    if always and source == "watchlist":
        return True
    if adv.action not in ("buy", "sell"):
        return False
    floor = float(os.environ.get("ADVISOR_PUSH_MIN_CONFIDENCE", "0.55"))
    return adv.confidence >= floor


def advise(
    quote,
    df_3mo: Optional[pd.DataFrame],
    df_1y: Optional[pd.DataFrame],
    news: List[Dict],
    horizon: str = "short",
    notes: str = "",
    macro=None,
) -> Optional[Advice]:
    if not advisor_enabled():
        return None

    closes_3mo = _closes(df_3mo)
    closes_1y = _closes(df_1y) if df_1y is not None else closes_3mo
    short_ma = moving_averages(closes_3mo, windows=(5, 20, 60))
    long_ma = moving_averages(closes_1y, windows=(120, 250))
    mas = {**short_ma, **long_ma}

    prompt = _build_prompt(quote, mas, closes_3mo, news, horizon, notes, macro)
    try:
        raw = _call_qwen(prompt)
    except Exception as e:
        print(f"[warn] qwen call failed: {e}")
        return None

    parsed = _parse(raw)
    if parsed is None:
        print(f"[warn] advisor JSON parse failed: {raw[:200]}")
        return None

    adv = Advice(
        action=parsed.get("action", "hold"),
        confidence=float(parsed.get("confidence") or 0.0),
        buy_legs=_clean_legs(parsed.get("buy_legs")),
        sell_legs=_clean_legs(parsed.get("sell_legs")),
        reference_ma=str(parsed.get("reference_ma") or ""),
        rationale=str(parsed.get("rationale") or "")[:300],
        risk=str(parsed.get("risk") or "")[:200],
        raw=raw,
    )
    if not _sanity_ok(adv, quote.price):
        print(f"[warn] advisor sanity reject: {adv.action} legs deviate too far")
        return None
    return adv


def _closes(df: Optional[pd.DataFrame]) -> pd.Series:
    if df is None or df.empty or "Close" not in df.columns:
        return pd.Series(dtype=float)
    return df["Close"].dropna()


def _build_prompt(quote, mas: dict, closes: pd.Series, news: List[Dict],
                  horizon: str, notes: str, macro=None) -> str:
    def f(v):
        return "—" if v is None else f"{v:.2f}"

    recent = closes.tail(5).round(2).tolist() if len(closes) else []
    vs_ma20 = _pct_vs(quote.price, mas.get("MA20"))
    vs_ma60 = _pct_vs(quote.price, mas.get("MA60"))

    # ── 宏观背景段（规则引擎给的事实，仅作为上下文，不让 LLM 重新判断）──
    macro_block = ""
    if macro is not None and getattr(macro, "scenario", 0) > 0:
        macro_lines = [
            f"  当前场景: {macro.scenario_name}",
            f"  策略方向: {macro.action}",
        ]
        if macro.vix is not None:
            macro_lines.append(f"  VIX: {macro.vix:.1f}（{macro.vix_status}）")
        if macro.spy_drop_pct is not None:
            macro_lines.append(f"  SPY 距高点: {macro.spy_drop_pct:+.2f}%")
        if macro.spy_rsp_div_pct is not None:
            macro_lines.append(
                f"  SPY-RSP 背离: {macro.spy_rsp_div_pct:+.2f}%（{macro.breadth_status}）"
            )
        if macro.hyg_status and macro.hyg_status != "—":
            macro_lines.append(f"  HYG: {macro.hyg_status}")
        macro_block = "\n[宏观背景]\n" + "\n".join(macro_lines) + "\n"

    # ── 分析师共识段 ──
    analyst_lines = []
    if quote.target_mean_price is not None:
        diff_pct = (quote.target_mean_price - quote.price) / quote.price * 100
        line = f"  共识目标价: ${quote.target_mean_price:.2f}（距现价 {diff_pct:+.1f}%）"
        if quote.target_high_price and quote.target_low_price:
            line += f"  区间 [${quote.target_low_price:.2f} – ${quote.target_high_price:.2f}]"
        analyst_lines.append(line)
    if quote.recommendation_key:
        rec_line = f"  评级: {quote.recommendation_key}"
        if quote.recommendation_mean is not None:
            rec_line += f"（{quote.recommendation_mean:.2f}/5，越小越看多）"
        if quote.num_analyst_opinions:
            rec_line += f"  来自 {quote.num_analyst_opinions} 位分析师"
        analyst_lines.append(rec_line)
    if quote.forward_eps is not None:
        fwd_line = f"  Forward EPS: {quote.forward_eps:.2f}"
        if quote.forward_pe is not None:
            fwd_line += f"  Forward PE: {quote.forward_pe:.1f}"
        analyst_lines.append(fwd_line)
    analyst_block = ""
    if analyst_lines:
        analyst_block = "\n[分析师共识 / 前瞻]\n" + "\n".join(analyst_lines) + "\n"

    momentum_lines = []
    if quote.williams_r is not None:
        momentum_lines.append(
            f"  Williams%R(14): {quote.williams_r:.1f}  → {quote.williams_signal or '—'}"
        )
    if quote.macd_hist is not None:
        momentum_lines.append(
            f"  MACD: hist={quote.macd_hist:+.3f}  signal={quote.macd_signal or '—'}"
        )
    if quote.kst_signal:
        momentum_lines.append(f"  KST: {quote.kst_signal}")

    vol_line = ""
    if quote.volume and quote.avg_volume:
        ratio = quote.volume / quote.avg_volume
        vol_line = (
            f"\n[量价]\n  今日成交: {quote.volume/1e6:.2f}M  "
            f"均量: {quote.avg_volume/1e6:.2f}M  (相对 {ratio:+.1%})"
        )

    w52 = ""
    if quote.week52_low and quote.week52_high and quote.week52_high > quote.week52_low:
        pos = (quote.price - quote.week52_low) / (quote.week52_high - quote.week52_low) * 100
        w52 = f"\n[位置]\n  52周区间: {quote.week52_low:.2f} – {quote.week52_high:.2f}  现处 {pos:.0f}%"

    news_block = ""
    if news:
        lines = []
        for n in news[:5]:
            ts = n.get("ts") or 0
            date = ""
            if ts:
                from datetime import datetime, timezone
                date = datetime.fromtimestamp(ts, timezone.utc).strftime("%Y-%m-%d")
            pub = n.get("publisher") or ""
            lines.append(f"  - {date} {pub}: {n['title']}")
        news_block = "\n[新闻] (近 7 天)\n" + "\n".join(lines)

    system = (
        "你是一名美股短线技术面助手。基于给定的指标、价格序列、新闻摘要、"
        "宏观场景与分析师共识，输出一份严格符合 JSON schema 的交易建议。\n"
        "- 价格建议必须贴近给定的均线/支撑/阻力位，禁止凭空给数字。\n"
        "- 给出 2 档买入价 + 2 档卖出价，每档配仓位百分比（合计 100%）。\n"
        "  若 action=hold 或建议方向单一，对应另一方向 legs 可为空数组。\n"
        "- 宏观背景是规则引擎给定的事实，必须采纳作为方向偏置（如场景四防守时勿建议大额加仓）。\n"
        "- 分析师共识仅作为参考，技术面与你的判断更重要。\n"
        "- 若信号矛盾或证据不足，action=hold 并解释。\n"
        "- rationale 用中文，≤200 字。risk 用中文，≤100 字。\n"
        "- 只输出 JSON，不要 markdown 代码块。"
    )

    user = (
        f"[标的] {quote.symbol} {quote.name}\n"
        f"[现价] {quote.price:.2f} {quote.currency} "
        f"({_signed_pct(quote.day_change_pct)} 当日)\n"
        f"[策略] horizon={horizon}  notes=\"{notes[:200]}\""
        + macro_block + analyst_block
        + f"\n[趋势]\n"
        f"  MA5={f(mas.get('MA5'))}  MA20={f(mas.get('MA20'))}  "
        f"MA60={f(mas.get('MA60'))}  MA120={f(mas.get('MA120'))}  "
        f"MA250={f(mas.get('MA250'))}\n"
        f"  现价 vs MA20: {vs_ma20}   vs MA60: {vs_ma60}\n"
        f"  最近 5 日收盘: {recent}\n\n"
        f"[动量]\n" + ("\n".join(momentum_lines) if momentum_lines else "  —")
        + vol_line + w52 + news_block + "\n\n"
        '[输出 schema]\n'
        '{\n'
        '  "action": "buy" | "sell" | "hold",\n'
        '  "confidence": <0..1>,\n'
        '  "buy_legs":  [{"price": <number>, "shares_pct": <int>}, ...],\n'
        '  "sell_legs": [{"price": <number>, "shares_pct": <int>}, ...],\n'
        '  "reference_ma": "MA5" | "MA20" | "MA60" | "MA120" | "MA250",\n'
        '  "rationale": "<≤200 字中文>",\n'
        '  "risk": "<≤100 字中文>"\n'
        '}\n'
    )

    return system + "\n\n---\n\n" + user


def _pct_vs(price: float, ma: Optional[float]) -> str:
    if ma is None or ma == 0:
        return "—"
    return f"{(price - ma) / ma * 100:+.1f}%"


def _signed_pct(v: Optional[float]) -> str:
    return "—" if v is None else f"{v:+.2f}%"


_RATE_LIMITER = {"last_call": 0.0, "consecutive_429": 0, "circuit_open": False}


def _call_qwen(prompt: str, timeout: int = 30) -> str:
    """Call DashScope OpenAI-compatible chat completions.

    Endpoint format matches OpenAI's /v1/chat/completions exactly, so the
    body uses `messages` / `response_format` instead of Gemini's `contents` /
    `generationConfig`.
    """
    import time as _t
    key = os.environ["DASHSCOPE_API_KEY"]
    model = os.environ.get("LLM_MODEL", "qwen-plus")
    # 注意：terraform 把 "" 也作为字符串传过来，os.environ.get(..., default) 此时
    # 不会 fallback 到默认值 → 用 `or` 让空串也走默认。
    base_url = (os.environ.get("DASHSCOPE_BASE_URL") or _DEFAULT_BASE_URL).rstrip("/")
    endpoint = f"{base_url}/chat/completions"
    # Qwen-plus 付费档 QPM ≥ 60，0.3s 间隔（=200 RPM）就够保守。
    min_interval = float(os.environ.get("LLM_MIN_INTERVAL_SECONDS", "0.3"))
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/json",
    }
    body = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "response_format": {"type": "json_object"},
        "temperature": 0.2,
        "max_tokens": 1024,
    }

    # Circuit breaker: 同一次 Lambda 跑里连续 3 个 429 → 直接放弃后续所有调用，
    # 避免 quota 耗尽时每个候选都 sleep 30s 然后超 timeout
    if _RATE_LIMITER["circuit_open"]:
        raise RuntimeError("qwen circuit open (quota likely depleted)")

    elapsed = _t.time() - _RATE_LIMITER["last_call"]
    if elapsed < min_interval:
        _t.sleep(min_interval - elapsed)

    for attempt in range(2):
        _RATE_LIMITER["last_call"] = _t.time()
        try:
            r = requests.post(endpoint, json=body, headers=headers, timeout=timeout)
            r.raise_for_status()
            _RATE_LIMITER["consecutive_429"] = 0
            break
        except requests.HTTPError as e:
            resp = e.response
            code = resp.status_code if resp is not None else "?"
            if code == 429:
                _RATE_LIMITER["consecutive_429"] += 1
                if _RATE_LIMITER["consecutive_429"] >= 3:
                    _RATE_LIMITER["circuit_open"] = True
                    raise RuntimeError(
                        "qwen http 429 (3rd consecutive, circuit opened)"
                    ) from None
                if attempt == 0:
                    wait_s = 30.0
                    if resp is not None:
                        retry_after = resp.headers.get("Retry-After")
                        if retry_after and retry_after.isdigit():
                            wait_s = min(60.0, float(retry_after))
                    print(f"[advisor] 429, retry in {wait_s:.0f}s")
                    _t.sleep(wait_s)
                    continue
            snippet = ""
            if resp is not None:
                try:
                    snippet = resp.text[:200].replace("\n", " ")
                except Exception:
                    pass
            # Authorization header 不会被 raise_for_status 暴露，body 里也只有错误描述，安全
            raise RuntimeError(f"qwen http {code} {snippet}") from None
        except requests.RequestException as e:
            raise RuntimeError(f"qwen network: {type(e).__name__}") from None

    data = r.json()
    choices = data.get("choices") or []
    if not choices:
        raise RuntimeError("empty qwen response")
    text = (choices[0].get("message") or {}).get("content", "").strip()
    if not text:
        raise RuntimeError("empty qwen text")
    return text


def _parse(raw: str) -> Optional[dict]:
    if not raw:
        return None
    s = raw.strip()
    if s.startswith("```"):
        # tolerate accidental fenced output
        s = s.strip("`")
        if s.lower().startswith("json"):
            s = s[4:]
        s = s.strip()
    try:
        d = json.loads(s)
    except Exception:
        return None
    if not isinstance(d, dict):
        return None
    action = d.get("action")
    if action not in ("buy", "sell", "hold"):
        return None
    return d


def _clean_legs(raw) -> List[Dict]:
    if not isinstance(raw, list):
        return []
    out: List[Dict] = []
    for leg in raw:
        if not isinstance(leg, dict):
            continue
        try:
            price = float(leg.get("price"))
            pct = int(leg.get("shares_pct") or 0)
        except (TypeError, ValueError):
            continue
        if price <= 0 or pct <= 0:
            continue
        out.append({"price": round(price, 2), "shares_pct": pct})
    return out


def _sanity_ok(adv: Advice, price: float) -> bool:
    """Reject legs that drift >25% from current price — almost certainly hallucination."""
    if price <= 0:
        return True
    for leg in adv.buy_legs + adv.sell_legs:
        if abs(leg["price"] - price) / price > 0.25:
            return False
    return True
