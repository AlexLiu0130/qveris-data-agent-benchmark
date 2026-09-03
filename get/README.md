# Public get contract (not implemented)

自研 `get` 尚未实现。它未来接收自然语言 Query，并只返回一个结构化响应。最小公共字段为：

```json
{
  "status": "success",
  "resolved_request": {},
  "data": null,
  "meta": {}
}
```

`status` 只能为 `success`、`partial`、`needs_clarification`、`unsupported`、`no_data` 或 `error`。`resolved_request` 供语义评分；`data` 是用户结果；`meta` 仅放允许公开的版本、时间和数据口径信息。不得把推理过程、凭据、供应商私有回执或评分写进响应。

内部模型调用必须仅通过 QVeris Gateway，并由运行时强制固定模型配置、禁止静默 provider fallback；这是正式 Benchmark 的准入要求，当前仓库尚未实现该门禁。
