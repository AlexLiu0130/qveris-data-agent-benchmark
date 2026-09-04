# QVeris Data Agent Benchmark

QVeris 的黑箱端到端数据取数评测：输入是真实风格的自然语言 Query，系统只能经一次公开 `get` 返回一个结构化响应。Benchmark 不检查内部推理、检索或数据供应商，只评测最终用户结果。

```text
Natural-language query -> one public get -> structured response -> scorer
```

## 范围与当前状态

目标是三个独立 Suite，各 100 题：`realtime_quote`、`historical_price`、`financial_statements`。每套遵循 80 道正常题 / 20 道边界题；市场配额为 A 股 29、港股 28、美股 28、日本 5、英国 5、德国 5。

目前仓库含 300/300 候选题：实时行情、历史行情、财务报表各 100 题。财报的 80 道正常题由 27 条 `frozen` Oracle 记录、1,198 条断言、复签审查账本及已验证 suite manifest 覆盖，具备 Data Accuracy 的数据评分条件；20 道边界题仅保留状态规则，不进入数值数据评分分母。实时行情是 100 题无数值阻塞清单，需绑定双授权来源的 Reference Snapshot；历史行情仍未冻结；两者均为 `not_scored`。

本地确定性 Runner、Scorer 和只读 Arena HTTP/SSE 投影已实现，但目前只接受独立 Run Manifest 与 `oracle-bundle/v1`，尚无 candidate/Oracle 到 Runner 的编译层，也未接入真实 QVeris Gateway。因此尚未对本 300 题产生正式评测、Case Pass 或榜单，亦不构成生产部署。

## 运行合同

- 每个 evaluation cell：`agent_variant × get_variant × case × trial`。
- 每个 cell 仅一个 Agent、一次公开 `get`、一个结构化输出；禁止 `Search` 与 `Inspect`。
- `get` 内部模型调用必须走 QVeris Gateway；这是设计要求，当前仓库尚未实现或验证此门禁。
- 合法响应状态：`success`、`partial`、`needs_clarification`、`unsupported`、`no_data`、`error`。`error` 不能是正确预期。

## 四项指标

| 指标 | 计算口径 |
| --- | --- |
| `semantic_accuracy` | 可评分 Case 中，`resolved_request` 与该题 Semantic Oracle 一致的比例。 |
| `data_accuracy` | 可评分原子数据断言的通过比例；Oracle 未冻结时必须为 `not_scored`。 |
| `end_to_end_latency` | Runner 从发出请求到收到完整结构化响应的单调时钟耗时。 |
| `token_usage` | `get` 内部 QVeris Gateway 的实际 token receipt；不可观测时为 `unknown`，不能估算为 0。 |

`Case Pass` 是派生门禁，而非第五个指标：`schema_valid AND status_correct AND semantic_pass AND data_pass AND NOT timeout`。当 `data_accuracy` 未评分时，不得产出正式 Case Pass 或总榜排名。

Benchmark 的规范指标名为 `end_to_end_latency`；现有 Runner 的字段仍为 `e2e_latency`，两者尚待适配，不能宣称指标已统一。

## 目录边界

- [`benchmarks/`](benchmarks/README.md)：候选题库、版本清单和题库验证说明。
- [`runner/`](runner/README.md)：已实现的本地 Runner、Scorer 和 Arena 的运行与记录合同。
- [`get/`](get/README.md)：未来自研公开 `get` 的响应合同；自研 `get` 尚未实现。
- [`docs/architecture.md`](docs/architecture.md)：责任边界与完整数据流。

禁止提交凭据、token、原始供应商响应、原始运行结果、私有 Oracle 快照，或任何 paid pilot / provider probe 资产。
