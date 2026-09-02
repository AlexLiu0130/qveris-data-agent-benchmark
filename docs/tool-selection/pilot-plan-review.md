# Pilot plan 独立复核（仅目录证据，未 Call）

复核对象：`pilot-plan.md`、`tool_manifest.candidate.json`，以及 2026-09-02 本地三个 Inspect 目录。未联网、未读取凭据、未执行 Call。证据路径均为 `artifacts/tool-audit/*_catalog.json` 的 `.requests.inspect.tools[]` 中对应 `tool_id`。

## 协议纠偏附记（后续事实）

本复核的“未 Call”仅描述其原始审计范围。后续 realtime v1 Alpha 与 v2 Finnhub Call 因请求体使用 `arguments` 而非官方 `parameters` 协议，均为 **INVALID_METHOD/DISCARDED**；不得用其 HTTP/业务/延迟/成本观察淘汰或评价工具。纠偏 v3 复用 Finnhub 以隔离协议变量，并冻结 `connector_protocol_version=qveris.execute.parameters.v1`。

后续 v3 Finnhub 已是 **VALID_RUN**：HTTP 200、business success、实际 1 credit、1211ms。该事实仅将其提升为 schema-qualified；响应无 `symbol`、`source`、`session`、`currency`，且 `t` 语义未被 Inspect 输出 schema 覆盖，所以 accuracy/freshness 仍 blocked，亦无最快、稳定或准确结论。

## 结论

- 9 个 `tool_id`、固定价、必填字段、字段类型和显式枚举均与 Inspect 目录一致；FMP 三表的 `24.2 × 3 = 72.6` 正确。
- 6 个候选具有可按目录构造的参数；3 个 Hang Seng `stockObject` 候选均为 **BLOCKED/P0**：目录只给 `array` 和类别式枚举（`STOCK_A_COMPANY` / `STOCK_HK_COMPANY`），没有元素 payload 的代码格式或实例。不得把该枚举名或猜测的代码字符串当作可执行合同。
- `cn_financial_pro.adjusted_price.v1` 使 100-credit 上限不可保证：其目录仅给 `0.04 credits/quantity`、每 Call 最低 1，未定义 quantity 上界或 quantity 与请求/响应的关系。`78.6` 是最低/估计额，不是最大目录成本；Call 后再根据收据停止，无法约束该 Call 自身超支。
- 未发现**已由目录同时证明相同准确性/语义且更低成本**的替代候选；但原计划中的“唯一”措辞过强，须删除或降为“当前首选”。有同价、可构造的替代项，见“选择断言修正”。

## 逐候选复核

| 别名 | 结论 | Inspect 合同与代表参数 | 成本/单次语义 | 缺口或行动 |
| --- | --- | --- | --- | --- |
| `rt_a_live` | **BLOCKED · P0** | `hangseng_polysource.a_shares_live_quote.query.v2.10fe0581`：必填 `stockObject: array`；可选 `pageNo: integer`、`pageSize: integer`；枚举仅 `STOCK_A_COMPANY`。 | `call_count`，1 credit/call；一次请求可带数组，目录未承诺只返回一个标的。 | 向 tool owner 索取 `stockObject` 元素 JSON/代码语法及单标的示例；获得前无代表参数。 |
| `rt_hk_live` | **BLOCKED · P0** | `hangseng_polysource.quote.hkshares.live.v2.dec427af`：必填 `stockObject: array`；可选 `pageNo: integer`、`pageSize: integer`；枚举仅 `STOCK_HK_COMPANY`。 | `call_count`，1 credit/call；数组表示可能批量。 | 同上；不得猜测 `00700`、`00700.HK` 或枚举标签本身。 |
| `rt_us_bulk_live` | **PASS（schema）；P0 代表标的来源** | `alphavantage.realtime_bulk_quotes.retrieve.v1.7aca3c4a`：必填 `function: string = REALTIME_BULK_QUOTES`、`symbol: string`；可选 `datatype: string ∈ {json,csv}`。代表：`{function:"REALTIME_BULK_QUOTES",symbol:"AAPL",datatype:"json"}`。 | `call_count`，1 credit/call；单 Call 允许最多 100 个逗号分隔 symbol，代表输入限制为 1。 | 输出的 quote time/session/delay/provenance 未 Inspect；目录没有给 `AAPL` 的本工具实例。该 ticker 必须由冻结 case 提供，不能作为无来源的猜测参数。 |
| `hist_a_adjusted_daily` | **PASS（目录合同）；预算 P0** | `cn_financial_pro.adjusted_price.v1`：必填 `codes,startdate,enddate: string`；可选 `cps,interval: string`。目录明确 `cps` 值 `1,2,3,6,7` 和 `interval` 值 `D,W,M,Q,Y`，但未机器枚举。代表：`{codes:"600519.SH",startdate:"2024-01-02",enddate:"2024-01-02",cps:"2",interval:"D"}`。 | `custom_usage`，0.04/quantity，最低 1/call；一次 Call 可有最多 50 个逗号分隔 code，日期范围可多日。代表参数限制为一代码一日期。 | quantity 公式/上界、返回 OHLCV/factor 实体、收据均未证明。`600519.SH` 是目录 examples 中的值；日期只是合格式，是否有有效日线仍待 Call。 |
| `hist_hk_range_adjusted` | **BLOCKED · P0** | `hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4`：必填 `beginDate,endDate: string` 和 `stockObject: array`；`endDate` 必须大于 `beginDate`。可选 `restorationStatus: string = FUQUAN`、`pageNo,pageSize: string`。代表已知部分：`{beginDate:"2024-01-02",endDate:"2024-01-31",restorationStatus:"FUQUAN"}`。 | `call_count`，1 credit/call；股票数组可多标的，目录描述为 range/interval quotes，未承诺日 OHLCV。 | `stockObject` 元素合同缺失；单标的请求无法构造。即使解除，输出仍须验证日线、复权和边界语义。 |
| `hist_us_eod_adjusted` | **PASS（目录合同）** | `tiingo.daily.ticker.prices.list.v1`：必填 `ticker: string`；可选 `startDate,endDate,format,resampleFreq: string`。代表：`{ticker:"AAPL",startDate:"2024-01-02",endDate:"2024-01-31"}`。 | `call_count`，1 credit/call；按 ticker 和可选日期范围的一次请求，目录未定义最大行数。 | 描述称 EOD OHLCV、adjusted、dividends/splits；实际字段、inclusive 语义、时区、provenance 和回执未证明。 |
| `fin_is_as_reported` | **PASS（schema）；P0 代表标的来源** | `financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47`：必填 `symbol: string`；可选 `limit: number`、`period: string ∈ {annual,quarter}`。代表：`{symbol:"AAPL",limit:1,period:"annual"}`。 | `call_count`，24.2 credits/call；一次 Call 只请求 IS endpoint，`limit:1` 限制返回条数。 | 精确 fiscal year 无输入；公司/市场、币种/单位、filing provenance 与收据均待 Call。`AAPL` 是文案 Apple 示例的外部 ticker 映射，冻结 case 未提供该映射，故此代表参数为 **P0**。 |
| `fin_bs_as_reported` | **PASS（schema）；P0 代表标的来源** | `financialmodelingprep.stable.balancesheetstatementasreported.retrieve.v1.8e37f918`：必填 `symbol: string`；可选 `limit: number`、`period: string ∈ {annual,quarter}`。代表：`{symbol:"AAPL",limit:1,period:"annual"}`。 | `call_count`，24.2 credits/call；一次 Call 只请求 BS endpoint，`limit:1` 限制返回条数。 | 同 IS；`AAPL` ticker 映射没有冻结 case 证据，故此代表参数为 **P0**。 |
| `fin_cf_as_reported` | **PASS（schema）；P0 代表标的来源** | `financialmodelingprep.stable.cashflowstatementasreported.retrieve.v1.753a5642`：必填 `symbol: string`；可选 `limit: number`、`period: string ∈ {annual,quarter}`。代表：`{symbol:"AAPL",limit:1,period:"annual"}`。 | `call_count`，24.2 credits/call；一次 Call 只请求 CF endpoint，`limit:1` 限制返回条数。 | 同 IS；`AAPL` ticker 映射没有冻结 case 证据，故此代表参数为 **P0**。 |

`PASS（目录合同）` 只表示输入可从 Inspect 构造，不表示业务成功、实时性、输出字段或准确性通过。

## 预算核算与 blocked 判断

固定计费候选为实时 3、历史港/美 2、FMP 72.6，共 **77.6** credits。A 股复权工具最低 1，故九项目录最低/预计额为 **78.6** credits：`3 + 2 + 72.6 + 1 = 78.6`。原 manifest 的两个数值均正确。

但 `adjusted_price` 是 quantity 计费而非 1-credit 固定计费；因此九项**最大目录成本为未知/无上界**，不能说在 100 内。剔除三个已 BLOCKED 的 Hang Seng 调用后，当前可构造的六项目录最低额是 **75.6** credits（固定 74.6 + CN 最低 1），最大仍未知。

若 100 credits 是硬预算，当前计划不能执行 CN 调用，除非 tool owner 先书面给出一个可用 quantity 的上界，并在该上界下满足：`77.6 + CN_actual ≤ 100`（九项）或 `74.6 + CN_actual ≤ 100`（去除 blocked 的六项）。否则“记录/收据达到 100 后停止”只能阻止后续 Call，不能避免本次 quantity 结算越界。

## 选择断言修正

没有发现目录已证明的“更低成本且同原始/as-reported 准确性”财报替代：`cn_financial_pro` 三表最低 1 credit，但仅声明财报数据库/报告类型，未声明 raw/as-reported；Alpha Vantage 三表也是 1 credit，却明确 normalized。因此不能替代 FMP 的原始性硬门。

实时与历史也没有低于 1-credit 最低价、且目录足以证明相同语义的候选。不过下列同价可构造候选使“唯一”不成立，应仅称现有工具为“首选”：

- `caidazi.get_real_time_record.execute.v1.7a43f96e`：1 credit，单 A 股 `symbol: string`，描述给出 `600519.SH`/`300750.SZ` 格式并声明 14 个快照字段和交易时间；但 schema 把 `symbol` 标作非必填，业务合同矛盾，不能直接替代，值得作为解除 Hang Seng P0 的对照候选。
- `cn_financial_pro.real_time_quotation.v1`：A/港实时、`codes: string` 例子明确，最低 1（同样 quantity 无上界）；可构造但不是更低成本，也未证明 as-of/provenance。
- `cn_financial_pro.history_quotation.v1`：与 A 股代表参数同样有 `codes/startdate/enddate`、`cps`、`interval`，最低 1；但目录没有明确 adjusted OHLC、量与因子，不能证明与 `adjusted_price` 同准确性。
- `qveris_finance.mkt_bars_eod`：描述明确“Daily closing OHLCV”与 optional adjusted/dividend/split，1 credit；其 `interval` schema 同时举例 `5min`，但这不能抹去 description 的 EOD 语义。由于没有明确的复权控制参数与港股覆盖，它不是已证明等价替代，却应从原计划的“工具名/5min”否定理由中移除。

## 可执行 Call allowlist（解除 P0 前）

以下仅为将来获得另行 Call 授权时的**参数 allowlist**；不代表现在可执行，也不降低各项业务/输出硬门。所有外部 ticker 映射须先由冻结 benchmark case 提供，避免把代表值当作目录事实。

```json
[
  {"tool_id":"alphavantage.realtime_bulk_quotes.retrieve.v1.7aca3c4a","params":{"function":"REALTIME_BULK_QUOTES","symbol":"AAPL","datatype":"json"}},
  {"tool_id":"cn_financial_pro.adjusted_price.v1","params":{"codes":"600519.SH","startdate":"2024-01-02","enddate":"2024-01-02","cps":"2","interval":"D"}},
  {"tool_id":"tiingo.daily.ticker.prices.list.v1","params":{"ticker":"AAPL","startDate":"2024-01-02","endDate":"2024-01-31"}},
  {"tool_id":"financialmodelingprep.stable.incomestatementasreported.retrieve.v1.a9a4ed47","params":{"symbol":"AAPL","limit":1,"period":"annual"}},
  {"tool_id":"financialmodelingprep.stable.balancesheetstatementasreported.retrieve.v1.8e37f918","params":{"symbol":"AAPL","limit":1,"period":"annual"}},
  {"tool_id":"financialmodelingprep.stable.cashflowstatementasreported.retrieve.v1.753a5642","params":{"symbol":"AAPL","limit":1,"period":"annual"}}
]
```

Excluded pending P0 payload contracts: `hangseng_polysource.a_shares_live_quote.query.v2.10fe0581`, `hangseng_polysource.quote.hkshares.live.v2.dec427af`, and `hangseng_polysource.hk.stock.range.quote.create.v2.820f91d4`.
