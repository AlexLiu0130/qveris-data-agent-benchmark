# QVeris Benchmark Arena：当前 Runner 后端交接（给 Claude Code 前端）

> 事实源：`src/qveris_benchmark/run_backend.py`、`src/qveris_benchmark/benchmark_scorer.py`、`src/qveris_benchmark/arena_http.py` 及对应测试。本文描述**当前已实现的本地只读投影**。

## 0. 先读这一节：范围与不可假设的能力

当前后端是一个串行、可恢复的 Benchmark Run 执行记录器，加确定性 Scorer 和本地只读 HTTP/SSE 投影。它可展示执行状态、四项已评分指标、coverage、eligibility 与确定性排名；真实 Provider、认证和生产部署仍未实现。`v2_compiler` 已将 v0.2 candidate 与 Oracle v2 编译为 300-case Run Manifest template 和 `oracle-bundle/v2`；补入真实 Variant 身份及 realtime reference contract 后才可运行。`v1` Manifest / `oracle-bundle/v1` 仅保留 legacy 兼容。

```mermaid
flowchart LR
  M[冻结 Manifest] --> J[私有 manifest.json + events.jsonl]
  J --> R[RunService: 单次 GET 执行]
  R --> X[BenchmarkScorer: 冻结 Policy + Oracle]
  X --> S[公开 Snapshot 投影]
  S --> H[本地只读 HTTP / SSE]
  H --> F[前端：显示服务端投影]
```

前端可做：读取、按 `stable_display_order` 展示、根据公开 `status`/执行计数显示进度、重连 SSE、在 Sheet 中显示已投影的 Variant 字段。

前端不可做：调用 Runner、创建/修改/删除 Run，或计算/补造四项指标、Case Pass Rate、coverage、eligibility、rank。它只能显示服务端已投影值；缺失、`null`、`UNSCORED` 必须原样展示为未提供。

## 1. 本地启动与网络边界

要求 Python **3.11**。`--root` 是私有 `RunStore` 根目录；目录不存在时会以私有权限创建，可以为空。HTTP CLI 会以空 `clients={}` 启动，因此只能读取既有 Run（或返回空列表），不能创建或执行 Run。

```bash
PYTHONPATH=src python3.11 -m qveris_benchmark.arena_http \
  --root /private/tmp/qveris-runs \
  --host 127.0.0.1 \
  --port 8765 \
  --allowed-origin http://localhost:5173
```

- `--host` 默认 `127.0.0.1`，只允许 `localhost` 或可解析的 loopback IP；`0.0.0.0` 等非 loopback 会在启动前拒绝。它是**无认证**服务，不能暴露到局域网/公网。
- `--port` 默认 `0`（系统分配随机端口）；示例固定为 `8765`，仅供本地开发。
- `--allowed-origin` 可省略。设置后，只有请求 `Origin` 与其**完全相等**时才返回 `Access-Control-Allow-Origin` 和 `Vary: Origin`；没有通配符、credentials、methods 或 header CORS 声明。
- 所有 JSON 与 SSE 响应均为 `Cache-Control: no-store`。前端不要持久缓存数据快照；内存中的最后安全 Snapshot 只可用于断线时的 stale 展示。
- 所有 API 都是 GET。浏览器应使用简单 GET / `EventSource`，不要主动加鉴权或自定义 header，以免触发当前未实现的 CORS preflight。

## 2. Runner 的真实执行合同

一个 Run 的 durable 顺序是：

`manifest → run_started journal →（realtime 时 reference before）→ dispatch_intent → 单次 GET → terminal →（realtime 时 reference after）→ snapshot`。

- 可执行 Run Manifest 必须为 `diagnostic` 或 `official`，含 2–8 个 Variant 且 `stable_display_order` 唯一；当前只支持 `concurrency: 1`。编译前的 `runner-run-manifest-template/v2` 合法使用 `variants: []`，但不是可执行 Run Manifest。
- 三个 Suite 固定为 `realtime_quote`、`historical_price`、`financial_statements`。`v1` legacy official Manifest 必须三套各 100 case，且每套严格为 80 normal / 20 boundary。编译后的 `v2` Manifest 仍要求每套 100 case，但状态分布由冻结 Manifest 显式给出：财报 88 success / 12 非 success、历史行情 82 / 18、实时行情 90 / 10；前端不得自行推导资格判断。
- 每个 `variant × case × trial(=1)` cell 最多一次 GET。没有重试、fallback、Search、Inspect、并发或额外模型调用。
- 每次 GET 有 Manifest 的 `timeout_ms` 外层 POSIX `SIGALRM` 超时。运行必须在 Python 主线程；timeout / client exception 均会写入 terminal 事实，而不会重试。
- `realtime_quote` 先后各做一次已冻结合同的 reference hook。before 或合同失败会阻止 GET；after 失败会使该 cell/run `incomplete`。
- 崩溃恢复：若已写 `dispatch_intent` 却没有 terminal，恢复时不会重发 GET，而会记为 `uncertain/recovery_uncertain`。Run 的事件序列和 SHA-256 hash chain 必须连续、不可篡改。

## 3. 磁盘结构与隐私边界

`--root` 下每个 Run 是一个安全 opaque `run_id` 目录：

```text
<root>/
  <run_id>/                 # 0700
    manifest.json           # canonical JSON, 0600, 私有
    events.jsonl            # append-only canonical JSONL, 0600, hash chain
    score-events.jsonl      # append-only scorer evidence, 0600, hash chain
    score-projection.json   # scorer public projection, 0600
    snapshot.json           # execute 后原子写入, 0600（读取会即时重建）
    .lock                   # 首次执行取得文件锁时创建
```

存储拒绝符号链接和非普通文件，Manifest/Journal 使用 canonical JSON、严格 sequence 与 hash chain 校验。HTTP 层再次执行白名单与敏感键检查；若投影有未知字段或疑似凭据/原始载荷，接口返回 `500 {"error":"unsafe_projection"}`，不会静默泄漏或静默删字段。

前端绝不读取磁盘文件。前端也绝不期望得到或记录：凭据、token、Authorization/header、idempotency key、prompt、raw usage、raw response、oracle、tool 参数、provider payload、内部 trace、完整 journal hash/cell/attempt 标识。它们不是公共 API 合同。

## 4. HTTP 路由（准确到当前实现）

共同规则：JSON 成功/错误均为 UTF-8 `application/json; charset=utf-8`、`Cache-Control: no-store`；任何 `POST`、`PUT`、`PATCH`、`DELETE` 返回 `405 {"error":"method_not_allowed"}` 和 `Allow: GET`。未匹配路径为 `404 {"error":"not_found"}`。Run/Variant id 含 `/`、`\\`、控制字符、`?` 或 `#` 等是非法输入；因 URL 解析会先影响路由，前端应只把它视为 `400 bad_request` **或** `404 not_found`，不能依赖固定为 400。

| Method / path | 成功响应 | 错误与注意事项 |
|---|---|---|
| `GET /v1/arena/runs` | `200`，`{schema_version:"arena-read/v1",runs:[RunSummary]}` | `500 unsafe_projection` 若 Store 投影不安全。|
| `GET /v1/arena/runs/:runId/snapshot` | `200`，`RunSnapshot` | 未知 Run `404 not_found`；不安全投影 `500`。|
| `GET /v1/arena/runs/:runId/variants/:variantId` | `200`，`{schema_version,run_id,variant}` | Run 或 Variant 不存在 `404 not_found`。`variant` 是白名单挑出的 Variant，不是原始 Run 对象。|
| `GET /v1/arena/runs/:runId/events?after=N` | `200 text/event-stream; charset=utf-8` | `N` 必须是 ASCII 十进制非负整数；重复 `after`、负数或非数字为 `400 bad_request`。详见第 6 节。|

`Last-Event-ID` 优先于 query `after`。无 `Last-Event-ID` 且无 `after` 时基线为 `0`。不存在 `OPTIONS` 路由；不要把它当作跨域生产 API。

`GET /runs` 的最小真实 wrapper 如下。数组当前由 `RunService.list_runs()` 按 `run_id` 字典升序生成；前端必须保持服务端顺序，不能自行按时间或状态重排。

```json
{
  "schema_version": "arena-read/v1",
  "runs": [
    {
      "schema_version": "qveris-run-snapshot/v1",
      "run_id": "run-demo",
      "manifest_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
      "status": "queued",
      "internal_status": "queued",
      "projection_status": "UNSCORED",
      "projection_reason": "scorer_projection_unavailable",
      "snapshot_sequence": 1,
      "event_cursor": 1,
      "updated_at": 1.0,
      "connection_basis": "durable_event_journal"
    }
  ]
}
```

## 5. 当前真实 JSON 合同

以下表格是 `RunService` 现在实际生成的字段，而不是画稿中可能出现的字段。数值都是 JSON number/int；时间没有 ISO 字符串保证。

### 5.1 `RunSummary`

`GET /runs` 的每个元素只有下列字段，不含 `variants`、`cells` 或 metrics：

| 字段 | 类型 / 可空 | 当前取值 |
|---|---|---|
| `schema_version` | string | `qveris-run-snapshot/v1` |
| `run_id` | string | Manifest 安全 opaque id |
| `manifest_hash` | string | 64 位小写 SHA-256 |
| `status` | `queued \| running \| completed \| incomplete` | `completed` 只表示已有 `SCORED` 且有排名；其他终态为 `incomplete`，见第 7 节 |
| `internal_status` | string | `queued \| running \| execution_complete \| execution_failed \| incomplete` |
| `projection_status` | `UNSCORED \| SCORED \| SCORED_NOT_RANKED` | 是否有验证过的 Scorer projection |
| `projection_reason` | string | `scorer_projection_unavailable` 或 `score_projection_available` |
| `snapshot_sequence` / `event_cursor` | non-negative integer | 当前 durable event sequence |
| `updated_at` | number \| null | 最后一条 durable event 的 `emitted_at`；正常由 `create_run` 创建后为 number |
| `connection_basis` | string | `durable_event_journal` |

### 5.2 `RunSnapshot`

`GET /snapshot` 返回上述 Run 字段，加上：

| 字段 | 类型 / 可空 | 说明 |
|---|---|---|
| `variants` | `Variant[]` | 按 `stable_display_order` 升序；前端必须保留该顺序，不得按结果重排。|
| `cells` | `Cell[]` | 每个 Variant × case 一条，trial 恒为 `1`。|
| `execution` | `Execution` | 当前执行事实计数。|
| `scoring` | `ScoringState` | 四项指标/coverage 已评分时为 `SCORED`；否则为 `UNSCORED`。|

```json
{
  "schema_version": "qveris-run-snapshot/v1",
  "run_id": "run-demo",
  "manifest_hash": "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
  "status": "incomplete",
  "internal_status": "execution_complete",
  "projection_status": "UNSCORED",
  "projection_reason": "scorer_projection_unavailable",
  "snapshot_sequence": 4,
  "event_cursor": 4,
  "updated_at": 1.0,
  "connection_basis": "durable_event_journal",
  "variants": [
    {
      "variant_id": "variant-a",
      "stable_display_order": 1,
      "suites": {
        "realtime_quote": {"completed": 0, "total": 0, "success": 0, "failed": 0, "incomplete": 0, "blocked": 0},
        "historical_price": {"completed": 1, "total": 1, "success": 1, "failed": 0, "incomplete": 0, "blocked": 0},
        "financial_statements": {"completed": 0, "total": 0, "success": 0, "failed": 0, "incomplete": 0, "blocked": 0}
      }
    }
  ],
  "cells": [{"variant_id": "variant-a", "case_id": "case-historical", "trial": 1, "state": "success"}],
  "execution": {"total": 1, "completed": 1, "success": 1, "failed": 0, "incomplete": 0, "blocked": 0},
  "scoring": {
    "semantic_accuracy": "UNSCORED",
    "data_accuracy": "UNSCORED",
    "end_to_end_latency": "UNSCORED",
    "token_usage": "UNSCORED",
    "coverage": null,
    "rank": null,
    "eligibility": null
  }
}
```

示例只使用真实 Snapshot 字段：它没有 `agent_name`、模型版本、Case Pass Rate、coverage 值或正式 rank，因此 UI 应显示 `Unknown / Not available`，不能自造。

### 5.3 嵌套对象

| 对象 | 精确字段 | 枚举 / 约束 |
|---|---|---|
| `Variant` | 基础 `variant_id`、`stable_display_order`、`suites`；评分后另有 `metrics`、`case_pass_rate`、coverage、`eligibility`、可选 `ineligible_reason`/`rank` | `SuiteName` 恒为三个固定 Suite；只显示实际存在字段。|
| `SuiteExecution` | `completed,total,success,failed,incomplete,blocked: integer` | 非负计数；`total` 可为 0。|
| `Cell` | `variant_id:string`、`case_id:string`、`trial:1`、`state:string` | `state` 为 `queued \| success \| failed \| incomplete \| blocked`。|
| `Execution` | `total,completed,success,failed,incomplete,blocked: integer` | 非负计数。`​completed = success + failed + incomplete + blocked`。|
| `ScoringState` | `semantic_accuracy,data_accuracy,end_to_end_latency,token_usage,coverage,rank,eligibility` | `UNSCORED` 时前四项为 `UNSCORED`、后三项为 `null`；评分后前五项为 `SCORED`，`rank`/`eligibility` 仅 `SCORED` 有值。|

### 5.4 Variant detail 和 HTTP 白名单的边界

Variant detail 的最小真实 wrapper 是：

```json
{
  "schema_version": "qveris-run-snapshot/v1",
  "run_id": "run-demo",
  "variant": {
    "variant_id": "variant-a",
    "stable_display_order": 1,
    "suites": {
      "realtime_quote": {"completed": 0, "total": 0, "success": 0, "failed": 0, "incomplete": 0, "blocked": 0},
      "historical_price": {"completed": 1, "total": 1, "success": 1, "failed": 0, "incomplete": 0, "blocked": 0},
      "financial_statements": {"completed": 0, "total": 0, "success": 0, "failed": 0, "incomplete": 0, "blocked": 0}
    }
  }
}
```

评分完成后，`variant` 真实包含严格投影的 `metrics`、`case_pass_rate`、`semantic_oracle_coverage`、`oracle_coverage`、`receipt_coverage`、`completeness_reasons`、`eligibility`、可选 `ineligible_reason` 和 `rank`。HTTP 会拒绝未知嵌套字段及任何 oracle/raw response/raw usage；前端也不得尝试访问或重算它们。

### 5.5 已评分 Variant 与榜单投影

Scorer 只在 Run `run_finished` 后，对 approved 的冻结 Policy/Oracle bundle 评分。当前 v2 Run 使用 `oracle-bundle/v2`；`oracle-bundle/v1` 仅用于 legacy Run。四项指标及结构如下；所有数值、coverage 和名次均为服务端结果，前端不得重算。

```json
{
  "projection_status": "SCORED",
  "variant": {
    "metrics": {
      "semantic_accuracy": {"passed": 1, "denominator": 1, "value": 1.0},
      "data_accuracy": {"passed_weight": 1.0, "eligible_weight": 1.0, "value": 1.0},
      "end_to_end_latency": {"count": 1, "raw_count": 1, "p50_ms": 12.3, "p95_ms": 12.3, "max_ms": 12.3, "timeout_rate": 0.0},
      "token_usage": {"count": 1, "receipt_coverage": 1.0, "input_mean": 2.0, "input_p50": 2.0, "input_p95": 2.0, "output_mean": 3.0, "output_p50": 3.0, "output_p95": 3.0, "total_mean": 5.0, "total_p50": 5.0, "total_p95": 5.0}
    },
    "case_pass_rate": {"passed": 1, "denominator": 1, "value": 1.0},
    "semantic_oracle_coverage": {"available": 1, "denominator": 1, "value": 1.0},
    "oracle_coverage": {"available": 1, "denominator": 1, "value": 1.0},
    "receipt_coverage": {"available": 1, "denominator": 1, "value": 1.0},
    "completeness_reasons": [], "eligibility": "eligible", "rank": 1
  }
}
```

`SCORED` 表示至少一个 eligible Variant 已依固定顺序排名。`SCORED_NOT_RANKED` 表示评分已完成但没有可排名 Variant（例如 policy 没有 ranking，或 coverage/receipt/execution gate 不通过）；其 Variant 为 `not_ranked` 或 `ineligible`，可带 `ineligible_reason`。Runner 当前将 projection 的 `ranked_results` / `ineligible_results` 保存在已验证 score projection 中；前端应以 Variant 的 `rank`/`eligibility` 为显示合同，不要求它们出现在 Snapshot 顶层。

## 6. SSE 合同与前端重连算法

SSE 是一次性本地 stream：响应 `Connection: close`。它不是 websocket，也不承诺永久订阅。

- **首次连接**：`GET .../events`（没有 `Last-Event-ID`/`after`）先发一个带 `id: snapshot_sequence` 的 `event: snapshot`，data 为完整安全 Snapshot，并把该序列作为基线。此前历史事件不再逐条重放。
- **重连**：发送 `Last-Event-ID: <最后接受的 sequence>`（优先）或 `?after=<sequence>`。服务不会发 snapshot；只发 sequence 连续且大于该值的 durable events。
- **普通 event envelope**：`id: <sequence>\nevent: <event_type>\ndata: <JSON>\n\n`。Runner event type 为 `run_started`、`dispatch_intent`、`reference_before`、`terminal`、`reference_after`、`reference_after_unavailable`、`run_finished`；评分完成后追加连续的 synthetic `scorer_projection`。其公开 data 仅含 `run_id`、`projection_status` 及实现提供时的 `projection_hash`，不含 Oracle、score record、raw usage 或 response。收到它后重拉 Snapshot，不从 SSE 重建指标。
- **heartbeat**：活跃 Run 无新事件时会写 `: heartbeat\n\n`，默认间隔 1 秒（`make_server` 参数；CLI 未暴露）。它没有 id，也不是业务事件。
- **缺口/非法未来 cursor**：若 `after > current` 或后续事件 sequence 不连续，服务只发无 `id` 的 `event: resync_required`，data 为 `{"snapshot_url":"/v1/arena/runs/<runId>/snapshot"}`，然后关闭。不要猜测或补事件。
- **终态关闭**：HTTP adapter 为未来兼容会在投影 `status=failed` 或 `status=incomplete` 时关闭；但当前 Runner 的公共终态只会是 `incomplete`（即使 internal 是 `execution_complete` 或 `execution_failed`）。前端应以 `internal_status` 和 `execution` 解释执行结果。任何 close 后先拉 Snapshot，不能把 close 解释为成功。

建议算法（不需要额外依赖）：以下固定上限与退避是**前端实现策略，不是后端 SLA**。它取消浏览器的自动重连，最多连续尝试 5 次；达到上限只显示 stale/offline，并由用户操作重新开始。

```ts
let cursor = 0;
let snapshot: RunSnapshot | null = null;
let stopped = false;
let reconciling = false;
let retryCount = 0;
let retryTimer: number | null = null;

const MAX_RECONNECTS = 5;
const BASE_BACKOFF_MS = 250;
const MAX_BACKOFF_MS = 4_000;

async function resync(): Promise<RunSnapshot> {
  snapshot = await getJson<RunSnapshot>(`/v1/arena/runs/${runId}/snapshot`);
  cursor = snapshot.event_cursor;
  render(snapshot, { connection: "live" });
  return snapshot;
}

function queueReconnect() {
  if (stopped || retryTimer !== null) return;
  if (retryCount >= MAX_RECONNECTS) {
    render(snapshot, { connection: snapshot ? "stale" : "offline" });
    return;
  }
  const delay = Math.min(BASE_BACKOFF_MS * 2 ** retryCount, MAX_BACKOFF_MS);
  retryCount += 1;
  render(snapshot, { connection: "reconnecting", retryInMs: delay });
  retryTimer = window.setTimeout(() => {
    retryTimer = null;
    openEvents(true);
  }, delay);
}

async function reconcileAfterClose(es: EventSource) {
  es.close(); // 禁用 EventSource 内建的无界自动重连。
  if (stopped || reconciling) return;
  reconciling = true;
  try {
    const fresh = await resync(); // error、终态 close、resync_required 都先校正 Snapshot。
    if (fresh.status === "incomplete") {
      stopped = true;
      render(fresh, { connection: "closed" });
      return;
    }
    if (fresh.status === "queued" || fresh.status === "running") {
      queueReconnect();
      return;
    }
    // 防御未来 HTTP adapter 的 failed 等终态；当前 Runner 不会产生它们。
    stopped = true;
    render(fresh, { connection: "closed" });
  } catch {
    queueReconnect();
  } finally {
    reconciling = false;
  }
}

function openEvents(reconnecting = false) {
  if (stopped) return;
  const suffix = reconnecting ? `?after=${encodeURIComponent(String(cursor))}` : "";
  const es = new EventSource(`/v1/arena/runs/${runId}/events${suffix}`);
  es.onopen = () => { retryCount = 0; };
  es.addEventListener("snapshot", (event) => {
    const message = event as MessageEvent<string>;
    snapshot = JSON.parse(message.data) as RunSnapshot;
    cursor = snapshot.event_cursor;
    render(snapshot, { connection: "live" });
  });
  es.addEventListener("resync_required", () => { void reconcileAfterClose(es); });
  for (const name of ["run_started", "dispatch_intent", "reference_before", "terminal", "reference_after", "reference_after_unavailable", "run_finished"]) {
    es.addEventListener(name, async (event) => {
      cursor = Number((event as MessageEvent<string>).lastEventId);
      // 事件不是完整状态或评分输入；以 Snapshot 为真源。
      const fresh = await resync();
      if (fresh.status === "incomplete") {
        stopped = true;
        es.close();
        render(fresh, { connection: "closed" });
      } else if (fresh.status !== "queued" && fresh.status !== "running") {
        stopped = true;
        es.close();
        render(fresh, { connection: "closed" });
      }
    });
  }
  es.onerror = () => {
    void reconcileAfterClose(es);
  };
}

openEvents();
```

`EventSource` 不能由浏览器 API 设置 `Last-Event-ID`；上面的 query `after` 是当前可行做法。若你改用自管 fetch stream，可用 `Last-Event-ID`，但不要同时相信不同 cursor。

## 7. 状态语义

| 字段 | 它表达什么 | UI 处理 |
|---|---|---|
| `status=queued/running` | Run 尚未形成终态投影 | 显示运行/等待；保留稳定卡片位置。|
| `status=completed` | 已有 `SCORED` 且存在 ranked result。 | 可以显示服务端 `rank`，仍不可前端排序或重算。|
| `status=incomplete` | 执行不完整/失败，或评分已完成但没有可排名结果。 | 与 `internal_status` 和 `projection_status` 一起解释；不能自行补 winner。|
| `internal_status=execution_complete` | 所有当前 cell execution 成功/blocked。 | 只有 Scorer projection 才能证明 rankable 或 eligible。|
| `internal_status=execution_failed` | 至少一个执行失败、没有 incomplete。 | 与公开 `incomplete` 一起解释为执行失败；详情用 `execution.failed`。|
| `projection_status=UNSCORED` | Scorer projection 不可用。 | 显示 `Unscored`，不以 0% 或空白排名代替。|
| `projection_status=SCORED` | 完整评分且至少一个 Variant 可依固定后端规则排名。 | 显示已投影 metric、coverage、eligibility 与 rank。|
| `projection_status=SCORED_NOT_RANKED` | 评分已完成，但没有 rankable Variant。 | 显示 metric 和不可排名原因，不显示名次或获胜者。|
| `coverage/rank/eligibility=null` | `UNSCORED` 时该值尚未提供。 | 显示 `Unknown / Not available`，不计算。|

`execution.success / execution.total` 从不等于 Case Pass Rate。仅 `SCORED` 才可显示 Leaderboard，`SCORED_NOT_RANKED` 是明确的已评分空榜状态。

## 8. 建议的客户端镜像类型（以服务端 schema 为准）

以下是无需依赖的 TypeScript 便利类型；**客户端镜像类型，以服务端 schema 为准**。不要把 optional 白名单字段升级成必填业务合同。

```ts
type SuiteName = "realtime_quote" | "historical_price" | "financial_statements";
type PublicRunStatus = "queued" | "running" | "completed" | "incomplete";
type InternalRunStatus = "queued" | "running" | "execution_complete" | "execution_failed" | "incomplete";
type CellState = "queued" | "success" | "failed" | "incomplete" | "blocked";
type Counts = { completed: number; total: number; success: number; failed: number; incomplete: number; blocked: number };
type Ratio = { available: number; denominator: number; value: number | string | null };
type MetricRatio = { passed: number; denominator: number; value: number | string | null };
type Variant = {
  variant_id: string; stable_display_order: number; suites: Record<SuiteName, Counts>;
  metrics?: {
    semantic_accuracy: MetricRatio;
    data_accuracy: { passed_weight: number | string; eligible_weight: number | string; value: number | string | null };
    end_to_end_latency: { count: number; raw_count: number; p50_ms: number | null; p95_ms: number | null; max_ms: number | null; timeout_rate: number | string | null };
    token_usage: { count: number; receipt_coverage: number | string | null; input_mean: number | null; input_p50: number | null; input_p95: number | null; output_mean: number | null; output_p50: number | null; output_p95: number | null; total_mean: number | null; total_p50: number | null; total_p95: number | null };
  };
  case_pass_rate?: MetricRatio; semantic_oracle_coverage?: Ratio; oracle_coverage?: Ratio; receipt_coverage?: Ratio;
  completeness_reasons?: string[]; eligibility?: "eligible" | "ineligible" | "not_ranked"; ineligible_reason?: string; rank?: number;
};
type RunBase = {
  schema_version: "qveris-run-snapshot/v1"; run_id: string; manifest_hash: string;
  status: PublicRunStatus; internal_status: InternalRunStatus;
  projection_status: "UNSCORED" | "SCORED" | "SCORED_NOT_RANKED"; projection_reason: "scorer_projection_unavailable" | "score_projection_available";
  snapshot_sequence: number; event_cursor: number; updated_at: number | null;
  connection_basis: "durable_event_journal";
};
type RunSnapshot = RunBase & {
  variants: Variant[];
  cells: Array<{ variant_id: string; case_id: string; trial: 1; state: CellState }>;
  execution: Counts;
  scoring: { semantic_accuracy: "UNSCORED" | "SCORED"; data_accuracy: "UNSCORED" | "SCORED"; end_to_end_latency: "UNSCORED" | "SCORED"; token_usage: "UNSCORED" | "SCORED"; coverage: null | "SCORED"; rank: null | "SCORED"; eligibility: null | "SCORED" };
};
```

## 9. 给 Claude Code 的实施清单与验收

1. 先读本文和 Stitch MCP 当前 Screen；把其视觉字段分成“已有服务字段”“可选未来字段”“不能实现的评分字段”。
2. 配置 API base（本地为 `http://127.0.0.1:8765`），只调用第 4 节四条 GET route；不改后端、不加 mock API 伪装成真实后端。
3. 初始 `GET /runs`：loading skeleton、空数组的 no-run、选择 Run 后 `GET /snapshot`。按 `stable_display_order` 渲染且永不根据执行结果重排。
4. 对 running/queued Run 接 SSE；使用第 6 节算法。任何 durable event 以 Snapshot 重拉为准；识别 `resync_required`。
5. 实现状态：loading、empty/no-run、live、reconnecting、stale（有旧 Snapshot 但 refresh 失败）、offline（无 Snapshot 且请求失败）、incomplete、execution failure（`internal_status=execution_failed`）。
6. Leaderboard/metrics UI 在 `UNSCORED` 时显示空状态；在 `SCORED_NOT_RANKED` 时显示已评分但无名次；仅在 `SCORED` 时显示服务端的 rank。Variant detail 只显示确实存在的字段，绝不前端重算。
7. 前端验收：
   - mocked `RunSnapshot` 验证 stable order、三个 Suite、五种 Cell state 与 `UNSCORED/null` 显示；
   - SSE initial snapshot、正常 `after` 重连、heartbeat 忽略、gap → snapshot resync、terminal close → snapshot 刷新；
   - 404、400、500、网络错误分别有可见可恢复状态；
   - 不发 POST/PUT/PATCH/DELETE，不依赖 CORS wildcard，不在 localStorage 写 API 原始数据；
   - 可访问性：网络状态文本、键盘可打开/关闭 detail Sheet、focus 管理、颜色之外有文字状态。

## 10. 已知缺口（不要声称 production-ready）

- 确定性 Scorer、四指标、coverage、eligibility 和固定排名已实现；Policy/Oracle 必须 approved 并与执行 journal 绑定。正式 300-case 冻结与参排运营仍未完成。
- 真实 Agent/Provider、Oracle 与正式 300-case run 未实现或未接入该 Arena HTTP 服务。
- HTTP/SSE 没有 auth、tenant 隔离、TLS、production runtime、部署、观测/告警或浏览器跨域部署合同。
- 当前 API/SSE 仅为本地只读投影；它不能控制 Run，也不代表真实 Provider 已接入、生产授权或生产可用性。

## 11. 可直接粘贴给 Claude Code 的首轮任务

```text
请在当前前端仓库实现 QVeris Benchmark Arena 的只读界面。

先完整阅读 docs/runner-backend-frontend-handoff.md，并通过 Stitch MCP 阅读当前 QVeris Benchmark Arena Screen。然后只做以下工作：
1. 检查当前前端技术栈、路由、样式系统和未提交改动；
2. 将 Stitch 的视觉映射到交接文档中已存在的 RunSnapshot / SSE 合同；
3. 实现本地只读 GET + EventSource 前端，不修改后端、不新增后端接口、不安装不必要依赖；
4. 处理 loading、empty、live、reconnecting、stale、offline、incomplete 和 execution_failed；
5. 按 stable_display_order 固定展示 Variant，SSE gap 时重新拉 Snapshot。

硬约束：
- 不计算、不推测或重排四指标、Case Pass Rate、coverage、eligibility、rank 或 winner；仅显示服务端在 `SCORED` / `SCORED_NOT_RANKED` 投影的值。`UNSCORED/null` 必须原样表示未提供。
- manifest 私有记录每个 Variant 的 agent/get/model 标识、版本和 model config digest；它们用于 journal/Scorer 绑定，不属于 Arena 公共投影。adapter evidence 只是可信本地 adapter 的自述，不能证明真实 runtime；真实 Gateway/Provider 仍需独立门禁。
- 只使用文档中四个 GET route；不得 POST/PUT/PATCH/DELETE，不改 Runner/HTTP 后端，不 commit/push/deploy。
- 先给出文件级实施计划与 API 字段映射，等待确认后再修改代码。
```
