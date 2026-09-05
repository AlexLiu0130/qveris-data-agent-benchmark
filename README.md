# QVeris Data Agent Benchmark

面向公开 `get` 接口的黑箱端到端评测。输入是自然语言 Query；每题只允许一个 Agent、一次公开 `get` 与一个结构化响应。`Search` 与 `Inspect` 禁止使用，内部推理和数据供应商不参与评分。

最终题库为 `v0.3/v3`：财报、历史行情、实时行情各 100 题，共 300 题。正式指标仅有四项：`semantic_accuracy`、`data_accuracy`、`end_to_end_latency`、`token_usage`。

## 运行

先验证冻结链：

```bash
python benchmarks/oracles/v2/validate_v2.py
python benchmarks/oracles/v3/validate_v3.py
```

编译最终 300 题。运行产物仍使用 runtime v2 合同，`v0.3/v3` 只表示题库与 Oracle 版本：

```bash
PYTHONPATH=src python -m qveris_benchmark.v2_compiler \
  --benchmark-root benchmarks \
  --candidate-revision v0.3 \
  --oracle-revision v3 \
  --output-dir /private/tmp/qveris-compiled
```

离线 fixture 运行完整的 300 题执行链：

```bash
python scripts/run_benchmark.py --fixture \
  --output-dir /private/tmp/qveris-benchmark-fixture
```

接入实际 public `get` 时，factory 必须为 `module:factory`，并返回 `{"variant": ..., "client": PublicGetClient}`：

```bash
python scripts/run_benchmark.py \
  --get-client your_module:make_client \
  --output-dir /private/tmp/qveris-benchmark-live
```

实际 GET 配置使用 `QVERIS_MODEL_GATEWAY_API_KEY`、`QVERIS_API_KEY` 和 `QVERIS_MODEL_GATEWAY_MODEL`；模型调用必须经 QVeris Gateway。实时行情只有具备运行时 reference receipt 才评分数据准确率；没有 receipt 时为 `not_scored`，不能形成正式 Case Pass 或排名。

## 目录

- [`benchmarks/`](benchmarks/README.md)：v0.3 候选题、v3 Oracle 和冻结验证。
- [`runner/`](runner/README.md)：Runner、Scorer 与运行记录合同。
- [`get/`](get/README.md)：public `get` adapter 与响应合同。
- [`docs/`](docs/architecture.md)：架构、编译和公开响应说明。

禁止提交凭据、token、原始供应商响应、原始运行结果或私有 Oracle 快照。
