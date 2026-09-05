# QVeris Data Agent Benchmark

QVeris 的黑箱端到端数据取数评测：输入是真实风格的自然语言 Query，系统只能经一次公开 `get` 返回一个结构化响应。Benchmark 不检查内部推理、检索或数据供应商，只评测最终用户结果。

```text
Natural-language query -> one public get -> structured response -> scorer
```

## 范围与当前状态

目标是三个独立 Suite，各 100 题：`realtime_quote`、`historical_price`、`financial_statements`。`v1` 的 legacy official Manifest 固定为每套 80 道 normal / 20 道 boundary；`v2` 改为由冻结 Manifest 显式定义状态分布：财报 88/12、历史行情 82/18、实时行情 90/10（success / 非 success）。市场配额为 A 股 29、港股 28、美股 28、日本 5、英国 5、德国 5。

推荐审阅入口为 `benchmarks/candidates/v0.2/` 与 `benchmarks/oracles/v2/`：三个 Suite 各 100 题。财报改为同一张原始报表、同一报告期内的 1–6 个直接披露字段（88 成功、5 澄清、7 无数据），不再要求完整报表或计算；历史行情以常见实体默认解析（82 成功、2 澄清、6 无数据、10 不支持），多市场场景保留来源一致的完整可接受变体；实时行情为 90 个运行时快照合同和 10 个冻结状态题。`v0.1` 与 `oracles/v1` 是不可变基线，仍保留用于 v2 的来源绑定。

`oracles/v2/validate_v2.py` 已校验 v2 的候选/Oracle 对齐、v1 绑定及哈希链、财报原始字段投影、历史的来源和 K 线期间、以及实时运行时回执合同；这仅是内容完整性门禁，不代表已运行真实评测。

历史行情 v2 不声称供应商授权、再分发权或官方交易所地位；严格双源授权核对是后续升级项。单一完整、可追溯的公开来源可构成一个候选变体；若完整来源之间存在差异，保留各自完整且来源一致的变体，不平均也不跨源拼接。

本地确定性 Runner、Scorer 和只读 Arena HTTP/SSE 投影已实现。`v2_compiler` 会将 v0.2 candidate 与 v2 Oracle 编译为 `run-manifest-template.v2.json` 和 `oracle-bundle.v2.json`；真实 Variant 与 realtime reference contract 可生成正式 ready Manifest。也可用下面的 300 题 diagnostic 入口检查任意公开 GET 插件的全链装配：它会执行三套各 100 题，但 realtime 的动态数据准确率会明确标为 `not_scored`，不产生排名或正式 Case Pass。QVeris Model Gateway 与 Tool client 已接入；AAPL quote 已完成一次单次 live smoke（一次模型调用、一次 Tool execution、严格结构化响应）。该单样本不证明三条开放路由稳定，不证明历史行情或财报已获 runtime 准入，也不构成本 300 题的正式评测、Case Pass、榜单或生产部署。`v1` 独立 Manifest / `oracle-bundle/v1` 仅作 legacy 兼容。

## 300 题 GET 插件装配

```bash
python scripts/run_benchmark.py --fixture --output-dir /private/tmp/qveris-benchmark-300
python scripts/run_benchmark.py --get-client your_module:make_client --output-dir /private/tmp/qveris-benchmark-300
```

插件 factory 必须返回 `{"variant": ..., "client": PublicGetClient}`；没有 `--fixture` 或 `--get-client` 不会默认使用 mock。Runner 子进程只把 `case_id`、`suite` 与 `query` 交给 GET；冻结 Oracle 与 Scorer 留在父进程。此为模块/子进程边界，并非同一 OS 账户下的绝对 sandbox；输出目录必须在仓库外。入口使用的 `runner-score-policy.v2.json` 仅限 diagnostic non-ranking；fixture 只验证 300 次调用和合同，不是模型得分，也不会发出 Provider 请求。

## OCI sandbox GET

`--sandbox-image` 不导入候选插件：每个 Case 启动一个固定 digest 的 Docker image，只有 `request_id` 与 Query 经 stdin 进入；无 repo、Oracle、socket、host 环境或 bind mount，且强制 nonroot、read-only、capability drop、资源上限和 `--network none`。image 通过受限 stdio broker 请求当前固定 Gateway/Tool URL；host 才持有凭据。broker 至多接受一次固定模型请求和一次固定 Tool 请求，记录 host-observed dispatch；这不是 image 内部推理/调用的自证，也不能用于 official run。

先以显式 runtime config 创建不含题库的 build context，再以 Docker 返回的 immutable digest 执行；不要以仓库根目录作为 Docker context：

```bash
python scripts/stage_sandbox_image.py --runtime-config /private/tmp/sandbox-runtime-config.json --output-dir /private/tmp/qveris-sandbox-context
docker build -t qveris-sandbox /private/tmp/qveris-sandbox-context
docker image inspect --format '{{.Id}}' qveris-sandbox
# use the resulting local sha256:... ID (or repository@sha256:...) plus a parent-only sandbox-get-descriptor/v1
python scripts/run_benchmark.py --sandbox-image sha256:... --sandbox-variant /private/tmp/sandbox-variant.json --output-dir /private/tmp/qveris-sandbox-run
```

The checked-in `runner/sandbox-fixture/` is network-free and only for a one-container isolation smoke; no 300-container sandbox run or paid Gateway call is included.

84 格工具盘点中的 `financial.direct_line_items.specified_period.v1` 只在已有规范化回包的边界内做确定性字段投影：语义层先将用户用语解析为规范字段和唯一所属三表，再只调用一项相应 Tool；跨三表请求必须拒绝，投影层不猜字段别名、不透传原始供应商字段。当前仅 SZSE 三表和 HKEX 的 FIU 13 个利润表字段有该投影证据；港股仅限 00700.HK FY2024，其他市场仍是待补 mapper 的 `gap`，均未运行时接入。

## 运行合同

- 每个 evaluation cell：`agent_variant × get_variant × case × trial`。
- 每个 cell 仅一个 Agent、一次公开 `get`、一个结构化输出；禁止 `Search` 与 `Inspect`。
- `get` 内部模型调用必须走 QVeris Gateway；固定模型配置与禁止静默 provider fallback 已接入。AAPL quote 的一次单次 live smoke 验证了该路径，但不构成三条开放路由稳定性或正式 benchmark 完成的证明。
- 合法响应状态：`success`、`partial`、`needs_clarification`、`unsupported`、`no_data`、`error`。`error` 不能是正确预期。

## 四项指标

| 指标 | 计算口径 |
| --- | --- |
| `semantic_accuracy` | 可评分 Case 中，`resolved_request` 与该题 Semantic Oracle 一致的比例。 |
| `data_accuracy` | 可评分原子数据断言的通过比例；Oracle 未冻结时必须为 `not_scored`。 |
| `end_to_end_latency` | Runner 从发出请求到收到完整结构化响应的单调时钟耗时。 |
| `token_usage` | `get` 内部 QVeris Gateway 的实际 token receipt；不可观测时为 `unknown`，不能估算为 0。 |

`Case Pass` 是派生门禁，而非第五个指标：`schema_valid AND status_correct AND semantic_pass AND data_pass AND NOT timeout`。当 `data_accuracy` 未评分时，不得产出正式 Case Pass 或总榜排名。

Benchmark、Runner、Scorer 与 Arena 统一公开指标名为 `end_to_end_latency`。读取旧版 policy 时兼容 `e2e_latency`，但投影和排名不会再输出旧名。

## 目录边界

- [`benchmarks/`](benchmarks/README.md)：候选题库、版本清单和题库验证说明。
- [`runner/`](runner/README.md)：已实现的本地 Runner、Scorer 和 Arena 的运行与记录合同。
- [`get/`](get/README.md)：已实现的 injected public `get` adapter 与响应合同；QVeris Model Gateway 与 Tool client 已接入，且 AAPL quote 已有一次单次 live smoke。
- [`docs/architecture.md`](docs/architecture.md)：责任边界与完整数据流。

禁止提交凭据、token、原始供应商响应、原始运行结果、私有 Oracle 快照，或任何 paid pilot / provider probe 资产。
