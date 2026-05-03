#!/usr/bin/env bash
# 一键部署。所有密钥从 .env.local（不入库）或当前 shell 环境变量读取。
#
# 首次使用:
#   cp .env.local.example .env.local   # 填入真实值
#   ./deploy.sh
#
# 或直接 export 后跑:
#   export AWS_ACCESS_KEY_ID=...
#   export AWS_SECRET_ACCESS_KEY=...
#   export TF_VAR_api_key=...
#   ./deploy.sh

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 优先用项目本地 .env.local，没有就靠当前 shell 已 export 的变量
if [ -f "$ROOT/.env.local" ]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT/.env.local"
  set +a
fi

# 必需变量校验（make check-env 也会查，但这里提前给更友好的提示）
missing=0
for v in AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY TF_VAR_api_key; do
  if [ -z "${!v:-}" ]; then
    echo "ERROR: $v 未设置" >&2
    missing=1
  fi
done
if [ "$missing" -ne 0 ]; then
  echo "" >&2
  echo "复制模板填值:" >&2
  echo "  cp .env.local.example .env.local && \$EDITOR .env.local" >&2
  exit 1
fi

# AWS region: Lambda / Terraform 实际落点。Makefile 里默认 ap-southeast-1。
export AWS_REGION="${AWS_REGION:-ap-southeast-1}"

# 把 api_key 留一份本地副本，前端配置页要填
echo "$TF_VAR_api_key" > ~/.stock-watcher-api-key

make deploy
