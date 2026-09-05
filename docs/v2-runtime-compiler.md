# v2 运行产物编译

运行 `python -m qveris_benchmark.v2_compiler --benchmark-root benchmarks --output-dir compiled` 默认校验 v0.2 候选、三份 v2 suite manifest、其声明的候选/Oracle/策略/v1 绑定哈希，再原子写出：

- `oracle-bundle.v2.json`：可由当前 Scorer 加载的 300 个编译 Oracle，以及冻结摘要、来源 manifest hash、Oracle 合同和运行时数据要求。财报使用 `data.facts.<normalized_assertion_id>`；历史行情的每个完整来源答案是一个 `alternative_assertion_sets` 分支，绑定 `resolved_request.accepted_variant_id`、`data.accepted_variant_id` 和完整 `data.bars`；90 个实时动态题均标为 `data_not_scored_until_receipt`，不含虚构价格。
- `run-manifest-template.v2.json`：300 个 Runner Case、四项指标、来源 manifest hash 和 freeze digest。没有真实 Agent/Get/Model 身份或实时 reference contract 时，它明确是模板，不能创建正式运行。

候选中的中文 `case_id` 会确定性映射为 ASCII 的 Runner `case_id` 和 `oracle_id`；两份编译产物都保留原始 `source_case_id`，因此运行日志仍可无歧义回溯到题库。

提供至少两个 `--variant-json` 和一个真实 `--reference-contract-json`（仅含 `source_contract_hash`、`window_rule_version`）后，输出为 `run-manifest.v2.json`。编译器不生成这些运行时身份或 reference hash。`freeze_digest` 绑定候选 manifest、三份 suite manifest、编译器、编译后 Oracle 内容和查询解析策略；`compiled_oracle_content_digest` 避免把 bundle 文件自身放入循环哈希。

最终 300 题使用同一编译器：增加 `--candidate-revision v0.3 --oracle-revision v3`。运行产物的 schema 仍为 runtime v2；`v3` 仅表示冻结题库与 Oracle 层，并继续复用 v2 查询解析策略。先运行 `python benchmarks/oracles/v3/validate_v3.py`：它会先要求 v2 基线通过，再检查 v3 哈希链、三套各 100 题、四项指标、ID/status 对齐、跨版本自然语言去重，以及财报字段组合与历史合同组合不重复。
