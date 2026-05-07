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
  timeout          = 90
  memory_size      = 1024

  environment {
    variables = merge(local.lambda_env, {
      FEISHU_WEBHOOK             = var.feishu_webhook
      SERVERCHAN_SENDKEY         = var.serverchan_sendkey
      MIN_ALERT_INTERVAL_HOURS   = tostring(var.min_alert_interval_hours)
      GAINER_PCT_THRESHOLD       = tostring(var.gainer_pct_threshold)
      GAINER_POOL_SIZE           = tostring(var.gainer_pool_size)
      ENABLE_GAINER_ALERTS       = var.enable_gainer_alerts ? "1" : "0"
    })
  }
}
