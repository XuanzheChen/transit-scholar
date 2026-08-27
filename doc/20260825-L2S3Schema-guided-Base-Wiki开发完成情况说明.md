# TransitScholar Layer2 Step3（L2S3）完成与冻结说明

> 文档状态：最终完成与冻结记录
>
> 更新日期：2026-08-27（T-005：按 Contract v4 组合证据门禁更新）
>
> 对应计划：[20260821-L2S3Schema-guided-Base-Wiki开发计划.md](20260821-L2S3Schema-guided-Base-Wiki开发计划.md)
>
> 结论：Contract v4 的 T-004 组合证据门禁（AC-001 至 AC-012）已全部通过，L2S3 V1 现正式冻结。本文件（T-005，对应 AC-013）仅在门禁通过后记录冻结状态。

---

## 1. 完成范围

L2S3 是受 Schema 约束、Workspace 隔离的 Base Wiki 编译器。它以当前
`SchemaDefinition`、`SchemaInstance`、`PaperMetadata` 和既有 Wiki 状态为输入；不读取
PDF、L2S1 chunk、BM25/Dense/Hybrid 原始证据检索或 Web。Wiki 是导航层，不替代 L2S2
字段证据或 L2S1/PDF 事实证据。

已完成的生产组合入口会：加载并校验权威 L2S2 输入；装配受治理的实体提议、实体消歧和
embedding Provider；执行有界 Workspace 构建；自动写入最终 `WikiManifest`；自动重建派生
索引（包括向量索引）；执行最终审计；并返回结构化构建结果。

实体提议和消歧均通过生产 structured-output LLM 边界调用。精确 canonical-name/alias 命中
直接复用并记录精确匹配原因；仅在语义候选检索与候选集约束的决策均成功后，才记录
`semantic_reuse`。低置信度、不可用、畸形或候选集外决策不会自动合并实体。

## 2. 完成语义与可追溯性

- 每个接受的 Entity Proposal 的 `source_field_id` 必须属于该 Paper 本次构建使用的 Field Card。
  Link 的 `source_status` 从该 Field Card 获取；缺失字段会作为无效提议记录，绝不会以
  `unknown` 伪造可持久化 Link。
- resolver、Entity 写入、Link 写入或必需审计发生错误时，Paper 不会标为 `complete`，并保留
  机器可读的 proposal trace；不会伪造 Entity 或 Link。
- `success_empty` 是成功的语义结果：Provider 正常完成、结构化输出有效且 `proposals` 为空时，
  不创建 Entity 或 Link，但若其他阶段和审计成功，Paper 和 Workspace 仍可为 `complete`。
  只有 `real_entity_proposal_executed=true` 且 proposal 阶段状态恰为 `success_empty` 时，
  `proposal_count=0` 才能通过门禁（REQ-005/AC-007/AC-008）。

## 3. 持久化语义向量索引

Wiki Page 与 Entity 向量均作为 Workspace-scoped、可重建的派生索引持久化在 Wiki 索引边界内。

- Page 向量文本至少包含 `title + summary`。
- Entity 向量文本至少包含 `canonical_name + aliases + description`。
- 构建/最终化时生成并保存语料向量；正常语义查询仅嵌入查询并复用已持久化的 Page/Entity
  向量，不会在每次查询时重新嵌入完整 Wiki 语料。
- 索引保存来源指纹和兼容性信息。陈旧或不兼容状态会暴露显式重建/错误路径，不会静默作为
  最新索引使用；跨 Workspace 的向量或搜索结果不会泄露。

## 4. 验证记录（Contract v4 组合证据，T-004）

T-004 的冻结门禁由以下组合证据构成（REQ-007）：Executor 在网络阻断环境中执行离线
确定性回归并合并证据清单，且制备了七个持久化输入的边界化回读验证工具
（`temp/t004_verify_l2s2.py`）；Supervisor（批准的长时运行执行方）执行全部长时间
真实 Provider 生产（七个 L2S2 运行与最终 L2S3 单篇 smoke），并使用 Executor 制备的
边界化验证工具执行七个持久化输入的独立回读（AC-006 允许任一方执行）。Executor
未复现任何长时间 Provider 调用（AC-002、AC-010，C-002、C-003）。

### 4.1 离线确定性回归（AC-001、AC-011）

在网络阻断环境中执行完整的 `test_l2s3_*.py` 确定性回归：

```powershell
$env:TRANSIT_SCHOLAR_BLOCK_NETWORK = 'true'
$env:TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK = 'false'
$testFiles = Get-ChildItem tests -Filter 'test_l2s3_*.py' | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest -q $testFiles
```

结果：`98 passed`（network-blocked deterministic L2S3 suite，证据：
`temp/t004_deterministic_suite.log`，2026-08-27，终局 `98 passed in 6.03s`）。
T-005 冻结记录前已再次核对该结论。

该回归覆盖 Package A–F 及本 Contract 新增回归，保持离线、fake-provider 边界可运行。覆盖内容
包括 Field Card/Link 可追溯性、失败不完整状态、`success_empty` 严格语义、精确与语义复用区分、
生产 Provider 适配、应用层自动最终化、Workspace 隔离、持久化向量的重载复用、Package F 的稳定
ID、导航、审计、幂等性与隔离保证，并持续证明：最终 Manifest/index/audit 一致性、
Workspace failed -> Manifest failed、缺失强制 Wiki 向量索引 -> 阻断式审计失败（AC-001）。

### 4.2 七个真实持久化 L2S2 输入（AC-003 至 AC-006）

长时间真实 Provider L2S2 `extract_schema()` 生产由 **Supervisor（批准的长时运行执行方）**
执行（REQ-002、REQ-004），产物持久化在唯一共享 Schema 存储根
`data/L2S2_smoke/t004/schema_runs`（与 L2S3 smoke 使用同一根，C-005）：
`schema_id=bus_control_rl`、`schema_version=1.0`、`top_k=8`。

恰好七个 Paper 各有一个当前 SchemaInstance（AC-003），且每个 run 的证据清单
（`data/L2S2_smoke/t004/l2s2_evidence_manifest.json`）均记录：
真实运行时 structured-output LLM（`openai_compatible` / `deepseek-v4-flash`）、
`llm_fake=false`（run manifest 与 extraction manifest 双 false）、
`HybridRetrievalWrapper` 检索、canonical `read_blocks` 证据访问、
正常 L2S2 验证流程执行（未跳过、未伪造）、`SchemaRunStorage` 持久化成功、
`current.json` current-pointer 更新成功、`get_schema()` 读回成功（AC-004、AC-005）。
七份运行均无 fake 替代（`fake_substitution=false`）。

| paper_id | run_id |
| --- | --- |
| `22e53f11abf0450b901396393886c6a3` | `945a2fae91374dd1b8ba3571b50d2deb` |
| `2fbde8c2104a4575b41527757b414583` | `d1f85e35d7fa4ccf8f587f1a87e4c176` |
| `5495024f6cd140ebbf3e707656c68931` | `5ff4043c8ab742409a3ecd5ae42d852f` |
| `8e5daaf696d14b46a9d682c92c0bb86f` | `e7806bfcb78141af99c01336f4adf2c5` |
| `9299e36caccb476ea156344ed97a414b` | `2aad825f6d204115a0861e8260aee9ea` |
| `c94a4f1b57bd45778354800a7a1f9812` | `b9d500a25cd34e30bb8f16be5870e6e1` |
| `eb9bb62d8ce040bb8b41fda52bd7d04a` | `7fb149298b9e47299fb2d1b33c12642d` |

独立验证（AC-006）使用 Executor 制备的边界化验证工具 `temp/t004_verify_l2s2.py`
（纯本地读、无 LLM、无网络），由 **Supervisor** 从记录的存储根执行：用正常读 API
（`get_schema` + `SchemaRunStorage`）第二次读取全部七个当前 SchemaInstance，校验
paper_id/schema_id/schema_version 身份、current 指针、run 完整性及双
`llm_fake=false`；结果 `independent_verification_all_pass=true`（证据：
`temp/t004_l2s2_evidence_verified.json`，所有 run 均带
`independent_get_schema_readback=true`）。AC-006 允许 Executor 或 Supervisor
任一方执行该独立回读。

如实记录：七份 run 的 L2S2 验证结果状态均为 `failed`（validation_report 中语义验证器
因真实 Provider 调用超时记录 `verifier_unavailable` 错误，并伴随 `missing_evidence` 类
警告）。按 Contract v4（REQ-003/AC-004），门禁要求的是“正常 L2S2 验证执行”
（不可跳过、不可伪造），并非“验证通过”；因此本说明如实记录验证执行正常且结果为
`failed`，不将任何失败或未执行的验证描述为“通过”。

### 4.3 真实 Provider L2S3 生产 smoke（AC-009、AC-010、AC-012）

长时间真实 Provider L2S3 生产 smoke（`l2s3_production_single_paper`）由
**Supervisor（批准的长时运行执行方）** 执行（REQ-006），候选 Paper 为七个已验证输入之一
`22e53f11abf0450b901396393886c6a3`，工作区 `l2s3-t004-v4-supervisor`，使用与 L2S2 相同的
Schema 存储根；脱敏结果证据：`temp/l2s3_t004_freeze_result.json`。Executor 未为了
“任务归属”而复跑该 smoke（AC-010、C-003）。

最终 smoke 结果（AC-012 全部通过）：

- `success=true`，`build_status=complete`；
- `real_llm_client=true`，`real_embedding_provider=true`（真实 LLM 与 embedding Provider）；
- `real_entity_proposal_executed=true`，`proposal_phase_status=success`，
  `proposal_count=11`（>0，未使用 `success_empty` 分支）；
- 存在提议时 resolver 实际执行：`real_resolver_executed_when_proposals_exist=true`，
  `accepted_link_count=11`，`accepted_links_traceable=true`；
- `manifest_written=true`，`paper_page_created=true`，
  `persistent_vectors_built=true`（强制持久化 Page/Entity 向量已构建，C-010）；
- `final_audit_no_blocking_error=true`，`audit_issue_codes=[]`；
- `application_composition_used=true`。

因提议数大于零，本次 smoke 未走零提议路径；真实 Entity Proposal 阶段成功执行
（状态 `success`）。若未来出现 `proposal_count=0`，仅当真实 Entity Proposal 阶段成功
执行且提案阶段状态恰为 `success_empty` 时才可通过（REQ-005/AC-007）；任何
`real_entity_proposal_executed=false`、provider_failure、缺失、畸形、跳过或
非 `success_empty` 状态都必须失败（AC-008）。该严格语义已由 `temp/t004_combine_evidence.py`
的合并门禁与 `temp/t004_combine_evidence_test.py` 八种门禁场景夹具测试覆盖。

### 4.4 组合证据门禁结论（T-004 通过）

`temp/t004_combine_evidence.py` 将七份已验证 L2S2 证据与真实 Provider smoke 结果合并，
产出 `data/L2S2_smoke/t004/t004_combined_evidence_manifest.json`：`l2s2_all_seven_accepted=true`、
`ac012_all_pass=true`、`final_gate=true`（退出码 0）。结合 T-004 的 result/review（status:
passed），AC-002 至 AC-012 全部满足。

## 5. Git 交付状态

L2S3 的既有核心实现、Package A–F 测试和 Package F fixture 已由 Git 提交
`308b6c32 feat: implement L2S3 schema-guided base wiki` 纳入版本历史；它们不是未跟踪文件。
本说明不将已提交的 L2S3 实现、测试或 fixture 表述为 untracked。工作区中如有其他待提交变更，
应按其自身交付批次审阅，不改变上述已验证的冻结结论。

## 6. 冻结判定（Contract v4）

按 Contract v4 验收编号：

- AC-001（REQ-001）：确定性回归持续证明最终 Manifest/index/audit 一致性、
  Workspace failed -> Manifest failed、缺失强制 Wiki 向量索引 -> 阻断式审计失败 —— 已满足。
- AC-002 至 AC-006（REQ-002/003/004）：Supervisor 产出的七个真实持久化 L2S2 输入
  已由 Supervisor 使用 Executor 制备的边界化验证工具（`temp/t004_verify_l2s2.py`）
  从记录根独立回读验证，证据字段齐全 —— 已满足。
- AC-007、AC-008（REQ-005）：零提议仅 `success_empty` 语义通过，其余状态失败，
  由合并门禁强制 —— 已满足。
- AC-009、AC-010（REQ-006）：Supervisor 执行的真实 Provider smoke 作为有效门禁证据被
  接受，Executor 无需复现 —— 已满足。
- AC-011（REQ-007）：网络阻断环境下确定性 L2S3 套件 `98 passed` —— 已满足。
- AC-012（REQ-007）：最终真实 smoke 全部必需项为真（success、build_status=complete、
  真实 LLM/embedding、真实 Entity Proposal 执行、Manifest 写入、持久化向量构建、
  最终审计无阻断错误、应用组合使用、11 个提议与 11 条可追溯 Link）—— 已满足。
- AC-013（REQ-007）：本说明仅在 AC-001 至 AC-012 以实际组合证据通过后（T-004 passed）
  才记录并声明正式冻结 —— 即本文档。

**L2S3 V1 已正式冻结。**
