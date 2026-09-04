# Benchmark candidates

`candidates/v0.1/` 是可审阅、可机读的 300 题候选包，不是已冻结的正式 Benchmark。

- `historical_price.cases.json`：100 道历史行情题；v1 已冻结其中 50 道数值题（55 个公开可追溯、来源一致的完整变体）与 50 道状态 Oracle。数值冲突保留完整变体，禁止平均或跨源拼接；状态题不进入 Data Accuracy 分母。
- `financial_statements.cases.json`：100 道财务报表题；80 道正常题映射至 27 条已冻结的 Data Oracle 记录（1,198 条断言），复签账本与 suite manifest 均已验证，可用于 Data Accuracy 数据评分；20 道边界题仅保留状态规则，不进入数值数据评分分母。本地 Runner/Scorer 框架已实现，但尚未接入候选题编译层与真实 QVeris Gateway，尚未产生正式评测分数或榜单。
- `realtime_quote.cases.json`：100 道实时行情题；82 道动态题标记 `reference_snapshot_required`，18 道状态题已有冻结 State Oracle。动态值由每轮运行时快照冻结，当前 Runner 尚不能生成 receipt。
- `manifest.json`：仅登记已包含文件及其 SHA-256。
- `VALIDATION.md`：静态完整性检查和正式使用前的阻塞项。

## 当前候选 schema（不可直接交给正式 Runner）

三份来源文件保留各自的候选结构：历史行情使用 `case_id` 和 `query`，其 `data_oracle` 以 `source_note` 标记候选来源；财务报表使用 `id`，同时保留 `query` / `prompt`，其 `data_oracle` 以 `reference_key` 关联来源；实时行情使用连续的 `case_id`、`query`、分类/风格/tags/intent/entities/required_fields，并以唯一 `expected_status` 表达终态。两套边界题也各自保留其 suite-specific 状态字段（历史的文本 `state_oracle`，财报的对象 `state_oracle` / `evidence`）。

它们的 `schema_version` 当前仍是 suite-specific schema，尚无统一顶层 Case schema。正式 Runner 实现前，必须先将三个来源规范化为一个显式版本化的 Case schema，并统一 case ID、query、data Oracle 与 state Oracle 的字段映射；不得直接把当前 JSON 喂给正式 Runner。

新增或冻结题库时，先更新案例与独立 Oracle，再重算 manifest 哈希并完成验证。不要把原始数据、供应商回执或运行结果放入本目录。
