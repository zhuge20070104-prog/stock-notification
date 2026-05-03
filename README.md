# Stock Watcher

美股价格监控 + 阈值告警 + 移动端 Web UI。
yfinance 取价 → DynamoDB 存关注列表 → 命中阈值推送飞书/Server酱 → React UI 在 CloudFront 上，手机随时改。

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
   北京时间                       │
   每 10 分钟                      ▼
                          [DynamoDB: state]
                                  │
                                  ▼
                  飞书群机器人 / Server酱
```

前端和 API **同源**（都走 CloudFront 域名），不需要 CORS preflight，没有跨域坑。
`/api/*` 路径在 CloudFront 上禁用缓存，转发到 API Gateway HTTP API。

调度时区是 `Asia/Shanghai`，两条规则覆盖美股盘中（夏冬令时都覆盖）：

| 规则 | 北京时间 | 对应美东 |
|------|----------|----------|
| evening | Mon–Fri 21:00–23:59 | 美股早盘 |
| morning | Tue–Sat 00:00–05:59 | 美股午盘到收盘 |

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

> **WSL 路径建议**：项目放在 WSL 文件系统里（如 `~/projects/stock-notification/`）跑得快很多。在 `/mnt/c/...` 下跑 npm install 会非常慢（跨 9P 文件系统）。

## 一次性配置：环境变量

最快: 复制模板填值，[deploy.sh](deploy.sh) 会自动 source：

```bash
cp .env.local.example .env.local
$EDITOR .env.local      # 填 AWS 凭证 / api_key / 推送 webhook
./deploy.sh
```

`.env.local` 已在 [.gitignore](.gitignore)，不会入库。

或者老派一点直接在 shell 里 export（写进 `~/.bashrc` 或 `~/.envrc`）：

### 必需

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."          # 注意不是 *_KEY_ID
export AWS_REGION="ap-southeast-1"          # 新加坡（默认值，可省）
export TF_VAR_api_key="$(openssl rand -hex 24)"   # 前端访问 API 的共享密钥，自己起一串长随机
```

把 `TF_VAR_api_key` 也存下来（手机端要用）：

```bash
echo "$TF_VAR_api_key" > ~/.stock-watcher-api-key
```

### 可选：推送通道

至少配一个，否则命中阈值时只能去 CloudWatch Logs 里看。运行 `make notify-help` 也能看到下面这些说明。

**A. 飞书自定义群机器人**（推荐）

1. 注册飞书 https://www.feishu.cn （手机号即可，个人版免费，不需要企业认证）
2. 飞书 App → 建一个群（自己一个人也行）
3. 群设置（右上角）→ 群机器人 → 添加机器人 → **自定义机器人**
4. 设置名字/头像，可选加"自定义关键词"等安全设置
5. 复制 webhook，形如 `https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx`

```bash
export TF_VAR_feishu_webhook="https://open.feishu.cn/open-apis/bot/v2/hook/..."
```

优点：免费、不限频率、卡片消息漂亮（自动按方向用红/绿色）、不依赖公司 IT 策略。

**B. Server酱**（个人微信，不需要飞书账号）

1. https://sct.ftqq.com 用 GitHub/微信扫码登录
2. 关注公众号绑定 → 获取 SendKey，形如 `SCTxxxxxxxx...`

```bash
export TF_VAR_serverchan_sendkey="SCTxxx..."
```

缺点：免费版每天 5 条上限。

> 两个可以同时配，命中时两边都推。

## 部署：一行命令

```bash
make deploy
```

这一条做完所有事：

1. 检查必需 env vars（缺任何一个直接报错退出）
2. 打 Lambda zip（`build/lambda.zip`，含 yfinance + Linux wheels）
3. `terraform init` + `terraform apply -auto-approve`
4. 编译 React（把 API URL 烧进 bundle）
5. 同步到 S3 + invalidate CloudFront

输出末尾会看到：

```
================================================
Frontend  : https://xxxxxxxxx.cloudfront.net
API direct: https://xxxxxxxxx.execute-api.ap-southeast-1.amazonaws.com  (debug only)
Region    : ap-southeast-1
================================================
```

第一次 `terraform apply` 大概 3–5 分钟（CloudFront distribution 的 deploy 慢）。后续增量改动会快很多。

> 切换 Function URL → API Gateway 的那次 `make apply` 因为要重建 CloudFront，可能要 5–10 分钟。

## 手机使用

1. 浏览器打开 Frontend URL（CloudFront 地址）
2. 第一次进会跳出 ⚙ 配置页：
   - **API Key** 填 `TF_VAR_api_key` 的值（保存在浏览器 localStorage，不会上传服务器）
   - 不需要填 API URL，前端自动走同源 `/api`
3. 搜索股票（按代码或公司名都行）→ 选阈值方向（低于/高于）→ 填阈值 → 添加
4. 关注列表里随时编辑/删除

任何改动几秒内同步到 DynamoDB；下一次 EventBridge 触发就生效。

## Makefile targets

| target | 干啥 |
|--------|-----|
| `make help` | 列出所有 target 和环境变量要求 |
| `make notify-help` | 推送通道配置说明 |
| `make check-env` | 只检查环境变量，不部署 |
| `make build` | 仅打 Lambda zip |
| `make apply` | 仅 `terraform apply`（含 build） |
| `make frontend` | 仅前端：编译 + 同步 S3 + invalidate |
| `make deploy` | **一键部署：apply + frontend + 打印 outputs** |
| `make outputs` | 重新打印 Frontend / API URL |
| `make destroy` | 销毁所有 AWS 资源 |
| `make clean` | 删本地 build/ 产物 |

### 增量部署

| 改动类型 | 用啥 |
|---------|------|
| 改后端 Python 代码 | `make apply` |
| 改前端 React 代码 | `make frontend` |
| 改 Terraform | `make apply` |
| 改阈值 / 增删股票 | 不用 redeploy，前端直接操作 |

## 本地 CLI（不部署也能用）

仓库里 [main.py](main.py) 是离线版：

```bash
pip install -r requirements.txt
python main.py query ORCL AMZN XLK GOOG    # 查当前价
python main.py search "Oracle"             # 公司名 -> 代码
python main.py check                       # 跑一次 config.yaml 里的阈值检查
python main.py monitor                     # 常驻轮询
```

阈值改 [config.yaml](config.yaml)。这条路径用本地 JSON 文件存状态，跟云上的 DynamoDB 是独立的。

## 调试 / 端到端测试

部署后想验证「确实能推送到飞书 / Server酱」？跑 [debug.sh](debug.sh)：

```bash
export AWS_ACCESS_KEY_ID="AKIA..."
export AWS_SECRET_ACCESS_KEY="..."
./debug.sh
```

脚本会按顺序做四件事：

1. **写 watchlist**：往 `stock-watcher-watchlist` DDB 表塞一条 `ORCL, direction=below, threshold=99999` —— 阈值故意调到 99999，任何真实股价都「低于」它，**必命中**。
2. **清当日 state**：从 `stock-watcher-state` 删掉 `ORCL` 那条，绕过 [monitor_handler.py:49-51](backend/monitor_handler.py#L49-L51) 的去重（同一天同一 symbol 只推一次）。
3. **手动触发 monitor Lambda**：`aws lambda invoke stock-watcher-monitor`，期望返回 `{"checked": N, "triggered": ["ORCL", ...]}`。命中后会立刻调用飞书 / Server酱 webhook。
4. **拉最近 5 分钟日志**：`aws logs tail /aws/lambda/stock-watcher-monitor --since 5m`，能看到每个 ticker 的 `HIT` / `ok` 判定和 notifier 报错。

环境变量可调：

| 变量 | 默认 | 说明 |
|------|------|------|
| `AWS_ACCESS_KEY_ID` | — | **必需**，未设置直接报错退出 |
| `AWS_SECRET_ACCESS_KEY` | — | **必需**，未设置直接报错退出 |
| `AWS_REGION` | `ap-southeast-1` | Lambda 实际部署的 region（注意不是 deploy.sh 里写的 `-2`） |
| `PROJECT` | `stock-watcher` | 对应 terraform `var.project`，影响 DDB 表名 / Lambda 函数名 |
| `SYMBOL` | `ORCL` | 想测的 ticker，例如 `SYMBOL=AMZN ./debug.sh` |

测完想恢复正常状态（不再每次轮询都推这条假阈值）：在前端 UI 把 ORCL 删了，或者 `aws dynamodb delete-item --table-name stock-watcher-watchlist --region ap-southeast-1 --key '{"symbol":{"S":"ORCL"}}'`。

### 常见报错对照

**`UnrecognizedClientException: The security token included in the request is invalid`**
当前 shell 没 export 凭证，或 region 错了。`deploy.sh` 里的 `export` 只对那次调用生效，新开 shell 要重新 export。注意 `AWS_REGION=ap-southeast-1`（实际部署位置），不是 `deploy.sh` 写的 `-2`。

**`Unknown options: --cli-binary-format`**
AWS CLI v1，去掉这个选项即可。或者升级到 v2（见 WSL 环境准备段）。

**Lambda 报 `Runtime.ImportModuleError: No package metadata was found for curl_cffi`**
`build_lambda.sh` 之前会删掉所有 `*.dist-info`，但 `curl_cffi` 在 import 时要用 `importlib.metadata.version()` 读自己的版本号，缺 dist-info 就抛这个错。已修复（保留 dist-info），重新 `make deploy` 即可。

## 故障排查

**`make: command not found`** — `sudo apt install make`

**`bash: zip: command not found`** — `sudo apt install zip`

**`terraform: command not found`** — 没装好，看 WSL 环境准备段

**`make deploy` 报 "ERROR: 必需环境变量 X 未设置"** — 在当前 WSL shell `export` 那个变量；要持久化写进 `~/.bashrc` 或 `~/.envrc`。

**`pip install ... --platform manylinux2014_x86_64` 报错** — 升级 pip：`pip install --upgrade pip`

**Lambda 报 `Unable to import module 'api_handler'`** — 通常是 zip 里 yfinance 装的是 Windows wheel。用 `make clean && make build` 重打。

**前端打开后 API 一直报 401** — `TF_VAR_api_key` 改过但前端还存着旧 key。手机上 ⚙ 重新填一遍。

**前端报 CORS** — 同源访问理论上不会出（前端和 API 都走 CloudFront）。如果出了八成是 CloudFront 还在部署中（5–10 分钟），刷新即可。

**`/api/*` 请求一直 403/404** — CloudFront 行为还在传播。等几分钟，或 `aws cloudfront create-invalidation --distribution-id <id> --paths "/api/*"`。

**直接 curl API Gateway URL 测试** — 调试时用 outputs 里的 `apigw_direct_url`：
```bash
curl -H "x-api-key: $TF_VAR_api_key" "$(cd terraform && terraform output -raw apigw_direct_url)/watchlist"
```
注意直连 API Gateway URL 时**没有** `/api` 前缀。

**EventBridge 没触发** — AWS 控制台 → EventBridge → Schedules（注意不是 Rules）→ 看 `stock-watcher-monitor-evening-bj` / `morning-bj` 的执行历史。

**收不到飞书推送** — Lambda CloudWatch Logs 看 `[warn] FeishuNotifier failed: ...`。常见原因：
- webhook URL 复制时把 `&amp;` 等编码进去了 → 重新复制
- 机器人配了"自定义关键词"安全策略，但消息里不包含该关键词 → 调整关键词或在消息里加上
- `feishu webhook error: {'code': ...}` → 看 [飞书错误码](https://open.feishu.cn/document/ukTMukTMukTM/uADOzYjLwgzM24CM4MjN)，常见 19021（IP 不在白名单）/ 19024（关键词不匹配）/ 9499（webhook 已被禁用）
- 机器人被群管理员撤了 → 重建

## 销毁清理

```bash
make destroy
```

确认 `yes`。所有 AWS 资源（CloudFront / S3 / Lambda / DynamoDB / IAM / Scheduler）都会被删，账单归零。本地 `build/`、`.terraform/` 还在，跑 `make clean` 一并清。

## 成本估算

个人级别基本在免费层内：

- Lambda 调用：每天 ~110 次，远低于 100 万/月免费额度
- DynamoDB on-demand：读写都是按需，几乎不计
- CloudFront：每月前 1TB 出站免费
- S3：几 MB 静态文件
- EventBridge Scheduler：免费

实际账单一般 < $1/月。
