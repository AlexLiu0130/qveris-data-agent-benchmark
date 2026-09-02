# Financial pilot v5：AAPL 收入表 schema 批次

## 决定

v5 采用最多两 Tool、两 case 的受控 schema pilot；它**不是**财务数据准确性 pilot，也不向 100 题 Suite 增加 case 或评分逻辑。

| case_id | tool_id / provider | Inspect 已证实的参数 | 单次目录成本 |
| --- | --- | --- | --- |
| `financial-v5-aapl-is-fmp-annual-schema` | `financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47` / Financial Modeling Prep | `{"symbol":"AAPL","limit":1,"period":"annual"}` | 24.2 credits |
| `financial-v5-aapl-is-alpha-schema` | `alphavantage.income_statement.retrieve.v1.7aca3c4a` / Alpha Vantage | `{"function":"INCOME_STATEMENT","symbol":"AAPL"}` | 1.0 credit |

总成本硬上限为 **25.2 credits**（`24.2 + 1.0`）；每个 case 只有一个 alias、一次 Execute、无自动重试。冻结 artifact 位于 `benchmarks/pilot/approved-runtime-{manifest,plan}-v5.json`，并按仓库规则被忽略，不应提交。

## 已验证的静态事实

- 本地目录明确将 FMP 工具描述为公司官方财报的未调整/as-reported 收入表，涉及 revenue、cost 和 expenses；其输入为必填 `symbol:string`，可选 `limit:number`、`period∈{annual,quarter}`，并列为 `call_count`、每 call 24.2 credits。[financial catalog](../../artifacts/tool-audit/financial_catalog.json) 第 3775-3817 行。
- Alpha 工具明确描述为返回年度及季度收入表、字段为映射后的 GAAP/IFRS taxonomy；输入为必填 `function=INCOME_STATEMENT` 与 `symbol:string`，并列为 `call_count`、每 call 1.0 credit。[financial catalog](../../artifacts/tool-audit/financial_catalog.json) 第 4195-4228 行。
- 目录候选列表只含 FIU 的美国 BS 和 CF basic 工具，而没有 FIU 美国收入表工具；因此 FIU 不能构成本批的 AAPL income-statement case。[financial catalog](../../artifacts/tool-audit/financial_catalog.json) 第 2823-2920、4306-4333 行。
- 现有 paid runner 只接受固定数值的逐 case 成本、批准的 manifest/plan hash 绑定和 `qveris.execute.parameters.v1`；请求体由 runner 固定封装为 `parameters`，同一 case 无重发。[runner review](../reviews/paid-pilot-runner-review.md) 第 13-25 行。

## v5 实际结果

批次已以 **25.2 / 25.2 credits** 完成：两 case 各一次，无重试。

| Tool | 已观测结果 | v5 结论 |
| --- | --- | --- |
| FMP as-reported IS | **VALID_RUN**；实际 24.2 credits、2896ms；直接返回 AAPL、FY2025、USD、revenue 与 net income/loss 字段。缺少 source 与 as-of 字段。 | **schema-qualified / accuracy-unverified**。该 Call 证明了最小收入表数据交付形态和 receipt，但不证明准确性、来源、披露时点或可泛化稳定性。 |
| Alpha IS | **VALID_RUN（transport/receipt）**；实际 1 credit、2348ms；响应只含 `content_schema`、`full_content_file_url`、`message`、`status_code`、`truncated_content`，没有内嵌财报字段。未 follow 该 URL。 | **incompatible_for_v1_data_delivery**。这是当前“一次 Tool、无额外 GET”合同下的数据交付不兼容，不是对 provider 的失败归因。 |

FMP 与 Alpha 的财报数值**不可比**：Alpha 本次没有在允许的单 Tool 响应内交付可抽取的收入表字段或可匹配的 reporting period。`data_accuracy` 仍不计分。

**accuracy blocked：**FMP 虽在回包中满足 AAPL/FY2025/USD 与两个目标字段的 schema 门，但仍缺 source/as-of；Inspect 也未提供可直接验证的完整输出 schema。Alpha 则未在内嵌响应中提供任何财报字段。因而这次不是独立 oracle 比对，不得把 FMP 的字段存在或两次 VALID_RUN 写成准确性、来源完整性或跨工具一致性结论。

## 选择与排除

- FMP as-reported：已从候选升级为本次 **schema-qualified** 的收入表交付工具；仍不因单样本而认定字段准确、来源已验证或稳定。
- Alpha retrieve：参数与固定成本均可构造，且 transport/receipt 有效；但实际返回形态要求额外 URL 获取内容，故在当前一次 Tool、无额外 GET 的 V1 合同下不适合基础财报数据交付。
- 排除 `alphavantage.income_statement.list.v1.467a92c0`：相同语义/费用的重复 Alpha 入口，不能增加本批的最小可比性。
- 排除 FMP standard income statement：参数与固定成本虽完整，但没有 as-reported 描述；在两 Tool 上限内不优于 FMP as-reported。
- 排除 FIU：当前 Inspect 证据只列出美国 BS/CF，不存在相应 US income-statement Tool，不能拼成此批目标。
- 排除所有 variable/custom usage 工具：没有每次成本硬上界，不满足 paid runner 的固定成本门。

## 运行边界

本次文档更新未执行 Search、Inspect、Execute 或联网。v5 的已记录结果不包含原始响应内容或执行标识；外部 Execute 仍需另行授权、外部 owner-only approval digest，且 runner 默认为 dry-run。任何一次失败/uncertain/receipt 缺失都只记录该 case，不重试、不切换工具、不调用补救工具。
