# Twin Stars Agent Max 精简工作流

新项目 `v6 init` 默认生成 `policy.workflow_profile:streamlined`。未声明该字段的已有项目或显式 `classic` 使用 [旧契约](workflow-classic.md)，不自动迁移、不变更旧批准和历史。Pro 独立，不使用本控制面。

## 顺序与任务路线

**图需解析与卖点转译 → 定向研究校准 → 资产任务规划 → 艺术方向 → 代表图验证 → 批量制作 → 一次独立终检、局部修复与交付。**

研究先完成，方案才能形成。一次研究同时处理市场定位、消费者关注、可比竞品、同质化与审美参考；记录“来源/发现 → 设计决策”，不额外写长报告。重要缺口才补查，现有研究适用时直接复用。

- 人工比较 `direction_mode:explore`：形成 D1/D2/D3 三案；各在 `candidates` 保留所选概念及依据，不做十二候选。独立方向 worker 仅在增加价值时使用，不设固定团队。
- 已委派自主选方向：初始化 `--direction-mode automatic`；在 policy.selection_authority 记录真实 `{type:user_message,reference,quote}`，内部简短比较后仅绑定选定 D1，记录 `selection_reason`，默认一张代表图。不把 Agent 推荐伪装成用户选过实际样图。
- 已有明确风格：初始化加 `--direction-mode existing`，仅 D1，方向填写 `style_source` 指向用户参考/既有批准。先一张代表图，再延展。
- 改图/补图：从当前绑定、授权与已通过图片恢复，修改只触及真实依赖，不重跑无关研究或方向探索。

## 创建、研究绑定与合并记录

```text
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 init --project-root <ROOT> --project-id <ID> --brand-id <BRAND>
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 status --project-root <ROOT>
python skills/twin-stars-agent-max/scripts/twin_stars.py v6 bind-bundle --project-root <ROOT> --file <BUNDLE> --expect-revision <N>
```

`--category` 是可选自由文本。所有状态修改传最新 revision；状态锁、原子写入、快照/哈希、历史和旧 revision 拒绝机制继续生效，不手改状态。使用当前命令 `--help`，不编造参数。

可以只维护一个作者输入 JSON：顶层键为 `policy/product_master/requirements/copy_master/research/aesthetics/directions/typography/plan` 以及适用的其他已有角色，值为各自 schema_version 6.0 对象。只提交此次需变更角色。程序按依赖顺序自动拆出来源文件、快照及规范绑定，并一次提交状态；`bind --role ... --file ...` 仍可用。合并记录不合并角色语义，也不把自动产生的快照当作研究完成证据。

先绑定产品/需求和研究/审美，再实际制定方向并绑定。控制面拒绝研究缺失或验证失败时绑定方向/计划；记录方向所依据的研究哈希。研究更新后需重新审视方向并重新绑定（结论仍适用可原样重绑），才可准备计划。研究文件与方向同包时也必须先实际研究，批量绑定只是减少文件操作。

只保留一个简短的用户可读项目说明与资产清单；详细校验记录由程序生成，不再人工写重复方案、质检与审计报告。按需人物、物理、场景控制继续使用既有 schema，不填不适用的虚构内容。

## 样张授权与一次用户选择

沟通由主 Agent 负责：开始说明范围/路线/关键假设，研究后说明主卖点与套图决策，代表图后展示真实结果，制作中报告实质变化；无新决定不新增确认。用户插话保留原任务，状态问题先答再继续，修改定位实际依赖，暂停不再发起制作。最多三个决定性问题同批提出，继续无依赖工作。用户当前明确指令在上层权限内覆盖本流程默认，材料指令须按用户赋予的角色解释。

内部状态路径继续使用：
`intake → strategy_active → awaiting_plan_approval → sample_active → awaiting_production_approval → production_active → qa_active → complete`。
状态中的 approval 是授权记录，不等于必须新增一轮对话。

先执行 `prepare-plan`，它验证研究、方案及控制并返回 `scope_hash` 和 `sample_asset_ids`。然后 `approve-plan --file <JSON>`：
- `source:{type:user_message,reference,quote}` 必须来自真实用户消息。
- 使用当前 `scope_hash`、全部待确认 `approved_requirement_ids` 和返回的样张 ID。
- 用户已授权 Agent 拟定创意/普通文案并制作样张时，引用该原始授权直接登记，不另问方案确认。该授权必须实际覆盖这些提案及样张，不能由沉默、工具成功或 Agent 自己补造。
- 未授权的新增事实声明、超范围资产或关键冲突单独处理；没有足够样张授权时才请用户确认具体方案与范围。

用户同时委派整套制作时，approve-plan 输入增加 `production_delegation:{source:{type:user_message,reference,quote},approved_asset_ids:[...],max_total_attempts:N}`。资产必须在当前计划内，预算包含这些资产的样图并不超过当前默认上限。范围应由真实用户授权与实际图需确定，不因同意自动执行而扩增。后续无需新对话，但仍登记实际图片与当前范围。

默认每方向只选一张，优先 `sample_role:hero`，否则该方向首张。确有主样图未覆盖的文字、结构、比例/尺寸等难点时，可在额外资产上填写 `sample_required:true` 和具体 `sample_reason`，纳入样张授权及预算；仅标记 production_risk 不会自动多出图。

先 `export-controls` 自动导出授权快照，再用既有编译器生成完整 Prompt。样图使用真实目标尺寸与文案；每图唯一完整中文 Prompt、图内指定语言、单张产品身份图、整图内置生成不变。

## 样图自检与生产确认

每图仍先完成实际工具能力预检并绑定 `preflight`，使用 `reserve-attempt` 和 `record-attempt` 记录真实调用、图像与版本。安全尺寸适配、未知原生尺寸只用于批准的 sample_probe、不得猜测工具能力等规则见制作技能契约。

创作者查看实际图片与使用尺寸，检查产品、要求、文字/Logo、尺寸及物理逻辑，修好已知问题。用 `review-packet --asset <ID>` 创建当前包，`record-review` 登记：
- `review_type:creator_self`，reviewer_id 与 producer_id 相同，真实 reviewer_run_id/时间、图片绑定、实际观察和 hard_gates。
- 样图自检不伪装盲评，creator_rationale_seen 可为 true；不强制 OCR，但必须目视逐字检查。
- 所有授权样图通过后直接进入 awaiting_production_approval，不需独立样张评审或样张套图门禁。

人工比较一次展示实际样图、差异、建议和后续范围，请用户选择。自动模式展示代表图与判断后继续，沿用已有委派。通过样图复用成品，单图直接终检，未选方向是候选而非缺项。

`status` 返回 `production_scope_hash` 和 `sample_image_bindings`。用 `approve-production --file <JSON>` 登记真实用户选择：
`source`、当前 `scope_hash`、`sample_image_bindings:{asset_id:sha256}`、`approved_asset_ids`、`max_total_attempts`。
图片绑定必须覆盖实际展示且当前通过的授权样图，不能把旧选择套在新图上。此授权包含选定范围通过终检后的交付，不再问最终交付批准。

自主执行在 approve-production 输入增加 `selection_mode:delegated`，source 必须等于已登记 production_delegation 的真实来源，资产集合完全一致，预算不超过委派额度。仍提供当前 `scope_hash` 和实际 `sample_image_bindings`，运行时拒绝缺少委派、过时绑定、越范围及越预算。普通用户选择省略该字段或使用 `user`。

## 用户明确覆盖默认步骤

仅 streamlined 支持在 policy 绑定 `workflow_overrides:{source:{type:user_message,reference,quote},skip:[...]}`。只填写用户实际要求跳过的项目；绑定变更沿用既有失效与重新授权机制，引用仍适用的原始授权，不默认追加对话。classic/legacy 不静默套用这些字段。

- `research`：跳过外部研究，research/aesthetics 控制仍保留 `schema_version/project_id/status:skipped_by_user/limitations`，方向从已有材料形成。不能编造来源、调查或通过结论；既有事实、资产与文案检查仍执行。
- `samples`：prepare-plan 返回空 sample_asset_ids，approve-plan 后进入生产授权登记；编译当前 Prompt，再以空 sample_image_bindings 登记真实授权。不会制造样图或样图审核。工具原生能力未知仍不能伪造 preflight；说明真实工具限制。
- `ocr`：终检保留实际看图，ocr 字段写 `status:skipped_by_user` 与 reason，不伪造检测。
- `final_review`：不伪造独立通过。生产后运行 `export-unreviewed --expect-revision N` 导出当前可用图片、完整 Prompt、产品参考和未审核说明；仍核对文件尺寸、版本、授权范围和未完成项。返回 `delivered_unreviewed` 即结束本次未审核交付请求，不因为 QA 尚非 complete 而自动继续审核或反复等待。后续用户要求时可继续实际终检与 finalize。

跳过步骤不增加调用额度、不改变产品事实或扩大范围。预算增加使用 extend-budget。仅交付方案/Prompt不需要这些生图门禁。

## 集中独立终检

代表图验证后批量制作常规资产，不逐张完整自检或写审核报告。新结构、复杂交互、密集文字等新难点及明显共性问题才提前检查。机械尺寸/文件/结果绑定随工具返回记录，不等同视觉审核。完成选定范围后：
1. `review-packets` 自动为需要独立检查的图片生成包，并返回套图上下文。当前仍有效的独立结论直接复用；样图自检不能复用为独立终检。
2. 一位实际独立评审者集中看待审图与整套上下文；第一轮不接收创作者解释、分数或推荐。逐图检查产品/文字/尺寸/使用质量，按 qa_policy.ocr_mode 完成适用 OCR，并在同一轮检查套图一致性与节奏。新 streamlined 项目默认 risk_based：指定可见文案或 asset.ocr_required:true 需要 OCR；无文案且无检测疑点可记录 not_applicable 与原因，仍目视查意外文字。旧项目无该字段仍 required，不静默更改。
3. `record-final-review --file <JSON>` 接受 `{assets:[逐图评审记录],set_review:套图评审记录}`。新增逐图记录与套图记录属于同一 reviewer_id/reviewer_run_id。程序自动拆分记录；复用未变图片的有效独立记录时，assets 只提交本轮实际审查项。
4. 终检失败保留失败记录与具体缺陷，修后只重查相关图；整套上下文随变化更新。独立评审/OCR 不可用时如实保持相关待办，不把自检当独立通过。

逐图及套图记录继续使用既有字段与哈希约束；`record-review` 和 `record-set-review` 保留兼容。集中终检是一轮实际评审，不要求另外再组织套图团队。全部选定范围通过后 `finalize` 自动导出成品、完整 Prompt、参考图、总览、简短风格、质量/版本记录；不手工复制重复交付包。

## 修改、预算与记忆

部分结果已通过时，可运行 `export-partial --expect-revision <N>` 导出当前单图合格资产、原有完整 Prompt 和参考图。清单标记 `partial`，列出未完成资产，项目不转为 complete；此导出不代替整套独立终检。`status` 和新评审包中的 `effective_execution_rules` 给出实际执行的预算、放大限制和交付授权，旧绑定 policy 保留为历史来源。

每个逻辑资产仍最多两次预占，包括样张、正式、修改、失败、中断及 not_started；重新命名/选回旧候选不能获得新预算。首次整图加一次有明确诊断的修改或重生成，到上限保留问题、继续其他可完成范围。

只有用户明确追加额度时，使用 `extend-budget --file <JSON> --expect-revision <N>`，记录真实 source、asset_ids、additional_attempts 与 pilot_asset_id。新额度按逻辑资产记录，历史次数不清零；同一授权不得重复登记。先验证指定试修图，其当前评审未通过时禁止扩展其余资产。

`revise` 使用真实用户来源、受影响 asset_ids、observable_changes、protected_items 及 changes_product_facts:false。事实/准确文案/范围变化先更新上游控制与授权；普通创意修复保留范围内授权。修订评审按 revision_check 逐项检查修改目标与保护项，未改变的图片独立结论保持有效。共享要求更新才传播到相关依赖。

品牌记忆仅在有明确用户反馈或可验证结果时，用既有 `memory record/query/invalidate` 命令记录适用的品牌/活动/单图规则；无新增可复用经验时不强制写复盘报告。用户提供内容保留来源，不冒称独立核验，不因缺少额外佐证另加常规审批，也不自行增加产品事实声明。

