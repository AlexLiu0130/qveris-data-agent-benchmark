# Architecture

```text
v0.3 Query + v3 frozen Oracle
              |
              v
Runner: one Agent, one public get, one structured response
              |
              v
Scorer: semantic_accuracy, data_accuracy,
        end_to_end_latency, token_usage
```

| Component | Owns | Does not own |
| --- | --- | --- |
| Benchmark | Natural-language cases, independent Oracle, versioning | Agent implementation or provider choice |
| Runner | One-cell execution, timing, versioned Variant identity, boundary evidence | Internal `get` trace, Oracle creation, score repair |
| `get` | One structured response for one Query | Its own score or reasoning trace |
| QVeris Gateway | Internal model call and observable usage receipt | Benchmark result calculation |
| Scorer | Deterministic scoring from response, run record and frozen Oracle | Missing Oracle evidence or fallback data |

题库层为 `v0.3/v3`，运行合同为 v2：编译器生成 `oracle-bundle/v2` 与 Runner manifest。Oracle 始终留在 Runner/Scorer 侧，不交给 public `get`。公共响应结构见 [public GET response contract](public-get-response-contract.md)，编译步骤见 [runtime compiler](v2-runtime-compiler.md)。

实时数据没有独立 runtime reference receipt 时，`data_accuracy` 必须为 `not_scored`；不得用 GET 自身返回值证明数据准确率，也不得产出正式排名。
