# 历史行情 Tool 候选审计（仅目录）

审计时间：2026-09-02T04:23:25Z。证据仅来自本地生成的 [historical_catalog.json](../../artifacts/tool-audit/historical_catalog.json)：5 次 Search（每次 `limit=10`）和 1 次去重后的 Inspect（20 个 tool）；未调用 Execute。因此下表的“覆盖”是工具目录声明和输入 schema，**不是**实际数据、响应 schema、权限或计费回执的验证。

## 结论

当前没有一个已 Inspect 的单一 tool 能证明同时满足 A/港/美股票、日线和分钟 OHLCV、日期范围、复权、交易日及明确时区。按仓库的“一 case 仅一次 tool execution”合同，不能把跨市场拼接方案直接纳入运行时 benchmark；先做下列三个有偿 pilot 的单工具实际验证，再决定是否按市场拆分 suite。

## 直接数据候选

| 市场/用途 | Tool（已 Inspect） | 目录声明的覆盖与输入 schema | 目录预计成本 | 硬门缺口 |
| --- | --- | --- | --- | --- |
| A 股日线、日期范围、复权 | `cn_financial_pro.history_quotation.v1` | 股票；`codes,startdate,enddate,interval,cps,fill`；日/周/月等，`cps` 可选 | 1 credit/result；最低 1/call | OHLCV 字段、复权语义/因子、实际可用日期与时区均未执行验证 |
| A 股复权日线 | `cn_financial_pro.adjusted_price.v1` | 调整后 OHLC、成交量、复权因子；`codes,startdate,enddate,cps,interval` | 0.04 credit/result；最低 1/call | 仅日以上周期；无分钟；响应字段和计量数量未证实 |
| A 股分钟 OHLCV | `cn_financial_pro.hf_basic_quotation.v1` | 1/3/5/10/15/30/60 分钟，开高低收量及成交额等；`codes,starttime,endtime,interval` | 0.00132 credit/result；最低 1/call | 没有复权参数；时区未在 schema 声明；时间跨度/返回上限未知 |
| A/港交易日 | `cn_financial_pro.trade_dates.v1` | SSE、SZSE、HKEX；`marketcode,startdate,enddate,mode,date_type,period,date_format` | 2 credits/call | 仅交易日期，无时区/交易时段字段的实际回包证据；美股未声明 |
| 港股区间与复权 | `hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4` | 港股区间报价、量额；`beginDate,endDate,stockObject,restorationStatus,pageNo,pageSize`；`FUQUAN` 枚举 | 1 credit/call | “interval prices”不是明确的日/分钟 OHLCV 合同；时区、逐 bar schema、范围上限未知 |
| 美股分钟 OHLCV | `alphavantage.time_series_intraday.retrieve.v1.1e18340d` | 1/5/15/30/60 分钟；`symbol,interval,adjusted,extended_hours,month,outputsize,datatype`；目录称 2000-01 起单月查询，含美东盘前盘后说明 | 1 credit/call | 仅目录声明美股；实际历史月份、调整结果、响应时区/上限、权限未验证；没有日线 |
| 美股日线、日期范围、复权字段 | `tiingo.daily.ticker.prices.list.v1` | EOD、OHLC、成交量、adjusted、股息、拆股；`ticker,startDate,endDate,format,resampleFreq` | 1 credit/call | 无分钟；时区、市场覆盖、逐字段回包未验证 |
| 多市场 EOD 备选 | `eodhd.eod_historical_data.retrieve.v1.a43f3b91` | 日/周/月及 `from,to`；`symbol_exchange,fmt,period,order,from,to,filter` | 2.81 credits/call | 没有分钟/复权参数；A/港交易所代码和实际覆盖未验证 |
| 多市场 EOD/潜在分钟备选 | `qveris_finance.mkt_bars_eod` | equities/ETFs、可选 adjusted、`D/W/M/5min`；`symbol,start_date,end_date,interval` | 1 credit/call | 工具名为 EOD 而 schema 描述含 `5min`，冲突需 Execute 澄清；A/港/美、复权和时区未验证 |
| 多市场图表备选 | `yahoo_finance.finance_chart.v1` | 1m 至月线和预设 `range`；`symbol,interval,range,events,includePrePost` | 1 credit/call | 没有任意起止日期参数；复权 OHLC、市场覆盖与时区未验证 |
| A 股日线备选 | `financialmodelingprep.stable.historicalpriceeod.dividendadjusted.retrieve.v1.1e0b27c9` | 目录明确 A 股示例、股息调整 OHLCV；`symbol,from,to` | 24.2 credits/call | 无分钟；港/美与时区未验证；成本显著高于其他未验证候选 |

目录还返回了衍生指标工具（A 股 ATR/OBV/MACD）、停牌工具和一个标记为 cryptocurrency 的 EODHD 变体；它们不构成所需的原始历史 OHLCV 候选，未列入 pilot。

## 有偿 pilot 候选（非排名、非准入）

1. **A 股：`cn_financial_pro.hf_basic_quotation.v1`**（分钟）与 `cn_financial_pro.adjusted_price.v1`（复权日线）分别做 case；另以 `cn_financial_pro.trade_dates.v1` 做日历 case。理由是目录 schema 直接声明了所需参数；仍不能合并为一个执行或一个最终候选。
2. **港股：`hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4`**。理由是目录唯一明确港股区间及 `FUQUAN` 参数的已 Inspect 项；先证实它是否返回日线 OHLCV 和日期边界。
3. **美股：`alphavantage.time_series_intraday.retrieve.v1.1e18340d`**（分钟）与 `tiingo.daily.ticker.prices.list.v1`（日线）分别验证。理由是两者各自直接声明分钟或 EOD/adjusted；它们是两个独立 provider/tool，不能声称为单工具覆盖。

上述顺序仅用于最小化信息缺口，不代表“最好”、价格最优、可生产或可作为 benchmark 的结论。

## 必经硬门与未知

- 每个候选仅做一次、带代表性 symbol 的付费 Execute 后，核对真实 OHLCV/复权/日期边界/交易时段/时区字段和实际收据成本；本审计没有执行该步骤。
- 验证符号格式、A/港/美实际覆盖、分钟历史保留期、单请求行数/分页、复权定义及公司行为回溯。
- 将日历与 bar 的交易所、时区和非交易日填充政策关联；目前只有部分输入 schema 宣称市场代码，未取得响应证据。
- 在增加 case 或评分代码前，先定义并冻结四个 benchmark 指标的名称与计算；本次没有新增 case/评分。
- 运行时不得 Search/Inspect；每个 case 只能有一次结构化输出和一次工具执行。因此如需要跨市场覆盖，应先用 pilot 结果决定按市场/频率拆分 suite，不能依赖运行时路由拼接。

## 审计计量与安全检查

- server-reported balance：首次允许请求后 `[redacted]`，最后 Inspect 后 `[redacted]`；语义仅为服务端在对应响应中报告的余额，不能推导本次真实扣费、费用或准确性。
- 请求数：Search `5`、Inspect `1`、Execute `0`；Search 去重后发现 `34` 个 tool，Inspect 请求并返回 `20` 个。
- 原始凭据未写入产物；产物仅保留安全摘要。复核命令须确认敏感键名不存在且 `request_count.execute == 0`、`execute_path_called == false`。
