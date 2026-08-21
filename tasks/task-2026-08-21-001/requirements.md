# L2S2 Structured Output Reliability Contract 与 Freeze 文档闭环

## 1. 任务目标

在上一任务已经完成真实 LLM runtime wiring 的基础上，补齐
`StructuredLLMClient` 的 structured-output reliability contract，使同一套
Pydantic 输出契约可靠服务于 Schema Extractor、真实 Semantic Verifier 和
Targeted Recheck，并用一次真正的单字段真实 smoke 证明：

```text
.env
  -> LLMConfig / OpenAICompatibleLLMClient
  -> one-field real extraction
  -> real semantic verification
  -> valid SemanticVerdict
```

同时更新开发情况文档，准确解释 85 条 warning 的来源和非阻断性质，并补齐
Package E 未直接汇总 strict traceability / not-found correctness 的报告口径。

## 2. 已确认根因

1. `OpenAICompatibleLLMClient.generate_structured()` 当前固定发送
   `response_format={"type":"json_object"}`。
2. 调用方传入的 Pydantic `output_schema` 只在响应后用于
   `model_validate()`，没有通过 `model_json_schema()`传给 provider。
3. JSON mode 下 `{}` 是合法 JSON，但不满足 `SemanticVerdict` 必需的
   `decision` 字段，因此真实 verifier smoke 抛出 `LLMInvalidOutputError`。
4. Semantic Verifier 当前没有共享边界内的有界格式纠错；Extractor 虽有业务层
   retry，但不能替代 provider/schema 层的通用 structured-output 可靠性。
5. deterministic tests 使用预设合法响应或 MockTransport，没有覆盖真实 endpoint
   对 strict JSON Schema / JSON mode 的能力差异。
6. 当前 smoke 的 `--fields` 只限制后续 recheck，初次 `extract_schema()` 仍提取并
   验证完整 39 字段，不能作为真正的单字段 structured-output smoke。

## 3. 冻结设计原则

### FR-001 Pydantic 是唯一结构契约

- `generate_structured(messages, output_schema, metadata)` 的
  `output_schema` 是内部结构的 Single Source of Truth。
- provider JSON Schema、JSON fallback prompt guidance、响应校验和纠错提示均由
  `output_schema.model_json_schema()`或其校验结果产生。
- 不得为 Extractor、Verifier、Recheck 分别维护另一套手写 provider schema。
- 现有业务 prompt 可以保留业务语义说明，但不得成为与 Pydantic 漂移的第二套
  结构契约。

### FR-002 Structured-output capability modes

- 在统一 LLM 配置边界增加可测试的 structured-output mode，语义为：
  `auto | json_schema | json_object`，默认 `auto`。
- 配置应可从现有 `TRANSIT_SCHOLAR_LLM_*` 环境边界读取；不得在底层重复加载
  `.env`，不得修改用户 `.env`。
- `json_schema`：发送由 Pydantic 生成的 strict JSON Schema；provider 明确拒绝
  时显式失败，不静默切换模式。
- `json_object`：直接使用 JSON object mode，并把同一 Pydantic JSON Schema 的
  精简指导加入 prompt。
- `auto`：先尝试 strict JSON Schema；只有 provider 明确表示不支持
  `response_format/json_schema` 时，才对该逻辑调用回退一次 JSON object mode。
- capability fallback 只处理明确的 schema capability incompatibility。认证失败、
  权限失败、限流、超时、网络错误、5xx 和其他真实请求错误不得被吞掉或改写成
  JSON fallback 成功。
- 非法 mode 配置必须在发网前显式失败。

### FR-003 Provider-enforced strict JSON Schema

- strict 请求体必须携带 `output_schema.model_json_schema()` 生成的 schema、稳定且
  合法的 schema name，以及 strict=true（按 OpenAI-compatible 请求格式组织）。
- 不得把 API key、Authorization header、完整 base URL 或其他 secret 放进 schema、
  prompt、日志、trace、manifest 或异常。
- 即使 provider 声称 strict，返回内容仍必须经过 JSON parse 和 Pydantic
  `model_validate()`，provider 声明不能替代程序侧校验。

### FR-004 JSON fallback 与本地校验

- JSON fallback 的结构指导必须来自同一 Pydantic schema，而不是手写字段副本。
- provider 返回的内容无论来自 strict 或 fallback，都必须依次通过：响应 envelope
  校验、JSON parse、对象类型检查、Pydantic validation。
- JSON fence 等现有兼容行为可保留，但不能放宽 Pydantic 契约。

### FR-005 有界 structured correction retry

- 首次成功 HTTP 响应若发生 JSON parse 或 Pydantic schema validation 失败，统一
  client boundary 最多追加一次定点纠错请求。
- 纠错上下文应向同一 client/model 提供前次无效输出及精简、脱敏、限长的校验错误，
  明确要求只返回修正后的 JSON 对象。
- 纠错结果再次执行完整 JSON + Pydantic 校验。
- 第二次仍失败必须抛出 `LLMInvalidOutputError`（或保持现有等价稳定错误码），不得
  猜测/填充 `decision="unclear"`、不得生成 fake 成功、不得转成 `not_found`。
- structured-format repair 由统一 client boundary 负责。Extractor 可以保留 evidence
  id、字段业务类型、absent-status 等后置业务纠错，但不得在 client 已耗尽格式修复后
  再做一轮同质的 schema-format retry。
- transport retry、capability fallback 和 structured correction 的计数/职责必须可
  区分且有界，测试可断言请求次数。

### FR-006 三个逻辑角色复用同一可靠性边界

- Schema Extractor、`StructuredSemanticVerifier`、Targeted Recheck 继续共用上层
  composition root 解析出的同一个 `StructuredLLMClient` 实例。
- Verifier/Recheck 不重新读取 `.env`、不新建 provider、不实现独立的 structured
  parser/retry。
- `FakeLLMProvider`、`FakeSemanticVerifier` 和 custom injection 继续保留给
  deterministic tests / explicit fake，且优先于 runtime resolve。
- 真实 verifier structured failure 继续映射为 `verifier_unavailable`，不得冒充五态
  semantic decision；真实请求/配置失败继续遵循 `system failure != not_found`。

### FR-007 真正单字段真实 smoke

- 调整 `scripts/l2s2_runtime_smoke.py` 或增加同等最小入口，使 smoke 对一个真实
  paper 的一个明确 field 只执行该字段的 real extraction 和 real semantic
  verification，而不是先跑完整 39 字段。
- smoke 必须复用同一个 runtime-resolved client 和真实 retrieval；不得修改
  BusControlRL Schema 字段树或 Gold 来制造单字段 schema。
- 成功证据至少包含：provider name、允许公开的 model name、client class、
  allow_network、被测 field id、extraction status、semantic decision、最终成功状态。
- 不得输出 API key、Authorization header 或完整 base URL。
- 真实 smoke 只运行一个 paper × 一个 field；如需解除网络阻断，只在该进程临时将
  `TRANSIT_SCHOLAR_BLOCK_NETWORK=0`，不得修改用户 `.env`。

## 4. Deterministic tests

至少覆盖：

1. strict 请求的 JSON Schema 来自传入 Pydantic model，包含 strict=true。
2. `json_object` 直接模式不发送 strict schema，prompt guidance 来自同一 schema。
3. `auto` 对明确 unsupported schema 响应只 fallback 一次并成功校验。
4. `auto` 不对 401/403/429/timeout/5xx/普通 4xx 做 capability fallback。
5. strict 与 fallback 的成功结果均再次通过 Pydantic。
6. 首次缺字段/非法枚举/非法 JSON 时发起一次纠错并可恢复。
7. 第二次仍无效时显式 `LLMInvalidOutputError`，无 guessed verdict、fake 或
   `not_found`。
8. 请求次数证明 capability fallback、transport retry、correction retry 均有界。
9. correction prompt 的 validation error 脱敏且限长；API key leakage tests 继续通过。
10. `StructuredSemanticVerifier` 使用传入的共享 client 和 `SemanticVerdict`，恢复后
    返回合法五态；最终失败仍为 `verifier_unavailable`，Evidence Set 不被修改。
11. Extractor 和 Targeted Recheck 复用同一 client boundary，依赖注入优先级不变。
12. `TRANSIT_SCHOLAR_BLOCK_NETWORK=1` 下 deterministic tests 不因开发者 `.env`
    偷偷联网。
13. smoke 的单字段行为可用 mock/deterministic test 证明不会调用完整 39 字段链路。

必须运行与记录：相关 focused tests、完整 L2S2 deterministic suite、网络阻断模式
suite，以及现有 API-key leakage tests。

## 5. 开发情况文档更新

更新：

`doc/20260814-L2S2-Schema提取与验证开发情况说明.md`

### DOC-001 Structured-output 最终状态

- 记录根因不是 Fake/runtime wiring，而是 provider request 只使用 JSON mode、没有把
  Pydantic Schema 下推以及缺少统一纠错闭环。
- 记录最终 strict/fallback/Pydantic/one-repair/explicit-failure 契约。
- 记录真实单字段 Extraction -> Semantic Verification smoke 的实际结果与脱敏证据。
- 只有真实 smoke 成功后，才能把此项从 Freeze blocker 改为已关闭。

### DOC-002 85 warnings 的准确解释

- 依据
  `output/l2s2-gold-acceptance/20260819T150047Z/acceptance_report.json` 记录：
  35 `value_mismatch` + 35 `judgement_conflict` + 15 `status_mismatch` = 85。
- 前 70 条来自同一批 35 个 underlying case 的双重诊断：exact equality 不一致，
  但人工 Gold judgement 为 `correct`。
- 15 条 status warning 全部是 Gold `explicit`、预测 `inferred`。
- 10 个 field instance 同时存在 exact/status warning，所以总计涉及 40 个不同
  paper-field instance，不是 85 个独立错误。
- 这些是 2026-08-19 Package E Gold comparison 的非阻断诊断，
  `blocking_error_count=0`，与 2026-08-20 real verifier structured-output smoke 没有
  因果关系。
- 本任务不修改 warning 生成代码、Gold judgement、value/status semantics 或
  acceptance 规则；只修正文档口径。

### DOC-003 Package E 指标汇总口径

- 说明正式 Package E 报告当前未直接汇总 `strict_traceability_rate` 和
  `not_found_correctness`，这是报告完整性问题，不是已证实的能力失败。
- 引用现有独立 Canonical 审计事实：quote mismatch 与 page untraceable 均为 0，
  不虚构报告中不存在的数值。
- 本任务不修改 Package E 报告器、acceptance 规则或重新跑六篇 × 39 fields；在
  structured-output 单字段真实 smoke 成功后，文档给出准确的 Freeze 剩余项。

## 6. 非目标与禁止修改

不得修改：

- BusControlRL Schema 字段树、schema plugins、Gold 数据或 judgement；
- L2S1 parsing/retrieval 行为、Jina retrieval 策略；
- evidence canonical binding 与 Package E acceptance/reporting 代码；
- status semantics、Wiki、Layer3、Knowledge Graph；
- 多模型 routing、verifier 专用模型、多 Agent 产品功能；
- 用户 `.env` 及其中的真实配置。

不得通过放宽 `SemanticVerdict`、默认填充合法字段、吞掉错误或 silent fallback Fake
来让 smoke 变绿。

## 7. 完成判定

- Pydantic `output_schema` 成为 provider constraint、fallback guidance 和最终校验的
  唯一结构来源。
- provider capability 差异被安全处理，非 capability 错误保持显式失败。
- structured validation 允许一次且仅一次通用纠错，最终失败语义不变。
- Extractor、Verifier、Recheck 使用同一 client/reliability boundary。
- deterministic、网络阻断和泄密测试通过。
- 一个真实 paper × 一个 field 的 Extraction -> Real Semantic Verifier smoke 返回
  合法 `SemanticVerdict` 并成功结束。
- 开发情况文档准确解释 85 warnings 与 Package E 指标缺口，并根据真实 smoke 更新
  L2S2 V1 Freeze 结论。
