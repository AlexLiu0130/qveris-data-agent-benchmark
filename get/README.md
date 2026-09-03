# Public get contract (not implemented)

自研 `get` 尚未实现。它未来接收自然语言 Query，并只返回一个结构化响应。最小公共字段为：

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

`status` 只能为 `success`、`partial`、`needs_clarification`、`unsupported`、`no_data` 或 `error`。`success`/`partial` 必须有 `resolved_request`、`data`、`as_of`、`source`，且 `clarification`/`terminal_reason` 为空；`needs_clarification` 必须有 `clarification` 且 `data` 为空；`unsupported`/`no_data`/`error` 必须有 `terminal_reason` 且 `data` 为空。`resolved_request` 供语义评分；`data` 是用户结果；`meta.usage` 仅可携带受信任 adapter 生成的结构化 receipt。不得把推理过程、凭据、供应商私有回执或评分写进响应。

Runner 只接受标准库 `PublicGetResult(public_response, execution_evidence)`：evidence 必须证明恰好一次 Agent、一次唯一 `get` 工具和一次结构化输出，并与 manifest 中版本化 agent/get/model identity 一致。它只是可信本地 adapter 的自述，不能证明真实 runtime；真实 Gateway/Provider 仍需独立门禁。内部模型调用必须仅通过 QVeris Gateway，并由运行时强制固定模型配置、禁止静默 provider fallback；真实 Provider 及该门禁仍未实现。
