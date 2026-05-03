# 调度全部用北京时间 (Asia/Shanghai)。EventBridge Scheduler 原生支持时区。
#
# 美股盘中（夏令时 EDT 9:30-16:00 ET = 北京 21:30-04:00 次日；
#         冬令时 EST 9:30-16:00 ET = 北京 22:30-05:00 次日）。
# 用两条 schedule 拼出来，覆盖夏冬令时：
#   evening : Mon-Fri 21:00-23:59 北京时间
#   morning : Tue-Sat 00:00-05:59 北京时间
# 北京时间不切换 DST，所以两条规则就够。

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

resource "aws_scheduler_schedule" "monitor_evening_bj" {
  name = "${var.project}-monitor-evening-bj"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(*/${var.poll_interval_minutes} 21-23 ? * MON-FRI *)"
  schedule_expression_timezone = "Asia/Shanghai"

  target {
    arn      = aws_lambda_function.monitor.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}

resource "aws_scheduler_schedule" "monitor_morning_bj" {
  name = "${var.project}-monitor-morning-bj"

  flexible_time_window {
    mode = "OFF"
  }

  schedule_expression          = "cron(*/${var.poll_interval_minutes} 0-5 ? * TUE-SAT *)"
  schedule_expression_timezone = "Asia/Shanghai"

  target {
    arn      = aws_lambda_function.monitor.arn
    role_arn = aws_iam_role.scheduler.arn
  }
}
