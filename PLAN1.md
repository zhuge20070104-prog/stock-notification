# PLAN1 — LLM 主导的交易建议（v2，重大架构转向）

## v2 vs v1 — 改了什么

v1 是"阈值告警 + LLM 二次评估"。v2 砍掉阈值，**LLM 是唯一通知入口**：

| 维度 | v1 | v2 |
|---|---|---|
| 通知触发 | 用户设阈值（below/above），命中后 fan_out + 可选 LLM | LLM 评估 → 只有 `action ∈ {buy, sell}` 且置信度够才推 |
| 候选池 | 仅 watchlist | watchlist + 大盘科技股 TopN，发现性扫描 |
| watchlist 字段 | `symbol, threshold, direction, strategy_horizon, strategy_notes` | `symbol, strategy_horizon, strategy_notes`（threshold/direction 弃用） |
| Hold / 低置信 | 不影响 — 还是会发原阈值卡 | **静默** — 不打扰 |
| 飞书卡片 | 1 张原告警 + 0/1 张 AI 卡 | **0 或 1 张 AI 卡** |
| 频次 | 每 1.5h，看运气几条告警 | 每 1.5h，看候选池 + LLM 共识，可能 0 条也可能十几条 |

---

## 目标

每次定时触发：
1. 构造候选池：**watchlist 全部** + **TECH_TICKERS 里有显著异动的**（涨跌幅 ≥ 3% 或 MACD 刚交叉或量比 >2）。
2. 对候选池逐只批量算指标 + 拉新闻 + 调 LLM。
3. LLM 返回 buy/sell/hold + 分批价。
4. 推送规则：
   - `action == "hold"` → **不推**
   - `action ∈ {"buy", "sell"}` 且 `confidence < 0.55` → **不推**
   - 其他 → 推一张 AI 评估卡片
5. 同 symbol cooldown 6h，日 budget 200 兜底。

候选池上限：`MAX_CANDIDATES_PER_RUN = 30`，超出按"watchlist 优先 → 异动幅度排序"截断。

---

## 候选池逻辑

```python
def build_candidates(watchlist_items, movers_pool, info_provider) -> list[Candidate]:
    out = []
    seen = set()

    # 1) Watchlist 全部进，除非 horizon=skip
    for it in watchlist_items:
        sym = it["symbol"].upper()
        if it.get("strategy_horizon") == "skip":
            continue
        out.append(Candidate(symbol=sym, source="watchlist",
                             horizon=it.get("strategy_horizon", "short"),
                             notes=it.get("strategy_notes", "")))
        seen.add(sym)

    # 2) Movers 里满足"显著异动"的进
    for r in movers_pool:
        sym = r["symbol"].upper()
        if sym in seen:
            continue
        if abs(r.get("change_pct", 0)) >= 3.0:
            out.append(Candidate(symbol=sym, source="mover",
                                 horizon="short", notes=""))
            seen.add(sym)

    # 3) 截断
    if len(out) > MAX_CANDIDATES:
        # watchlist 全保留；mover 按异动幅度排序后取剩余 quota
        wl = [c for c in out if c.source == "watchlist"]
        mv = sorted(
            [c for c in out if c.source == "mover"],
            key=lambda c: -abs(_change_pct.get(c.symbol, 0))
        )
        out = wl + mv[: max(0, MAX_CANDIDATES - len(wl))]
    return out
```

`Candidate` 记录最终来源（watchlist / mover），便于推送时给用户标"⭐ 关注列表" / "🔍 异动发现"。

---

## 推送规则（取代旧 threshold/gainer 渲染）

```python
PUSH_MIN_CONFIDENCE = 0.55   # env: ADVISOR_PUSH_MIN_CONFIDENCE

def should_push(adv: Advice) -> bool:
    if adv.action == "hold":
        return False
    if adv.confidence < PUSH_MIN_CONFIDENCE:
        return False
    return True
```

飞书卡片完全用 `render_advice`（v1 已实现），只在 title 末尾加来源标记：

```
🤖 AI BUY  NOW (72%)  ⭐
🤖 AI SELL ORCL (66%)  🔍
```

⭐ = watchlist，🔍 = 异动发现（不在 watchlist）。

---

## Dead code（可删 / 可留）

- `render_threshold_alert`, `render_gainer_alert` — 可删；为保守先保留但调用点全砍。
- monitor 路径里的 `_hit()`、`threshold` 分支 — 删。
- `top_gainers_above()` — 改为复用 `top_movers()` 统一取异动，删 `top_gainers_above`。
- `kind="threshold"` / `kind="gainer"` 的 state 记录 — DDB 里旧记录留着，新代码不再写入也不再读取（dedupe 只看 `kind="advice"`）。
- watchlist 表里旧的 `threshold`/`direction` 字段 — 后端读时 `.get()` 兜底，前端不再展示/编辑。**无需 migration**。

---

## 频次与成本估算

- 每次定时触发：候选池 ≤ 30 → ≤ 30 次 Gemini call
- EventBridge 9:00–21:00 每 1.5h，9 次/天 → ≤ 270 次/天
- Gemini 2.0 Flash 免费档：1500 RPD，**够用 5.5×**

如果某天异动密集：候选池 cap=30 是硬上限。再叠加 6h 同 symbol cooldown，单日推送上限大约 `30 × (24/6) = 120` 张卡，可控。

---

## 涉及改动文件

### 后端 — 主管道重写
- [backend/monitor_handler.py](backend/monitor_handler.py) — `_run()` 改成新管道：build_candidates → quote/indicators → LLM → push 过滤。砍掉所有 threshold / gainer 分支。
- [src/monitor.py](src/monitor.py) — 同步改本地 CLI 版本。

### Advisor 微调
- [src/advisor.py](src/advisor.py) — 已有 `Advice` 和 `advise()`，添加 `should_push(adv)` helper。

### Notifier
- [src/notifier.py](src/notifier.py) — `render_advice` 加 source 后缀（⭐ / 🔍）。

### API / Store
- [backend/api_handler.py](backend/api_handler.py) — `POST /watchlist` 不再要求 `threshold`/`direction`，接受空。
- [src/store.py](src/store.py) — `upsert_watchlist` 把 threshold/direction 设为完全可选。

### 前端
- [frontend/src/App.jsx](frontend/src/App.jsx)：
  - `SearchRow`：去掉阈值/方向选择，加 horizon 下拉（短/长/不评估）+ notes 文本框。
  - `WatchCard`：去掉阈值显示和编辑，加 horizon/notes 编辑。
  - `MoverRow`："加入关注"按钮直接添加（horizon=short）。

### Terraform / env
- [terraform/variables.tf](terraform/variables.tf) — 新增 `advisor_push_min_confidence`（默认 0.55）和 `advisor_max_candidates`（默认 30）；保留 `gainer_pct_threshold` 改名/复用为 `mover_change_pct_threshold`（默认 3.0）。
- [terraform/lambda.tf](terraform/lambda.tf) — 透传新 env：
  - `ADVISOR_PUSH_MIN_CONFIDENCE`
  - `ADVISOR_MAX_CANDIDATES`
  - `MOVER_CHANGE_PCT_THRESHOLD`
  - 删 `ENABLE_GAINER_ALERTS` / `GAINER_PCT_THRESHOLD` / `GAINER_POOL_SIZE`（功能被合并），或先留着不读，下次清理。

---

## 落地步骤

### Step A — 主管道重写（核心）
1. `backend/monitor_handler.py` 重写 `_run()`：
   - 删 `_Alert` 与 threshold / gainer 双分支
   - 新增 `_build_candidates()` + `Candidate` dataclass
   - 单 for 循环：quote → enrich indicators → advise → 过滤 → push
2. `src/monitor.py` 同步重写 `check_once()`
3. 注意 `_enrich_with_indicators` 现在返回 `{sym: df_3mo}`（v1 已改），继续复用

### Step B — 推送过滤
1. `src/advisor.py` 添加 `should_push(adv) -> bool`
2. `src/notifier.py` `render_advice(q, adv, source="watchlist" | "mover")` 加来源后缀
3. 调用方传 source

### Step C — Watchlist 简化
1. `backend/api_handler.py` 的 POST /watchlist 不再校验 threshold/direction
2. `src/store.py` 的 `upsert_watchlist` 让 threshold/direction 默认 None
3. `frontend/src/App.jsx`：
   - SearchRow / WatchCard：阈值 UI 删，换成 horizon + notes
   - MoverRow：直接添加按钮
4. `frontend/src/api.js` 的 `upsert()` 参数对齐

### Step D — env / terraform
1. variables.tf 加新变量
2. lambda.tf 透传
3. .env.local.example 加 `TF_VAR_advisor_push_min_confidence`（可选）

### Step E — 文档
1. CLAUDE.md 更新 Architecture 段：阈值流程已删，advisor 是唯一通知入口
2. README.md 同步说明（如有）

### Step F — 旧 schedule 调整（可选，不做也不影响）
[terraform/schedule.tf](terraform/schedule.tf) 还是每 1.5h，本期不动。

---

## 风险与权衡

1. **过度推送**：LLM 一开心可能给一堆"buy 信号"。三层兜底：候选池 cap 30 + 置信度 ≥0.55 + 同 symbol 6h cooldown。日上限约 120 张卡。
2. **过度静默**：LLM 全说 hold 就一条不发。这是设计意图，不是 bug。极端市场（横盘）确实可能一天 0 推送，可以接受。
3. **发现性的噪音**：异动 ≥3% 进候选，但 LLM 看完可能全 hold。属于正常过滤，免费档配额够。
4. **watchlist 字段迁移**：旧表里 threshold/direction 字段留着不删；前端不渲染。未来真要清理可手写一次性脚本。
5. **回测仍未做**：v1 风险点 5 依旧成立 — 本系统只生成建议，不验证胜率。
