# 实时行情 Tool 只读目录审计

审计时间：2026-09-02T04:22:46Z。证据文件：[realtime_catalog.json](../../artifacts/tool-audit/realtime_catalog.json)。仅 Search/Inspect；搜索排序不构成质量或适用性排序。

## 进入付费试点（最多 3 个）

| 状态 | 市场 | ID / 名称 | Provider | 覆盖与已检视 schema | 预计成本 |
| --- | --- | --- | --- | --- | --- |
| 主候选（paid pilot） | A 股 | `hangseng_polysource.a_shares_live_quote.query.v2.10fe0581` / A Shares Live Quote | Hang Seng Poly Source | 描述声明 A 股实时行情、交易状态、最新价、开盘/涨跌/量；`stockObject` 必填（`STOCK_A_COMPANY`），可选分页 | 1 credit/call |
| 主候选（paid pilot） | 港股 | `hangseng_polysource.quote.hkshares.live.v2.dec427af` / HShareLiveQuote | Hang Seng Poly Source | 描述声明港交所股票实时行情和交易状态；`stockObject` 必填（`STOCK_HK_COMPANY`），可选分页 | 1 credit/call |
| 主候选（paid pilot） | 美股 | `alphavantage.realtime_bulk_quotes.retrieve.v1.7aca3c4a` / Realtime Bulk Quotes | Alpha Vantage | 描述声明 US-traded symbols、最多 100 标的、常规及盘前/盘后；`function=REALTIME_BULK_QUOTES`、`symbol` 必填，`datatype` 可选 | 1 credit/call |

这三项仅获准进入受控 paid pilot，尚未被证明为生产可用或真正实时。每个市场仅选一个，避免以 Search 排名或未执行的目录描述替代验证。

## 备选与阻断项

| 状态 | ID / 名称 | Provider | 覆盖与已检视 schema | 预计成本 |
| --- | --- | --- | --- | --- |
| 备选 | `cn_financial_pro.real_time_quotation.v1` / 实时行情 | cn financial pro Data | 描述声明 A 股、港股及 bid/ask；`codes` 必填，`indicators` 可选 | 1 credit/result；自定义用量、最低 1/call |
| 备选 | `fiu_mcp_server.postv3stockquote.create.v2.a49a2677` / post_v3_stock_quote | 融聚汇 | 描述称实时/延时、批量、最新快照/挂单/逐笔；`fields`、`symbols`、`timeMode` 必填，`timeMode` 0 实时/1 延时 | 1 credit/call |
| 备选 | `qveris_finance.mkt_l1_rt` / Real-time Level 1 Quotes | QVeris Finance | 描述称 equities/ETFs 的 real-time or near-real-time L1（last/bid/ask/change/volume）；`symbol` 必填 | 1 credit/call |
| 备选 | `finnhub_io_api.stock.quote` / Quote | Finnhub API | 描述称 US stocks real-time；`symbol` 必填 | 1 credit/call |
| 备选 | `twelvedata.quote.retrieve.v1.affbefe3` / Quote | Twelve Data API | 描述称 real-time quote；标的可由 `symbol`/FIGI/ISIN/CUSIP 识别，附 `prepost`、时区等可选项 | 2.37 credits/call |
| blocked（明确延时） | `eodhd.live_v2.us_quote_delayed.retrieve.v1.f0e13d45` / Retrieve Delayed US Stock Quotes | EODHD | 描述明确 exchange-compliant delayed；`s` 必填，可选分页/格式 | 2.81 credits/call |
| blocked（明确延时） | `eodhd.real_time.retrieve.v1.3b8a5cf8` / Retrieve Real-Time (Delayed) Stock Prices | EODHD | 名称明确 delayed，且描述与加密货币不一致；`symbol` 必填 | 2.81 credits/call |
| blocked（非标的行情） | `hangseng_polysource.index.livequote.query.v2.2730eef8` / IndustryIndexLiveQuote | Hang Seng Poly Source | 产业指数；`indexObject=SECU_INDEX` 必填 | 1 credit/call |
| blocked（非标的行情） | `hangseng_polysource.quote.conceptIndexLiveQuote.create.v2.e4813778` / Industry Index Live Quote | Hang Seng Poly Source | 行业/概念指数；`indexObject=SECU_INDEX` 必填 | 1 credit/call |
| blocked（历史混合） | `cn_financial_pro.quotation.v1` / Historical and Real-Time Quotes | cn financial pro Data | 必填 `codes`、`startdate`、`enddate`，不适合作为当前快照的首选 | 1 credit/result；最低 1/call |
| blocked（非股权现货） | `theta_data.option.attime.quote.retrieve.v3.07adf5d6` / Quote | Theta Data | 指定时刻的 OPRA 期权 NBBO，需日期/到期日/时间等参数 | 4.8 credits/call |
| blocked（非行情） | `fiu_mcp_server.postv3utilstradingsessionstatus.create.v2.24a2124f` / post_v3_utils_tradingSessionStatus | 融聚汇 | 交易时段状态，不提供价格；`timeMode` 必填 | 1 credit/call |
| blocked（非行情） | `fiu_mcp_server.postv1stockquoteextend.create.v2.a30e86e7` / post_v1_stock_quote_extend | 融聚汇 | 描述为基本信息，非报价快照；`symbols`、`timeMode` 必填 | 1 credit/call |
| blocked（未纳入试点） | `caidazi.get_real_time_record.execute.v1.7a43f96e` / A股实时行情快照 | Financial Buddy | 单只 A 股快照，描述有交易时间、bid/ask；schema 中 `symbol` 未标记为必填 | 1 credit/call |

另外 5 个已 Inspect 项是交易所/加密市场时间或市场状态工具，不是 A/HK/US 个股实时价格候选；完整 20 个 Inspect 记录和 31 个去重发现 ID 保留在 JSON。

## 硬门缺口与未知项

- 未执行任何候选；因此无响应样本、业务成功、实际扣费、可用性、权限或真实性证明。
- 对三个主候选，Inspect schema/描述均未证明响应中的 `quote_time`、时区、交易日、延迟秒数、as-of、行情会话、来源许可或实时/延时标识；不得将其输出标作实时事实。
- A/HK 候选没有在本次 Inspect 中声明美股覆盖；美股候选也未证明 A/HK 覆盖。跨市场统一工具仍是未知，不能假定。
- `cn_financial_pro.real_time_quotation`、FIU v3、QVeris L1、Finnhub、Twelve Data 的目录描述可作为备选信号，但并不替代市场/会话/时间字段的逐项验证。
- 付费试点前必须冻结：每市场一个合法标的、预期会话、允许延迟阈值、必须返回的价格/bid/ask/quote-time/session/provenance 字段、最大成本和失败即停止条件；试点执行需单独授权。

## 请求与余额

- Search：5 次（每次 `limit=10`）；去重候选 31 个；Inspect：1 次，20 个 Tool ID；Execute：0。
- 服务器在首个许可请求后报告余额 `[redacted]`，末个 Inspect 后报告 `[redacted]`。这不是调用前的余额，且显示值发生变化，可能为显示精度或服务端记账语义；不能据此推导成本、免费额度或准确性。
- JSON 已按脚本规则剔除敏感字段；验证确认无认证值、请求 header 或 `/execute` 调用记录，`execute_path_called=false`。
