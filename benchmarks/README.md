# Benchmark corpus

正式测试输入为 `candidates/v0.3/` 与 `oracles/v3/`：财务报表、历史行情、实时行情各 100 题。题目为接近真实用户表达的自然语言 Query；Oracle 与 public `get` 隔离。

- 财报：88 `success`、5 `needs_clarification`、7 `no_data`；成功题只取同一公司、同一张原始报表、同一期间的直接披露字段。
- 历史：82 `success`、2 `needs_clarification`、6 `no_data`、10 `unsupported`；可接受答案变体必须是完整、来源一致的答案，不得跨变体拼接。
- 实时：90 `success`、6 `needs_clarification`、2 `no_data`、2 `unsupported`；成功题冻结请求/语义合同，运行时 receipt 决定能否评分数据准确率。

先执行 `python oracles/v2/validate_v2.py`，再执行 `python oracles/v3/validate_v3.py`。v3 验证器检查 3×100、候选/Oracle/manifest 哈希链、ID 与状态对齐、四指标、跨版本 Query 去重，以及财报/历史的组合去重。

不要修改 `v0.1/v1` 或 `v0.2/v2`：它们只作为 v3 冻结链引用的不可变基线。新增版本时先完成独立 Oracle，再重算 manifest 哈希并通过验证；不要保存原始数据、供应商回执或运行结果。
