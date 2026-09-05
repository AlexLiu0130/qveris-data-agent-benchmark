# Local Runner and Scorer

本仓库已实现本地确定性的 Runner、Scorer 和只读 Arena HTTP/SSE 投影。每次运行的最小单元是 `agent_variant × get_variant × case × trial`，并只允许一个 Agent、一次公开 `get` 和一个最终结构化响应；禁止 `Search` 与 `Inspect`。

每个 cell 必须记录：case ID、suite、显式版本化的 Agent / get / model identity、trial、请求与响应的 schema 校验结果、最终 status、`resolved_request`、单调时钟 E2E 延迟、Gateway token receipt（或 `unknown`）、时间戳和失败原因。客户端必须返回 `PublicGetResult` 和受信任 adapter execution evidence；Runner 校验恰好一次 Agent、唯一 `get`、一次结构化输出，拒绝裸响应。记录不得包含凭据、原始供应商响应或私有 Oracle。

Runner 只调用公共 `get`，不读取其内部 trace，也不替结果补分。Scorer 依据冻结 Oracle 计算四项指标；未冻结 Oracle 的数据准确率必须是 `not_scored`。

OCI sandbox mode is diagnostic-only. The parent never imports a candidate plugin; it launches one digest-pinned, network-disabled Docker container per Case and sends only `request_id` plus Query over JSONL stdin. Oracle, scoring, credentials, sockets, and repository mounts stay outside the container. The trusted host broker permits at most the fixed model request and one fixed Tool request, so its dispatch observations—not image-provided evidence—supply the Runner’s boundary counts. It does not prove hidden image internals and cannot admit an official run.

`runner-run-manifest/v2` 是 v0.2 题库的正式运行输入：三个 Suite 各 100 Case，并在 `expected_status_counts`（或候选清单形状的 `suite_composition`）中声明每套状态分布；Runner 会拒绝声明与 `score_case.expected_status` 不一致的 Manifest。v2 模板 `runner-run-manifest-template/v2` 可用 `variants: []` 保存编译前绑定，但不可执行。旧版正式 Manifest 只有明确标记 `runner-run-manifest/v1` 时才保留每套 80 normal / 20 boundary 的兼容校验。

正式 v2 的实时行情 Case 若缺少 `reference_contract.source_contract_hash` 或 `window_rule_version`，Runner 不会补造参考来源或计数据准确率。唯一例外是明确的 300 题 diagnostic non-ranking 装配运行：它仍调用 GET 以验证完整执行链，但 realtime 数据准确率为 `not_scored`，不能形成正式 Case Pass 或排名。

实现边界：`src/qveris_benchmark/run_backend.py` 持久化不可变 manifest 与执行 journal；`benchmark_scorer.py` 只从它、冻结 Policy/Oracle 和公开响应计算四项指标；`arena_http.py` 仅在 loopback 提供只读 JSON/SSE 投影，不泄露 adapter evidence。Evidence 只是可信本地 adapter 的自述，不能证明真实 runtime；真实 Gateway/Provider 仍需独立门禁。真实 GET Provider、正式运行/排名和生产部署均未实现。
