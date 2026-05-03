#!/usr/bin/env bash
# 端到端测试：往 watchlist 塞一个必命中的 symbol → 清当日推送状态 → 手动触发
# monitor Lambda → 打印结果和最近日志。期望飞书 / Server酱 群里能收到一条推送。
#
# 用法:
#   ./debug.sh              # 默认 symbol=ORCL, 阈值 below 99999 (永远命中)
#   SYMBOL=AMZN ./debug.sh  # 换一个 symbol 测
#
# 必需环境变量:
#   AWS_ACCESS_KEY_ID
#   AWS_SECRET_ACCESS_KEY
#
# 可选环境变量:
#   AWS_REGION  (默认 ap-southeast-1)
#   PROJECT     (默认 stock-watcher，对应 terraform var.project)
#   SYMBOL      (默认 ORCL)

set -euo pipefail

# ---------- 1. 校验凭证 ----------
missing=0
if [ -z "${AWS_ACCESS_KEY_ID:-}" ]; then
  echo "ERROR: AWS_ACCESS_KEY_ID 未设置" >&2
  missing=1
fi
if [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo "ERROR: AWS_SECRET_ACCESS_KEY 未设置" >&2
  missing=1
fi
if [ "$missing" -ne 0 ]; then
  echo "" >&2
  echo "在当前 shell 里 export 后再跑:" >&2
  echo "  export AWS_ACCESS_KEY_ID='AKIA...'" >&2
  echo "  export AWS_SECRET_ACCESS_KEY='...'" >&2
  exit 1
fi

export AWS_REGION="${AWS_REGION:-ap-southeast-1}"
PROJECT="${PROJECT:-stock-watcher}"
SYMBOL="${SYMBOL:-ORCL}"
SYMBOL_UPPER=$(echo "$SYMBOL" | tr '[:lower:]' '[:upper:]')
TODAY=$(date -u +%Y-%m-%d)

WATCHLIST_TABLE="${PROJECT}-watchlist"
STATE_TABLE="${PROJECT}-state"
MONITOR_FN="${PROJECT}-monitor"

echo "[debug] region=$AWS_REGION project=$PROJECT symbol=$SYMBOL_UPPER"
echo ""

# ---------- 2. 往 watchlist 塞一条必命中的记录 ----------
# direction=below + threshold=99999 → 任何美股价都低于 99999 → 必命中
echo "[1/4] 写入 watchlist (${SYMBOL_UPPER}, below 99999, 必命中)..."
aws dynamodb put-item \
  --table-name "$WATCHLIST_TABLE" \
  --region "$AWS_REGION" \
  --item "{
    \"symbol\":    {\"S\": \"${SYMBOL_UPPER}\"},
    \"direction\": {\"S\": \"below\"},
    \"threshold\": {\"N\": \"99999\"}
  }" \
  >/dev/null
echo "      ok"

# ---------- 3. 清掉当日 state，避免去重逻辑跳过 ----------
# monitor_handler.py 里同一天同一 symbol 只推一次
echo "[2/4] 清除 state 表里 ${SYMBOL_UPPER} 的当日推送记录..."
aws dynamodb delete-item \
  --table-name "$STATE_TABLE" \
  --region "$AWS_REGION" \
  --key "{\"symbol\": {\"S\": \"${SYMBOL_UPPER}\"}}" \
  >/dev/null
echo "      ok (今日: $TODAY)"

# ---------- 4. 手动触发 monitor Lambda ----------
echo "[3/4] 调用 ${MONITOR_FN}..."
OUT=$(mktemp)
aws lambda invoke \
  --function-name "$MONITOR_FN" \
  --region "$AWS_REGION" \
  "$OUT" \
  >/dev/null

echo "      Lambda 返回:"
sed 's/^/      /' "$OUT"
echo ""

# 期望: {"checked": N, "triggered": [..., "ORCL", ...]}
if grep -q "\"$SYMBOL_UPPER\"" "$OUT"; then
  echo "      ✓ ${SYMBOL_UPPER} 在 triggered 列表里，飞书 / Server酱 群应该已收到推送"
else
  echo "      ⚠ ${SYMBOL_UPPER} 不在 triggered 列表，看下面日志查原因" >&2
fi
rm -f "$OUT"
echo ""

# ---------- 5. 拉最近 5 分钟日志 ----------
echo "[4/4] 最近 5 分钟 monitor 日志:"
aws logs tail "/aws/lambda/${MONITOR_FN}" \
  --region "$AWS_REGION" \
  --since 5m \
  --format short \
  | sed 's/^/      /' \
  || echo "      (拉日志失败，可能 log group 还没创建；去 CloudWatch 控制台手动看)"
