"""
AWS Lambda 入口：每次 EventBridge 触发执行一次 check_once。

注意：
  - /tmp 状态文件在容器复用期间有效，冷启动会重置 -> 极端情况下同一标的可能多推一次。
  - 想要严格 "每天每标的只推一次"，把 state 改存到 DynamoDB / S3 即可。
  - EventBridge 建议 cron(*/10 13-21 ? * MON-FRI *) （UTC，覆盖美东盘中 9:30-16:00）。
"""
import os
import yaml

from src.monitor import check_once
from src.notifier import build_notifiers


def handler(event, context):
    cfg_path = os.environ.get("CONFIG_PATH", "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    notifiers = build_notifiers(cfg.get("notifiers", {}))
    triggered = check_once(
        cfg["watchlist"],
        notifiers,
        state_path="/tmp/state.json",
        dedupe_per_day=cfg.get("dedupe_per_day", True),
    )
    return {"triggered": triggered}
