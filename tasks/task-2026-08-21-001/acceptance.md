# L2S2 Structured Output Reliability Contract 验收标准

状态：Planner 已审核并冻结。来源为外部 Evaluator acceptance-design，
`DECISION=accept`、`BLOCKING_COUNT=0`、`REQUIRES_USER_DECISION=false`。

## AC-01 Pydantic Single Source of Truth

- provider schema、JSON-object prompt guidance、correction guidance 与最终校验均从
  调用方传入的 `output_schema` 派生。
- provider schema 与 `output_schema.model_json_schema()` 等价；Extractor、Verifier、
  Recheck 不维护第二套手写结构契约。
- 每个成功 provider response 都经过 envelope、JSON、object type 与
  `output_schema.model_validate()` 校验。

## AC-02 Strict JSON Schema 请求

- strict mode 发送 OpenAI-compatible `response_format.type=json_schema`，其中包含稳定
  合法的 name、`strict=true` 及 Pydantic JSON Schema。
- 即使 provider 声称 strict，返回 200 但不符合 Pydantic 时仍必须拒绝。
- schema/name/messages/errors/logs/traces/manifests 不得包含 API key、Authorization
  value 或完整 base URL。

## AC-03 Structured-output modes

- 统一配置公开 `auto | json_schema | json_object`，默认 `auto`，从一个已文档化的
  `TRANSIT_SCHOLAR_LLM_*` 环境变量读取；非法值在发网前失败。
- `json_schema` 只发 strict schema，provider 拒绝时不切换模式。
- `json_object` 只发 JSON object response format，并加入由同一 Pydantic schema
  派生的精简 guidance。
- `auto` 先 strict；只有 provider 明确拒绝 `response_format/json_schema` capability
  时才 fallback 一次 JSON object。

## AC-04 窄化 capability fallback

- 明确 unsupported fixture 在 `auto` 下恰好产生 strict + JSON object 两次请求，
  fallback 响应仍完整 Pydantic 校验。
- 401、403、429、timeout、connection failure、5xx 与普通 4xx 不触发 capability
  fallback；generic “schema/JSON” 文本本身不足以触发分类。
- `json_schema` 模式面对同一 unsupported response 显式失败。
- capability rejection 与 HTTP 200 invalid model output 保持可区分。

## AC-05 一次 structured correction

- malformed JSON、non-object、缺必填字段、非法 enum 或其他 Pydantic failure 在首次
  200 response 后恰好触发一次 correction request。
- correction 使用同一 client/model，携带前次无效输出及精简、脱敏、限长的校验
  信息，并只要求修正 JSON object。
- correction response 再走完整 validation；第二次失败抛
  `LLMInvalidOutputError`。
- 禁止猜测 `decision="unclear"`、补默认必填字段、fake success 或转换
  `not_found`。

## AC-06 retry 职责与上界

- tests 可分别统计 transport retry、capability fallback、structured correction。
- transport retry 保持现有 eligible failure 和配置上界；capability fallback 每个逻辑
  调用至多一次；structured correction 每个逻辑调用至多一次。
- fallback 后若输出无效可 correction 一次，但不得重启 strict 或再次 fallback。
- client 已耗尽 schema-format repair 后，Extractor 不再进行等价 format retry；只可
  保留 evidence id、业务字段类型、absent-status 等后置业务纠错。

## AC-07 共享 client 与失败语义

- Extractor、`StructuredSemanticVerifier`、Targeted Recheck 使用 composition root
  传入的同一 `StructuredLLMClient` 对象。
- Verifier/Recheck 不重载 `.env`、不解析第二 client、不建第二 provider、不实现独立
  parser/correction。
- 显式 custom/fake/client/verifier/recheck injection 继续优先。
- correction 后的 `SemanticVerdict` 必须为现有五态且 Evidence Set 不变；最终格式失败
  映射 `verifier_unavailable`；配置/请求/transport failure 不得变成 `not_found`。

## AC-08 deterministic 单字段 smoke wiring

- deterministic smoke test 选择一个 paper + 一个 field，断言 retrieval、extraction、
  real `StructuredSemanticVerifier` 只调用该 field，其他 BusControlRL 字段均不被提取、
  验证或 recheck。
- 使用现有 schema definition，不修改或合成缩减的 Schema/Gold。
- Extraction 与 semantic verification 使用同一 injected client 实例。

## AC-09 真实单字段 smoke

- smoke 接受且只接受一个 paper + 一个 field；零字段或多字段在执行前拒绝。
- 使用 runtime-resolved `OpenAICompatibleLLMClient`、真实 retrieval、one-field
  extraction 与 `StructuredSemanticVerifier`。
- 成功必须 exit 0，并记录 provider、允许公开的 model、client class、有效网络许可、
  field id、extraction status、semantic decision 与 final success。
- 不输出或留存 key、Authorization/bearer value、完整 base URL，不修改 `.env`；网络
  阻断只在 smoke 进程临时解除。
- capability failure 与 200 invalid output 分开报告；skip、fake、full-schema run 或
  classified failure 均不算成功。

## AC-10 网络阻断与脱敏

- `TRANSIT_SCHOLAR_BLOCK_NETWORK=1` 下完整 L2S2 deterministic suite 不发真实请求，
  不受开发者 `.env` 影响。
- sentinel key、authorization value、完整 base URL 不出现在 exception、prompt、
  schema、correction、call record、JSON、manifest、trace、smoke output 或文档。
- 显式 fake/custom injection 即使存在真实环境变量也保持离线。

## AC-11 文档事实

`doc/20260814-L2S2-Schema提取与验证开发情况说明.md` 必须：

- 将根因准确写为固定 JSON-object request、未下推 Pydantic schema、缺少统一
  correction，而非 Fake/runtime wiring。
- 记录 strict schema、窄 fallback、Pydantic revalidation、一次 correction、最终显式
  failure 契约与实际脱敏 single-field smoke；只有 AC-09 成功后关闭 blocker。
- 写明 85 warnings = 35 `value_mismatch` + 35 `judgement_conflict` + 15
  `status_mismatch`；前 70 条是同一 35 个 underlying exact-match case 的双重诊断；
  15 条均为 Gold `explicit` vs predicted `inferred`；10 个实例重叠，因此是 40 个
  distinct paper-field instances，不是 85 个独立错误。
- 写明 warnings 是 2026-08-19 Package E 非阻断诊断，
  `blocking_error_count=0`，与 2026-08-20 verifier smoke 无因果关系。
- 写明 Package E 的 `strict_traceability_rate` 与 `not_found_correctness` 当前为
  `null`，属于报告完整性缺口；只引用现有 canonical audit 的 quote mismatch=0、
  page untraceable=0，不虚构指标。

## AC-12 范围保护

- 不修改 BusControlRL field tree、schema plugins、Gold、L2S1、Jina、canonical evidence
  binding、Package E code/rules、status semantics、Wiki、Layer3、Knowledge Graph、DB、
  多模型 routing、verifier 专用模型、多 Agent 产品行为或用户 `.env`。
- 不放宽 `SemanticVerdict`，不为其必填语义输出增加默认值，不重跑六篇 Package E。
- 产品变更仅限共享 structured-output boundary、必要配置/导出 wiring、single-field
  smoke、focused tests 与指定开发情况文档。

## 验证命令

```powershell
python -m pytest tests/test_l2s2_llm_client.py tests/test_l2s2_llm_real_provider.py tests/test_l2s2_runtime_wiring.py tests/test_l2s2_validation_semantic.py tests/test_l2s2_validation_pipeline.py tests/test_l2s2_recheck.py -q
python -m pytest tests/test_l2s2_*.py -q
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="1"; python -m pytest tests/test_l2s2_*.py -q'
powershell -NoProfile -Command '$env:TRANSIT_SCHOLAR_BLOCK_NETWORK="0"; python scripts/l2s2_runtime_smoke.py --paper transit-001 --field research_problem.control_type'
```

真实 smoke 的 CLI 若保留 `--fields`，必须强制恰好一个值并拒绝零/多个值。
