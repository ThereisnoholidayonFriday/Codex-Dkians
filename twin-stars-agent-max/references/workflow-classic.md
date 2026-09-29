# Twin Stars V6 Classic 工作流契约（已有项目）

仅适用于 V6；Pro 使用其自包含入口，旧项目保留对应版本。先查看当前阶段，再按需读取以下章节；不为继续单图修订重新跑完整流程。

- 新建/恢复：入口与版本、状态与两个确认点、绑定文件与职责。
- 方案/制作：真正的 Agent 调度、研究/样张/质检、修订/诊断/预算。
- 记忆或交付：仅在实际记录/检索记忆或交付时读取对应章节。

用户明确指令优先于技能默认值，已有授权在实际范围内复用。运行时无法表示所请求变更时报告具体差异，不手改状态绕过校验；一般创意判断不增加确认关卡。

## 入口与版本

新项目使用同一入口的 `v6` 子命令：

```text
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 --help
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 init --help
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 init --project-root <ROOT> --project-id <ID> --brand-id <BRAND>
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 status --project-root <ROOT>
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 validate --project-root <ROOT>
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 bind --project-root <ROOT> --role <ROLE> --file <FILE> --expect-revision <N>
```

其余阶段命令为 `prepare-plan`、`approve-plan`、`export-controls`、`approve-production`、`reserve-attempt`、`record-attempt`、`select-candidate`、`review-packet`、`record-review`、`record-set-review`、`revise`、`finalize`；记忆命令为 `memory record/query/invalidate`。各子命令 `--help` 是当前参数语法依据，不编造命令或直接修改状态替代它。

`init` 可选 `--category <自由文本>`，省略时记录 `auto_detect`，由实际产品与需求识别。品类是开放词汇，无预定义白名单；六类验收样例不限制可处理产品，也不是要求用户提供六类素材。`brief` 可作为绑定角色保留实际项目目标、品类和来源说明。

`control/workflow_state.json` 使用 `schema_version:6.0`。确定性写入使用最新 `state_revision`、进程间锁、原子替换和历史记录；旧 revision 拒绝写入。来源先复制成不可变快照并记录 SHA，外部文件修改不会悄悄更改已批准内容。重新绑定才引入新版本，并让相应依赖失效。

不带 `v6` 的命令保留 V5.1 行为，仅用于相应旧项目。历史模板与数量/字体规则不成为 V6 门槛。迁移先检查报告，保持原文件及批准证据；无法恢复的事实、来源、图片评审与用户批准不得补造。

## 状态与两个确认点

主要状态：

`intake → strategy_active → awaiting_plan_approval → sample_active → awaiting_production_approval → production_active → qa_active → complete`

`blocked` 表示具体受阻情况，并记录受影响范围和可恢复动作。实际状态由命令前置条件控制；禁止通用强制跳转绕过批准。

1. **方案确认：**完整需求/提案、研究与审美依据、三方向、代表图描述/文案、样张资产范围及预算。没有确认的 Agent 提案不能进入样张。
2. **样张与生产确认：**通过的实际样张版本、选定方向、整套资产范围和预算；包含批准范围内通过 QA 后的交付。

已有明确授权直接记录，不对同一范围重复询问。无单独的产品证据确认或最终交付确认；遇到实质缺失/冲突，只暂停依赖内容并提出解决选择。批准绑定实际版本与范围，不能把旧图或缩小的样张范围批准当成任意生产授权。

`prepare-plan` 校验已绑定前提并进入方案待确认。提案确认后 `export-controls` 导出 `control/authorized/r<revision>/`，仅把有实际审批依据的条目标为已确认供编译使用，不修改来源或状态。新的范围、事实或已锁定要求变更需要更新对应控制和授权，普通范围内修复不增加审批轮次。

每条新增文案必须用单数 `requirement_id` 归属一个 `origin:agent_proposal,status:proposed` 的需求行；方案批准确认对应需求 ID 后，导出授权文案并编译 Prompt。不能通过手改 `copy_master` 状态代替批准，源提案保留原样。

两种批准命令均使用 `--file` 输入包含 `source:{type:user_message,reference,quote}` 的确认记录及当前范围哈希。`approve-plan` 使用 `prepare-plan` 的 `scope_hash`，并列明 `approved_requirement_ids` 和 `sample_asset_ids`；`approve-production` 使用当前 `production_scope_hash` 作为确认的范围哈希，并列明 `approved_asset_ids`、`max_total_attempts`。总预算包含已使用的样张尝试，不能把它解释为额外额度。

`record-review` 登记单图实际评审，`record-set-review` 登记精确批准范围的套图评审；全部当前样张与套图通过后才进入第二确认，正式资产与套图通过后才进入 `qa_active`。`review-packet` 会登记包和版本，需传最新 `--expect-revision`。不要把生成评审包当已执行评审。

评审失败后返回相应活动阶段处理缺陷；已 `complete` 的项目不能直接覆盖评审，须先用 `revise` 记录明确修改请求，再选择候选或执行修复及重新评审。已交付历史仍保留。

## 绑定文件与职责

| 绑定角色 | 内容与责任 |
|---|---|
| `product_master` | 产品事实、结构、支持视角和未知区域；product-evidence-control |
| `requirements`、`copy_master` | 原始要求、来源、Agent 提案、确认状态和逐字文案；product-evidence-control |
| `policy` | 目标市场/渠道、尺寸、预算、工具路线与项目规则；根 Agent |
| `research`、`aesthetics` | 问题覆盖、市场/参考来源、推断、标准与反例；研究与艺术指导 |
| `directions` | 三个方向、探索候选、淘汰理由、风格及差异；brand-art-direction |
| `typography`、`plan` | 项目排版、传播目的、套图、样张与整套资产；commerce-visual-strategy |
| `prompt_package` | 每资产完整独立通用 Prompt；commerce-visual-strategy |
| `cast_presence`、`scene_truth`、`physical_logic` | 适用的人物、场景、物理事实控制 |
| `prompt_source`、`revision_ledger`、`layout` | 内部可维护 Prompt、修订依据和版式控制 |
| `preflight` | 当前内置工具能力证据、原生/目标尺寸及文字和裁切风险；制作前实际核验，不改变已批准范围 |

文件可保留熟悉的产品主控、需求矩阵等名称；以角色和哈希绑定，不依赖路径命名猜测。V6 初始化模板和各 Skill 的 `*-v6.template.json` 模板帮助编写当前结构。下游不能改上游事实，只能提出带版本的修订。

保留本地工作目录：`inputs/`、`control/`、`research/`、`strategy/`、`prompts/`、`concepts/`、`production/`、`qa/`、`review/`、`outputs/`、`history/`、`workstreams/`。产物按需要创建，源文件只读，候选与正式输出区分。

## 真正的 Agent 调度

根 Agent 调用可用协作工具，按阶段复用六个 Skill；CLI负责校验、绑定和状态，不会自己凭空开展市场研究或生图。

市场与参考研究独立并行；重要问题覆盖后，D1/D2/D3独立探索。每个方向默认四个候选，筛选并深化一案。先锁事实/品牌边界，后锁创意/镜头，不提前给所有方向同一构图答案。

根 Agent 合并方向成果并做差异检查；方向 Agent 只写自己的 workstream。生产根据批准资产切分最多三个有用且不重叠的 worker；独立评审另用实际不同的 reviewer，第一轮不接收创作者解释、分数和推荐。

尊重实际并发槽位，可分批运行；能力不可用如实记录，并保留同等工作交接。记录真实工具/Agent ID、输入绑定、开始/结束和输出。注册名称、填 JSON 或生成空文件不代表任务完成。

## 研究、样张与质检

研究以决定性问题、来源适用性、反证和信息增量验收；V6无固定50参考、十竞品或来源家族配额。销售表现需可观察的日期/范围证据，审美标杆不等于市场畅销。无法打开的资料只能当发现线索。

每方向默认三个不同样张资产，覆盖主视觉、关键表达、制作难点，数量不超过实际资产；重叠角色不重复造图。样张直接覆盖目标尺寸和文字难点。通过且仍有效的样张可复用为正式交付。

全图生成与单张产品身份图＋唯一完整通用 Prompt 约束不变。产品真值、文字、品牌、尺寸与物理逻辑是硬约束；艺术指导和传播效果需要实际图像判断。使用手机/缩略图/裁切环境复核，不只看放大图。

`reserve-attempt` 前必须绑定通过的 `preflight`：`execution_engine:codex_builtin`，工具观察时间/名称、引用输入已验证、证据文件和 SHA；目标宽高格式逐项匹配批准资产。`native_size` 为验证过的 `[W,H]` 或未知时 `null`，已知尺寸声明 `none/scale/safe_crop`，裁切附保护计划，预测放大不得超策略阈值。`typography_risk` 为 `low/sample_required/verified`。未知原生尺寸只可在第一阶段对列出的样张用 `sample_probe` 并记录原因，正式批量前依据实样更新；不为填字段猜尺寸。预检失败发生在预算预占前。

所有评审绑定实际图片 SHA/版本、身份、时间、方法和可观察证据。独立评审不可用保持待评审。低置信度 OCR或与目视冲突进入记录化复核；缺少OCR不声称已通过。

## 修订、诊断与预算

自然语言反馈转成具体动作、受影响资产和保护项。方向变化传播至该方向，单图变化只触及本图。修后检查通过区域，避免修好文案破坏产品；不重做无关方向。

受修订影响的评审包携带 `revision_request`，列出当前修订目标与保护项；评审结果必须提供 `revision_check`，绑定 `revision_id`，在 `changes` 和 `protected_items` 中逐项目记录 `target`、`status`、`evidence`。所有目标和保护项均须通过并有实际图像证据，缺项或失败不能交付。该核对补充通常的完整图像门禁，不代替产品、文字、尺寸及独立评审；修订目标不包含创作者自评分或推荐。

每次失败分类为证据、需求冲突、创意、Prompt冲突、模型执行、尺寸适配或检测误差。下一次说明解决哪项原因及如何验证；连续同类失败先诊断再扩批。

调用图像工具前先用 `reserve-attempt --asset <ID> --producer-id <NAME> --expect-revision <N>` 预占尝试；重试另传 `--file` 指定原因、观察和具体下一步。返回后用 `record-attempt --attempt-id <ATT-ID> --file <RESULT> --expect-revision <N>` 保存实际 `tool_run_id`、`engine:codex_builtin_imagegen`、`image_path`/`native_path`，或失败状态、分类和观察。中断后的预占也计数，恢复不能再次获得同一次额度。

确实未发起工具调用时，结果文件使用 `status:not_started` 和真实 `observations`，不得填写虚构 `tool_run_id`。该状态解除待处理预占但仍消耗一次预算，不能当成已经生成过图片。

活动中的已批准阶段可用 `select-candidate --attempt-id <ATT-ID> --expect-revision <N>` 选择范围内已有候选；同一逻辑资产存在待完成预占时先如实结束该记录。命令核验候选/原生图哈希，使该资产和相关套图的旧评审失效，必须建立新评审包并按当前要求重评。选择历史候选不重置或增加尝试预算，也不能沿用旧结论宣称通过。

每个逻辑资产最多两次尝试，包含样张、正式、修订、恢复和未完成的预占。别名/新文件不重置预算。保留初始整图和一次定向编辑或重生成的上限策略；大问题可提前重生成但无额外额度。到上限保存候选与建议，相关范围保持未完成。

用户明确追加时才可用 `extend-budget --file <JSON> --expect-revision <N>`：source 为真实追加授权，asset_ids 为范围，additional_attempts 为每图新增次数，pilot_asset_id 为先行试修图。保留历史计数，禁止重复消费同一授权；试修当前评审通过后才允许扩展其他图。追加授权的试修可以在记录了真实尺寸未知原因时使用 sample_probe，不伪造预检尺寸。

修订保存来源、差异、旧/新版本和失效范围；当前图片变化使其旧评审失效。产品/需求等共享依赖变化应传播到所有实际依赖资产，未影响的资产保持有效。不可将旧批准任意移到新版本。

## 品牌记忆与受控学习

通过 `v6 memory` 使用本地持久记录，而不是每次只在对话中声称“已记住”。记录必须有品牌、来源项目、内容、适用条件和证据，范围分为：

- `brand`：品牌长期规则，可在同品牌后续项目检索。
- `campaign`：当前项目中的活动规则，需匹配项目和活动。
- `asset`：当前项目中的单图规则，需匹配资产及适用活动。

类型区分偏好、拒绝、失败、通过模式和性能数据。只采纳 `explicit_user_feedback` 的原话/引用或有文件 SHA 的 `verified_result`；一次否定不能自动扩大为全品类禁令。读到旧记录仍检查适用条件，当前指令优先。

查询只返回同品牌适用的有效记录；证据文件被改动或丢失的结果不能继续复用。纠正保留取代关系，失效保留历史；不能跨品牌或扩大原记录范围。实际投放数据与设计判断分开，无对照数据不推断设计因果。

项目结束整理已确认规律、失败原因和待验证建议。改通用 Skill 必须提出具体差异、回归验证并获得明确确认，不能从一次反馈自动修改系统。

## 完成与真实验收

已有单图通过但整套未完成时，运行 `export-partial --expect-revision <N>`。只导出重新校验仍合格的图片及原 Prompt/参考图，清单明确 partial、未完成范围和整套未认证状态；不改变批准范围、预算或项目完成状态。`status` 与新评审包的 `effective_execution_rules` 表示当前实际口径，历史 policy 不覆盖它。

`finalize` 只接受当前批准全范围中全部适用门禁通过的图片和一致清单。未完成资产要列明，不能缩小清单后宣布原任务完成。

实际导出到当前版本目录：每个选定资产的成品、`.prompt.txt`、唯一 `.product-reference` 图和 `.review.json`；同目录包含 `overview.html` 套图总览、`style-guide.md`、`version-record.json` 和 `set-review.json`。交付清单 `support_files` 逐件绑定哈希，`not_selected_asset_ids` 单独列明未选择资产；未选择不等于选定范围中的失败/遗漏。导出不调用生图，HTML是已有成品的浏览索引，不是额外生产图片。

交付成品、套图总览、风格规范、逐图完整 Prompt、质量与版本记录。通过检查的文件进入 `outputs/`；候选和失败保留。软件控制面验证与真实画面验收分开；商业可用与高端品牌水平须有实际画面、匹配用途比较和用户认可，不由程序测试代签。按现有真实项目进行验收并说明覆盖范围，不以尚无素材的示例品类阻塞通用系统交付。

## 用户提供内容

用户给定声明与功能场景按原文执行，保留来源并标明用户提供，不冒称独立核验；缺少额外佐证不单独构成门禁。Agent 不自行增加事实声明。

