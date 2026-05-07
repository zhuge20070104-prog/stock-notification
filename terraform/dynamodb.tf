resource "aws_dynamodb_table" "watchlist" {
  name         = "${var.project}-watchlist"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "symbol"

  attribute {
    name = "symbol"
    type = "S"
  }
}

resource "aws_dynamodb_table" "state" {
  name         = "${var.project}-state"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "symbol"

  attribute {
    name = "symbol"
    type = "S"
  }
}

resource "aws_dynamodb_table" "metrics_cache" {
  name         = "${var.project}-metrics-cache"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "symbol"

  attribute {
    name = "symbol"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }
}
