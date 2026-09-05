# Runner and Scorer

Runner 以 `agent_variant × get_variant × case × trial` 执行最终 `v0.3/v3` 的 300 题；每个 cell 只允许一个 Agent、一次公开 `get` 和一个结构化响应，禁止 `Search` 与 `Inspect`。

`runner-run-manifest/v2` 是运行合同，`v0.3/v3` 是其输入题库/Oracle 版本。模板 `runner-run-manifest-template/v2` 尚未绑定运行 Variant，不能直接执行；绑定 Variant 和实时 reference contract 后才可产生 runnable manifest。

Runner 记录 case、suite、版本化 variant identity、schema 校验、status、`resolved_request`、单调时钟 `end_to_end_latency`、token receipt 或 `unknown`、时间戳与失败原因。它只调用 public `get`，不读取内部 trace，也不补写结果。Scorer 只根据冻结 Oracle、运行记录和公开响应计算四项指标：语义准确率、数据准确率、端到端延迟、Token 使用量。

实时行情缺少完整 runtime reference receipt 时，数据准确率为 `not_scored`；这种运行仅用于执行链验证，不能产出正式 Case Pass 或排名。记录不得包含凭据、原始供应商响应或私有 Oracle。
