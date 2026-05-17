# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

US stock watcher: yfinance → DynamoDB (watchlist + alert state + info cache) → Feishu / Server酱 push, with a React UI served from CloudFront. Two parallel paths share the same domain code:

- **AWS path** ([backend/](backend/), [terraform/](terraform/)): EventBridge Scheduler invokes the monitor Lambda; API Gateway HTTP API behind CloudFront serves the React app at the same origin (no CORS).
- **Local CLI path** ([main.py](main.py)): same library code, but state lives in `state.json` / `metrics_cache.json` and config comes from [config.yaml](config.yaml).

The local CLI and the deployed Lambdas are independent — they do not share state.

## Common commands

All deployment goes through the Makefile, which `source`s [`.env.local`](.env.local.example) (or env vars). Run from WSL bash.

```bash
make deploy        # build Lambda zip + terraform apply + frontend build + S3 sync + CF invalidate
make apply         # rebuild Lambda + terraform apply (use when changing backend/ or terraform/)
make frontend      # rebuild React + S3 sync + CF invalidate (use when changing frontend/)
make build         # only rebuild build/lambda.zip
make outputs       # print Frontend URL and direct API Gateway URL
make destroy       # tear down all AWS resources
make clean         # rm -rf build/ frontend/dist
make check-env     # validate required env vars without doing anything
```

Required env vars: `AWS_ACCESS_KEY_ID`, `AWS_SECRET_ACCESS_KEY`, `TF_VAR_api_key`. Optional push channels: `TF_VAR_feishu_webhook`, `TF_VAR_serverchan_sendkey` (at least one of these or alerts only land in CloudWatch Logs).

**Local CLI:**

```bash
pip install -r requirements.txt
python main.py query ORCL AMZN XLK GOOG    # current quotes
python main.py search "Oracle"             # company name -> ticker
python main.py check                       # one-shot threshold check using config.yaml
python main.py monitor                     # long-running poller
python main.py gainers                     # Top20 gainers >= configured pct
```

**End-to-end deployed test:** [debug.sh](debug.sh) puts a guaranteed-hit watchlist row (`below 99999`), clears the alert state row, invokes the monitor Lambda, then tails CloudWatch logs. Override symbol with `SYMBOL=AMZN ./debug.sh`. Remember to delete the test row from the UI when done.

There are no automated tests in this repo.

## Architecture

### Single Lambda zip, two handlers

[scripts/build_lambda.sh](scripts/build_lambda.sh) copies `src/*.py` **plus** `backend/api_handler.py` and `backend/monitor_handler.py` into one flat directory and zips it. Terraform creates two `aws_lambda_function` resources pointing at the same S3 zip but different handler entrypoints (`api_handler.handler` / `monitor_handler.handler`).

Because src/* is flattened to the zip root, modules import each other **without** the `src.` prefix when running in Lambda. The codebase handles this with a dual-import pattern, e.g. [src/notifier.py:5-8](src/notifier.py#L5-L8):

```python
try:
    from .fetcher import Quote
except ImportError:  # Lambda zip flattens src/* to the root
    from fetcher import Quote
```

The `backend/*_handler.py` files use flat imports (`from fetcher import ...`) because they only ever run inside the Lambda zip. When adding a new shared module, add it to the `cp` line in [scripts/build_lambda.sh](scripts/build_lambda.sh) — files are not picked up automatically.

### LLM-driven pipeline (v2 — replaces v1 threshold flow)

The monitor is now LLM-first. There are **no** threshold/gainer alert cards anymore — every push is an AI verdict card. Pipeline (mirrored in [backend/monitor_handler.py](backend/monitor_handler.py) and [src/monitor.py](src/monitor.py); the local CLI is the JSON-state mirror of the Lambda DDB-state version):

1. **Build candidate pool**: all watchlist items (unless `strategy_horizon="skip"`) + TECH_TICKERS with `|change_pct| ≥ MOVER_CHANGE_PCT_THRESHOLD` (default 3%). Capped to `ADVISOR_MAX_CANDIDATES` (default 30), watchlist always wins, movers ranked by `|change|`.
2. **Batched 3mo history** for the whole pool → `apply_signals` (Williams %R / MACD / KST).
3. For each candidate: per-symbol 1y history (for MA250) + yfinance news → [src/advisor.py](src/advisor.py) `advise()` → Qwen (DashScope OpenAI-compatible API).
4. **Push gate** ([src/advisor.py](src/advisor.py) `should_push(adv, source)`):
   - **watchlist** + `ALWAYS_PUSH_WATCHLIST=1` (default): push regardless of action/confidence — twice-daily briefing.
   - Otherwise: only `action ∈ {buy, sell}` AND `confidence ≥ ADVISOR_PUSH_MIN_CONFIDENCE` (default 0.55). `hold` and low-confidence are silent.
5. Per-symbol 6h cooldown via state table with `kind="advice"`. Daily budget guard (`ADVISOR_DAILY_BUDGET`, default 200).

Card title encodes source: `⭐` = watchlist, `🔍` = mover-discovery (off-watchlist).

When changing pipeline logic, update **both** [backend/monitor_handler.py](backend/monitor_handler.py) and [src/monitor.py](src/monitor.py). They diverge only in storage backend (DDB vs state.json).

The active scheduled handler is `monitor_handler.handler`. `lambda_handler.py` at the repo root is unused legacy and not bundled into the zip.

### Advisor details

- Gated by `DASHSCOPE_API_KEY` (terraform `var.dashscope_api_key`); absent → entire monitor is a no-op.
- Watchlist items: `strategy_horizon ∈ {short, long, skip}` (default `short`); `skip` excludes from the pool entirely. `strategy_notes` (≤200 chars) is passed verbatim into the prompt.
- LLM legs deviating >25% from current price are rejected as hallucination (`_sanity_ok`).
- The advisor uses `requests` only — no Google SDK — to keep the Lambda zip slim.

### Legacy fields

DDB rows from v1 may still have `threshold` / `direction` fields. v2 code ignores them entirely; no migration needed. The frontend no longer shows or edits them.

### Schedule (Beijing time)

[terraform/schedule.tf](terraform/schedule.tf) defines **two** EventBridge schedules together producing every-1.5h firing from 09:00–21:00 Asia/Shanghai (one schedule at integer hours `9,12,15,18,21`, the other at half hours `10:30,13:30,16:30,19:30`). The README's "evening / morning" description is older — trust the .tf file. Fires regardless of US market hours; off-hours, yfinance returns the prior close, which is intentional repeat-reminder behavior.

### Same-origin frontend / API

CloudFront serves the React app at `/` (from S3) and forwards `/api/*` to the API Gateway HTTP API. Therefore:

- Frontend uses relative `/api/...` URLs ([frontend/src/api.js:24](frontend/src/api.js#L24)) — no CORS.
- The Lambda strips the `/api` prefix before routing ([backend/api_handler.py:52-59](backend/api_handler.py#L52-L59)).
- When curling the API Gateway URL directly (debug), there is **no** `/api` prefix.

### DynamoDB tables (created by Terraform)

- `stock-watcher-watchlist` — user-managed symbols (PK `symbol`).
- `stock-watcher-state` — per-symbol-per-kind last alert timestamp (PK `symbol`, but stored as `"{SYMBOL}#{kind}"`).
- `stock-watcher-metrics-cache` — yfinance `info` dict cache with TTL (default 24h) to avoid hammering yfinance.

Lambda env wires these via `WATCHLIST_TABLE` / `STATE_TABLE` / `METRICS_CACHE_TABLE`.

### yfinance gotchas baked into [src/fetcher.py](src/fetcher.py)

- Prices come from `fast_info`; `_last_price` tries multiple key names because yfinance has been renaming them across versions.
- `info` dict is slimmed to `_INFO_FIELDS_TO_CACHE` before caching to keep DDB items small.
- `dividendYield` is sometimes percent (e.g. `2.5`) and sometimes decimal (`0.025`). Heuristic: `>1` is treated as percent and kept as-is. If you touch this, preserve the heuristic.
- All yfinance calls go through `_retry` with exponential backoff.
- For technical indicator enrichment, [src/monitor.py:75-91](src/monitor.py#L75-L91) does **one** batched `yf.download(period="3mo")` for all alerted symbols, not per-symbol calls.

### Lambda zip build pitfalls

- **Don't delete `*.dist-info`** in [scripts/build_lambda.sh](scripts/build_lambda.sh) — `curl_cffi` (a yfinance transitive dep) reads its own version via `importlib.metadata.version()` and crashes at import time without it.
- Wheels are pinned to `manylinux2014_x86_64` + `cp3.12` + `--only-binary=:all:` so the bundle stays compatible with the `python3.12` Lambda runtime regardless of build host. Don't relax these flags.

## Deployment region

Default region is `ap-southeast-1` (Singapore), set in [terraform/variables.tf](terraform/variables.tf) and the Makefile. Some older debug docs reference `-2`; always trust the Makefile / terraform variable.

## Frontend

Vite + React 18 (no router, no state library). Entry: [frontend/src/App.jsx](frontend/src/App.jsx). API key is stored in `localStorage` under `stock-watcher-config` ([frontend/src/api.js:1](frontend/src/api.js#L1)) — `TF_VAR_api_key` must be entered on the settings page on each new browser.
