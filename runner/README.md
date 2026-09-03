# Runner contract (not implemented)

正式 Runner 尚未实现。本目录只定义未来执行边界：每次运行的最小单元是 `agent_variant × get_variant × case × trial`，并只允许一个 Agent、一次公开 `get` 和一个最终结构化响应；禁止 `Search` 与 `Inspect`。

每个 cell 必须记录：case ID、suite、Agent / get 版本、trial、请求与响应的 schema 校验结果、最终 status、`resolved_request`、单调时钟 E2E 延迟、Gateway token receipt（或 `unknown`）、时间戳和失败原因。记录不得包含凭据、原始供应商响应或私有 Oracle。

Runner 只调用公共 `get`，不读取其内部 trace，也不替结果补分。Scorer 依据冻结 Oracle 计算四项指标；未冻结 Oracle 的数据准确率必须是 `not_scored`。

当前 v0.1 候选 JSON 不是 Runner 输入：历史行情用 `case_id` / `query` / `data_oracle.source_note`，财务报表用 `id` / `query`（兼容 `prompt`）/ `data_oracle.reference_key`，边界状态字段形状也不同。正式 Runner 前必须先规范化为统一、显式版本化的 Case schema。
