# 最小付费 pilot 计划（冻结，含失败记录）

依据：2026-09-02 的本地目录审计 [realtime](../../artifacts/tool-audit/realtime_catalog.json)、[historical](../../artifacts/tool-audit/historical_catalog.json)、[financial](../../artifacts/tool-audit/financial_catalog.json)。本文件不发起 Call、不新增 benchmark case 或评分代码；`Search` 排名不作为选择证据。

## 冻结范围与选择规则

- 实时：分别验证 A 股、港股、美股的一次报价；不把目录描述称作实时证明。
- 历史：首批只验证区间日线与复权。分钟工具保留为后续次级问题，不在首批调用。
- 财报：V1 只验证公司/市场标识、单张 IS/BS/CF、财期和原始（as-reported）语义。项目合同是一 case 一次 Tool execution，故三表是三个代表 case，不能合并为一个请求。
- 排序：准确性与业务成功硬门 > 覆盖 > 稳定性证据 > 实测延迟 > QVeris credit 成本。当前目录没有业务成功、稳定性或延迟样本，故它们均为 Call 后硬门，不以缺失事实做排序。

## 候选批次（未获严格批准）

候选清单为 9 个 Tool 调用（每域 3 个主候选；无备用调用）。目录固定价小计为 78.6 credits：实时 3 + 历史 3 + 财报 72.6。`cn_financial_pro.adjusted_price.v1` 是按 quantity 计费、最低 1 credit，目录未给 quantity 上界；因此 78.6 是按目录列示的单次最低/预计成本，**不是可保证的实际扣费上限**。首批控制为一个代码和单一/短日期范围；真实收据若使累计将超过 100 credits，立即停止后续 Call。

| 顺序 | 域 | Tool | 代表验证 | 目录预计成本 | 严格复核状态 |
| --- | --- | --- | --- | --- | --- |
| 1 | realtime | `hangseng_polysource.a_shares_live_quote.query.v2.10fe0581` | A 股最新报价 | 1 | blocked：`stockObject` 的可执行代码格式缺失 |
| 2 | realtime | `hangseng_polysource.quote.hkshares.live.v2.dec427af` | 港股最新报价 | 1 | blocked：同上 |
| 3 | realtime | `alphavantage.realtime_bulk_quotes.retrieve.v1.7aca3c4a` | 美股最新报价 | 1 | INVALID_METHOD/DISCARDED：v1 使用了 `arguments` 请求封装，不符合官方 `parameters` 协议；不评价业务、延迟或稳定性，也不淘汰该 Tool |
| 3b | realtime | `finnhub_io_api.stock.quote` | 美股最新报价（v3 纠偏） | 1 | VALID_RUN、schema-qualified；HTTP 200、business success、实际 1 credit、1211ms。accuracy/freshness blocked：回包无 symbol/source/session/currency，且 `t` 语义未由 Inspect schema 验证 |
| 4 | historical | `cn_financial_pro.adjusted_price.v1` | A 股单日复权日线 | 最低 1；无目录上界 | blocked：quantity 计费无目录上界 |
| 5 | historical | `hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4` | 港股区间复权日线 | 1 | blocked：`stockObject` 的可执行代码格式缺失 |
| 6 | historical | `tiingo.daily.ticker.prices.list.v1` | 美股区间 EOD/adjusted | 1 | blocked：目录未明确 AAPL 的市场覆盖 |
| 7 | financial | `financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47` | 单表 IS，年度 as-reported | 24.2 | blocked：市场覆盖与特定财年参数未明确 |
| 8 | financial | `financialmodelingprep.stable.balancesheetstatementasreported.retrieve.v1.8e37f918` | 单表 BS，年度 as-reported | 24.2 | blocked：市场覆盖与特定财年参数未明确 |
| 9 | financial | `financialmodelingprep.stable.cashflowstatementasreported.retrieve.v1.753a5642` | 单表 CF，年度 as-reported | 24.2 | blocked：市场覆盖与特定财年参数未明确 |

没有获得另行执行授权前，不执行以上任一项。三个 `stockObject` 阻断项也不得猜填：先由 MCP/tool owner 提供可执行的股票代码 payload 合同，才可进入此批次。该补充不是运行时 Search/Inspect，也不能用运行时路由绕开一次执行合同。

## 协议纠偏与严格 approved 切片

既有 realtime v1 Alpha 与 v2 Finnhub 两次真实 Call 都是 **INVALID_METHOD/DISCARDED**：当时请求封装使用 `arguments`，而官方执行协议要求请求体为 `parameters`。因此这两次响应（包括任何 HTTP 状态、业务字段、成本、execution_id、有无数据或耗时）均不得用于评价工具业务成功、稳定性、延迟或成本，也不得据此淘汰任一候选。它们只证明旧封装方法无效。

纠偏 v3 继续使用 Finnhub，以隔离**请求协议**这一唯一改变的变量：目录 Inspect 明确其为 US stocks 的 real-time quote，必填 `symbol: string`，固定 `call_count=1 credit/call`。冻结参数为 `{"symbol":"AAPL"}`；没有加入任何未 Inspect 的参数字段。v3 的 manifest 与 plan 均固定 `connector_protocol_version="qveris.execute.parameters.v1"`，runner 将内部 `arguments` 映射为官方 HTTP body `{"parameters": ...}`。工件为 [approved-runtime-manifest-v3.json](../../benchmarks/pilot/approved-runtime-manifest-v3.json) 与 [approved-runtime-plan-v3.json](../../benchmarks/pilot/approved-runtime-plan-v3.json)：新 `case_id`、新 approval ID、总预算 **1 credit**、一 alias 一次 Call、无重试。

v3 的实际结果为 **VALID_RUN**：HTTP 200、business success、实际扣费 1 credit、1211ms。它只使 Finnhub 成为 **schema-qualified / accuracy-unverified** 候选。回包自身没有 `symbol`、`source`、`session` 或 `currency`，且 `t` 的含义未由 Inspect 输出 schema 验证，因此 accuracy 与 freshness 继续 **blocked**。单样本不得宣称最快、稳定、准确或真正实时。

FIU `post_v1_stock_quote` / `post_v3_stock_quote` 虽为固定 1 credit，但 Inspect 未明确 US 市场覆盖，`symbols` 也没有可执行代码示例；不批准。`post_v1_stock_quote_extend` 是基本信息而非实时价格，不批准。Hang Seng 的 `stockObject` 不明、cn historical 的 quantity 成本无上界、Tiingo 的 AAPL 市场覆盖未在 Inspect 描述中明确、FMP As Reported 的 AAPL 市场覆盖和特定财年参数均不明确，均继续 blocked。原 [candidate manifest](../../benchmarks/pilot/tool_manifest.candidate.json) 保留为历史目录证据，未修改。

## 每次 Call 的统一验收与停止条件

每次仅保存经脱敏的结构化结果与收据摘要，不保存凭据或原始结果工件。下列任一项失败，即该 Tool 不进入 benchmark manifest；不以其他工具补拼该 case。

1. 业务成功：有非错误结构化响应和实际扣费收据；认证、权限、限流或空响应均为失败/降级，不重试扩张。
2. 语义：实时必须有可识别的标的、价格和可核对的 as-of/会话/延迟信息；历史必须有日线 OHLCV、日期边界及复权语义；财报必须有公司与市场识别、报告期、币种/单位、原始/filing provenance。目录尚未证明这些输出字段。
3. 成本：记录每次真实 credits；累计预估或收据达到 100 即停止，不执行未完成候选。
4. 稳定性与延迟：首轮只采集单样本，不把它称为稳定性/SLA 证明。仅记录端到端时间，后续是否复测由单独决定。

## 选择与淘汰

| 域 | 主候选选择理由 | 延后/淘汰的目录候选及理由 |
| --- | --- | --- |
| realtime | 三个主候选分别是唯一在已 Inspect 描述中直接指向 A 股、港交所股票、US-traded symbols 的低固定成本报价 Tool。 | `cn_financial_pro.real_time_quotation.v1`：A/港描述但美股未证明且 custom usage；`fiu_mcp_server.postv3stockquote.create.v2.a49a2677`：`fields`/`symbols`/`timeMode` 均必填，三市场适配未证明；`qveris_finance.mkt_l1_rt`、`finnhub_io_api.stock.quote`：A/港覆盖未证明；`twelvedata.quote.retrieve.v1.affbefe3`：目录价 2.37/call，覆盖与实时语义仍未证实；EODHD 两项明确 delayed。 |
| historical | A 股 `adjusted_price` 直接声明调整后 OHLC、量与复权因子；港股 Tool 直接声明区间报价及 `FUQUAN`；Tiingo 直接声明 EOD OHLCV、adjusted、日期范围。三者先验证日线/复权而不是分钟。 | `cn_financial_pro.hf_basic_quotation.v1` 和 `alphavantage.time_series_intraday.retrieve.v1.1e18340d`：分钟，超出首批；`qveris_finance.mkt_bars_eod`：工具名 EOD 而 schema 描述含 5min，且三市场/复权未证明；`eodhd.eod_historical_data.retrieve.v1.a43f3b91`：没有复权参数；`financialmodelingprep.stable.historicalpriceeod.dividendadjusted.retrieve.v1.1e0b27c9`：24.2/call，会令目录固定价预算超过 100。 |
| financial | FMP As Reported 三张单表是已 Inspect 项中唯一同时明确描述 as-reported/unadjusted 的同源 IS/BS/CF 组合，且三次调用刚好覆盖 V1 三表原始性问题。 | `cn_financial_pro.financial_statements.v1`：目录仅直接证实 A 股示例，且 `statement_type` 一次只选一张表；`alphavantage.*statement*`：明确 normalized，不能通过原始数据硬门；FMP 标准三表：非 as-reported；Twelve Data：未发现 IS；融聚汇 F10：本次只发现 BS。 |

## 财报多市场结论

这 3 个 FMP Call 是**原始三表语义的最小验证组合**，不是多市场覆盖已通过的结论。目录的 As Reported 描述只以 Apple 为例，没有承诺 A/港/美完整覆盖，也没有输出 schema。若其回包没有公司、市场、财期、币种/单位和 filing/as-reported provenance，就不能把它升级为 100 题多市场 V1 候选；需要重新选择或明确缩小 V1 声明，不能以 symbol 输入或 Search 排名补足。

详见机器可读的 [候选 manifest](../../benchmarks/pilot/tool_manifest.candidate.json)。
