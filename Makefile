# stock-watcher 一键部署 Makefile
#
# 用法:
#   make help            列所有 target 和需要的环境变量
#   make notify-help     推送通道配置说明
#   make deploy          一键部署 (build + apply + frontend)
#   make destroy         销毁所有资源
#
# 必需环境变量:
#   AWS_ACCESS_KEY_ID
#   AWS_SECRET_ACCESS_KEY      (注意不是 *_KEY_ID)
#   TF_VAR_api_key             前端访问 API 的共享密钥，自己起一串长随机
#
# 可选环境变量 (推送通道，至少配一个否则只有 CloudWatch Logs):
#   TF_VAR_feishu_webhook      飞书自定义群机器人 webhook
#   TF_VAR_serverchan_sendkey  Server酱 SendKey

SHELL := bash
.SHELLFLAGS := -ec

REQUIRED_ENV := AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY TF_VAR_api_key
OPTIONAL_ENV := TF_VAR_feishu_webhook TF_VAR_serverchan_sendkey

# 默认 region = 新加坡。如果用户已 export 过，使用用户值。
export AWS_DEFAULT_REGION ?= ap-southeast-1
export AWS_REGION ?= ap-southeast-1

BUILD_LAMBDA := bash scripts/build_lambda.sh

.PHONY: help check-env notify-help build init apply frontend deploy destroy clean outputs

help:
	@echo "Targets:"
	@echo "  make deploy        一键部署 (build + apply + frontend)"
	@echo "  make build         打 Lambda zip"
	@echo "  make apply         terraform apply"
	@echo "  make frontend      构建前端 + 同步 S3 + 失效 CloudFront"
	@echo "  make outputs       打印 terraform outputs"
	@echo "  make destroy       销毁所有资源"
	@echo "  make clean         删 build 产物"
	@echo "  make notify-help   推送通道配置说明"
	@echo ""
	@echo "必需环境变量:"
	@for v in $(REQUIRED_ENV); do echo "  $$v"; done
	@echo ""
	@echo "可选环境变量 (推送通道):"
	@for v in $(OPTIONAL_ENV); do echo "  $$v"; done
	@echo ""
	@echo "Region : $$AWS_REGION (默认 ap-southeast-1 / 新加坡)"

check-env:
	@missing=0; \
	for v in $(REQUIRED_ENV); do \
	  if [ -z "$${!v}" ]; then \
	    echo "ERROR: 必需环境变量 $$v 未设置" >&2; \
	    missing=1; \
	  fi; \
	done; \
	if [ -n "$$AWS_SECRET_ACCESS_KEY_ID" ] && [ -z "$$AWS_SECRET_ACCESS_KEY" ]; then \
	  echo "提示: 你设置的是 AWS_SECRET_ACCESS_KEY_ID，正确名是 AWS_SECRET_ACCESS_KEY (没有 _ID 后缀)" >&2; \
	fi; \
	if [ $$missing -ne 0 ]; then \
	  echo "" >&2; \
	  echo "在 WSL bash 里设置示例:" >&2; \
	  echo "  export AWS_ACCESS_KEY_ID='AKIA...'" >&2; \
	  echo "  export AWS_SECRET_ACCESS_KEY='...'" >&2; \
	  echo "  export TF_VAR_api_key=\"\$$(openssl rand -hex 24)\"" >&2; \
	  echo "" >&2; \
	  echo "make notify-help 查看推送通道配置说明" >&2; \
	  exit 1; \
	fi
	@echo "[env] required ok (region=$$AWS_REGION)"
	@anyOpt=0; \
	for v in $(OPTIONAL_ENV); do \
	  if [ -z "$${!v}" ]; then echo "[env] $$v 未设置 (该通道禁用)"; \
	  else echo "[env] $$v 已设置"; anyOpt=1; \
	  fi; \
	done; \
	if [ $$anyOpt -eq 0 ]; then \
	  echo "WARN: 没有配置任何推送通道，命中阈值时不会推送，只能看 CloudWatch Logs" >&2; \
	fi

notify-help:
	@echo "==================================================================="
	@echo "推送通道（任选其一或两者都配，均通过 TF_VAR_* 环境变量传入）"
	@echo "==================================================================="
	@echo ""
	@echo "[选项 A] 飞书自定义群机器人 (推荐：API 干净、卡片美观、免费)"
	@echo "  1. 注册飞书 https://www.feishu.cn (手机号即可，个人版免费)"
	@echo "  2. 飞书 App 建一个群 (自己一个人也可以)"
	@echo "  3. 群设置 (右上角) -> 群机器人 -> 添加机器人 -> 自定义机器人"
	@echo "  4. 设置名字/头像，复制 Webhook，形如:"
	@echo "     https://open.feishu.cn/open-apis/bot/v2/hook/xxxxxxxx-xxxx-xxxx"
	@echo "  5. 在 WSL bash 里设置:"
	@echo "     export TF_VAR_feishu_webhook='https://open.feishu.cn/open-apis/bot/v2/hook/...'"
	@echo ""
	@echo "[选项 B] Server酱 (个人微信，不需要飞书账号)"
	@echo "  1. https://sct.ftqq.com 用 GitHub/微信扫码登录"
	@echo "  2. 关注公众号绑定后获取 SendKey，形如 SCTxxxxxxxxx..."
	@echo "  3. 在 WSL bash 里设置:"
	@echo "     export TF_VAR_serverchan_sendkey='SCTxxx...'"
	@echo ""
	@echo "Server酱免费版每天 5 条上限；飞书无限制 -> 推荐 A。"

build: check-env
	@$(BUILD_LAMBDA)

init:
	@cd terraform && terraform init -input=false -upgrade

apply: build
	@cd terraform && terraform init -input=false && terraform apply -input=false -auto-approve

frontend: check-env
	@cd terraform && \
	  bucket="$$(terraform output -raw frontend_bucket)"; \
	  dist="$$(terraform output -raw cloudfront_distribution_id)"; \
	  cd ../frontend && \
	  if [ ! -d node_modules ]; then npm install; fi && \
	  npm run build && \
	  aws s3 sync dist "s3://$$bucket" --delete && \
	  aws cloudfront create-invalidation --distribution-id "$$dist" --paths "/*" >/dev/null
	@echo "[frontend] deployed"

deploy: apply frontend outputs

outputs:
	@cd terraform && \
	  echo ""; \
	  echo "================================================"; \
	  echo "Frontend  : $$(terraform output -raw frontend_url)"; \
	  echo "API direct: $$(terraform output -raw apigw_direct_url)  (debug only)"; \
	  echo "Region    : $$AWS_REGION"; \
	  echo "================================================"; \
	  echo "手机浏览器打开 Frontend，进设置页只需要填 API Key (TF_VAR_api_key)"

destroy: check-env
	@cd terraform && terraform destroy

clean:
	rm -rf build/lambda build/lambda.zip frontend/dist frontend/.env.production
