# L2S2 Structured Output Reliability Contract 实施计划

来源：外部 Generator planning-only round；Planner 已审核，等待用户批准。

## 1. 修改文件

- `src/transit_scholar/layer2/schema_extraction/llm.py`
  - 扩展 `LLMConfig` 的 `auto | json_schema | json_object` mode。
  - 从 Pydantic `output_schema` 生成 strict payload 与 JSON fallback guidance。
  - 实现 capability classification、共享 parse/validation、一次 correction、重试计数与
    脱敏。
- `src/transit_scholar/layer2/schema_extraction/errors.py`
  - 增加必要的 capability/format failure 分类信息，同时保持 unavailable、request、
    invalid-output 的错误边界与稳定错误码。
- `src/transit_scholar/layer2/schema_extraction/engine.py`
  - 保留 evidence id、字段类型、absent-status 等业务纠错。
  - client 已耗尽 format correction 后不再重复同质格式 retry。
  - 提供仅供真实 smoke 使用的窄 one-field in-memory extraction 路径，不修改 schema。
- `src/transit_scholar/layer2/schema_extraction/semantic.py`
  - 继续由共享 client 生成并校验 `SemanticVerdict`，保留五态、Evidence Set 不变与
    `verifier_unavailable`。
- `src/transit_scholar/layer2/schema_extraction/api.py`
  - 保持 composition root 与 injection precedence，确保三角色仍拿到同一 client。
- `src/transit_scholar/layer2/schema_extraction/__init__.py`
  - 只导出调用方/测试确实需要的新增稳定类型。
- `scripts/l2s2_runtime_smoke.py`
  - 改为恰好一个 paper + 一个 field，只运行该字段真实 extraction 与 verifier。
  - 输出仅保留脱敏 identity、field status、semantic decision 和成功状态。
- `tests/test_l2s2_llm_client.py`
  - mode parsing/default/invalid、Pydantic 单一契约、redaction。
- `tests/test_l2s2_llm_real_provider.py`
  - strict/json-object payload、窄 fallback、一次 correction、请求次数与 transport
    failure，全部使用 MockTransport。
- `tests/test_l2s2_extraction_engine.py`
  - format repair 与 business retry 分离；one-field extraction 不访问其他字段。
- `tests/test_l2s2_runtime_wiring.py`
  - 同一 client identity、injection precedence、blocked-network。
- `tests/test_l2s2_validation_semantic.py`
  - verifier correction 成功/耗尽、Evidence 不变、`verifier_unavailable`。
- 新增或扩展一个 `tests/test_l2s2_*.py` smoke 测试
  - 参数校验与单字段 wiring，不发真实网络。
- `doc/20260814-L2S2-Schema提取与验证开发情况说明.md`
  - structured-output 根因/最终契约、85 warnings、Package E null metrics、真实 smoke
    证据与 Freeze 结论。

## 2. 配置与请求模式

`LLMConfig` 增加：

```text
structured_output_mode: "auto" | "json_schema" | "json_object"
```

通过一个 `TRANSIT_SCHOLAR_LLM_*` 环境变量读取，默认 `auto`；非法值在 provider
构造或发网前失败。现有 timeout、transport retry、RPM、provider/model/key/base URL
与网络开关不变。

`json_schema` 请求使用：

```json
{
  "type": "json_schema",
  "json_schema": {
    "name": "<stable sanitized schema name>",
    "strict": true,
    "schema": "<output_schema.model_json_schema()>"
  }
}
```

`json_object` 只发送 `{"type":"json_object"}`，并把同一 Pydantic JSON Schema 的
精简 guidance 加入 messages。`auto` 先 strict，仅在明确
`response_format/json_schema unsupported` 时 fallback 一次；generic schema/JSON
字样、401/403/429/timeout/connection/普通 4xx/5xx 不触发 fallback。

## 3. 校验与一次 correction

每个 200 response 共用同一条 pipeline：

1. provider envelope；
2. message content 与现有 JSON fence 兼容；
3. JSON parse；
4. 必须为 object；
5. `output_schema.model_validate()`。

parse/object/schema failure 最多发一次 correction request。使用同一 client/model，
仅携带限长、脱敏的前次无效输出和 validation details，要求返回修正 JSON。第二次仍
失败抛 `LLMInvalidOutputError`，不填 `unclear`、不 fake、不变成 `not_found`。

transport retry、capability fallback、structured correction 分别计数且有界。client
format repair 耗尽后，Extractor 只允许现有后置业务纠错，不能再做等价 schema-format
repair。

## 4. 共享 Runtime

composition root 继续每次操作只解析一个 client，并将同一个对象传给：

- `ExtractionEngine`；
- `StructuredSemanticVerifier`；
- `build_runtime_recheck_callable`。

Verifier/Recheck 不重载 `.env`、不解析或构造第二 provider、不复制 parser/retry。
custom/fake/client/verifier/recheck injection 继续高于 runtime 默认解析。

## 5. 单字段 Smoke

smoke 强制一个 `--paper` 与一个 `--field`，在任何 provider call 前拒绝缺失或多个
field。它从现有 `bus_control_rl` definition 找到该 field，只对该字段执行真实
retrieval/extraction，然后用同一 client 调用 `StructuredSemanticVerifier`。

不修改 schema tree、Gold、持久化定义或 `.env`。网络解除仅限 smoke 进程。输出不含
key、Authorization value 或完整 base URL。

## 6. 验证

- focused MockTransport / semantic / extraction / smoke tests；
- 完整 `tests/test_l2s2_*.py`；
- `TRANSIT_SCHOLAR_BLOCK_NETWORK=1` 完整 L2S2 suite；
- 现有与新增 API-key/redaction tests；
- 最后只运行一次 paper × field 的真实 smoke，并保存 exit code 与脱敏输出。

本计划不修改 BusControlRL schema、Gold、L2S1/Jina、canonical evidence binding、
Package E code/rules、status semantics、Wiki、Layer3、Knowledge Graph、多模型 routing、
数据库或用户 `.env`。
