# QVeris Data Agent Benchmark

一个受控的基础数据取数 benchmark：检验用户请求能否被一次模型语义规划正确映射为一个固定别名的 QVeris Tool Execute 调用，并产出可评分的结构化结果。

完整设计见 [架构说明](docs/architecture.md)。本仓库不是生产 Agent、搜索产品或数据分析产品。

## 目标与当前范围

最终目标是三个独立 Suite，各 100 个 case：

- 实时行情；
- 历史行情；
- 财报。

当前实现和证据范围更小：

- 一个离线、3-case 的 synthetic replay fixture self-check（每个 Suite 一个 synthetic fixture case）；它输出 not_scored_oracle 和 self_check，而非 success 或数据准确率；
- 一个受批准的 realtime Tool pilot，而不是三域 Tool selection 或 300-case benchmark；
- v3 pilot 的固定别名为 rt_us_finnhub_quote_protocol_v3，对应 finnhub_io_api.stock.quote，并冻结 qveris.execute.parameters.v1 协议。

因此，仓库当前不能支持“已准确”“已交付”“已上线”或“Finnhub 最佳”的结论。

## 运行合同

每个 runtime 请求遵循唯一链路：

~~~text
用户/Kimi 输入
  → 单一模型的一次 SemanticPlanReceipt（plan + raw_usage）
  → 确定性 validation
  → Manifest 固定 alias connector
  → replay fake connector，或受控 paid 脚本的 QVeris Execute
  → 结构化结果与评分记录
~~~

Agent 不在请求时运行 Search 或 Inspect，不调用多个 Tool，也不在取数后再次调用模型。Search/Inspect 仅属于构建期 Tool 目录和证据审查，不属于用户请求路径。

Agent 只返回 SemanticPlanReceipt：SemanticPlan 的状态为 READY、CLARIFY 或 REJECT，加上模型原样返回的 raw_usage。Agent 不计时、不计算 token/费用、不评分。只有外部 Harness 的确定性 validation 通过 schema、状态、alias 和参数 allowlist 后，READY plan 才可以进入 connector。模型不接触 QVeris 或供应商凭据。

## QVerisGet 公共合同（当前实现）

QVerisGet 是当前最小的业务接口，而非完整 benchmark 模板。调用输入为 query、request_id 与 idempotency_key；后两者是安全的 opaque identifier。公共输出只有：

~~~text
request_id, status, tool_alias, payload, message
~~~

status 可为 SUCCESS、EMPTY、BLOCKED、FAILED、UNCERTAIN、CLARIFY、REJECT 或 SEMANTIC_ERROR。公共 response 不含 plan、usage、token、cost、latency、metrics、Tool ID、headers、key、idempotency 或 oracle。

READY 路径恰有 1 次 Agent 调用与至多 1 次 connector 调用；CLARIFY、REJECT 和语义错误各有 1 次 Agent 调用、0 次 connector 调用。内部 trace_sink 接收 receipt、connector 结果与调用计数，供外部 Harness 做 metrics；它不是公共 response，sink 失败也不改变业务结果。

Agent 与 connector 必须共享**同一个** runtime Manifest。response schema 必须是递归 closed object schema（object 均为 additionalProperties=false），且拒绝 secret、token、credential、header、key、Tool ID、idempotency 等敏感字段名。底层 Connector 的 LiveTransport 响应上限是 1 MiB；当前 QVerisGet 拒绝 LiveTransport。

因此，可 allowlist 的真实模型只能配合 fake Tool connector 做语义联调，不等于 QVeris live-ready。真实 live activation 仍待固定 Tool、授权 adapter 与单独验证；后处理/转换/二次 Agent 调用继续 deferred。

详细的 replay/live 边界、请求次数、指标和 Tool selection 规则见 [架构说明](docs/architecture.md)。

## 四项 benchmark 指标

| 指标 | 外部 Harness 口径 |
|---|---|
| 语义准确率（semantic_exact） | Harness 将 receipt.plan 的状态、语义槽位、固定 alias 和参数与冻结 case 比较。 |
| 数据准确率（data_accuracy） | 仅未来 live runner 配合可比较的 independent_source oracle 才可评分；当前 fake replay 一律为 not_scored，不得补造成准确率。 |
| Token（token_usage） | Harness 从 receipt.raw_usage 派生 prompt、completion、total；未报告时为 unknown。没有已批准的价格表时 token cost=unknown。 |
| 端到端延迟（e2e_ms） | Harness 用单调时钟记录 e2e，并单列 agent_call_ms、connector_ms（及需要时的确定性 validation）；replay 与 live 分开报告。 |

四项指标由 Harness 模块外拆，不属于 Agent 输出或 Agent 计算；它们不等同于供应商可靠性、生产 SLA 或用户价值。

## Replay、模型 live replay 与 paid Execute

默认 replay 使用 fake model transport 与 fake QVeris transport，目的是验证语义、validation、alias 路由、fixture 比较和记录格式。它不发起外网模型或 Tool 调用。其三条 smoke 记录为 not_scored_oracle；self_check=pass 仅说明 fixture 链路自洽，不是 success、oracle accuracy 或 live 能力。

真实模型只能以显式 model_live_replay_data 模式配合 replay data 使用；它仍使用 fake connector，且模型 API base 必须位于 MODEL_API_BASE_ALLOWLIST。普通 replay 不接受外网模型 transport。

QVeris live 只允许通过受控 paid pilot 脚本，不是 core runner 功能。脚本默认 dry-run；只有传入 --execute、仓库外且 owner-only 0600 的 approval digest 文件、并且该 digest 匹配冻结 plan hash 后，才可尝试一个 approved Execute POST。逐 Tool 仍需要当前 Inspect 证据、明确授权、真实业务成功与 receipt、实际费用和 as-of 验证；replay、HTTP 200 或目录记录均不能替代这些证据。

v3 realtime Tool pilot 的已记录证据仅表明：纠正为 parameters 协议后有一次有效业务回执。其响应仍缺少 symbol、source、session、currency 等准确性/新鲜度所需字段或合同证据；它不是实时准确、最低延迟、稳定性或 Finnhub 最佳的证明。[v3 计划](benchmarks/pilot/approved-runtime-plan-v3.json) 和 [独立复核](docs/tool-selection/pilot-plan-review.md) 是该有限结论的本地证据。

## Tool selection

候选 Tool 必须先通过准确性 gate：请求/响应 schema、域语义、provenance、as-of/时间口径、授权以及可比较 oracle 或相应 live 证据都应成立。通过该 gate 的候选，才按 latency 与 reliability 的 Pareto 前沿比较；不预设“最快”或“最可靠”的单一赢家。

当前有限的 v3–v5 evidence 不构成三域 Tool selection：Tiingo 历史 EOD 与 FMP as-reported income statement 均仅为 schema-qualified / accuracy-unverified；Alpha Vantage income statement 在 V1 单 Tool、无额外 GET 的数据交付合同下不兼容。它们均未证明准确、稳定、最快或最佳。

## 本地运行

要求 Python 3.11。离线 smoke 不需要 API key：

~~~bash
PYTHONPATH=src python3.11 -m qveris_benchmark \
  benchmarks/pilot/cases.example.jsonl \
  --results /private/tmp/qveris-benchmark-replay.jsonl
~~~

它应输出 mode=replay_fixture_self_check、3 个 case 和 chain_self_checks，并把三条 not_scored_oracle/self_check 记录写到指定路径。可运行测试：

~~~bash
PYTHONPATH=src python3.11 -m unittest discover -s tests -v
~~~

## 环境变量与密钥

[.env.example](.env.example) 列出当前预留的配置名：

~~~text
QVERIS_API_KEY=
MODEL_API_BASE=
MODEL_API_BASE_ALLOWLIST=
MODEL_API_KEY=
MODEL_ID=
MODEL_REASONING_EFFORT=
~~~

当前 replay CLI 不自动读取该文件，也不需要这些值。真实模型接入时，model provider/version/settings 必须作为被测 run 的冻结 profile 记录，且 MODEL_API_BASE 必须匹配 MODEL_API_BASE_ALLOWLIST；不得把模型、URL、header、凭据或 Tool ID 交给模型生成。

runtime Manifest 的 schema 是 tool-manifest.v1；它与 paid approval artifact（approved runtime plan/manifest、approval digest）是两套不可互换的合同。前者只绑定 alias/schema/connector；后者才绑定外部 Execute 的批准范围和 plan hash。approved pilot artifacts 与 artifacts/ 均被 .gitignore 忽略，不能视为可提交、可移植或已发布证据。

不要提交 .env、.env.local、token、API key、raw response、receipt、approved pilot artifact 或结果工件。只在获得单独授权的受控环境设置外部凭据；不要为了运行 replay 填写 QVeris 密钥。

## 已知未完成

- 三 Suite 各 100 case、冻结 dev/selection/holdout 集及完整 oracle；
- 三域 Tool 的 live selection 与可比较的 accuracy/reliability/latency 证据；
- Kimi 的真实模型 profile、token 与延迟 baseline；
- 可报告的 300-case benchmark 结果。

这些缺口存在时，应报告 blocked 或 degraded，而不是推断为成功。
