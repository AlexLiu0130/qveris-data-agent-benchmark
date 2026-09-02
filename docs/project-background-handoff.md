# QVeris 数据采购交付：项目背景交接

## 项目目的

本项目服务于 Kimi 的数据采购交付：让 Kimi 能通过 QVeris `get` 接口取得受控的基础金融数据。目标是把经后续选择与验证的 QVeris Tool 调用能力包装为稳定的数据取数链路；这不是 Kimi 侧产品或生产交付已完成的声明。

全链路为：

```text
用户 → Kimi → QVeris get 接口 → QVeris Tool/供应商 → 结果 → Kimi → 用户
```

Kimi 是目标调用方和交付背景。当前不假设内部语义 Agent 的运行时模型已确定为 Kimi；实际 model profile 仍待后续配置和复核。

## 当前唯一研发范围

我们只负责 QVerisGet 接口内部链路：

```text
语义 Agent → 固定 alias connector → 原样 payload 或受控状态 → QVerisGet 公共 response
```

运行时不执行 Search。Agent 保持小而精：只负责把用户请求结构化为语义和参数，并映射到一个固定 Tool。Token 计费、延迟和准确率不属于 Agent 或 `get` 业务逻辑，后续由外部评测框架处理。

QVerisGet 输入是 query、request_id、idempotency_key；公共输出严格只有 request_id、status、tool_alias、payload、message，不返回 metrics、plan、usage、token、cost、latency、Tool ID、凭据或 idempotency。READY 是 1 次 Agent + 至多 1 次 connector；CLARIFY、REJECT、SEMANTIC_ERROR 是 1 次 Agent + 0 次 connector。内部 trace_sink 只供外部 Harness 取 receipt、connector 结果与调用计数，不能进入公共输出。

同一 QVerisGet 的 Agent 和 connector 必须绑定同一 runtime Manifest。response schema 必须闭合且拒绝敏感字段名；底层 LiveTransport 有 1 MiB 响应上限，但当前 QVerisGet 拒绝 LiveTransport。允许 allowlisted live model 加 fake Tool 进行语义联调；这不是 QVeris live-ready。真实 live activation 待固定 Tool、授权 adapter 和单独验证。

结果输出仅做原样返回或必要的无业务变换封装；不在当前实现中做推导、计算、分析或二次 Agent 调用。DIRECT → TRANSFORM → AGENT_FALLBACK 是已讨论的后处理演进方向，但目前 deferred，不进入本轮实现。

## 数据范围与非目标

目标覆盖三类基础数据：

- 实时行情；
- 历史行情；
- 财报数据。

只做基础理解与取数。不做分析、推荐、计算、新闻搜索或多步研究。当前各域仍仅有受限的 pilot/候选链路，尚未完成 Tool selection 或正确性验证。

角色边界明确：我们交付 QVerisGet 链路；Benchmark 设计与评分由后续独立工作处理。

## 当前代码与验证状态

工作仓库：`/Users/liuqiyu/Desktop/Benchmark`。

当前基线包含受控的基础实现和历史验证记录；验证是否通过以仓库当前测试和最终复核结果为准。已记录的 Tool 观察均为 schema pilot，而非数据质量结论：

- realtime：Finnhub；
- historical：Tiingo；
- financial：FMP；
- Alpha 财报需要额外内容获取，不符合当前单次返回的约束。

这些 pilot 不能证明 Tool 最佳、数据准确、稳定性或生产可用。

## 尚未完成与不能声称

当前不能声称：最佳 Tool、数据准确率、300 题 Benchmark、生产交付完成。

Benchmark 与 metrics 是独立的后续工作流；本文件不定义其内容，也不属于当前 `get` 链路研发范围。
