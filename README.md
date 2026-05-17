# Stock Watcher

美股 **LLM 主导**的交易建议机器人 + 移动端 Web UI。
yfinance 取价 + 算指标 → Qwen (阿里云百炼) 评估"该买/该卖/观望" → 只有 buy / sell（置信度 ≥0.55）才推飞书 → React UI 在 CloudFront 上随时改关注列表。

**v2 关键变化**：不再让你设阈值 below/above。AI 是唯一通知入口，且**不在关注列表的大盘科技股**只要当日异动 ≥3% 也会被评估并推送（带 🔍 标记）。

## 架构

```
[Mobile/Web]
    │
    ▼
[CloudFront]
    ├── /          ─────────────────────────> [S3] (React 静态文件)
    └── /api/*     ──> [API Gateway HTTP API] ──> [Lambda: api]
                                                       ▲
                                                       │
                                                  [DynamoDB: watchlist]
                                                       ▲
                                                       │ yfinance
[EventBridge Scheduler]──>[Lambda: monitor]────────────┘
   Asia/Shanghai                  │
   每 1.5h (09:00–21:00)           ├── 候选池 = watchlist + Top movers (|chg|≥3%)
                                  ├── 算指标 (MA/MACD/Williams%R)
                                  ├── 拉 7 天新闻
                                  ▼
                          [Qwen-plus (DashScope)]
                                  │
                                  ▼  (仅 buy/sell 且置信≥0.55)
                          飞书群机器人 / Server酱
                                  │
                                  ▼
                          [DynamoDB: state] (6h cooldown)
```

前端和 API **同源**（都走 CloudFront 域名），不需要 CORS preflight。
`/api/*` 在 CloudFront 上禁用缓存，转发到 API Gateway HTTP API。

调度时区是 `Asia/Shanghai`，每天 **10:00** 和 **18:00** 各触发一次（[terraform/schedule.tf](terraform/schedule.tf)）：

| 时段 | 用途 |
|------|------|
| 10:00 BJ | 早间简报（覆盖前一日美股收盘） |
| 18:00 BJ | 晚间简报（美股盘前/盘中） |

每次触发**最多评估 30 只**（watchlist 全部 + 异动 Top 补齐），单只 6h cooldown。

**Watchlist 默认强制推送**（含 hold / 低置信），作为固定早晚简报。异动 mover 仍走置信度过滤，避免噪音。

## WSL 环境准备

只装一次。在 WSL 里运行：

```bash
sudo apt update
sudo apt install -y make zip python3 python3-pip unzip curl

# Terraform
curl -fsSL https://apt.releases.hashicorp.com/gpg | sudo gpg --dearmor -o /usr/share/keyrings/hashicorp-archive-keyring.gpg
echo "deb [signed-by=/usr/share/keyrings/hashicorp-archive-keyring.gpg] https://apt.releases.hashicorp.com $(lsb_release -cs) main" | sudo tee /etc/apt/sources.list.d/hashicorp.list
sudo apt update && sudo apt install -y terraform

# AWS CLI v2
curl -fsSL "https://awscli.amazonaws.com/awscli-exe-linux-x86_64.zip" -o /tmp/awscliv2.zip
unzip -q /tmp/awscliv2.zip -d /tmp && sudo /tmp/aws/install --update

# Node.js (LTS) — nvm 推荐
curl -fsSL https://raw.githubusercontent.com/nvm-sh/nvm/v0.40.1/install.sh | bash
source ~/.bashrc
nvm install --lts
```

验证：

```bash
make --version && terraform -version && aws --version && node -v && python3 --version
```

> **WSL 路径建议**：项目放在 WSL 文件系统里（如 `~/projects/stock-notification/`）跑得快很多。`/mnt/c/...` 下跑 npm install 会非常慢（跨 9P 文件系统）。

## 一次性配置：环境变量

```bash
cp .env.local.example .env.local
$EDITOR .env.local      # 填 AWS 凭证 / api_key / Qwen key / 推送 webhook
```

`.env.local` 已在 [.gitignore](.gitignore)，不会入库。Makefile 会自动 source 它。

### 必需

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."          # 注意不是 *_KEY_ID
export AWS_REGION="ap-southeast-1"          # 新加坡（默认值，可省）
export TF_VAR_api_key="$(openssl rand -hex 24)"   # 前端访问 API 的共享密钥
```

把 `TF_VAR_api_key` 也存下来（手机端要用）：

```bash
echo "$TF_VAR_api_key" > ~/.stock-watcher-api-key
```

### AI 评估（强烈建议配，否则 monitor 是 no-op）

[阿里云百炼 DashScope](https://bailian.console.aliyun.com/)：qwen-plus 月费约 ¥9（按当前用量），起充 ¥1，新用户每模型送 100 万 token 试用额度（180 天有效）。

1. 打开 https://bailian.console.aliyun.com/
2. 阿里云账号登录（手机号即可）→ 顶部菜单 **API-KEY** → **创建 API Key**
3. 复制出来形如 `sk-xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx`（35 字符左右）
4. 写进 `.env.local`：

```bash
export TF_VAR_dashscope_api_key="sk-..."
```

不配也能部署，但 `monitor` Lambda 启动后直接打印 `[advisor] disabled` 然后返回，**不会推任何通知**。

### 推送通道

至少配一个，否则 AI 评估出结果也只能去 CloudWatch Logs 看。运行 `make notify-help` 也能看到下面这些说明。

**A. 飞书自定义群机器人**（推荐）

1. 注册飞书 https://www.feishu.cn （手机号即可，个人版免费）
2. 飞书 App → 建一个群 → 群设置 → 群机器人 → 添加机器人 → **自定义机器人**
3. 复制 webhook，形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx`

```bash
export TF_VAR_feishu_webhook="https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

优点：免费、不限频率、卡片消息漂亮（按 buy/sell 自动用红/绿色）、有 ⭐ / 🔍 标记区分来源。

**B. Server酱**（个人微信）

1. https://sct.ftqq.com 用 GitHub/微信扫码登录
2. 关注公众号绑定 → 获取 SendKey

```bash
export TF_VAR_serverchan_sendkey="SCTxxx..."
```

缺点：免费版每天 5 条上限，对 AI 推送场景很容易爆。建议用飞书。

### 可选：advisor 调参

| 变量 | 默认 | 说明 |
|------|------|------|
| `TF_VAR_advisor_push_min_confidence` | `0.55` | mover (异动发现) 的置信度门槛 |
| `TF_VAR_always_push_watchlist` | `true` | watchlist 每次都推（含 hold）；false 时走置信度门槛 |
| `TF_VAR_mover_change_pct_threshold` | `3.0` | 非 watchlist 标的进候选池所需的当日 \|涨跌幅 %\|。降到 0 表示 TECH_TICKERS 全评估 |
| `TF_VAR_advisor_max_candidates` | `30` | 单次运行最多评估几只（成本兜底） |
| `TF_VAR_advisor_cooldown_hours` | `6.0` | 同一标的两次推送的最小间隔 |
| `TF_VAR_advisor_daily_budget` | `200` | 单日最多推几张卡（兜底保险丝） |
| `TF_VAR_llm_model` | `qwen-plus` | LLM 型号；省钱可换 `qwen-turbo`，质量极致可换 `qwen-max` |

## 部署：一行命令

```bash
make deploy
```

这一条做完所有事：

1. 检查必需 env vars（缺任何一个直接报错退出）
2. 打 Lambda zip（`build/lambda.zip`，含 yfinance + pandas Linux wheels）
3. `terraform init` + `terraform apply -auto-approve`
4. 编译 React → S3 sync → CloudFront invalidate

输出末尾会看到：

```
================================================
Frontend  : https://xxxxxxxxx.cloudfront.net
API direct: https://xxxxxxxxx.execute-api.ap-southeast-1.amazonaws.com  (debug only)
Region    : ap-southeast-1
================================================
```

第一次 `terraform apply` 大概 3–5 分钟（CloudFront distribution 部署慢）。后续增量改动会快很多。

## 手机使用

1. 浏览器打开 Frontend URL
2. 第一次进会跳出 ⚙ 配置页：填 `TF_VAR_api_key`（保存在浏览器 localStorage，不上传服务器）
3. 搜索股票（代码或公司名都行）→ 选 **horizon**（短线评估/长线持有/不评估）→ 填策略备注（可选，会原文喂给 LLM）→ 添加
4. 关注列表里可随时改 horizon 或备注

> **horizon=skip** 的标的完全不评估，相当于"删除"但保留记录。
> **horizon=long** 的长线持仓也会跳过 LLM（长线不需要短线噪音）。

任何改动几秒内同步 DynamoDB；下次 EventBridge 触发就生效。

## Makefile targets

| target | 干啥 |
|--------|-----|
| `make help` | 列出所有 target 和环境变量要求 |
| `make notify-help` | 推送通道配置说明 |
| `make check-env` | 只检查环境变量，不部署 |
| `make build` | 仅打 Lambda zip |
| `make apply` | 仅 `terraform apply`（含 build） |
| `make frontend` | 仅前端：编译 + S3 + invalidate |
| `make deploy` | **一键部署：apply + frontend + outputs** |
| `make outputs` | 重新打印 Frontend / API URL |
| `make destroy` | 销毁所有 AWS 资源 |
| `make clean` | 删本地 build/ 产物 |

### 增量部署

| 改动类型 | 用啥 |
|---------|------|
| 改后端 Python 代码 | `make apply` |
| 改前端 React 代码 | `make frontend` |
| 改 Terraform / 调 advisor env 参数 | `make apply` |
| 改关注列表 / horizon / 备注 | 不用 redeploy，前端直接操作 |

## 本地 CLI（不部署也能用）

仓库里 [main.py](main.py) 是离线版：

```bash
pip install -r requirements.txt
python main.py query ORCL AMZN XLK GOOG    # 查当前价
python main.py search "Oracle"             # 公司名 → 代码
python main.py check                       # 跑一次完整管道（候选 → LLM → 推送）
python main.py monitor                     # 常驻轮询
```

`python main.py check` 需要把 DashScope key 也 export 进当前 shell：

```bash
export DASHSCOPE_API_KEY="sk-..."         # 注意 CLI 直接读 DASHSCOPE_API_KEY，不是 TF_VAR_ 前缀
export FEISHU_WEBHOOK="https://..."
python main.py check
```

watchlist 改 [config.yaml](config.yaml)。这条路径用本地 JSON 文件存状态，跟云上的 DynamoDB 独立。

## 调试 / 端到端测试

部署后想验证 AI 卡能推到飞书？最直接的办法是临时把置信度门槛调到 0，强制每次都推：

```bash
# 临时改 .env.local（或在 shell 里 export）
export TF_VAR_advisor_push_min_confidence="0"
make apply         # 几十秒
```

然后手动触发一次 monitor：

```bash
aws lambda invoke \
  --function-name stock-watcher-monitor \
  --region ap-southeast-1 \
  /tmp/out.json && cat /tmp/out.json

# 看日志
aws logs tail /aws/lambda/stock-watcher-monitor --since 5m
```

期望日志里看到：

```
[run] candidates: 30 (watchlist=8, movers=22)
[advisor] ORCL silent: hold (45%)         ← 这条本来不会推，但你把门槛降到 0 就会推
[advisor] NVDA cooldown, skip              ← 6h 内已推过
```

飞书群应该陆续收到几张 🤖 卡片。验证完**记得把门槛改回去**：

```bash
export TF_VAR_advisor_push_min_confidence="0.55"
make apply
```

[debug.sh](debug.sh) 是 v1 遗留物（基于阈值的），在 v2 下还能跑（put-item 成功）但**不再保证必出 push**——v2 没有阈值的概念。

### 常见报错对照

**Lambda CloudWatch 里看到 `[advisor] disabled`**
没配 `TF_VAR_dashscope_api_key`，或 `ADVISOR_ENABLED=0`。

**`[warn] qwen call failed: qwen http 401`**
key 错或被删了。重新 https://bailian.console.aliyun.com/ 生成并 `make apply`。

**`[warn] qwen call failed: qwen http 429`**
触发 QPM 限流或欠费。看 https://bailian.console.aliyun.com/ → "我的额度"。`circuit_open` 会让 Lambda 一次连续 3 个 429 后停止后续调用，避免空跑。

**`[warn] advisor JSON parse failed`**
LLM 偶尔不守 schema。重跑一次试试。频繁出现可以换 `qwen-max` 或降 temperature（advisor.py 里已经 0.2）。

**`[warn] advisor sanity reject`**
LLM 给的价格偏离现价 >25% 被 [src/advisor.py](src/advisor.py) `_sanity_ok` 拦截。重跑或检查 prompt 里 MA 数值是否合理。

**Lambda 报 `Runtime.ImportModuleError: No package metadata was found for curl_cffi`**
`build_lambda.sh` 之前会删 `*.dist-info`，但 `curl_cffi` 在 import 时要 `importlib.metadata.version()` 读自己的版本号。已修复（保留 dist-info），`make clean && make deploy` 重打即可。

**前端 401** — API key 改过但前端还存着旧的。手机 ⚙ 重新填一遍。

**前端没 CORS 但访问失败** — CloudFront 还在部署中（5–10 分钟），刷新。

**`make apply` 改了 advisor 参数但没生效** — 看 `.env.local` 里 `TF_VAR_*` 是否拼写正确，再 `source .env.local && make apply`。

**EventBridge 没触发** — AWS 控制台 → EventBridge → Schedules → 看 `stock-watcher-monitor-*` 的执行历史。

**收不到飞书** — Lambda CloudWatch Logs 看 `[warn] FeishuNotifier failed: ...`：
- webhook URL 复制时把 `&amp;` 等编码进去 → 重新复制
- 机器人配了"自定义关键词"安全策略，消息不含该关键词 → 调整或在备注里加
- `feishu webhook error: {'code': ...}` → 看 [飞书错误码](https://open.feishu.cn/document/ukTMukTMukTM/uADOzYjLwgzM24CM4MjN)

## 销毁清理

```bash
make destroy
```

确认 `yes`。所有 AWS 资源（CloudFront / S3 / Lambda / DynamoDB / IAM / Scheduler）都会删，账单归零。本地 `build/` / `.terraform/` 还在，跑 `make clean` 一并清。

## 成本估算

个人级别基本在免费层内：

- **Qwen API**：每天 ~270 次调用 ≈ 月费 ¥9（按 qwen-plus 单价 ¥0.8/1M input + ¥2.0/1M output 算）
- **Lambda**：每天 9 次 schedule × 30 候选 = 270 次执行 + ~10 次 API 调用，远低于 100 万/月免费额度
- **DynamoDB on-demand**：watchlist + state + metrics-cache 三张表，读写按需计费，几乎不计
- **CloudFront**：每月前 1TB 出站免费
- **S3**：几 MB 静态文件
- **EventBridge Scheduler**：免费

AWS 实际账单一般 < $1/月。Qwen 那边 ¥9/月（约 $1.25）。整体每月 < $2.5。
