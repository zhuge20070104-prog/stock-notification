from typing import List, Protocol

import requests


class Notifier(Protocol):
    def send(self, title: str, message: str) -> None: ...


class ConsoleNotifier:
    def send(self, title: str, message: str) -> None:
        print(f"\n[ALERT] {title}\n{message}\n")


class FeishuNotifier:
    """飞书自定义群机器人 webhook。用 interactive card，支持 lark_md 富文本。"""

    def __init__(self, webhook_url: str):
        self.url = webhook_url

    def send(self, title: str, message: str) -> None:
        # direction=below 用红色 (跌破)，above 用绿色 (涨破)，否则蓝色
        template = "red" if "below" in title or "📉" in title else (
            "green" if "above" in title or "📈" in title else "blue"
        )
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
        # 飞书 webhook 即使失败也返回 200，需要看 body.code
        data = r.json()
        if data.get("code", 0) != 0:
            raise RuntimeError(f"feishu webhook error: {data}")


class ServerChanNotifier:
    """Server酱 (sct.ftqq.com)：把消息推到个人微信。"""

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
