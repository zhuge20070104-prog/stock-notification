# 北京时间每天 10:00 和 18:00 各触发一次。
# 不管美股开没开盘 —— 盘外 yfinance 返回的是上一交易日收盘价，是用户明确要的"按时简报"行为。
# 一次 watchlist + Top movers 评估，结果（含 hold）都推送，作为早晚的固定播报。

resource "aws_iam_role" "scheduler" {
  name = "${var.project}-scheduler"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Action    = "sts:AssumeRole"
      Principal = { Service = "scheduler.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "scheduler_invoke" {
  name = "${var.project}-scheduler-invoke"
  role = aws_iam_role.scheduler.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = aws_lambda_function.monitor.arn
    }]
  })
}

resource "aws_scheduler_schedule" "monitor_bj_daily" {
  name = "${var.project}-monitor-bj-daily"

  flexible_time_window {
    mode = "OFF"
  }

  # 每天 10:00 和 18:00 (北京时间)
  schedule_expression          = "cron(0 10,18 * * ? *)"
  schedule_expression_timezone = "Asia/Shanghai"

  target {
    arn      = aws_lambda_function.monitor.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
