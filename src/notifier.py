from typing import List, Optional, Protocol

import requests

try:
    from .fetcher import Quote
except ImportError:  # Lambda zip flattens src/* to the root
    from fetcher import Quote  # type: ignore[no-redef]


class Notifier(Protocol):
    def send(self, title: str, message: str) -> None: ...


class ConsoleNotifier:
    def send(self, title: str, message: str) -> None:
        print(f"\n[ALERT] {title}\n{message}\n")


class FeishuNotifier:
    """飞书自定义群机器人 webhook。interactive card + lark_md 富文本。"""

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, title: str, message: str) -> None:
        if "🔴" in title or "📉" in title or "below" in title.lower():
            template = "red"
        elif "🟢" in title or "📈" in title or "above" in title.lower() or "🚀" in title:
            template = "green"
        elif "⚠" in title or "ERROR" in title.upper():
            template = "orange"
        else:
            template = "blue"
        body = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": [
                    {"tag": "div", "text": {"tag": "lark_md", "content": message}},
                ],
            },
        }
        r = requests.post(self.url, json=body, timeout=10)
        r.raise_for_status()
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"feishu webhook error: {data}")


class ServerChanNotifier:
    """Server酱 (sct.ftqq.com)：把消息推到个人微信。免费版每天 5 条上限。"""

    def __init__(self, sendkey: str):
        self.url = f"https://sctapi.ftqq.com/{sendkey}.send"

    def send(self, title: str, message: str) -> None:
        r = requests.post(self.url, data={"title": title, "desp": message}, timeout=10)
        r.raise_for_status()


def build_notifiers(cfg: dict) -> List[Notifier]:
    notifiers: List[Notifier] = []
    if cfg.get("console", {}).get("enabled"):
        notifiers.append(ConsoleNotifier())
    fs = cfg.get("feishu", {})
    if fs.get("enabled") and fs.get("webhook_url"):
        notifiers.append(FeishuNotifier(fs["webhook_url"]))
    sc = cfg.get("serverchan", {})
    if sc.get("enabled") and sc.get("sendkey"):
        notifiers.append(ServerChanNotifier(sc["sendkey"]))
    return notifiers


def fan_out(notifiers: List[Notifier], title: str, message: str) -> None:
    for n in notifiers:
        try:
            n.send(title, message)
        except Exception as e:
            print(f"[warn] notifier {type(n).__name__} failed: {e}")


def _fmt_money(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v >= 1e12:
        return f"${v / 1e12:.2f}T"
    if v >= 1e9:
        return f"${v / 1e9:.2f}B"
    if v >= 1e6:
        return f"${v / 1e6:.2f}M"
    return f"${v:.2f}"


def _fmt_volume(v: Optional[float]) -> str:
    if v is None:
        return "—"
    if v >= 1e9:
        return f"{v / 1e9:.2f}B"
    if v >= 1e6:
        return f"{v / 1e6:.2f}M"
    if v >= 1e3:
        return f"{v / 1e3:.1f}K"
    return f"{v:.0f}"


def _pct(v: Optional[float], suffix: str = "%") -> str:
    return "—" if v is None else f"{v:+.2f}{suffix}" if suffix == "%" else f"{v:.2f}{suffix}"


def _w52_position(price: float, low: Optional[float], high: Optional[float]) -> str:
    if low is None or high is None or high <= low:
        return "—"
    pos = (price - low) / (high - low) * 100.0
    return f"{pos:.0f}% (${low:.2f} – ${high:.2f})"


def _tech_signals_line(q: Quote) -> Optional[str]:
    parts = []
    if q.williams_signal:
        v = f"{q.williams_r:.0f}" if q.williams_r is not None else "—"
        parts.append(f"Williams%R {v} {q.williams_signal}")
    if q.macd_signal:
        parts.append(f"MACD {q.macd_signal}")
    if q.kst_signal:
        parts.append(f"KST {q.kst_signal}")
    return "  ·  ".join(parts) if parts else None


def render_quote_metrics(q: Quote) -> str:
    """Markdown table 风格的指标块（飞书 lark_md 支持）。"""
    lines = [
        f"**{q.name} ({q.symbol})**  当前 ${q.price:.2f} {q.currency}  "
        f"({_pct(q.day_change_pct)})",
        "",
        f"- 昨收：${q.previous_close:.2f}" if q.previous_close else "- 昨收：—",
        f"- 今日区间：${q.day_low:.2f} – ${q.day_high:.2f}"
        if (q.day_low and q.day_high) else "- 今日区间：—",
        f"- 52周位置：{_w52_position(q.price, q.week52_low, q.week52_high)}",
        f"- 市盈率(TTM)：{q.pe_ratio:.2f}" if q.pe_ratio is not None else "- 市盈率(TTM)：—",
        f"- 前瞻PE：{q.forward_pe:.2f}" if q.forward_pe is not None else "- 前瞻PE：—",
        f"- 市值：{_fmt_money(q.market_cap)}",
        f"- 成交量：{_fmt_volume(q.volume)}  (均量 {_fmt_volume(q.avg_volume)})",
        f"- Beta：{q.beta:.2f}" if q.beta is not None else "- Beta：—",
        f"- 股息率：{q.dividend_yield:.2f}%" if q.dividend_yield is not None else "- 股息率：—",
    ]
    if q.pre_market_price:
        lines.append(f"- 盘前：${q.pre_market_price:.2f}")
    if q.post_market_price:
        lines.append(f"- 盘后：${q.post_market_price:.2f}")
    tech = _tech_signals_line(q)
    if tech:
        lines.append(f"- 技术信号：{tech}")
    return "\n".join(lines)


def render_threshold_alert(q: Quote, threshold: float, direction: str) -> str:
    diff = q.price - threshold
    diff_pct = (diff / threshold * 100.0) if threshold else 0.0
    head = (
        f"触发条件：{direction} ${threshold:.2f}  "
        f"(当前距阈值 {diff:+.2f} / {diff_pct:+.2f}%)"
    )
    return f"{head}\n\n{render_quote_metrics(q)}"


def render_gainer_alert(q: Quote) -> str:
    head = f"涨幅 **{_pct(q.day_change_pct)}** 进入 Top20 关注池"
    return f"{head}\n\n{render_quote_metrics(q)}"


def render_error_alert(context: str, err: BaseException) -> str:
    return f"**{context}** 执行失败：\n```\n{type(err).__name__}: {err}\n```"


def render_advice(q: Quote, adv, source: str = "watchlist") -> tuple:
    """Render an Advice object → (title, lark_md body) for fan_out.

    `source`: 'watchlist' (⭐ 用户关注列表) or 'mover' (🔍 异动发现) — shown in title.
    """
    emoji = {"buy": "🟢", "sell": "🔴", "hold": "⚪"}.get(adv.action, "⚪")
    src_tag = "⭐" if source == "watchlist" else "🔍"
    title = (
        f"🤖 AI {adv.action.upper()} {q.symbol} ({adv.confidence:.0%}) {src_tag}"
    )
    lines = [
        f"**{emoji} {adv.action.upper()}**  ·  {q.name}",
        f"**当前**：${q.price:.2f} {q.currency}",
    ]
    if adv.reference_ma:
        lines.append(f"**参考均线**：{adv.reference_ma}")
    if adv.buy_legs:
        lines.append("")
        lines.append("**分批买入**：")
        for leg in adv.buy_legs:
            lines.append(f"  - ${leg['price']:.2f}  仓位 {leg['shares_pct']}%")
    if adv.sell_legs:
        lines.append("")
        lines.append("**分批卖出**：")
        for leg in adv.sell_legs:
            lines.append(f"  - ${leg['price']:.2f}  仓位 {leg['shares_pct']}%")
    if adv.rationale:
        lines.append("")
        lines.append(f"**逻辑**：{adv.rationale}")
    if adv.risk:
        lines.append(f"**风险**：{adv.risk}")
    return title, "\n".join(lines)
