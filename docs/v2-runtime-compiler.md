# 最终题库的运行产物编译

运行时继续使用 v2 合同；最终 300 题使用候选题库 `v0.3` 和 Oracle `v3`。先验证冻结链，再编译：

```bash
python benchmarks/oracles/v3/validate_v3.py
PYTHONPATH=src python -m qveris_benchmark.v2_compiler \
  --benchmark-root benchmarks \
  --candidate-revision v0.3 \
  --oracle-revision v3 \
  --output-dir /private/tmp/qveris-compiled
```

编译器原子写出：

- `oracle-bundle.v2.json`：300 个可由现有 Scorer 加载的编译 Oracle、冻结摘要、来源 manifest hash 与实时数据要求。
- `run-manifest-template.v2.json`：300 个 Runner Case、四项指标、来源 manifest hash 与 freeze digest。没有真实 Agent/Get/Model identity 与 realtime reference contract 时只能作为模板。

财报使用 `data.facts.<normalized_assertion_id>`；历史的每个完整答案是绑定 `resolved_request.accepted_variant_id`、`data.accepted_variant_id` 与完整 `data.bars` 的一个 `alternative_assertion_sets` 变体；实时动态题不含静态价格，标为 `data_not_scored_until_receipt`。

提供至少两个 `--variant-json` 和一个只含 `source_contract_hash`、`window_rule_version` 的 `--reference-contract-json` 后，编译器写出可运行的 `run-manifest.v2.json`。它不会生成 Variant identity 或 reference hash。`freeze_digest` 绑定候选 manifest、suite manifest、编译器、编译 Oracle 和查询解析策略。
