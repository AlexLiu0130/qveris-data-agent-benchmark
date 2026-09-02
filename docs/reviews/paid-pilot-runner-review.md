# Paid-pilot runner 安全审查

审查日期：2026-09-02；本文已随 runner 的后续修复更新。初版审查的 P0 与“未执行真实 Call”仅是历史快照，不代表当前实现或 v3 结果。

## 当前结论

当前 runner 已具备用于**单个、冻结、固定价** pilot 的本地执行控制；每次真实 Call 仍须单独获得外部授权。v3 Finnhub 是一次 VALID_RUN（HTTP 200、business success、实际 1 credit、1211ms），但它只证明纠偏后的协议和最小收据路径，并不证明准确性、新鲜度、稳定性或低延迟。

## 历史 P0 状态（已修复）

| 历史问题 | 当前状态与控制 |
| --- | --- |
| `pending` 可触发 live Call | **已修复。** manifest policy 和 candidate 都必须是 `approved_for_pilot`。 |
| `arguments` 而非官方 `parameters` 请求体 | **已修复。** `CONNECTOR_PROTOCOL_VERSION=qveris.execute.parameters.v1` 被锁定，`post()` 发送 `{"parameters": arguments}`。v1 Alpha、v2 Finnhub 使用旧封装，均为 `INVALID_METHOD/DISCARDED`。 |
| manifest/plan 可被替换或篡改 | **已修复为本地 hash 绑定。** plan 含 manifest hash；manifest policy 含 approved plan hash；两者及协议版本在 dispatch 前校验。 |
| check-then-append 的并发 TOCTOU | **已修复为本地 ledger 锁。** `LockedLedger` 的排他锁覆盖 ledger 读取、预算/幂等检查、planned 记录及单次 dispatch。 |
| unknown/variable 成本可继续 dispatch | **已修复。** 只接受非负数固定 `expected_cost` 和固定总预算；未知上界在 dispatch 前拒绝。 |

## 当前执行控制

- **显式批准：**仅 `approved_for_pilot` 的候选可解析；case、alias、参数和固定成本须与冻结 manifest/plan 完全匹配。
- **协议与 hash：**manifest policy 与 plan 都必须携带 `qveris.execute.parameters.v1`；计划/manifest 双向 hash 绑定后才可 dispatch。
- **本地串行与一次性：**使用 `fcntl` ledger 锁；同一冻结 plan/case/idempotency key 计入预算并阻止重复 dispatch；正常路径无自动重试。
- **默认 dry-run：**未给 `--execute` 时只解析、验证并返回 dry-run，既不读取 key 也不 POST。
- **外部执行门：**`--execute` 还要求外部 owner-only `0600` regular-file digest 与当前 plan hash 完全一致；digest 缺失、权限或内容不符均拒绝。
- **私有结果：**JSON 响应只写入 `artifacts/private/` 的受限权限文件；该目录和 approval/digest 工件被 `.gitignore` 忽略。ledger 仅留 hash、摘要和收据字段，不写凭据或原始 payload。

## 当前局限与未通过门

- v3 响应缺少 `symbol`、`source`、`session`、`currency`；`t` 的语义也未由 Inspect 输出 schema 验证。因此 Finnhub 仅为 **schema-qualified / accuracy-unverified**，accuracy 与 freshness 仍 blocked。
- 一个 1211ms 样本不是稳定性或延迟结论；不得称最快、稳定、准确或真正实时。
- 本地 ledger 锁只能保护共享同一 ledger 的本机进程；provider 端幂等语义、跨主机协调和余额 API 语义仍未验证。
- 外部 digest 是额外本地授权门，不是密码学签名或独立审批系统；其 owner/host 管理属于运行环境责任。
- receipt 的真实性、provider 数据许可、字段 provenance、市场会话和内容准确性仍需各域 response gate 验证，不能从 HTTP 200 或 `success=true` 推断。

## 验证边界

本次文档更新未执行。当前本地测试覆盖 dry-run 默认、`parameters` 请求体、协议/hash 篡改拒绝、固定预算、锁定 ledger 和私有工件权限；真实 provider 的上述语义与跨主机行为不在这些测试的证明范围内。
