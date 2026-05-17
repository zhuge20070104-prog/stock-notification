data "aws_iam_policy_document" "lambda_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["lambda.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "lambda" {
  name               = "${var.project}-lambda"
  assume_role_policy = data.aws_iam_policy_document.lambda_assume.json
}

resource "aws_iam_role_policy_attachment" "basic" {
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

data "aws_iam_policy_document" "ddb" {
  statement {
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:DeleteItem",
      "dynamodb:UpdateItem",
      "dynamodb:Scan",
      "dynamodb:Query",
    ]
    resources = [
      aws_dynamodb_table.watchlist.arn,
      aws_dynamodb_table.state.arn,
      aws_dynamodb_table.metrics_cache.arn,
    ]
  }
}

resource "aws_iam_role_policy" "ddb" {
  name   = "${var.project}-ddb"
  role   = aws_iam_role.lambda.id
  policy = data.aws_iam_policy_document.ddb.json
}

# Lambda zip is large (yfinance + pandas + numpy). Upload via S3 to bypass the 50MB direct-upload limit.
resource "aws_s3_bucket" "lambda_artifacts" {
  bucket        = "${var.project}-artifacts-${random_id.suffix.hex}"
  force_destroy = true
}

resource "aws_s3_object" "lambda_zip" {
  bucket = aws_s3_bucket.lambda_artifacts.id
  key    = "lambda-${filemd5(var.lambda_zip_path)}.zip"
  source = var.lambda_zip_path
  etag   = filemd5(var.lambda_zip_path)
}

locals {
  lambda_env = {
    WATCHLIST_TABLE      = aws_dynamodb_table.watchlist.name
    STATE_TABLE          = aws_dynamodb_table.state.name
    METRICS_CACHE_TABLE  = aws_dynamodb_table.metrics_cache.name
  }
}

resource "aws_lambda_function" "api" {
  function_name    = "${var.project}-api"
  role             = aws_iam_role.lambda.arn
  handler          = "api_handler.handler"
  runtime          = "python3.12"
  s3_bucket        = aws_s3_object.lambda_zip.bucket
  s3_key           = aws_s3_object.lambda_zip.key
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  timeout          = 30
  memory_size      = 1024

  environment {
    variables = merge(local.lambda_env, {
      API_KEY = var.api_key
    })
  }
}

resource "aws_lambda_function" "monitor" {
  function_name    = "${var.project}-monitor"
  role             = aws_iam_role.lambda.arn
  handler          = "monitor_handler.handler"
  runtime          = "python3.12"
  s3_bucket        = aws_s3_object.lambda_zip.bucket
  s3_key           = aws_s3_object.lambda_zip.key
  source_code_hash = filebase64sha256(var.lambda_zip_path)
  # 30 候选 × ~5s/call (含 RPM pacing) + yfinance batch + 余量
  timeout          = 240
  memory_size      = 1024

  environment {
    variables = merge(local.lambda_env, {
      FEISHU_WEBHOOK                = var.feishu_webhook
      SERVERCHAN_SENDKEY            = var.serverchan_sendkey
      DASHSCOPE_API_KEY             = var.dashscope_api_key
      DASHSCOPE_BASE_URL            = var.dashscope_base_url
      ADVISOR_ENABLED               = var.advisor_enabled ? "1" : "0"
      ADVISOR_COOLDOWN_HOURS        = tostring(var.advisor_cooldown_hours)
      ADVISOR_DAILY_BUDGET          = tostring(var.advisor_daily_budget)
      ADVISOR_PUSH_MIN_CONFIDENCE   = tostring(var.advisor_push_min_confidence)
      ALWAYS_PUSH_WATCHLIST         = var.always_push_watchlist ? "1" : "0"
      ADVISOR_MAX_CANDIDATES        = tostring(var.advisor_max_candidates)
      MOVER_CHANGE_PCT_THRESHOLD    = tostring(var.mover_change_pct_threshold)
      LLM_MODEL                     = var.llm_model
      LLM_MIN_INTERVAL_SECONDS      = tostring(var.llm_min_interval_seconds)
    })
  }
}
