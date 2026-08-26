# TransitScholar Layer2 Step3（L2S3）完成与冻结说明

> 文档状态：最终完成与冻结记录
>
> 更新日期：2026-08-26
>
> 对应计划：[20260821-L2S3Schema-guided-Base-Wiki开发计划.md](20260821-L2S3Schema-guided-Base-Wiki开发计划.md)
>
> 结论：L2S3 V1 的实现、离线确定性回归和真实 Provider 单篇 smoke 均已通过，现正式冻结。

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

## 3. 持久化语义向量索引

Wiki Page 与 Entity 向量均作为 Workspace-scoped、可重建的派生索引持久化在 Wiki 索引边界内。

- Page 向量文本至少包含 `title + summary`。
- Entity 向量文本至少包含 `canonical_name + aliases + description`。
- 构建/最终化时生成并保存语料向量；正常语义查询仅嵌入查询并复用已持久化的 Page/Entity
  向量，不会在每次查询时重新嵌入完整 Wiki 语料。
- 索引保存来源指纹和兼容性信息。陈旧或不兼容状态会暴露显式重建/错误路径，不会静默作为
  最新索引使用；跨 Workspace 的向量或搜索结果不会泄露。

## 4. 验证记录

### 4.1 离线确定性回归

在网络阻断环境中执行：

```powershell
$env:TRANSIT_SCHOLAR_BLOCK_NETWORK = 'true'
$env:TRANSIT_SCHOLAR_RETRIEVAL_ALLOW_NETWORK = 'false'
$testFiles = Get-ChildItem tests -Filter 'test_l2s3_*.py' | ForEach-Object { $_.FullName }
.\.venv\Scripts\python.exe -m pytest -q $testFiles
```

结果：`92 passed`（network-blocked deterministic L2S3 suite, as independently rerun for T-004）。

该回归覆盖 Package A–F 及本 Contract 新增回归，保持离线、fake-provider 边界可运行。覆盖内容
包括 Field Card/Link 可追溯性、失败不完整状态、`success_empty`、精确与语义复用区分、生产
Provider 适配、应用层自动最终化、Workspace 隔离、持久化向量的重载复用和 Package F 的稳定
ID、导航、审计、幂等性与隔离保证。

### 4.2 真实 Provider 单篇 smoke

T-004 独立重试的 `l2s3_production_single_paper` smoke（证据：`temp/l2s3-direct-retry.json`）成功：

- `success: true`，`build_status: complete`；
- `real_llm_client: true`、`real_embedding_provider: true`，`persistent_vectors_built: true`；
- 最终审计无阻断错误（`final_audit_no_blocking_error: true`）；
- 生成并持久化 1 个 Page 向量、17 个 Entity 向量及 17 条可追溯 Link。

该结果满足 AC-016。生产完成的必要条件仍是针对最终 Wiki 状态构建有效、持久化且当前的
vector index：必须覆盖所有 Page，且存在 Entity 时覆盖所有 Entity；缺少或无效的索引不得将
生产 Wiki 标记为 `complete`。

### 4.3 验收脚本目录职责

仓库根目录的 `scripts/` 是命令行验收与运行时 smoke 工具目录，不是应用运行时包。`scripts/l2s3_production_smoke.py`
负责使用真实 L2S2 输入、真实 LLM/Embedding Provider 和持久化 Wiki 路径执行单篇生产 smoke，并输出脱敏结果；
Contract T-009 将 `scripts/**` 列为该任务的允许范围。`scripts/l2s2_runtime_smoke.py` 保留用于既有 L2S2
单字段 smoke 及其回归测试兼容。两类脚本均由显式命令调用，不会被普通离线 `pytest` 自动执行；应用包本身仍只从
`src/` 目录安装。

## 5. Git 交付状态

L2S3 的既有核心实现、Package A–F 测试和 Package F fixture 已由 Git 提交
`308b6c32 feat: implement L2S3 schema-guided base wiki` 纳入版本历史；它们不是未跟踪文件。
本说明不将已提交的 L2S3 实现、测试或 fixture 表述为 untracked。工作区中如有其他待提交变更，
应按其自身交付批次审阅，不改变上述已验证的冻结结论。

## 6. 冻结判定

AC-015 已满足：禁网确定性 L2S3 回归为 `92 passed`。现有 Wiki 的 reload → Page/Entity
mutation → index freshness detection → controlled rebuild 生命周期也由该回归覆盖；重建后
semantic search 使用更新向量而不会静默使用 stale index。

AC-016 已满足：真实 Provider smoke 成功构建并验证了强制持久化 Page/Entity 向量索引，且最终
审计无阻断错误。AC-015、AC-016 与本节记录的修复验收均已满足。

**L2S3 V1 已正式冻结。**

## 7. T-005 修复后冻结核对（2026-08-26）

本节以 T-004 的验证输出为准：禁网确定性回归为 `92 passed`，真实 Provider 单篇 smoke 为
`success=true`、`build_status=complete`、`persistent_vectors_built=true`，并确认真实 embedding
Provider 生成了 1 个 Page 向量和 17 个 Entity 向量，最终审计无阻断错误且有 17 条可追溯 Link。

生产完成要求最终 Wiki 状态具有有效、持久化且当前的 vector index（覆盖 Page，并在存在 Entity
时覆盖 Entity）；缺少、陈旧、不兼容或生成失败时不得标记 `build_status=complete`。Wiki
reload → Page/Entity mutation → stale 检测 → controlled rebuild 的 freshness lifecycle 已由
确定性回归验证；重建后 semantic search 使用更新向量，不会静默使用 stale index。

AC-015、AC-016、AC-017 均满足，L2S3 V1 正式冻结。
