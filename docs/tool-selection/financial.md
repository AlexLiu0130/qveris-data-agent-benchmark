# 财报数据 Tool 候选审计

审计范围：实体、市场、利润表（IS）、资产负债表（BS）、现金流量表（CF）、年报/季报语义与原始数据；不做计算。来源仅为 2026-09-02 的目录 Search/Inspect 审计（[audit](../../artifacts/tool-audit/financial_catalog.json)），不是运行时数据验证，也不以 Search 排名断言优劣。

## 已核验的目录事实

| 候选（同源组合） | 市场覆盖（目录明确项） | IS / BS / CF | 输入与财期语义 | 目录预计成本 |
| --- | --- | --- | --- | --- |
| cn financial pro：`financial_statements` 单入口，或 IS/BS/CF 三个专用 Tool | A 股代码示例为 `.SZ`/`.SH`；港/美未声明 | 三表均有 | `codes`、`year`、`period`（`0331`/`0630`/`0930`/`1231`）、`type`；单入口另有 `statement_type`（三选一） | 0.04 credits/quantity，单次最低 1 credit；元数据亦写作 1 credits/result |
| Financial Modeling Prep（FMP）标准三表 | A 股明确为 `.SS`/`.SZ`，且明确 `.SH` 不支持；港/美未在本目录元数据中承诺 | 三表均有 | `symbol`、可选 `limit`、`period`；标准 Tool 枚举包含 `annual`/`quarter`（且 BS 还列 Q1–Q4/FY） | 24.2 credits/调用；三表各一次约 72.6 credits |
| FMP As Reported 三表 | Apple 为描述示例；未核验港股或完整多市场可用性 | 三表均有，且每项描述为 as-reported / unadjusted | `symbol`、可选 `limit`、`period`=`annual`/`quarter` | 24.2 credits/调用；三表各一次约 72.6 credits |
| Alpha Vantage 三表 | 目录仅给 IBM 示例；字段映射至 SEC 的 GAAP/IFRS taxonomy，未承诺 A/港覆盖 | 三表均有 | `function` 固定为对应报表、`symbol`；描述声明 annual + quarterly | 1 credit/调用；三表各一次约 3 credits |
| 融聚汇 F10（沪深 / 港股） | 沪深 BS 明确 `.SH`/`.SZ`；港股 BS 明确 `.HK` | 仅 BS 在本次 Inspect 集合中发现 | 沪深：`reportType`=1/6/9/12 等；港股：`reportType`=F/I/Q1–Q6 等，均有日期范围 | 1 credit/调用 |
| Twelve Data | 以 `symbol`/`figi`/`isin`/`cusip`/`exchange`/`mic_code`/`country` 输入，未据此推定市场可用性 | BS、CF；未发现 IS | `period`、起止 fiscal date、`outputsize` | 2.37 credits/调用 |
| QVeris Finance `fundamentals_cf` | `symbol` 可为 market-qualified，但具体市场未声明 | 仅 CF | 可选 `fiscal_year`、`fiscal_period`=`Q1`–`Q4`/`FY`、`report_scope` | 1 credit/调用 |

证据位置：cn financial pro 的三表、财期与合并范围见 audit:3103-3298；FMP 标准 BS 的 A 股代码、`period` 与成本见 audit:3330-3376，IS/CF Tool IDs 见 audit:3467-3471、4052-4067；FMP As Reported IS/CF/BS 见 audit:3775-3862、3971-4013；Alpha Vantage BS/IS/CF 见 audit:3380-3413、4195-4228、4267-4300；沪深/港股 F10 BS 见 audit:3416-3464、3610-3660；Twelve Data、QVeris CF 的目录项见 audit:3517-3598、3915-3968、4102-4189。

## 同源组合与硬门

1. **原始数据优先**：仅 FMP As Reported 的三表描述明确称 as-reported/unadjusted；这仍不是字段级原始性、币种、filing ID、修订版本或来源链的证明。Alpha Vantage 明示 normalized，不满足“原始”硬门。
2. **三表与单次执行不兼容**：本项目每 case 只有一次 Tool 执行；所有已发现的三表组合都需要按 IS/BS/CF 分调用。cn 单入口的 `statement_type` 也是三选一，未证明一次返回三表。全三表 case 必须拆为单表 case，或以后另行验证真正的原子三表 endpoint。
3. **多市场硬门未通过**：本次可从输入说明直接确认的仅是 A 股（cn、FMP、沪深 F10）与港股 BS（港股 F10）。没有一个已 Inspect 的同源三表组合同时明确覆盖 A、港、美。
4. **实体解析硬门未通过**：除代码/`symbol` 格式外，目录未给统一实体 ID、交易所映射、同名/ADR/跨上市消歧或退市代码规则。
5. **财期与结果合同未通过**：输入有财期枚举，但未 Inspect 输出 schema；财年结束日、报告发布日期、TTM、币种/单位、累计/单季口径、重述、缺失期、审计状态与 source provenance 均未知。
6. **成本与可用性未通过**：成本是目录 expected cost，不是实际扣费；Search/Inspect 未证明 Tool 执行成功、返回行数、地区权限、速率限制或新鲜度。

## 付费 pilot 候选（非排序结论）

以下是按“先验证最大缺口”的建议顺序，而不是 Search 排名或最终供应商选择；任何 Execute 均需另行授权。

1. **FMP As Reported 三表（先美股）**：唯一已发现、在三表描述中都明确 as-reported/unadjusted 的同源组合。预算上限可先按 72.6 credits/实体/财期（三次调用）估算。准入：返回未调整的 IS/BS/CF，且每条包含可核对的报告期、币种/单位与 provenance；否则淘汰。
2. **cn financial pro 三表（先 A 股）**：明确 A 股代码、季度/年报和 consolidated/parent/adjusted 选择，目录最低成本约 3 credits/实体/财期（三次最小计费）。准入：同一实体同一财期三表完整、口径可区分；港/美不纳入此 pilot 的成功范围。
3. **Alpha Vantage 三表（先美股成本基线）**：目录成本约 3 credits/实体/财期，且三表 annual/quarterly 语义完整；但它是 normalized，不能作为“原始数据”通过项。准入目标仅为成本、财期映射和结果 schema 对照，不替代候选 1 的原始性验证。

## 余额、请求与未知项

- 审计共 6 次 Search（每次 `limit=10`）和 1 次去重后 Inspect（24 个 Tool ID）；执行次数为 0，执行路径未调用。[audit:14-24, 4304-4334]
- server-reported balance：首个允许请求后为 **69,209.099** credits，最后一次允许请求后为 **69,209.1** credits；该差异在显示精度内，不能据此推断 Search/Inspect 或未来 Execute 的真实成本。[audit:20-23]
- 6 个查询均返回 10 个结果；去重候选为 26 个，因 Inspect 上限仅审查前 24 个。未 Inspect 的两个候选是美股 BS/CF（`fiu_mcp_server.postv2usfinancebalancebasic.create.v2.c10668b2`、`fiu_mcp_server.postv2usfinancecashbasic.create.v2.02d2f850`），因此不得据此断定其 schema、价格或适配性。[audit:4306-4334]
- 未知且不能由本次 Search/Inspect 推断：返回原始字段与样本值、数据许可、覆盖证券清单、实际扣费收据、SLA/限流、历史深度、freshness/as-of、错误/降级行为、港/美三表同源可用性。
