# Local Runner and Scorer

本仓库已实现本地确定性的 Runner、Scorer 和只读 Arena HTTP/SSE 投影。每次运行的最小单元是 `agent_variant × get_variant × case × trial`，并只允许一个 Agent、一次公开 `get` 和一个最终结构化响应；禁止 `Search` 与 `Inspect`。

每个 cell 必须记录：case ID、suite、显式版本化的 Agent / get / model identity、trial、请求与响应的 schema 校验结果、最终 status、`resolved_request`、单调时钟 E2E 延迟、Gateway token receipt（或 `unknown`）、时间戳和失败原因。客户端必须返回 `PublicGetResult` 和受信任 adapter execution evidence；Runner 校验恰好一次 Agent、唯一 `get`、一次结构化输出，拒绝裸响应。记录不得包含凭据、原始供应商响应或私有 Oracle。

Runner 只调用公共 `get`，不读取其内部 trace，也不替结果补分。Scorer 依据冻结 Oracle 计算四项指标；未冻结 Oracle 的数据准确率必须是 `not_scored`。

当前 v0.1 的 200 道候选 JSON 不是 Runner 输入：历史行情用 `case_id` / `query` / `data_oracle.source_note`，财务报表用 `id` / `query`（兼容 `prompt`）/ `data_oracle.reference_key`，边界状态字段形状也不同。正式输入仍须规范化为统一、显式版本化的 Case schema，且三个 Suite 必须各 100 Case。

实现边界：`src/qveris_benchmark/run_backend.py` 持久化不可变 manifest 与执行 journal；`benchmark_scorer.py` 只从它、冻结 Policy/Oracle 和公开响应计算四项指标；`arena_http.py` 仅在 loopback 提供只读 JSON/SSE 投影，不泄露 adapter evidence。Evidence 只是可信本地 adapter 的自述，不能证明真实 runtime；真实 Gateway/Provider 仍需独立门禁。真实 GET Provider、冻结 300 Case/Oracle、正式排名和生产部署均未实现。
