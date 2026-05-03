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

variable "poll_interval_minutes" {
  description = "巡检间隔 (分钟)。仅 1/2/3/4/5/6/10/12/15/20/30 这种能整除 60 的值有意义。"
  type        = number
  default     = 10
}

variable "lambda_zip_path" {
  type    = string
  default = "../build/lambda.zip"
}
