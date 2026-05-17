variable "region" {
  description = "AWS region. Default = Singapore."
  type        = string
  default     = "ap-southeast-1"
}

variable "project" {
  type    = string
  default = "stock-watcher"
}

variable "api_key" {
  description = "Shared secret sent as x-api-key from the frontend"
  type        = string
  sensitive   = true
}

variable "feishu_webhook" {
  description = "飞书自定义群机器人 webhook URL (可选)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "serverchan_sendkey" {
  description = "Server酱 SendKey (可选)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "min_alert_interval_hours" {
  description = <<EOT
Lambda 内部的去重保护（小时）。EventBridge 每 1.5h 触发一次，
这个值设小于 1.5 是为了避免 cron 抖动 ±60s 把第二次卡掉。
仅在手动连续调用 Lambda 时起去重作用。
EOT
  type        = number
  default     = 1.0
}

variable "enable_gainer_alerts" {
  description = "是否启用 Top20 涨幅榜告警。"
  type        = bool
  default     = true
}

variable "gainer_pct_threshold" {
  description = "进入 Top20 后涨幅 >= 多少 % 才推送。"
  type        = number
  default     = 5.0
}

variable "gainer_pool_size" {
  description = "涨幅池大小，从 movers Top N 里筛。"
  type        = number
  default     = 20
}

variable "lambda_zip_path" {
  type    = string
  default = "../build/lambda.zip"
}

variable "dashscope_api_key" {
  description = <<EOT
DashScope (阿里云百炼) API key，从 bailian.console.aliyun.com → API-KEY 拿。
不配则 advisor 整体禁用。Qwen-plus 月费约 ¥9（按当前用量），起充 ¥1。
EOT
  type        = string
  default     = ""
  sensitive   = true
}

variable "dashscope_base_url" {
  description = <<EOT
DashScope OpenAI-compatible base URL（含 /compatible-mode/v1 但不含 /chat/completions）。
- 公共 bailian: 留空（默认 https://dashscope.aliyuncs.com/compatible-mode/v1）
- 私有 MaaS workspace: https://ws-xxx.cn-beijing.maas.aliyuncs.com/compatible-mode/v1
EOT
  type        = string
  default     = ""
}

variable "advisor_enabled" {
  description = "全局开关。0 关闭 advisor 调用，即便 key 已配。"
  type        = bool
  default     = true
}

variable "advisor_cooldown_hours" {
  description = "同一标的两次 AI 评估之间的最小间隔（小时）。"
  type        = number
  default     = 6.0
}

variable "advisor_daily_budget" {
  description = "单日 AI 评估调用上限，保险丝。"
  type        = number
  default     = 200
}

variable "llm_model" {
  description = <<EOT
LLM 模型名。默认 qwen-plus（性价比/质量均衡，月费约 ¥9）。
其他选项: qwen-turbo（更便宜 ¥2.5/月）/ qwen-max（顶级但贵 ¥250/月）
完整模型列表: https://help.aliyun.com/zh/model-studio/getting-started/models
EOT
  type        = string
  default     = "qwen-plus"
}

variable "llm_min_interval_seconds" {
  description = <<EOT
两次 LLM 调用之间的最小间隔（秒）。
- Qwen-plus 付费 QPM ≥ 60，0.3s（=200 RPM）足够保守。
EOT
  type        = number
  default     = 0.3
}

variable "advisor_push_min_confidence" {
  description = "LLM 给出的置信度 ≥ 此值才推送（仅对 mover 生效；watchlist 看 always_push_watchlist）。"
  type        = number
  default     = 0.55
}

variable "always_push_watchlist" {
  description = <<EOT
true: watchlist 标的每次评估都推送（含 hold / 低置信），用于早晚固定简报。
false: 沿用 confidence ≥ advisor_push_min_confidence + buy/sell 的过滤。
mover (异动发现) 始终走过滤，不受此开关影响。
EOT
  type        = bool
  default     = true
}

variable "advisor_max_candidates" {
  description = "单次运行最多评估多少只标的（watchlist 优先，剩余给异动 mover）。"
  type        = number
  default     = 30
}

variable "mover_change_pct_threshold" {
  description = "非 watchlist 的标的进入候选池所需的最小当日 |涨跌幅 %|。"
  type        = number
  default     = 3.0
}
