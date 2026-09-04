# Public get contract

`qveris_benchmark.public_get.PublicGetAdapter` 接收自然语言 Query，并只返回一个结构化响应。它注入一次语义 resolver 与一次 Gateway executor：模型只能输出 `public-get.semantic/v1` 的业务语义，不能输出 Tool、provider、parser 或 provider 参数；固定运行时目录决定唯一 Route、参数渲染和 parser。

生产语义 resolver 使用 `QVerisModelGatewaySemanticResolver.from_environment()`：它只读取 `QVERIS_MODEL_GATEWAY_API_KEY` 与 `QVERIS_MODEL_GATEWAY_MODEL`，以固定的非流式 `POST https://aigateway.qveris.ai/v1/chat/completions` 调用一次，不重试或 fallback。resolver 的调用合同为 `resolver(query, request_id=...) -> SemanticResolution`；它在本地拒绝非 JSON、重复 JSON key、非 `stop` 输出及不符合完整业务 schema 的语义。只有 QVeris Model Gateway 响应中同时存在非估算 billing、匹配的 `X-QVeris-Call-ID` 和完整 Chat Completions token usage 时，才生成 `meta.usage`；数据 Tool envelope 的 `usage` 永远不会用于 token 指标。Model Gateway 与 Tool client 已接入；AAPL quote 已有一次单次 live smoke（一次模型调用、一次 Tool execution、严格结构化响应）。

当前可调度的固定路由只有 US Alpha quote/last price 与 HKEX L1。HKEX calendar 与 SSE dividend 保留 Tool/parser/postprocess 证据，但因没有可信 source-data `as_of`，会在调用前以 `unsupported` 返回。US Alpha L1、US after-hours 与所有财报 Tool 仍保留 build-time 调用证据，但尚未冻结 renderer/parser/postprocess，因此同样不会调度。84 个预设格均存在于静态 runtime catalog；运行时不会读取 build-time registry、Search、Inspect、fallback 或重试。

最小公共字段为：

```json
{
  "schema_version": "get-response/v1",
  "status": "success",
  "resolved_request": {},
  "data": {},
  "as_of": "2026-09-03T00:00:00Z",
  "source": "provider-name",
  "clarification": null,
  "terminal_reason": null,
  "meta": {"usage": {"receipt_id": "opaque-id", "measurement_version": "v1", "cache_status": "miss", "request_id": "attempt-id", "issuer": "trusted-adapter", "input_tokens": 0, "output_tokens": 0, "total_tokens": 0}}
}
```

`status` 只能为 `success`、`partial`、`needs_clarification`、`unsupported`、`no_data` 或 `error`。`success`/`partial` 必须有 `resolved_request`、`data`、`as_of`、`source`，且 `clarification`/`terminal_reason` 为 `null`；`needs_clarification` 必须有 `clarification`，`unsupported`/`no_data`/`error` 必须有 `terminal_reason`。所有非成功终态不得带 `data`，且可省略不适用字段。`resolved_request` 供语义评分；`data` 是用户结果；`meta.usage` 仅可携带受信任 adapter 生成的结构化 receipt。不得把推理过程、凭据、供应商私有回执或评分写进响应。

Runner 只接受标准库 `PublicGetResult(public_response, execution_evidence)`。已派发的 route 证明一次 Agent、一次唯一 `get` 工具和一次结构化输出；澄清/不支持等预派发终态为一次 Agent、一次结构化输出、零 Tool execution。内部模型调用必须仅通过 QVeris Gateway，并由运行时强制固定模型配置、禁止静默 provider fallback。上述 AAPL 单样本不证明三条开放路由稳定，不证明历史行情或财报已获 runtime 准入，也不证明正式 benchmark 已完成。
