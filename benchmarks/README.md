# Benchmark candidates

`candidates/v0.1/` 是可审阅、可机读的 300 题候选包，不是已冻结的正式 Benchmark。

- `historical_price.cases.json`：100 道历史行情题；80 道正常题的 Data Oracle 标注为 `candidate/unverified`。
- `financial_statements.cases.json`：100 道财务报表题；80 道正常题映射至 27 条已冻结的 Data Oracle 记录（1,198 条断言），复签账本与 suite manifest 均已验证，可用于 Data Accuracy 数据评分；20 道边界题仅保留状态规则，不进入数值数据评分分母。Runner/Scorer 尚未实现，故尚无实际分数或排名。
- `realtime_quote.cases.json`：100 道实时行情题；动态数值题标记 `reference_snapshot_required`，状态题也仅为 candidate，未冻结任何 Oracle。
- `manifest.json`：仅登记已包含文件及其 SHA-256。
- `VALIDATION.md`：静态完整性检查和正式使用前的阻塞项。

## 当前候选 schema（不可直接交给正式 Runner）

三份来源文件保留各自的候选结构：历史行情使用 `case_id` 和 `query`，其 `data_oracle` 以 `source_note` 标记候选来源；财务报表使用 `id`，同时保留 `query` / `prompt`，其 `data_oracle` 以 `reference_key` 关联来源；实时行情使用连续的 `case_id`、`query`、分类/风格/tags/intent/entities/required_fields，并以唯一 `expected_status` 或显式 `status_conflict` 表达终态。两套边界题也各自保留其 suite-specific 状态字段（历史的文本 `state_oracle`，财报的对象 `state_oracle` / `evidence`）。

它们的 `schema_version` 当前仍是 suite-specific schema，尚无统一顶层 Case schema。正式 Runner 实现前，必须先将三个来源规范化为一个显式版本化的 Case schema，并统一 case ID、query、data Oracle 与 state Oracle 的字段映射；不得直接把当前 JSON 喂给正式 Runner。

新增或冻结题库时，先更新案例与独立 Oracle，再重算 manifest 哈希并完成验证。不要把原始数据、供应商回执或运行结果放入本目录。
