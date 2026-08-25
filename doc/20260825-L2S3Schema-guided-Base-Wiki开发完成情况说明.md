# TransitScholar Layer2 Step3 开发完成情况说明

> 文档状态：基于当前工作区的实现审计
>
> 审计日期：2026-08-25
>
> 对照计划：[20260821-L2S3Schema-guided-Base-Wiki开发计划.md](20260821-L2S3Schema-guided-Base-Wiki开发计划.md)
>
> 结论：**L2S3 的离线可验证核心已完成；计划尚未以“生产运行时全链路”意义完全闭环。**

---

## 1. 审计范围与结论口径

本说明只依据当前工作区中可读取的实现、测试和 Git 状态作出结论，不把未落盘的
设想、外部会话结论或未运行的真实服务视为已交付能力。

本次核查的主要对象为：

- `src/transit_scholar/layer2/wiki/` 中的 L2S3 实现；
- `tests/test_l2s3_package_a_*.py` 至 `tests/test_l2s3_package_f_acceptance.py`；
- `tests/l2s3_package_f_support.py` 与 `tests/fixtures/l2s3_package_f/`；
- L2S3 的开发计划及当前 Git 工作区状态。

这里将“完成”分为两个层级：

1. **核心开发完成**：模型、持久化、治理工具、Field Card、提议与消歧边界、
   bounded builder、离线验收均已实现并通过自动测试。
2. **生产全链路完成**：除核心代码外，还必须有真实 L2S2 产物到 L2S3 的运行时装配、
   可配置的 LLM/消歧调用、实际向量检索接入、构建后 Manifest/索引编排，以及供
   Layer3 使用的受控入口。

当前工作区满足第 1 层，不满足第 2 层。因此本阶段应表述为“**开发核心完成并通过
离线验收，生产接线待完成**”，而不应表述为“L2S3 已整体上线”。

---

## 2. 当前交付物

### 2.1 生产代码

`src/transit_scholar/layer2/wiki/` 已包含 8 个模块：

| 模块 | 已实现内容 |
| --- | --- |
| `models.py` | Workspace 上下文、Page、Entity、PageEntityLink、Manifest、检索结果、审计结果及 workspace-scoped 稳定 ID。 |
| `store.py` | `manifest.json`、三个 JSONL 源快照、完整性校验、原子写入、重载和损坏检测。 |
| `service.py` | Page/Entity/Link 维护、检索、共享 Entity 关联论文计算、索引重建和只读审计。 |
| `field_cards.py` | 从 SchemaDefinition 与 SchemaInstance 确定性生成、深度不可变的 Field Card。 |
| `proposals.py` | EntityProposal 结构、确定性请求/提示载荷和可注入 structured-output 调用边界。 |
| `resolution.py` | 名称规范化、canonical/alias 精确匹配、语义候选、受控复用/创建/歧义决策。 |
| `builder.py` | 单篇和 Workspace 级 bounded WikiBuilder，以及逐阶段 trace。 |
| `__init__.py` | 对外公开的 L2S3 API。 |

### 2.2 测试与验收资产

- Package A 至 E 各有独立的单元及安全测试；
- Package F 有 3 篇经审阅论文记录的 fixture、泛化 Schema 装载器和端到端验收；
- 验收覆盖 Page、Entity、Link、Manifest、检索、关联论文、重载、幂等性、审计、
  workspace 隔离及安全边界；
- Package F 明确只读取提交的 fixture，不在运行时读取 `data/` 原论文目录，也不访问网络。

---

## 3. 与开发计划的逐项对照

### 3.1 设计原则与输入边界

| 计划要求 | 核查结果 | 证据与说明 |
| --- | --- | --- |
| Schema-guided，不为特定领域字段写死规则 | 已落实 | `build_field_cards()` 依据通用 SchemaDefinition/SchemaInstance 配对；Package C 安全测试验证无 `bus_control_rl` 等领域硬编码。 |
| Page-first、Entity-linked | 已落实 | `WikiPage` 与论文一一对应；`PageEntityLink` 保存 Page—Entity 语义连接。 |
| Entity 不等于 Field/Value | 已落实 | Entity 仅来自提议边界；Field Card 保留字段语义和原始值，不将全部值自动实体化。 |
| LLM 做语义判断，程序做治理 | 已落实为可注入边界 | 提议和消歧均有受控 provider 接口；ID、别名、持久化、链接和审计由程序治理。尚无默认的生产 LLM 装配，见第 6 节。 |
| 不接入 L2S1 Hybrid Retrieval | 已落实 | Wiki 模块没有调用 L2S1 chunk、BM25、Dense 或 Web；Package E/F 测试覆盖离线和网络阻断边界。 |
| 输入为 Workspace、Schema、Instance、Paper Metadata | 已落实为代码级输入 | `WorkspaceContext`、`SchemaDefinition`、`SchemaInstance`、`PaperMetadata` 均为 builder 的显式输入；运行时从已有 L2S2 持久化资产装载的适配器尚未实现。 |

### 3.2 Field Card（计划第 5 节）

已落实。

- 仅从 SchemaDefinition 与 SchemaInstance 确定性组装；
- 按 Schema 作者定义的顺序输出，精确按 `field_id` 配对；
- 保留 section、label、question、description、type、options、constraints、
  evidence 要求、output guidance、value、status、confidence、notes 和 evidence；
- 默认跳过 `not_found` 与 `not_applicable`，弱状态可显式选择；
- 对嵌套值、options、guidance 和 evidence 进行深度不可变保护；
- 不调用 LLM，也不改写或猜测字段语义；
- schema/version 不一致、缺失字段、畸形模型均返回类型化失败。

对应的 Package C 测试验证了无损映射、任意嵌套值、JSON 确定性、状态筛选、不可变性和
LLM/provider 失败不会改变已生成的 Card。

### 3.3 Wiki 数据模型与持久化（计划第 6、13、16 节）

已落实。

- Page ID、Entity ID 与 Link ID 均由 workspace 范围内的稳定哈希生成；
- Page 保存论文、Schema 版本、摘要、构建状态和修订号；
- Entity 保存 canonical name、aliases、description 和可选 kind；
- Link 额外保存 `paper_id`、`schema_id`、`schema_version`、`source_field_id`、
  `source_status` 和 confidence，满足从 Wiki 回溯 Schema Field 的最低要求；
- 存储布局为 `{storage_root}/{workspace_id}/wiki/` 下的 `manifest.json`、
  `pages.jsonl`、`entities.jsonl`、`page_entity_links.jsonl` 和 `index/`；
- 写入先验证完整快照和临时文件，再以替换方式落盘；失败时恢复先前内容；
- 读取会拒绝缺文件、无结尾换行、重复记录、跨 workspace 对象、悬空链接、
  不一致 ID 和非法 Manifest。

这比计划中的最低字段集更严格，并已由 Package A、B 和 F 的持久化、重载、损坏及
隔离测试覆盖。

### 3.4 Entity Proposal 与 Resolution（计划第 7、9 节）

**接口与治理逻辑已落实，实际模型调用尚未接线。**

已经实现：

- `EntityProposal` 的结构化字段、别名去重、置信度范围和不可变性；
- 仅由 Field Card 组成的确定性 request/prompt payload；
- provider 异常、空输出、畸形输出和非法输出的显式、脱敏失败状态；
- Unicode/case/空白/标点规范化；
- canonical 和 alias 精确匹配时直接复用，不调用语义搜索或决策器；
- 语义候选只来自当前 Workspace 的 `search_entities(..., mode="semantic")`；
- 有候选时必须经 decision provider 作出 `reuse`、`create` 或 `ambiguous` 决策；
- 决策缺失、置信度不足、目标不在候选集中或无效输出时一律为 `ambiguous`，不写入；
- 歧义不会自动 merge；复用时仅增补不冲突的 aliases。

当前 `EntityProposalRunner` 和 `EntityResolver` 都接受外部注入 provider。Package F 使用
`ProposalFake`、`DecisionFake` 和 `FakeEmbedding` 验证真实的 A–E 调用链；这证明
边界、治理和回退逻辑正确，但并不证明某个云端 LLM 或生产 embedding 服务已可用。

### 3.5 WikiBuilder bounded compiler（计划第 8、18 Package E）

已落实核心固定循环：

```text
校验绑定 → Field Cards → ensure page → 更新摘要 → 一次提议
       → 对每个候选 resolve/create/reuse → link → audit page → 更新构建状态
```

- 提议数上限为 `MAX_PROPOSALS = 100`，不实现开放式 ReAct 循环；
- builder 的输入和可调用边界被 preflight 检查；错误绑定时不会调用 provider；
- 不读取 PDF、L2S1 chunks、Web 或通用 Python 工具；
- Page summary 当前为来自元数据和 Field Card 的确定性摘要；计划将 LLM 摘要表述为
  “可由”生成，因此此实现不构成违背；
- provider 失败、resolve/link 失败和审计失败会保留 trace，并将单篇标为
  `incomplete` 或 `failed`，不会伪造实体；
- Workspace 构建按 `context.paper_ids` 顺序运行，单篇缺输入不会影响其他论文。

`build_wiki_for_workspace()` 本身**不会**自动写入最终 Manifest，也不会自动调用
`rebuild_indexes()`；Package F 在构建成功后显式完成这两步。因此 builder 的核心循环已完成，
但生产编排仍缺少一个统一的“构建—Manifest—索引”入口。

### 3.6 维护、检索、图关系和审计（计划第 10、11、12、14、15 节）

| 计划项 | 结果 |
| --- | --- |
| `ensure_paper_page`、读取/更新 Page | 已实现。 |
| Entity 搜索、创建、更新、别名维护 | 已实现。 |
| Link 创建、解除、双向查询 | 已实现；创建幂等且不产生悬空 Link。 |
| `audit_page`、`audit_wiki` | 已实现，审计严格只读。 |
| `search_wiki`、`search_pages`、`search_entities` | 已实现，统一返回 type、ID、标题/名称、score、snippet/description。 |
| Page → Entity、Entity → Page | 已实现。 |
| Page → Page | 已通过共享 Entity 动态计算 `find_related_pages`，未持久化 `related_to`。 |
| 复杂 Entity—Entity ontology/图数据库 | 未实现，符合“明确不做”项。 |
| 语义检索 | 已有可注入 EmbeddingProvider 的查询时 cosine 相似度路径；provider 不可用时降级并给出结构化状态。 |
| 持久化 semantic vector index | 未实现。当前 `index/package_b_index.json` 是可审计的源快照投影，不是向量索引。 |

因此，计划中的基本导航和动态跨论文关系已落实；“建立 Page/Entity semantic index”仅落实为
可注入的查询时能力，尚未落实为持久化向量索引。

### 3.7 错误处理、隔离与明确不做项

已落实以下计划约束：

- 单篇失败隔离；provider 失败不伪造 Entity；歧义不强制合并；不存在 Page/Entity
  时拒绝 Link；
- workspace A/B 之间的读取、写入、服务绑定与 ID 均被拒绝或隔离；
- 没有把 Wiki 混入 L2S1 检索、原始 evidence、Web enrichment、Graph DB、
  自动 Paper-Paper 建模、复杂 ontology 或多智能体构建；
- Package F 的安全扫描还验证 fixture/support 中不含凭据形态、provider URL、网络/
  进程导入和越界数据目录读取。

---

## 4. Package A–F 完成状态

| Package | 计划目标 | 当前状态 | 核查结论 |
| --- | --- | --- |
| A | Core model、Store、持久化和隔离 | 完成 | 模型、稳定 ID、JSON/JSONL、CRUD、原子快照、重载和损坏检测均存在。 |
| B | Maintenance 与 Retrieval tools | 基本完成 | 维护、检索、关联论文、审计和投影索引完成；持久化向量索引未完成。 |
| C | Field Card 与 Entity Proposal | 基本完成 | 泛化 Card 和 structured-output 边界完成；无默认生产 LLM adapter/配置装配。 |
| D | Entity Resolution | 基本完成 | 规范化、精确匹配、语义候选和受控决定完成；语义/决定服务需由运行时注入。 |
| E | bounded WikiBuilder | 基本完成 | 单篇/Workspace builder、上限、trace、失败隔离和审计完成；最终 Manifest 与索引仍需调用方编排。 |
| F | 3–5 篇论文验收 | 离线验收完成 | 3 篇经审阅论文 fixture 跑通 A–E；使用 fake proposal/decision/embedding，不能替代真实模型 smoke。 |

---

## 5. 自动化验证结果

### 5.1 已执行命令

在仓库根目录执行：

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = '1'
.\.venv\Scripts\python.exe -m pytest `
  tests/test_l2s3_package_a_models.py `
  tests/test_l2s3_package_a_store.py `
  tests/test_l2s3_package_b_index.py `
  tests/test_l2s3_package_b_service.py `
  tests/test_l2s3_package_c_field_cards.py `
  tests/test_l2s3_package_c_proposals.py `
  tests/test_l2s3_package_c_safety.py `
  tests/test_l2s3_package_d_normalization.py `
  tests/test_l2s3_package_d_resolution.py `
  tests/test_l2s3_package_d_safety.py `
  tests/test_l2s3_package_e_builder.py `
  tests/test_l2s3_package_f_acceptance.py -q
```

结果：

```text
67 passed in 3.33s
```

### 5.2 测试结果的含义

- Package A：模型校验、workspace-scoped ID、JSON round trip、CRUD、原子失败恢复、
  完整性和隔离；
- Package B：维护关系、词法/语义检索、索引重建和只读审计；
- Package C：Field Card 无损/不可变性、泛化性、proposal 的结构化失败边界和离线性；
- Package D：规范化、精确匹配、候选约束、错误/歧义的零写入和别名治理；
- Package E：上限、序列化、绑定失败不调用 provider、单篇 A–D 链路 smoke；
- Package F：3 篇论文、5 个 Entity、6 条可追溯 Link、共享 Entity、双向查询、
  动态关联论文、审计、重载、幂等重跑、workspace 隔离和 fixture 安全扫描。

### 5.3 测试环境说明

直接运行 `python -m pytest` 或未禁用自动加载的 `.venv` pytest 时，环境会从
`F:\Miniconda3` 加载第三方 `anyio` 插件，并因 `_ssl` DLL 权限错误在测试收集前退出。
这发生在 L2S3 测试模块导入之前。设置 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 后，使用
项目 `.venv` 的专项测试全部通过；该设置只规避环境插件污染，不跳过任何 L2S3 断言。

---

## 6. 尚未完成的计划闭环

以下事项不影响“核心开发和离线验收完成”的结论，但阻止将本阶段称为生产全链路完成。

1. **L2S2 → L2S3 的运行时装载/调用入口未实现。**
   当前 builder 需要调用方传入 SchemaDefinition、SchemaInstance、PaperMetadata 与
   WorkspaceContext。仓库中未发现 `wiki` 被 Layer3、API 或其他生产编排模块调用。

2. **没有默认的实体提议和语义消歧运行时组合。**
   已有 provider protocol，项目也有可复用的 L2S2 structured LLM 和 embedding provider
   基础设施，但 L2S3 未提供把它们配置、适配并安全传给 `EntityProposalRunner`/
   `EntityResolver` 的生产 factory。Package F 使用 fake 实现。

3. **持久化向量索引未实现。**
   当前 `package_b_index.json` 用于投影校验和审计；semantic search 在调用时通过注入的
   embedding provider 计算，不保存 Page/Entity 向量。若计划第 14 节的“semantic index”
   被视为必须交付，应补齐该项。

4. **Workspace 构建后的 Manifest 与索引没有统一自动编排。**
   当前使用者必须在 builder 成功后显式 `upsert_manifest()` 和 `rebuild_indexes()`；
   应提供受控的顶层用例服务，并清楚定义部分失败时 `build_status` 的更新规则。

5. **未做真实 provider 的单篇 smoke。**
   现有“真实论文”验收指真实审阅 fixture，而非真实 LLM、真实 embedding 或真实
   L2S2 持久化读取。真实服务测试需要单独的凭据、网络和成本审批，不能据此文档擅自执行。

---

## 7. 工作区与交付风险

截至本次审计，L2S3 源码、fixture 和测试均为未跟踪文件，尚未进入 Git 历史。因此：

- 当前测试通过只证明当前工作目录内容正确；
- 在提交前，L2S3 交付物容易因切换分支、清理或选择性暂存而丢失；
- 应先人工确认并暂存 `src/transit_scholar/layer2/wiki/`、所有 `tests/test_l2s3_*`、
  `tests/l2s3_package_f_support.py`、`tests/fixtures/l2s3_package_f/` 及本说明文档；
- 根 `.gitignore` 已忽略 `doc/`，因此若要把本说明纳入 Git 提交，必须明确使用
  `git add -f doc/20260825-L2S3Schema-guided-Base-Wiki开发完成情况说明.md`；
- 当前还存在 `.gitignore` 修改和 `scripts/l2s2_runtime_smoke.py` 删除。二者不是
  L2S3 开发计划的明确交付物，应在提交前由负责人确认其是否需要与 L2S3 变更同批交付。

本次审计没有修改上述已有源码、测试、fixture、`.gitignore` 或删除项。

---

## 8. 最终判断与建议下一步

**计划落实判断：核心计划已大体落实，验收计划已在离线、可重复的范围内通过；但尚未
完全落实为可直接投入生产的 L2S3 全链路。**

建议按以下顺序收尾：

1. 审核并提交当前 L2S3 代码、测试和 fixture；
2. 新增一个显式的 L2S3 应用服务，负责从 L2S2 资产装载输入、构造 provider、
   调用 builder、写 Manifest 并重建索引；
3. 确定是否把持久化 vector index 作为 L2S3 V1 的硬性范围；若是，则实现并为其增加
   重载与 workspace 隔离测试；
4. 在单独授权的环境中，用真实但最小范围的 LLM/embedding provider 运行一篇论文 smoke；
5. 为 Layer3 暴露只读、workspace-bound 的 `search_wiki`、`search_entities`、
   `find_pages_by_entity` 与 `get_page` 入口。

在第 2 至 4 项完成前，对外状态应保持为：**“L2S3 Schema-guided Base Wiki 核心开发完成，
生产运行时集成待完成。”**
