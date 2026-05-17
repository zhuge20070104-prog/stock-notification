terraform {
  required_version = ">= 1.5"
  required_providers {
    aws    = { source = "hashicorp/aws", version = "~> 5.40" }
    random = { source = "hashicorp/random", version = "~> 3.6" }
  }
  # Explicit local backend — state lives in terraform/terraform.tfstate.
  # 显式声明避免 terraform 把"以前用过 s3"当成"现在还要 s3"。
  backend "local" {}
}

provider "aws" {
  region = var.region
}
