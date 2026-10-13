---
name: pdf-refine
description: PDF refinement skill for pair distribution function fitting using the current PdfFit snapshot workflow. This skill must be read first whenever the user requests PDF data refinement. This skill should be elevated to a system prompt to guide refinement-related decisions and operations continuously.
---

# PDF Auto Refiner

## 工具

当前 MCP 服务 PDFRefinementTools 一共提供 6 个工具，必须按当前接口理解，不能使用旧版 refine_config 思路。

1. InitializeProject
    - 输入原始 PDF 数据文件和结构文件列表。
    - 创建第一份项目快照 project_path。
    - 快照核心字段包括：data_file、structure_files、nonRefineValue、constraintSet、refineState、output_files。

2. UpdateNonRefineValueAndConstraintSet
    - 用于编辑 nonRefineValue，并增量编辑 constraintSet。
    - 约束编辑不是整体替换，而是通过两个参数完成：
       - mergeConstraintSet：新增或覆盖约束。
       - deleteConstraintSet：按 selector 删除约束。
    - 工具会自动同步 refineState：
       - 新引用到的 @n 会自动创建。
       - 不再被任何约束引用的 @n 会自动移除。

3. UpdateRefineState
    - 只用于编辑现有自由参数的初值和 refine 开关。
    - refineState 的每一行格式为 ["@n", value, should_refine]。
    - 这个工具不会创建新的自由参数；新增 @n 应通过 UpdateNonRefineValueAndConstraintSet 里的 mergeConstraintSet 触发。

4. AutoSpaceGroupConstraints
    - 根据空间群自动生成原子位置和热参数的对称性约束。
    - 可按 phase_indices 指定相。
    - 可选择只生成坐标约束、只生成热参数约束，或两者一起生成。
    - 该工具会保留与对称性替换范围无关的现有约束，只替换目标范围内的对称性相关原子约束。

5. Refine
    - 基于当前 project_path 执行一次精修。
   - 输出新的快照、.res 结果、.fgr 拟合谱文件、拟合图，以及新的 refined structure 文件。
    - 注意：Refine 成功后，新快照中的 structure_files 会被更新为精修后的 .rstr 文件路径，而不再是初始 CIF 路径。

6. ExportCIFs
   - 输入是 rstr_files 列表；可选传入 project_path（用于旧快照缺失 fgr 时补生成）。
   - 将 refined structure 文件转换为 CIF 文件列表返回；若可用会一并返回 fgr_file。

## 当前数据模型

所有 PDF 精修都围绕不可变快照进行。每一步编辑或精修都会生成一个新的 project_path。

快照中的关键字段含义如下：

1. data_file
    - 原始 PDF 数据文件路径。

2. structure_files
    - 当前用于下一步精修的结构文件列表。
    - 初始化后通常是 CIF。
    - Refine 成功后会更新为对应相的 .rstr 文件列表。

3. nonRefineValue
    - 保存非精修参数。
    - 当前应通过它控制的主要内容：
       - 全局：qmax、rmin、rmax、pdf_source
       - 相级：phase[i].stepcut、phase[i].rcut
    - 不要把 dscale、qdamp、qbroad 放进 nonRefineValue，它们属于可精修量。

4. constraintSet
    - 是一个字典，包含两个等长列表：
       - params
       - constraints
    - params 中每项是物理量 selector，例如：
       - dscale
       - qbroad
       - phase[1].pscale
       - phase[1].lat.a
       - phase[1].atom[3].x
    - constraints 中每项是与之对应的线性表达式，例如：
       - @1
       - @2+0.5
       - -@3
       - 2*@4

5. refineState
    - 是自由参数表。
    - 每一行格式为 ["@n", 初值, 是否放开精修]。
    - Refine 会读取这些自由参数的初值和开关状态。

6. output_files
    - 保存最近一次 Refine 产生的结果文件路径。
   - 当前通常包含：results(.res)、fgr(.fgr)、plot(.png)、refined_structures(.rstr 列表)。

## 标准流程

如果你判断用户希望你自己把数据修好，那么你必须围绕当前快照架构反复调用工具，直到完成稳定的结构精修，不能使用旧版 refine_config 思路。

### 1. 定位精修所需文件

- 开始 PDF 精修必须至少具备：
   - 一个 PDF 数据文件（.txt 或 .dat，第一列通常为 r）
   - 至少一个结构文件（通常是 .cif）
- 优先使用用户当前提供或之前已提供的文件。
- 不要去读PDF数据文件的内容，里面都是数据点，占用上下文空间，无法提供有用信息。
- 若文件未上传，需要根据用户提供的信息去数据库寻找。
- 数据库位置通常在：~/rongzai-agent/datasets/
- 常见结构：

   datasets/
   |--BLXXX/
   |   |--RUNdataFiles/
   |      |--RUNXXX/
   |         |--cifFiles/
   |         |   |--XXX.cif
   |         |--XXX.txt
   |         |--XXX.dat

- 如果用户信息不足以唯一定位文件，需要询问用户。
- 如果关键文件缺失，要明确告知并结束本轮。
- 该步骤的结果必须是明确的绝对路径。

### 2. 检查并连接 MCP 工具

- 你需要确保已经连接上 PDFRefinementTools MCP 服务，并且已经拿到工具说明。
- 如果当前没有可用代理工具，使用以下连接信息：
   - type: sse
   - url: http://127.0.0.1:${MCP_PDF_REFINE_PORT}/sse
- 连接成功后，应确认可用工具至少包括：
   - InitializeProject
   - UpdateNonRefineValueAndConstraintSet
   - UpdateRefineState
   - AutoSpaceGroupConstraints
   - Refine
   - ExportCIFs

### 3. 初始化

- 当拿到 PDF 数据和结构文件后，先调用 InitializeProject 创建第一份快照。
- 初始化不允许留下未处理的问题：
   - 若返回 error，必须先解决。
   - 若返回 warnings 或 structure_checks 中存在明显结构问题，必须先处理，再重新初始化。
- 结构文件若有明显问题，可以直接修改 CIF 后重试初始化。

### 4. 先设置基础 nonRefineValue

- 在开始真正的参数开放前，先通过 UpdateNonRefineValueAndConstraintSet 设置合理的 nonRefineValue。
- 主要包括：
   - qmax
   - rmin
   - rmax
   - pdf_source（默认 N，除非用户明确要求 X）
- r 区间选择原则：
   - 过宽容易导致最初的拟合失败。
   - 过窄可能丢掉结构信息。
   - 优先选择峰形清晰、对拟合敏感的主要区间，如果用户没有明确要求，先选择 1.5~20 Å。

### 5. 峰位和尺度预对齐

- 初始阶段先只放开少量稳定参数，优先让主峰位置和整体幅度对齐。
- 常见优先顺序：
   - phase[i].pscale
   - phase[i].lat.a / lat.b / lat.c
- 做法：
   - 用 UpdateNonRefineValueAndConstraintSet 的 mergeConstraintSet 增加这些物理量与自由参数的映射。
   - 再用 UpdateRefineState 控制哪些 @n 在当前步真正放开。
   - 注意, phase[i].lat.a / lat.b / lat.c的绑定必须遵循对称性约束，例如：立方晶系下 a=b=c，六方晶系下 a=b≠c。
- 一次最多新增一个参数组。
- 如果出现发散、奇异矩阵、rw 明显变差，立即回退到上一个稳定 project_path。

### 6. 逐步开放非结构参数

- 当峰位和尺度基本稳定后，再逐步尝试：
   - phase[i].delta1
   - phase[i].delta2
   - 全局 qdamp
   - 全局 qbroad
   - 原子热参数，例如 u11、u22、u33
- 仍然遵循一次只新增一个参数组的原则。
- 如果需要对称性一致的原子位置/热参数约束，优先调用 AutoSpaceGroupConstraints，而不是手工把同类原子全部逐个绑定。

### 7. 结构精修

- 在前面阶段稳定后，再逐步开放真正的结构参数：
   - 原子位置 x/y/z
   - 占位率 occ
   - 必要时更完整的热参数
- 必须少量逐步开放，避免一次放开过多原子导致发散。
- 若使用 AutoSpaceGroupConstraints：
   - 先决定处理哪些相。
   - 决定只生成坐标约束、只生成热参数约束，还是两者都生成。
   - 生成后再通过 UpdateRefineState 控制新产生的 @n 是否参与本轮精修。

### 8. 每一轮 Refine 的工作方式

- 每次执行 Refine 前，要先向用户说明：
   - 当前快照状态
   - 接下来准备开放哪些参数
   - 为什么这样做
- 然后自行调用 Refine。
- Refine 后必须检查：
   - rw 是否改善
   - 参数变化是否合理
   - 拟合图是否更接近实验数据
   - 是否出现 refinement failed、Singular matrix 或明显异常参数
- 若本轮结果不合适：
   - 回退到上一个稳定 project_path
   - 换更保守的参数开放策略

### 9. 导出 CIF

- 当用户需要最终结构文件时，使用最新稳定快照中的 structure_files 作为 rstr_files 输入给 ExportCIFs。
- 正常流程下不需要 project_path；只有在旧快照缺失 fgr 且需要补生成时，才额外传 project_path。

## 约束编辑规则

必须使用当前约束编辑模型，不能再使用旧版整体替换思路。

1. 新增约束
    - 使用 mergeConstraintSet。
    - 如果 selector 已存在，则会覆盖旧表达式。

2. 删除约束
    - 使用 deleteConstraintSet。
    - 按 selector 删除，未列出的约束保持不变。

3. 更新自由参数
    - 若 mergeConstraintSet 中引入了新的 @n，工具会自动把它加入 refineState。
    - 若删除约束后某个 @n 不再被任何表达式引用，工具会自动把它从 refineState 中移除。

4. 控制当前是否放开
    - 使用 UpdateRefineState 修改 refineState 中每个 @n 的 should_refine。
    - 不要把“是否放开”写进 nonRefineValue 或别的结构里。

## 专家知识

1. 参数开放顺序遵循“由少到多、由稳到险”：先尺度与晶胞，再仪器/峰宽相关项，再结构参数。
2. 每一步只新增一个参数组最稳妥。一次开放过多参数会明显增加奇异矩阵和发散风险。
3. 判断结果必须同时看三件事：拟合图细节（黑色为实验谱，红色为拟合谱，蓝色为差值谱）、rw 数值、参数物理合理性，不能只看 rw。
4. 若出现 refinement failed、Singular matrix、rw 明显恶化或参数失去物理意义，立刻回退到上一个稳定快照。
5. 占位率应在合理范围内，热参数不应明显为负，晶格参数不能偏离常识范围太多。
6. 差值谱若存在明显系统峰形，说明仍有结构或仪器项未匹配，需要继续迭代。
7. 拟合好的拟合图中会有丰富的信息。比如：如果某个拟合谱的峰位和计算谱的峰位不一致，其他的一致，说明真实结构中可能存在局域畸变。你要充分动用自己的专业知识来挖掘拟合图中的信息。
8. AutoSpaceGroupConstraints 适合快速建立对称性相关约束，但是否放开这些新自由参数，仍应由 UpdateRefineState 分阶段控制。
9. Refine 成功后 structure_files 会变成 .rstr；后续 AutoSpaceGroupConstraints 仍可继续使用这些 refined structures 自动识别空间群，但若个别 .rstr 缺失空间群信息，需要显式传入 space_group。
10. ExportCIFs 只是结构导出步骤，不参与约束或精修决策。
11. 当用户给出明确目标，例如 rw 小于某阈值时，可以以达标为目标，但不能牺牲参数物理合理性。
12. PDF谱的低r区间反应局域结构，高r区间反应长程结构。若低r区间拟合不佳，说明局域结构模型有问题；若高r区间拟合不佳，说明长程结构或仪器峰宽模型有问题。
