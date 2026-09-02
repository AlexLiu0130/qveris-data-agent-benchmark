# V1 场景与候选 Tool 矩阵

[qveris-scenario-tool-matrix-v1.json](qveris-scenario-tool-matrix-v1.json) 是构建期的机器可读注册表：Agent 最终应先识别 `scenario_id` 和标准参数，确定性 Router 再根据**未来冻结的**场景映射调用一个 Tool。

它不是运行时 Manifest：没有 `active_tool_id`、排序、阈值、fallback 或默认市场覆盖。当前的 Finnhub、Tiingo、FMP 只在其已实际验证的窄切片中标为 `existing_executed_candidate`，不是方向赢家。

候选必须按同一 `scenario_id × market × 代表性请求` 做一次 Tool Execute，记录直返字段质量、实际 cost receipt、端到端延迟和失败类型；完成可比测试后才可以排序、冻结，并把独立结果写入新的运行时 Registry。`latest_filed` 目前保持 blocked，因为“最新”需要已验证的披露可得时间和排序语义。
