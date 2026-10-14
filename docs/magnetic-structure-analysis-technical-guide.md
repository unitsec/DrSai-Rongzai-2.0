# 磁结构分析能力——完整技术文档

**写给需要向他人推广、讲解或接手维护的读者。** 本文档从"零基础"出发，按架构→工具→流程→设计决策的顺序，逐层展开所有技术细节。

---

## 目录

1. [整体架构：一个 Skill + 一个独立 MCP 服务](#1-整体架构)
2. [六个工具逐个详解](#2-六个工具逐个详解)
3. [Skill 的设计思路与决策表](#3-skill-的设计思路与决策表)
4. [传播矢确认：怎么找到 k 的](#4-传播矢确认怎么找到-k-的)
5. [每张图的含义与读法](#5-每张图的含义与读法)
6. [候选枚举与磁空间群约束](#6-候选枚举与磁空间群约束)
7. [精修策略、平局裁决与交接深修](#7-精修策略平局裁决与交接深修)
8. [进度通道：为什么等待时也能看到进展](#8-进度通道为什么等待时也能看到进展)
9. [关键设计决策与理由](#9-关键设计决策与理由)
10. [文件索引](#10-文件索引)

---

## 1. 整体架构

### 1.1 "一个 Skill + 一个 MCP 服务"模式

rongzai 智能体已有的五大能力（核 Rietveld、PDF、小角、Irena、zrietveld）全部采用同一种架构：

- **一个 MCP 服务**（独立进程，端口固定），对外暴露若干工具，每个工具签名统一为 `user_id + session_id + mission_name + task_id`，长任务在子进程执行并用同一 `mission_name + task_id` 轮询。
- **一个 Skill 文件**（`agent_skills/<name>/SKILL.md`），说明该能力有哪些工具、按什么顺序用、遇到什么情况怎么决策、以及专家知识。

磁结构分析完全遵循这个模式：

| 组件 | 位置 | 端口 |
|---|---|---|
| MCP 服务（6 个工具） | `tools/magnetic_structure_mcp.py` | 42512 |
| Skill | `agent_skills/magnetic-structure-analysis/SKILL.md` | — |
| 引擎（核心逻辑） | `tools/GSAS-II-master/magnetic_analysis/mission.py` + `native.py` + `present.py` | — |
| 配图模块 | `tools/GSAS-II-master/magnetic_analysis/present.py` | — |
| 深修交接目标 | gsas2-refine（同一 GSAS-II，已扩展磁能力） | 42506 |

### 1.2 数据流向

```
用户上传文件（.cif / .dat / .instprm）
        │
        ▼
┌─────────────────────────────────────────────────────┐
│  MCP 服务 (42512)                                    │
│  六个工具按序调用，每个在独立子进程中跑               │
│  返回 JSON + 绝对路径的 PNG 图 + 表格                │
└──────────┬──────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐    ┌─────────────────────────┐
│  mission.py          │    │  present.py              │
│  - 会话/任务隔离     │    │  - 每阶段生成 PNG 配图   │
│  - 输入配对与校验    │    │  - Rietveld 拟合面板     │
│  - 五阶段编排       │    │  - 传播矢证据图          │
│  - 进度日志         │    │  - 候选与模型对比图       │
└──────────┬───────────┘    └─────────────────────────┘
           │
           ▼
┌─────────────────────────────────────────────────────┐
│  native.py (科学引擎)                                │
│  - Bilbao k-SUBGROUPSMAG 查询（通过 GSAS-II 接口）   │
│  - 对称性约束的磁 Rietveld 精修                      │
│  - BIC 排名、平局裁决、失败三分类                    │
│  - 墙钟优雅降级                                     │
└─────────────────────────────────────────────────────┘
           │
           ▼ (深修部分)
┌─────────────────────────────────────────────────────┐
│  gsas2-refine (42506)                                │
│  - 逐步精修（一次只放开一组参数）                     │
│  - 磁矩收尾精修 (bootstrap)                          │
│  - 假成功防护                                       │
└─────────────────────────────────────────────────────┘
```

### 1.3 会话与工作区隔离

所有读写锁在 `WORKDIR/<user_id>/<session_id>/<mission_name>/` 下：

```
agentWorkSpace/
└── <user_id>/
    └── <session_id>/
        └── <mission_name>/
            ├── input/          ← 配对的输入文件
            ├── analysis/       ← 引擎产出（报告/GPX/进度日志）
            ├── figures/        ← PNG 图（每阶段至少一张）
            └── submission/     ← 最终 MCIF
```

`user_id` / `session_id` / `mission_name` 三者都通过白名单校验（`[A-Za-z0-9._:-]` 最多 128 字符），路径中不允许 `..` 或 `/`，因此跨会话访问会被拒绝。

---

## 2. 六个工具逐个详解

### 2.1 InitializeMagneticMission（初始化）

**做什么**：
1. 校验并配对输入——通过文件名 token 把高温谱、低温谱、仪器参数按 bank 对上（例如 `RUN0002218_groupBS.dat` 配 `BS_2025.instprm` → bank `bs`），配对失败时明确报告哪些 bank 的哪些文件没配上。
2. 精修顺磁相（高温）核结构（scale→cell→ADP，3 阶段 24 周期），作为后续所有磁模型的基准。
3. 建立低温"纯核"对照——同一个核模型放在低温数据上拟合，reduced χ² 高说明存在磁散射贡献（这正是要找的对象）。
4. 出 3 张图：输入总览（五 bank 高/低温叠图）、高温核拟合、低温对照拟合。

**为什么要这一步**：
谱仪科学家第一步永远是确认核结构修好了，核结构没修好就去分析磁结构，磁矩会去吸收核结构的偏差（给出错的方向或幅值），Skill 的决策表甚至要求"高温差值谱有残余→先停下用核精修把结构修好再回来"。

**输入示例**：
```json
{
  "user_id": "u1", "session_id": "s1", "mission_name": "magnetic-MnTe-001", "task_id": "init",
  "initial_cif": "<绝对路径>.cif",
  "high_temperature": "<目录/路径>", "low_temperature": "<目录/路径>", "instruments": "<目录/路径>"
}
```

**返回示例（节选）**：
```json
{
  "status": "completed",
  "banks": ["bs","ha","la","ma","se"],
  "unpaired_banks": {"high":[],"low":[],"instruments":[]},
  "magnetic_elements": ["Mn"],
  "high_reduced_chisq": 11.99,
  "low_nuclear_reduced_chisq": 283.95,
  "overview_figure": "<绝对路径>/input_overview.png",
  "high_fit_figure": "<绝对路径>/high_temperature_nuclear_fit.png",
  "low_fit_figure": "<绝对路径>/low_temperature_nuclear_fit.png",
  "summary": "顺磁相核结构已精修（reduced χ²=11.99）；低温相纯核模型 reduced χ²=283.95，两者差异就是磁散射要找的对象。"
}
```

### 2.2 SearchPropagationVector（传播矢搜索）

**做什么**：
1. 找出母相晶胞"解释不了"的峰——即低温核模型拟合后，偏差最大的反射（可能是纯磁反射，例如系统消光的 001）。这一步不会简单地"看低温多了哪些峰"，因为 k=000 时**没有新峰**，磁散射只改变已有峰的强度，所以用的指标是"核模型对某个峰的强度低估了多少"。
2. 用 GSAS-II 的 k-vector search 对证据峰做候选 k 计算，返回各 k 的指标距离（indicator distance）和 d 偏差。
3. 图中左边列出证据峰的 d 值与所属 bank，右边列出每个候选 k 的指标距离。

**关键技术点**：
- 证据峰来自 `inspect_spectra` 报告的峰表，与核模型残差对比，选择相对偏差（`|I_obs - I_calc| / I_obs`）最大的若干个。
- k 搜索调用 `k_vector_candidate_search.py`（你提供的脚本），搜索模式支持高对称点（option=0）、高对称点加路径（option=1）、全网格（option=2，需要编译扩展 `kvec_general`）。

**示例返回**：
```json
{
  "status": "completed",
  "chosen_k": [0.0, 0.0, 0.0],
  "k_candidates": [{"k":[0,0,0],"indicator_distance":0.00578,...},...],
  "evidence_peaks": [
    {"d":6.6502,"banks":["la","ma"],"significance":653.6},
    {"d":1.9698,"banks":["bs","ha","ma","se"],"significance":155.9},
    {"d":2.2194,"banks":["bs","ha","ma","se"],"significance":139.5}
  ],
  "propagation_figure": ".../propagation_vector.png"
}
```

### 2.3 EnumerateMagneticCandidates（候选枚举）

**做什么**：
1. 按传实验得到的 k，向 Bilbao k-SUBGROUPSMAG 查询该母空间群在 k 下允许的所有磁子群。
2. 为每个候选用 GSAS-II 构建**带磁矩约束**的磁相——晶胞变换矩阵、原点偏移、每个磁性位点的独立磁矩分量与约束关系（例如 `parent_basis_constraint_relations = [[1,1,0]]` 表示 Mx=My、Mz=0）。
3. 出图列出各候选的 BNS 号、磁空间群符号、独立磁矩分量数。

**`candidate_scope` 参数**（默认 `"magnetic_site"`）：
- `"magnetic_site"`：只保留让磁性离子留在母相 Wyckoff 轨道上的设置（等价于 PPT 里"预选 2a 位"）。参考案例从 131 个候选缩到 **14 个**，总耗时从约 100 分钟降到约 12 分钟。
- `"all_subgroups"`：要全部 k-子群（131 个），用于磁有序可能涉及非磁性位点或离子离开原轨道的场景。
- 缺 ase/spglib 时自动回退并报告。

**为什么要保留"全部了解"**：候选的整体信息（磁胞变换、原点偏移、约束关系）必须**一并保留**，不能只取磁群名字——不同的变换/原点对应不同的物理结构，同一个磁群的两份候选可能精修出不同的磁矩方向。

**示例返回**：
```json
{
  "status": "completed",
  "candidate_scope": "magnetic_site",
  "candidate_count": 14,
  "candidates": [{"id":"subgroup_006","bns_number":"63.457","magnetic_space_group":"C m c m",...}],
  "candidates_figure": ".../magnetic_candidates.png"
}
```

### 2.4 RefineMagneticCandidates（磁 Rietveld 精修与比较）

**做什么**：
1. 对全部候选（当 `shortlist=null`，默认）或短名单做**完全相同**的磁 Rietveld 精修。
2. 阶梯是先磁矩分量（固定核相 Cell 与 ADP，防止它们吸收磁信号）→ 再放开核相 XU/cell/峰型，全过程中**磁矩始终受磁空间群约束**（例如 Mx=My、Mz=0）。
3. 按 BIC 排名，选出最优。统计不可区分时（相对 reduced χ² 差 ≤0.1%）由简约性裁决（自由磁矩分量更少者优先），并把这次裁决写进 `scientific_status.tie_break`。
4. 墙钟上限到达时**不再丢弃已完成候选**，而是照常按 BIC 选出已评出的最优，标记 `complete=false`。
5. 出图：模型对比图（ΔBIC、磁矩大小、reduced χ²）+ 最优模型的多 bank 拟合图。

**关键参数**：
| 参数 | 作用 | 默认 |
|---|---|---|
| `shortlist` | `null`＝全量单次，`<整数>`＝预筛+短名单 | null |
| `final_cycles` | 每候选迭代周期 | 24 |
| `final_time_limit_seconds` | 精修墙钟上限 | 5400 |
| `native_solver` | 求解器 | `analytic Jacobian` |
| `background_coeffs` | 本底项数 | 6（偏高时决策表要求加大后重跑） |
| `refine_profile` | 是否放开峰型 | True |
| `refine_cell` | 是否放开晶胞 | True |

**返回示例（节选）**：
```json
{
  "status": "completed",
  "best_model": "subgroup_006",
  "models_evaluated": 14,
  "models_total": 14,
  "complete": true,
  "ranking": [
    {"id":"subgroup_006","bns_number":"63.457","reduced_chisq":45.09,"moment_magnitudes":[3.67]},
    {"id":"subgroup_005","bns_number":"63.462","reduced_chisq":45.08,"moment_magnitudes":[3.67]}
  ],
  "scientific_status": {
    "tie_break": {
      "preferred": "subgroup_006",
      "over": "subgroup_005",
      "rule": "fewest free moment components among statistically tied models",
      "tied_models": ["subgroup_005","subgroup_006"],
      "relative_chisq_gap": 0.000122
    },
    "failure_classes": {"solver": 10, "geometry": 2}
  },
  "comparison_figure": ".../model_comparison.png",
  "fit_figure": ".../best_magnetic_fit.png"
}
```

### 2.5 RefineBestMagneticModel（交接给 gsas2-refine）

**做什么**：
把比较阶段选定的最优模型导出成一个**独立的 GSAS-II 项目文件** (`selected_model.gpx`)，附带一份逐步精修建议（`selected_model.md`）。后续由智能体调用 `gsas2-refine`（42506）对该项目做"一次只放开一组参数"的深修。

**为什么不再自己深修**：
旧版自研阶梯尝试过"一次把晶胞+ADP+峰型+磁矩全部放开"——导致 `Nonfinite calculated intensity`（违反"一次只放开一组"铁律的代价），后来又尝试了"磁矩 +5% 扰动重启"等自创策略，实测均未改善且不可靠。因此改为交给成熟的、决策驱动的 gsas2-refine。详见 [第 7 节](#7-精修策略平局裁决与交接深修)。

**返回**：`handoff_gpx` (路径)、`handoff_note` (路径，含参数示例)、`before_reduced_chisq`、`model`。

### 2.6 ExportMagneticStructure（导出）

**做什么**：
把最终磁结构导出为 MCIF（含 `_atom_site_moment.crystalaxis_*` 的 CIF 格式），写入指定位置（或默认写到 session 的 `submission/final.mcif`）。

**`gpx_path` 参数**：如果深修阶段用了 gsas2-refine 精修并产生了新的 gpx 项目文件，传入该路径，导出工具会从精修后的项目里取出磁相并生成最终 MCIF。

---

## 3. Skill 的设计思路与决策表

### 3.1 设计原则

1. **先看核结构**：核拟合有残余→不分析磁（否则磁矩会吸收核偏移）。
2. **k 来自实验证据**：看"母相解释不了的峰"，不凭材料名或历史案例猜。
3. **候选整体保留**：磁胞变换+原点偏移+约束关系，不能只取磁群名字。
4. **磁矩始终受磁群约束**：例如 Cmcm 要求 Mx=My、Mz=0——放开这个约束去修会得到"不合法"的磁结构。
5. **失败≠被排除**：候选收敛失败永远是"没跑到"，不是"被实验排除"。
6. **每步看图**：Rwp 和 BIC 只是数字，差值谱的系统性残余才是真正的裁判。
7. **不用材料专有信息**：元素身份用于散射因子和"哪个位点可能磁"，不用于查文献磁结构。

### 3.2 决策表

Skill 里列出了一些常见的"看到什么→怎么办"：

| 观察到的现象 | 决策 | 为什么 |
|---|---|---|
| 高温核拟合差值谱有系统性峰形/峰位未对齐 | 先停下，用核精修技能把结构修好再重跑 | 磁矩会吸收核模型误差 |
| 低温 reduced χ² 与高温接近 | 如实说明"未观察到明显磁散射"，检查 k 与数据配对 | 不要硬找磁结构 |
| 证据峰很少或各 k 指标距离都大 | 报告证据不足；加大 `max_index` 或换 `search_option` 重跑 | 不凭材料名猜 k |
| 窄范围候选没有能解释证据峰的模型 | 用 `candidate_scope="all_subgroups"` 复核，两次结果都报告 | 物理范围选择 |
| **所有候选 reduced χ² 都偏高（>10）** | 加大 `background_coeffs`（如 6→9）后 **重跑整个比较** | 必须对所有候选一视同仁 |
| `complete=false` 或 `time_bound_reached=true` | 只能声明"已评估 N 个候选中最优"，可加大时限重跑 | 诚实报告 |
| 出现 `tie_break` | 报告平局集合与"数据无法区分下的约定选择" | 不是唯一解 |
| 大量候选失败（看 `failure_classes`） | 报告失败分类；`solver` 类可换求解器重跑 | 失败≠被排除 |
| 深修返回 `improved=false` | 如实说"未改善，已保留原结果" | 不吝啬回退 |
| `moment_refinement.warnings` 有假成功告警 | 这一步没生效→必须回退或换参数组 | 不要把静默失败当改善 |

### 3.3 每步核验依据（gsas2-refine 阶段）

对 gsas2-refine 的每一次 `Refine`，智能体必须查：
- `moment_refinement.actually_refined_components`：真正进精修的分量（可能少于请求，因为磁群固定了某些方向）
- `moment_refinement.bootstrap.performed` + `rwp_before → rwp_after`：收尾精修是否执行、rwp 是否下降
- `moment_refinement.warnings`：出现"分量数值没变化/最小二乘没有推进"→这一步没生效，必须回退
- `magnetic_moments.<相>.<位点>.magnitude_before_uB → magnitude_after_uB` + `moment_delta`：是否真的动了

---

## 4. 传播矢确认：怎么找到 k 的

这是整个流程里最体现物理洞察的一步。完整实现分为三段：

### 4.1 找"母相晶胞解释不了的峰"

**输入**：初始化阶段的低温核对照拟合——同一个核模型放在低温数据上，哪些峰的实际强度与计算强度相差最大？

工具对每个 bank 的每个峰计算一个"相对偏差"（`|I_obs - I_calc| / max(I_obs, ε)`），取每 bank 的 top-N 个，再跨 bank 合并（同 d 值附近的峰视为同一个"候选证据峰"）。

**直觉**：这等于在问"有哪些峰在低温下变强/变弱了，而纯核模型解释不了"。对于 k=000（磁胞等于晶胞、没有卫星峰），这是唯一能观察到的磁信号；对于 k≠0，卫星峰也会出现在这里。

### 4.2 GSAS-II k-vector search

把这些证据峰的 d 值交给 GSAS-II 的 k-vector search（高对称点模式，option=0），逐个测试每个高对称 k 点是否能指标化这些峰（给出一个"指标距离"——越小越好），最终输出候选表。

实际的 k 搜索是通过 GSAS-II 内部的 `GSASII.SUBGROUPS` 模块实现的：它生成母空间群的对称操作列表，用 `postURL` 发给 Bilbao 的 `subgrmag1_general_GSAS.pl` 端点，再解析返回的候选中包含的磁群和 k 点信息。

**调用链**：`search_propagation_vector` → `mission.propagation` → `k_vector_candidate_search.py`（你提供的脚本）→ GSAS-II 内部 → Bilbao。

### 4.3 出图

`propagation_figure` 包含两个子图：
- **左图**：证据峰的 d 值 + 所属 bank（横条图）。长横条表示该 d 值在多个 bank 里都表现出偏差，信号更可信。
- **右图**：每个候选 k 的指标距离（柱状图）。第一个柱条（最短）对应距离最小的候选——工具自动选它为 `chosen_k`。

**示例**：MnTe 案例的证据峰是 d=6.650/1.970/2.219 Å ——这是 `001`、`111`、`003` 三条反射，恰好是母空间群 P6₃/mmc 系统消光的反射（6₃ 螺旋轴使 00l 在 l 为奇时消光）。它们出现在低温、且被 k=000 指标化，说明磁性没有打破晶胞平移对称性。

---

## 5. 每张图的含义与读法

每个阶段至少返回一张 PNG 图，所有图都用 **matplotlib Agg 后端**渲染（无 GUI 依赖），存放在 `<mission>/figures/` 下，返回给智能体的是**绝对路径**。

### 5.1 input_overview.png（输入总览）

**内容**：五个 panel（每个 bank 一个），每个 panel 里蓝色=高温谱，红色=低温谱（TOF 中子衍射，横轴 d-spacing 或 TOF，纵轴强度）。

**怎么读**：
- 信噪比是否足够？
- 高低温之间的差异是否明显的？（差异越大，磁信号越强）
- 各 bank 覆盖的 d 范围是否互补？

### 5.2 high_temperature_nuclear_fit.png（高温核拟合）

**内容**：五个 panel，每个是一个 bank 的 Rietveld 拟合图。蓝点=实测，红线=计算，绿线=背景，黑线=差值（obs-calc），底部竖线=布拉格峰位。

**怎么读**：
- **红色线和蓝点是否重合？** 不重合→核结构没修好，不能往下走。
- **差值谱（黑线）有没有系统性峰形？** 有→还有未被核模型吸收的贡献，应继续修核结构（决策表第一条）。
- **低 TOF/大 d 区域有没有拟合？** 大 d 区域峰密集，容易漏峰（特别是 TOF 中子数据的前区）。

### 5.3 low_temperature_nuclear_fit.png（低温纯核对照）

**内容**：同上，但数据是低温的。同一个核模型直接放到低温数据上。

**怎么读**：
- 与高温对比：**reduced χ² 是否显著变大？**（本例 11.99 → 283.95）——是→磁散射存在。不是→可能没有磁有序，或磁信号太弱。
- 差值谱里有没有**系统的峰形残余**？这些残余就是"磁散射贡献"的候选位置。

### 5.4 propagation_vector.png（传播矢证据图）

**左图**：横轴=证据峰的 d 值，纵轴=相对强度偏差。蓝色横条表示该 d 值被多个 bank 同时看到。

**右图**：横轴=候选 k 的标签（例如 "GAMMA [0,0,0]"），纵轴=指标距离（越小越好，灰色虚线=阈值）。

**怎么读**：左图看"有多少个可信的证据峰"——峰越多、跨 bank 越多，信号越可靠。右图看"哪个 k 离这些证据峰的 d 最近"——距离远→候选 k 不会是这个，应如实说明证据不足。

### 5.5 magnetic_candidates.png（候选概览图）

**内容**：横向条形图，每条一个候选，标记 BNS 编号和磁空间群符号。条形颜色按"独立磁矩分量数"着色（1=深蓝，2 及以上=浅色）。

**怎么读**：
- 默认范围（14 个）比全量（131 个）少很多，但不丢参考等价的群。如果图中只有 2-3 个收敛且 χ² 都很高→应考虑用 `all_subgroups` 复核。
- 分量数 1 的候选（深蓝）意味着磁矩方向完全被锁定——这类候选在"数据无法区分"时会因简约性被优先选择。

### 5.6 model_comparison.png（模型对比图）

**内容**：三个子图：ΔBIC（与最优模型的差）、拟合磁矩大小、reduced χ²，各候选按 BIC 排序。最佳候选在最左边。

**怎么读**：
- 左侧有明显的"台阶"（1-2 个模型遥遥领先）→数据能区分。左侧几根柱子高度差不多→数据不能区分，应看 `tie_break`。
- 磁矩条形图如果全都很接近→说明磁矩大小是稳定的（数据约束较好）。
- reduced χ² 越靠左越低越好——如果全都接近甚至 >10，说明**全体拟合**有问题（决策表：加大本底项重跑整个比较）。

### 5.7 best_magnetic_fit.png（最优模型拟合图）

**内容**：同 Rietveld 拟合，但这次核相+磁相一起拟合。底部竖线有两组（核/磁各一组，磁的通常和核位置相同——因为 k=000 时磁胞=晶胞）。

**附录：deepened_fit.png（深修后拟合图）** 与 **selected_model_deepening.png（深修前后对比）** 的内容说明参考第 7 节。

---

## 6. 候选枚举与磁空间群约束

### 6.1 Bilbao 查询的技术路径

1. 母空间群取自 `initial.cif`。
2. k 来自传播矢搜索阶段（或用户可以显式传 `k_vectors`）。
3. `enumerate_native` 通过 `GenericSymmetryCache` 劫持 `GSASIIpath.postURL`——所有发给 Bilbao 的载荷都经过字段白名单验证，确保只传对称操作与 k 分量（不传原子/结构数据），并把响应缓存到 `generic_symmetry_cache/` 下（SHA-256 为键），后续重复查询全免费。
4. 候选生成使用 `magnetic_structure_candidates.search_magnetic_structure_candidates(...)`，每个候选用 PSI/PARENT 展开成 GSAS-II 的内部形式（晶胞变换矩阵、原点偏移、独立磁矩分量等）。

### 6.2 磁空间群约束的机制

每个候选的磁相在构建时，磁空间群自身的对称操作已经把允许的磁矩方向**编码进相里**——例如 `Cmcm` (63.457) 要求 Mn 位点的磁矩只能在对角线方向 a+b（即 `parent_basis_constraint_relations = [[1,1,0]]`，等价于 Mx=My、Mz=0）。精修时这些约束**不会被放开**——放开约束去修会得到一个"修得更好"但违反磁空间群的结果，不合法。

### 6.3 候选数量与范围

- 母群 194、k=000、不预选磁性位点 → **131 个候选（64 个磁空间群）**
- 开启 `exact_wyckoff_orbit` → **14 个**（仅限磁性离子留在母相 Wyckoff 轨道的设置）
- `preserve_parent_wyckoff_type`（较弱版）→ 114 个
- `maximal_only` → 2 个——**注意：会丢掉答案**（63.457 不在其中）

默认使用"磁位点收窄范围"（14 个），耗时约为全量的十分之一，且其中包含参考同群。

---

## 7. 精修策略、平局裁决与交接深修

### 7.1 候选内固定阶梯（公平比较的前提）

每个候选的磁 Rietveld 精修走**完全相同的两步**（`native.py`）：

1. **先定磁矩分量与公共标度**：核相固定（`Cell=False, Atoms=" "`），磁相只放开磁矩分量（`atom[mct+1]="M"`，其中 `mct` 是磁矩类型指针，磁矩分量位于 `AtomPtrs[0]+4 ~ +6`）→ fit #1。
2. **再放开核相 XU/cell + 磁相 cell + 磁矩幅值 + 峰型与 ADP**：`atom[mct+1]="MXU"` → `_release_adp_boundaries` → `profile_flags` → fit #2。

这个阶梯对**每一个**候选一模一样——因为 BIC 比较只有在"大家都被同等对待"时才有意义。如果给不同候选手工调参、调出不同的参数组合，排名反映的是调参技巧而非模型好坏。

### 7.2 平局裁决（最后一公里）

当最优模型的相对 reduced χ² 差距 ≤0.1%（`TIE_RELATIVE_CHISQ = 1e-3`）时，判定统计不可区分——这远低于 Rietveld 系统误差水平，按噪声裁决等于碰运气。

引擎在平局集合里取**自由磁矩分量更少**者，并把裁决写入 `scientific_status.tie_break`：
```json
{
  "preferred": "subgroup_006", "over": "subgroup_005",
  "rule": "fewest free moment components among statistically tied models",
  "tied_models": ["subgroup_005","subgroup_006"],
  "relative_chisq_gap": 0.000122,
  "note": "The tied models fit within the Rietveld systematic-error level; the more constrained one is exported and the tie is reported."
}
```
这条规则只涉及候选本身的约束数据（自由分量数），不含任何材料先验。

### 7.3 交接给 gsas2-refine（深修）

比较结束后，选定模型被导出为 `selected_model.gpx`（完整的 GSAS-II 项目——核相+磁相+磁空间群约束）。为什么不再自己深修？

旧版阶梯的失败证实了两点：
1. "一次性放开所有参数"会导致病态 Jacobian → `Nonfinite calculated intensity`（与"一次只放开一组"铁律矛盾）。
2. 自创启发式（磁矩 +5% 扰动重启）不是标准实践且不可靠。

因此改为交给 gsas2-refine——它的成熟决策链（18 条专家规则 + 回退能力）正好适合"对一个模型下重手"。交接后智能体的操作：

1. 调 `RefineBestMagneticModel` → 拿到 `handoff_gpx` 和 `handoff_note`
2. 调 gsas2-refine 的 `Refine`：`{"gpx_file":"…","refine_config":{"refine_moments":[{"phase":"…","atom":"Mn1","components":"sym"}]}}`
3. 逐步放开（先磁矩→再晶胞→再原子/峰型），每步看图确认
4. 完成后调 `ExportMagneticStructure(gpx_path="<new_gpx_path>")` 导出最终 MCIF

gsas2-refine 的磁矩精修还有一个关键的**收尾精修（bootstrap）**：主精修之后，额外跑一轮"只放开磁矩+标度/背景"的极小参数集精修——这解决了主精修在病态参数集下"零位移却报成功"的问题，并把磁矩拉回真正的极小点。

---

## 8. 进度通道：为什么等待时也能看到进展

### 8.1 问题

MCP 轮询基类 `ProcessPollingMcpToolBase` 的 `running` 信封默认只有 `task_id/mission_name/state`——精修跑 10 分钟，用户每次轮询只看到"还在跑"，完全不知道当前是第几步、哪个候选、进展如何。

### 8.2 解法

两层修改：

**引擎侧（mission / native）**：每个阶段和每个候选结束时，把一行人类可读的步骤写进 `<mission>/analysis/progress.log`。例如：

```
[14:21:29] 磁精修进度 3/14：subgroup_002（164.89）ok，当前最优 subgroup_001 reduced χ²=196.55
[14:22:21] 磁精修进度 4/14：subgroup_003（63.464）failed，当前最优 subgroup_001 reduced χ²=196.55
[14:24:43] 磁精修进度 6/14：subgroup_005（63.462）ok，当前最优 subgroup_005 reduced χ²=45.08
```

**框架侧（mcp_task_runner.py）**：给 `ProcessPollingMcpToolBase` 加了一个可选的 `progress_file` 约定——声明它的工具，每次 poll 时 runner 会读该文件的最新 6 行，放进 `snapshot["progress"]`（含 `lines`、`age_seconds`、`path`）。磁分析服务与 gsas2-refine 服务都声明了这个约定。

**Skill 侧**：要求智能体**每次轮询都把 `progress.lines` 最新一行转述给用户**，而且要说明"现在在做什么、下一步是什么、大概还要多久"，绝不能让用户面对长时间静默。

这样，一次精修阶段的轮询结果就变成：

```json
{
  "task_id": "refine", "mission_name": "magnetic-MnTe-001", "state": "running",
  "progress": {
    "lines": ["[14:24:43] 磁精修进度 6/14：subgroup_005（63.462）ok，当前最优 subgroup_005 reduced χ²=45.08", ...],
    "age_seconds": 18.5,
    "path": ".../analysis/progress.log"
  }
}
```

---

## 9. 关键设计决策与理由

| 决策 | 理由 |
|---|---|
| 6 个工具而非一个大接口 | 与 house 风格一致；每阶段返回自己的图，智能体能中道判断（核拟合不好→停止，候选找不到→换范围等） |
| 候选比较采用固定阶梯而非逐步决策 | BIC 比较要求每个候选同等对待；给不同候选不同参数组→排名的不是模型而是调参技巧 |
| 逾时不抛异常而是优雅降级 | 已完成候选的有效性不依赖"是否跑完"；只丢掉它们是对用户工作的浪费 |
| 候选范围默认磁位点收窄到 14 | PPT 里科学家就是先预选 2a 位；实测 14 个包含参考同群而 131 个同样会被平局裁决选对但多花约 10 倍时间 |
| 深修从自研阶梯改为 gsas2-refine 逐步精修 | 自研阶梯违反"一次只放一组"的规则导致 nonfinite；gsas2-refine 的成熟决策链能回退，正好对接 |
| 磁矩精修的收尾自举（bootstrap） | GSAS-II 在主精修参数集过大时会病态→MINPACK 零位移但报成功→加一轮只放磁矩+标度/背景的重跑拉回真极小点 |
| 失败三分类而非通通"失败" | 求解器失败 / 越界 / 几何不合法属于不同根因；全摞在一起无法判断"哪些模型其实没被公平尝试" |
| 从 `applied`（是否新打开标志）改为 `refine_moments`（调用方是否请求） | 交接场景下磁矩标志早已打开→传统判据会让整条深修链静默失效 |
| `progress_file` 框架级约定而非只在磁分析里特制 | 一笔改动让两服务的所有长耗时工具都具备了实时进度，不局限于本次开发 |

---

## 10. 文件索引

| 你要找的 | 在哪里 |
|---|---|
| MCP 工具的签名与文档 | `tools/magnetic_structure_mcp.py`（6 个函数 + `build_server()`） |
| Skill 全文（流程 + 决策表 + 13 条专家知识） | `agent_skills/magnetic-structure-analysis/SKILL.md` |
| 阶段编排与会话适配 | `tools/GSAS-II-master/magnetic_analysis/mission.py` |
| 科学引擎（精修/枚举/平局裁决/失败分类/墙钟降级） | `tools/GSAS-II-master/magnetic_analysis/native.py` |
| 配图（7 种图） | `tools/GSAS-II-master/magnetic_analysis/present.py` |
| 引擎级 API 说明 | `tools/GSAS-II-master/magnetic_analysis/README.md` |
| k-vector 搜索脚本 | `tools/GSAS-II-master/k_vector_candidate_search/k_vector_candidate_search.py` |
| 磁结构候选脚本 | `tools/GSAS-II-master/magnetic_structure_candidates/magnetic_structure_candidates.py` |
| gsas2-refine 磁能力扩展 | `tools/GSAS-II-master/GSASII/0neutron_gsas_refine.py` |
| 轮询基类的进度通道 | `src/core/tools/mcp_task_runner.py`（`progress_file` 参数） |
| 契约测试（25 项） | `tests/test_magnetic_mcp.py` |
| 引擎测试 | `tools/GSAS-II-master/magnetic_analysis/test_engine.py` / `test_native.py` |
| Benchmark（判分器+协议+参考） | `BenchMark/MagneticStructureAnalysis/` |