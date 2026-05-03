output "frontend_url" {
  description = "CloudFront URL — 用户/手机就用这个，API 走同源 /api/* 路径"
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "apigw_direct_url" {
  description = "API Gateway 原始 URL，调试用（curl 测试时用）。前端请走 CloudFront 的 /api。"
  value       = aws_apigatewayv2_api.api.api_endpoint
}

output "frontend_bucket" {
  value = aws_s3_bucket.frontend.id
}

output "cloudfront_distribution_id" {
  value = aws_cloudfront_distribution.frontend.id
}

output "watchlist_table" {
  value = aws_dynamodb_table.watchlist.name
}
