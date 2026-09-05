# Public get contract

`qveris_benchmark.public_get.PublicGetAdapter` 接收自然语言 Query，并只返回一个结构化响应。它注入一次语义 resolver 与一次 Gateway executor：模型只能输出 `public-get.semantic/v1` 的业务语义，不能输出 Tool、provider、parser 或 provider 参数；固定运行时目录决定唯一 Route、参数渲染和 parser。

生产 factory 是 `live_get_client.build_qveris_public_get_client(QVerisPublicGetConfig.from_environment())`。它只需要 `QVERIS_MODEL_GATEWAY_API_KEY`、`QVERIS_API_KEY` 与 `QVERIS_MODEL_GATEWAY_MODEL`；后者是运行 Variant 的显式模型名，不在代码中锁定 Terra。三个模型应各自创建一个 config/Variant，不共享或自动选择模型。factory 构造本身不做 I/O；模型目录 preflight 是显式操作。

语义 resolver 只允许一个 `public-get.semantic/v1` JSON：模型不能输出 Tool、provider、parser 或 provider 参数。静态 catalog 当前为 113 格，86 格有唯一 fixed route；覆盖与 27 个非 dispatch 格的精确原因见 [`../docs/get-route-coverage.md`](../docs/get-route-coverage.md)。运行时不会读取 build-time registry、Search、Inspect、fallback 或重试。

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

`status` 只能为 `success`、`partial`、`needs_clarification`、`unsupported`、`no_data` 或 `error`。`success`/`partial` 必须有 `resolved_request`、`data`、`as_of`、`source`，且 `clarification`/`terminal_reason` 为 `null`；`needs_clarification` 必须有 `clarification`，`unsupported`/`no_data`/`error` 必须有 `terminal_reason`。所有非成功终态不得带 `data`，且可省略不适用字段。`get-response/v1` 是基本/legacy 响应；已接线的 domain projection 可输出 `get-response/v2`（含 `as_of_status` 与 `coverage`），Runner 按 Suite 严格规范化。`resolved_request` 供语义评分；`data` 是用户结果。不得把推理过程、凭据、供应商私有回执、执行 ID 或评分写进 `public_response`。

Runner 只接受标准库 `PublicGetResult(public_response, execution_evidence)`，不是裸 JSON。`execution_evidence` 是 adapter 的私有可信证明，含 Variant identity、agent/tool/structured-output 计数、`tools_used` 与阶段耗时；它不进入公共响应，也不是 Oracle。`meta.usage` 只接受已绑定的 QVeris Model Gateway 实际 receipt；模型失败前若已得到合格 receipt 会保留，缺失/无效/缓存 receipt 统一标为 `unavailable`，而不是把 token 猜成 0。数据 Tool 的 usage 永不用于 token 指标。`sandbox_get_entry`/broker 的历史行情及 Alpha pointer JSONL 离线链路已通过：host 在 Tool 后至多一次受控绑定结果下载，image 保持 `--network none`，Oracle 不进入该链路。六个代表性真实请求也各以一个模型调用和一个 Tool 调用成功；正式 600 次评测尚未执行，不能宣称 86 格 live 验收成功。
