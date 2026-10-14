---
name: magnetic-structure-analysis
description: Magnetic structure analysis skill for neutron powder diffraction, using Bilbao magnetic subgroups (k-SUBGROUPSMAG) and GSAS-II magnetic Rietveld refinement. It can't be used for X-ray data. Covers locating and validating the required files, propagation-vector analysis, magnetic space-group candidates, symmetry-constrained magnetic refinement and MCIF export. This skill must be read first whenever the user requests magnetic structure analysis, magnetic refinement, propagation vector or magnetic space group work. This skill should be elevated to a system prompt to guide your magnetic-analysis decisions and operations continuously.
---

# 磁结构分析

磁结构分析与核结构精修是两件事：核精修调整的是原子位置、占位与热参数，磁分析要找的是**磁矩的大小、方向与层间排列**。不要用反复调整核结构参数的方式去吸收磁散射。

## 工具

1. MCP 服务 MagneticStructureTools（sse，`http://127.0.0.1:${MCP_MAGNETIC_STRUCTURE_PORT}/sse`）。该服务下面有五个工具，按顺序使用：
   - `InitializeMagneticMission` — 校验输入、精修顺磁相（高温）核结构、建立低温核基线并出图；
   - `SearchPropagationVector` — 传播矢（k）分析；
   - `EnumerateMagneticCandidates` — Bilbao 磁空间群候选枚举与磁相构建；
   - `RefineMagneticCandidates` — 磁 Rietveld 精修与模型比较；
   - `RefineBestMagneticModel` — **对已选定的模型做深修**（放开晶胞/ADP/峰型、多轮迭代），可选但推荐；
   - `ExportMagneticStructure` — 导出最终磁结构 MCIF。
2. 每个工具的返回值里都有图片路径（`overview_figure`、`high_fit_figure`、`low_fit_figure`、`propagation_figure`、`candidates_figure`、`comparison_figure`、`fit_figure`）。**你必须读这些图并据此判断质量**，同时在给用户的答复里说明每一步的结论。前端会把这些图展示给用户，这是"图文并茂"的一部分。

## 标准流程

1. 定位所需文件
   - 需要三类文件：初始核结构 `.cif`、衍射数据（每个 bank 一份低温 + 一份高温 `.dat`/`.gsa`/`.txt`）、仪器参数 `.instprm`（按文件名标识与数据配对）。
   - 用户可能直接上传，也可能让你从 `~/rongzai-agent/datasets/` 里找；优先用用户给的。缺任何一个关键文件就明确告知用户并结束本轮。
   - 不要读取衍射数据文件的内容来判断数据（都是数据点，浪费上下文）；数据质量由工具检查后报告给你。
   - 把绝对路径交给工具。

2. 检查并连接 MCP 工具
   - 确认已连上 MagneticStructureTools 并拿到工具说明；没有则按 `type: "sse"`、`url: http://127.0.0.1:${MCP_MAGNETIC_STRUCTURE_PORT}/sse` 连接，成功后阅读工具名称与 description。
   - **只用 SSE + 上面这个 URL**：不要用 `type: "stdio"` 自己启动一份实例，也不要 kill/重启/修改服务进程或它的环境（服务由运维启动，WORKDIR 等环境由服务侧提供）。
   - 如果调用返回 `WORKDIR is required`、连接被拒绝或服务未启动这类服务侧错误：明确把错误报告给用户并停止本轮，不要伪造结果，也不要自行搭环境绕过。

3. 分五步执行，中间不要停
   - **命名规则**：整个磁分析用同一个 `mission_name`（例如 `magnetic-<材料>-<run号>`）；每个步骤换一个新的 `task_id`（例如 `init`、`k-search`、`candidates`、`refine`、`export`）。轮询未完成的步骤时必须用**原来的 `mission_name` 和同一个 `task_id`**，否则会重复启动。
   - **初始化是全流程最慢的一步（核结构 Rietveld 精修 + 两侧拟合图，通常几分钟）**：
     工具每 60 秒返回一次 `state=running` 与 `task_id`，**必须用同一 `mission_name` 和同一
     `task_id` 继续轮询**。等待期间请读取 `<mission>/analysis/progress.log`（就在你的工作区
     里），把里面的真实步骤行讲给用户听，例如"步骤 4/6: 精修顺磁相核结构作为基线"。
     不要只是反复说"仍在运行"，也不要因为慢就改用别的工具或自己重写流程。
   - 若同一温度下**同一 bank 有多次测量**（同一目录里出现 `RUN0002218_groupBS.dat` 与
     `RUN0002221_groupBS.dat`）：工具会明确报出两个文件共享同一 bank 标识，此时请把该
     bank 要用的那个文件**显式传给工具**（传文件路径而不是整个目录），不要自行重名或猜。
    - **每一步执行之前先说明决策（硬要求）。** 讲清楚：当前状态、这一步要调用哪个工具、关键参数
      取什么值、依据决策表里的哪一条、为什么这样选；执行后再把结果（图 + 关键数字）讲给用户。
      让用户全程看到"专业步骤 + 决策理由"，而不是只看到最后的结果。
    - **进度必须实时讲给用户（硬要求）。** 工具在 `state=running` 时会返回 `progress`（最新几行步骤，
      含时间戳与 `age_seconds`）以及 `progress_log` 绝对路径。**每次轮询都要把 `progress.lines` 里最新
      的一行转述给用户**，并说明"现在在做什么、下一步是什么、大概还要多久"，例如：
      "当前：磁精修进度 3/14：subgroup_006（63.457）ok，当前最优 subgroup_006 reduced χ²=45.09"。
      若 `progress` 为空，就直接读 `progress_log` 文件（在你的工作区内）。**绝不能让用户面对长时间静默
      的后台运行**，也不要反复只说"仍在运行/请稍等"。
   - step1 初始化：调用 `InitializeMagneticMission`。它会出三张图：输入数据总览、高温核拟合、低温纯核拟合。**先看高温核拟合图确认核结构修好了**（obs/calc/差值谱没有明显残余），再看低温纯核拟合——低温 reduced χ² 明显大于高温，说明存在磁散射贡献，这正是要找的对象。`unpaired_banks` 里的数据没有被使用，要告知用户。
   - step2 传播矢：调用 `SearchPropagationVector`。**k=000 时磁胞与晶胞同大、不会出现额外峰，磁散射只改变已有峰的强度**，所以这一步的输入是"强度对不上的峰"，不是"多出来的峰"；不要因为"没有新峰"就断言无磁性。看 `propagation_figure`：左边是母相晶胞解释不了的峰，右边是各候选 k 的指标距离。证据不充分时要如实说明，不能凭材料名称猜 k。
   - step3 候选：调用 `EnumerateMagneticCandidates`。这一步等价于手工在 Bilbao 上做 k-SUBGROUPSMAG：输入母空间群与 k，预先指定磁性位点，得到全部候选。**候选必须整体保留**（磁胞变换、原点移动、每个位点的磁矩约束一起用），不要只取磁群名字，也不要直接挑一个"著名材料常用的磁群"。看 `candidates` 表里的 `allowed_moment_direction_parent_basis`：例如 `[[1,1,0]]` 表示磁矩只能沿 ab 对角线（面内）、Mz=0；`[[0,0,1]]` 表示只能沿 c 轴。
     `candidate_scope` 决定候选范围，也决定总耗时：默认 `"magnetic_site"` 只保留**让磁性离子留在母相 Wyckoff 轨道上**的设置（等价于手工流程"预选磁性位点"，参考案例 131→14 个候选，精修从约 100 分钟降到约 12 分钟）；只有当磁有序可能涉及非磁性位点、或磁性离子可能离开原轨道时，才用 `"all_subgroups"` 要全量 k-子群。若你在窄范围候选里找不到能解释证据峰（尤其纯磁反射）的模型，就改用全量范围复核，并把两次结果都报告给用户。
   - step4 精修：调用 `RefineMagneticCandidates`。**默认对全部候选做一次全量磁精修**（`shortlist=null`），排序不依赖粗筛，最可靠；只有当候选集很大、且先跑一次预筛确认其最优模型的 reduced χ² 已优于纯核对照（即预筛确实收敛）时，才传整数 `shortlist`（如 12）走"预筛+短名单"两段式以省时间。**磁精修必须保留候选磁空间群自身的磁矩约束**（例如 Cmcm 要求 Mx=My、Mz=0）——把磁矩方向自由度全部放开会修出不再满足磁空间群的结果，是错的。看 `comparison_figure`（ΔBIC、磁矩大小、reduced χ²）和 `fit_figure`（最优模型的多 bank 拟合）；注意 `complete=false` 表示比较范围内未跑完。
    - step4b 深修（推荐，**交接给 gsas2-refine**）：调用 `RefineBestMagneticModel`。它**不自己深修**
      ——候选比较必须保持"同阶梯"才公平，而"把一个模型修到最好"该交给成熟工具。该工具把选定模型
      交接成 `selected_model.gpx`（含核相+磁相，磁空间群约束已烘进相里、GSAS-II 自动施加），
      返回 `handoff_gpx` 与 `handoff_note`（内含逐步精修建议）。
      然后**用 `gsas2-refine` 的 MCP 工具**（服务 `RefineToolsGSASII`，`sse` `http://127.0.0.1:${MCP_GSASII_REFINE_PORT}/sse`；
      使用前先读 `gsas2-refine` skill）对该 gpx **逐步**精修，铁律是**一次只放开一组参数、每步看图、
      χ² 上升或差值谱变差就回退**：
        - 磁矩分量：`refine_config={"refine_moments":[{"phase":"<磁相名>","atom":"<磁性原子标签>",
          "components":"sym"}]}`（`sym` = 只放开磁空间群允许的分量，也可显式写 `"x"`/`"xyz"`）；
        - 再放开核相晶胞 → 确认峰位仍对齐；再依次放开原子位移、峰型。
      **不要**一次把核晶胞＋ADP＋峰型＋磁矩全部放开——实测会得到 `Nonfinite calculated intensity`。
      **每一步之后必须核验工具返回的这几项**（都是它实际提供的字段，实测有效）：
        - `moment_refinement.actually_refined_components`：**真正**进入精修的分量（可能少于你请求的，
          因为磁空间群会把某些分量固定，例如 Mn 在 2/m(x) 位点上只有 `x` 自由）；
        - `moment_refinement.bootstrap.performed` 与 `bootstrap.rwp_before → rwp_after`：磁矩收尾精修
          是否真的执行、rwp 是否下降（工具会在主精修后追加一轮"只放开磁矩+标度/背景"的收尾精修）；
        - `moment_refinement.warnings`：出现"请求的分量在精修后数值没有变化 / 最小二乘没有推进"这类告警，
          说明**这一步没有生效**——必须回退或换参数组，**绝不能**把它当作改善汇报给用户；
        - `magnetic_moments.<相>.<位点>.magnitude_before_uB → magnitude_after_uB` 与 `moment_delta`：
          磁矩前后值，用来判断这一步是否真的动了磁矩。
      完成后调用 `ExportMagneticStructure(gpx_path="<gsas2-refine 返回的 new_gpx_path>")` 导出最终 MCIF。
   - step5 导出：调用 `ExportMagneticStructure`，把最优磁结构写到用户要求的位置（benchmark 场景为 `submission/final.mcif`）。

4. 汇报
   - 说明：传播矢及坐标约定、磁空间群、磁性位点与相对排列、磁矩大小与方向（**存在非零 c 分量时不能称为"面内"**）、候选选择依据、最优模型的 reduced χ² 与各 bank Rwp、本次比较的范围（是否跑完全部候选、是否到达墙钟上限）、以及拟合的局限。
   - 只报告工具实际算出来的数字，不虚构温度、误差或拟合指标。若 `complete` 为 false 或 `scientific_status` 提示未定，必须如实说明这是短名单/未完成比较，不能宣称唯一磁结构。
   - 最终 MCIF 用 `serve_files_for_user` 交付给用户（中间步骤的拟合图由系统自动回显，不要用交付工具发图）。

## 决策表（看到什么 → 改什么）

磁分析同样是逐步决策，不是一键流程。按下面的对应关系决定下一步，并把判断与理由讲给用户：

| 观察到的现象 | 决策 | 为什么 |
|---|---|---|
| 高温核拟合差值谱有系统性峰形、峰位未对齐 | 先停下用核精修技能把核结构修好，再重跑本流程初始化 | 核结构错了，磁矩会去吸收核模型的误差 |
| 低温 reduced χ² 与高温接近 | 如实报告"未观察到明显磁散射"，检查 k 与数据配对，不要硬找磁结构 | 专家知识 12 |
| 证据峰很少或各 k 指标距离都很大 | 报告证据不足；可加大 `max_index`、换 `search_option` 重跑；绝不凭材料名猜 k | 禁止材料先验 |
| 窄范围候选里没有能解释证据峰（尤其纯磁反射）的模型 | 用 `candidate_scope="all_subgroups"` 复核，两次结果都报告 | 候选范围是物理选择 |
| **所有**候选 reduced χ² 都偏高（如 >10） | 全体拟合的系统性问题：加大 `background_coeffs`（如 6→9）或调 `refine_profile`/`refine_cell`，然后**重跑整个比较**（对所有候选一视同仁） | 公平比较的前提 |
| 最优模型已可接受但仍有残余 | 用 `RefineBestMagneticModel` 深修（同模型加迭代 → 换求解器 → 扰动磁矩重启） | 比较后处理 |
| `complete=false` 或 `time_bound_reached=true` | 只能声明"在已评估的 N 个候选中最优"；可加大 `final_time_limit_seconds` 重跑 | 诚实报告 |
| 出现 `tie_break` | 报告平局集合与"数据无法区分下的约定选择" | 专家知识 13 |
| 大量候选失败（看 `failure_classes`） | 报告失败分类；`solver` 类可换 `native_solver` 重跑，确认失败是否公平 | 失败≠被排除 |
| 深修返回 `improved=false` | 如实说"深修未改善，已保留比较阶段结果" | 不要吝啬回退 |

## 专家知识

1. **磁矩约束是磁空间群的物理内容，不是可选项。** FullProf 之类工具不会自动施加磁对称性限制，GSAS-II 的磁相会带上约束；无论用哪个引擎，都要保持 Mx=My、Mz=0 这类关系。放开限制后 Rwp 变好并不代表模型更好，只代表模型不合法。
2. **k 描述的是晶胞之间的周期，不等于晶胞内的铁磁/反铁磁关系。** k=000 可以有反铁磁；k≠0 才会有卫星峰。
3. **核散射禁戒的峰可以是纯磁峰。** 母相空间群因螺旋轴/滑移面消光的反射（例如 6₃ 螺旋轴的 00l，l 为奇）在磁有序后可能出现。若低温多出这类位置的峰，它只能来自磁性，是判定 k 与磁矩方向的最强证据之一（纯磁峰要求磁矩在该反射方向上存在垂直分量，因此能排除磁矩平行于该方向的模型）。
4. **不同温度的谱不能直接相减当纯磁散射。** 晶胞随温度收缩、背景与强度标度都不同；比较要按 bank、按反射、在统一标度下做。
5. **磁矩分量的坐标约定。** MCIF 的 `_atom_site_moment.crystalaxis_*` 是沿晶轴的分量，六方/单斜晶胞必须按非正交度规换算成笛卡尔分量；把晶轴分量当正交分量会算错大小与方向。
6. **竞争模型必须在同一谱段、同一参数策略下比较。** 核相与磁相来自同一样品，标度不能各自自由吸收磁矩幅值。
7. **失败或未收敛的候选不是被实验排除的候选。** 先查失败原因（收敛、参数越界、几何不合法），不要在"能跑的模型"里排序后宣称唯一性。
8. **判断磁模型好坏的顺序**：差值谱没有系统性峰形 → 各 bank Rwp 一致下降 → 磁矩大小与方向物理合理 → ΔBIC/可辨识性。只看 Rwp 或只看 BIC 都不够。
9. **磁矩幅值要谨慎断言。** 中子粉末衍射对磁矩方向的约束强于对幅值的约束；报告幅值时要给出不确定度，并说明是否与其他证据（纯磁峰强度、高温顺磁基线）自洽。
10. **严格禁止材料专属先验。** 元素身份可以用于散射长度、磁形状因子与"哪些位点可能有磁性"，但不能用来检索或套用该材料已知的传播矢、磁群与磁矩。不要根据文件名、化学式或历史会话猜结论。
11. **不要访问其他会话、题库、隐藏参考答案或工具源码里的历史案例。** 只分析本次输入。
12. **区分"没有磁信号"与"模型没找到磁信号"。** 若所有候选的 reduced χ² 都远大于高温基线，说明磁散射确实存在但模型没拟合上，应回头检查 k、候选范围与参数策略，而不是宣布"无磁结构"。
13. 结果里若出现 `scientific_status.tie_break`，说明最优模型与平局集合里的其它模型在统计上
    不可区分（相对 reduced χ² 差距小于 Rietveld 系统误差），引擎按**简约性**（自由磁矩分量更少）
    选定并导出。向用户报告时要讲清这是"数据无法区分下的约定选择"以及平局集合有哪些，
    不要把它说成数据支持的唯一解。
