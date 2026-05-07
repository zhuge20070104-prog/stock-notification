"""AWS Lambda 入口：每次 EventBridge 触发执行一次 check_once。

- 阈值告警：每 `min_alert_interval_hours` 小时重发一次（默认 1.5h），不再 dedupe per day
- 涨幅告警：从 Top20 涨幅榜挑出 >= 5% 的标的同样 1.5h 节流
- 失败告警：顶层异常会发一条飞书 / Server酱 通知
"""
import os
import yaml

from src.monitor import check_once, check_with_failure_alert
from src.notifier import build_notifiers


def handler(event, context):
    cfg_path = os.environ.get("CONFIG_PATH", "config.yaml")
    with open(cfg_path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)

    notifiers = build_notifiers(cfg.get("notifiers", {}))
    return check_with_failure_alert(
        notifiers,
        lambda: check_once(
            cfg["watchlist"],
            notifiers,
            state_path="/tmp/state.json",
            metrics_cache_path="/tmp/metrics_cache.json",
            min_alert_interval_hours=float(cfg.get("min_alert_interval_hours", 1.5)),
            enable_gainer_alerts=cfg.get("enable_gainer_alerts", True),
            gainer_pct_threshold=float(cfg.get("gainer_pct_threshold", 5.0)),
            gainer_pool_size=int(cfg.get("gainer_pool_size", 20)),
        ),
        context="monitor",
    )
