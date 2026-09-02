# 历史 OHLCV pilot v4

## 决定

v4 仅选择一个 Tool：`tiingo.daily.ticker.prices.list.v1`，请求
`AAPL` 的 `2026-08-28` 单日 EOD 数据。它只获准作为单 Tool 的 OHLCV
合同 pilot；两 Tool 直接比较仍为 **blocked**。该 case 仅一次 Execute、无重试、无 fallback，硬预算为 **1 credit**。

此范围不能在实际回包前证明交易日历、时区、响应 schema、来源、公司行动语义或真实收据成本。

## 保留的 Search/Inspect 证据

来源：`artifacts/tool-audit/historical_catalog.json`，捕获于
`2026-09-02T04:23:25Z`；其中有 5 次 Search、1 次 Inspect、0 次 Execute。

| 项 | Tiingo v4 事实 |
| --- | --- |
| Tool / provider | `tiingo.daily.ticker.prices.list.v1` / `Tiingo (Market Data)` |
| AAPL 输入 | Inspect 的 `ticker` 示例为 `AAPL` |
| 日期参数 | `startDate`、`endDate`，均给出 ISO 风格日期示例 |
| 未填可选参数 | `format`、`resampleFreq`；Inspect 未完整枚举默认值/可取值，v4 不猜填 |
| 目录声明的字段 | EOD open/high/low/close/volume、调整价格、分红和拆股 |
| 成本 | `call_count`，每 Call 固定 1 credit |
| 上界 | 1 case × 1 Call × 1 credit = 1 credit |

`2026-08-28` 相对 catalog 捕获时间已过去，但并非经权威美国交易日日历核验的交易日。该日有非空回包是本 pilot 的必要证据，不构成日历合同证明。

## 实际 single-Tool 结果

Tiingo case 为 **VALID_RUN**：HTTP 与 provider 业务成功，收据成功，实际成本为 1 credit，端到端耗时 1871ms。回包直接提供目标单日的 OHLCV、adjusted、dividend 与 split；日期字段存在且内部一致。

该回包未回显 `symbol`、`source` 或 `asOf`，标的身份仅依赖冻结 request 的 `ticker=AAPL` 绑定。因此结果状态为 **schema-qualified / accuracy-unverified**：它证明该固定请求返回了可解析的单日字段，不证明独立身份、来源、准确性、新鲜度、稳定性或最快速度。跨 Tool 比较仍为 **blocked**。

## 无第二个 Tool 的原因

唯一已 Inspect 的 Alpha Vantage 候选为
`alphavantage.time_series_intraday.retrieve.v1.1e18340d`：虽固定 1 credit、参数完整，但它是以 `month` 选取历史的日内 Tool，不能直接生成与 Tiingo 单日 EOD 原生 OHLCV 可比的记录。Alpha Vantage 日线 Tool 只出现于 Search，未进入保留的 Inspect 批次，不能写入冻结计划。

EODHD 虽有固定成本和精确日期参数，但保留 catalog 只声明历史日/周/月数据，未建立可直接比较的 OHLCV 回包字段合同，因此不能作为第二 Tool。

## 单次验收与停止

Runner 使用冻结的 `parameters` 协议，运行时不得 Search/Inspect。仅当以下条件全部成立才接受：

1. QVeris 传输和 provider 业务成功均明确；
2. 回包识别 AAPL，并含目标日的 date/open/high/low/close/volume；调整值、分红、拆股必须与 raw OHLCV 分开标记；
3. 有 source/as-of/timezone，缺失则仅能 `degraded`；
4. 已对账 receipt 的实际成本不超过 1 credit。

任何失败均消耗唯一尝试，不得换 Tool 或补发。一次通过只使 Tiingo 单 Tool 合同进入复核，不证明准确性、新鲜度、日历覆盖或跨 provider 一致性。

## 工件

- `benchmarks/pilot/approved-runtime-manifest-v4.json`
- `benchmarks/pilot/approved-runtime-plan-v4.json`

两者均由现有 `benchmarks/pilot/approved-*.json` 规则忽略。
