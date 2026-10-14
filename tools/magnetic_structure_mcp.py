"""Mission-scoped magnetic structure analysis tools for RongZai.

Follows the same shape as the other refinement servers: one MCP process, one
polling runner per tool, and business functions that only do the work. Progress
lines are printed by the mission layer and stream to the frontend; every stage
returns absolute paths, including PNG figures, so the analysis is 图文并茂 like
the nuclear, PDF and small-angle capabilities.
"""

from __future__ import annotations

import os
import re
import sys
from pathlib import Path
from typing import Any

from dotenv import load_dotenv
from mcp.server.fastmcp import FastMCP

ROOT = Path(__file__).resolve().parents[1]
for path in (str(ROOT), str(ROOT / "tools" / "GSAS-II-master")):
    if path not in sys.path:
        sys.path.insert(0, path)

# The service is launched by start_tools.sh, which does not export the repository's
# .env; without this the mission layer cannot resolve WORKDIR and every call fails
# with "WORKDIR is required for magnetic analysis".
load_dotenv(ROOT / ".env")

from src.core.tools import ProcessPollingMcpToolBase  # noqa: E402


def _limit_threads() -> int:
    """Pin BLAS/OpenMP threads in the child: these normal matrices are tiny."""
    threads = int(os.getenv("RONGZAI_MAGNETIC_THREADS", "1"))
    if not 1 <= threads <= 32:
        raise ValueError("RONGZAI_MAGNETIC_THREADS must be between 1 and 32")
    for variable in ("OPENBLAS_NUM_THREADS", "MKL_NUM_THREADS", "OMP_NUM_THREADS"):
        os.environ[variable] = str(threads)
    return threads


def _elements(value: Any) -> list[str] | None:
    if value is None:
        return None
    if isinstance(value, str):
        return [item.strip() for item in value.replace(";", ",").split(",") if item.strip()]
    return [str(item) for item in value]


def initialize_magnetic_mission(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    initial_cif: str,
    high_temperature: str,
    low_temperature: str,
    instruments: str,
    magnetic_elements: Any = None,
    cycles: int = 24,
    background_coeffs: int = 6,
    profile_comparison: bool = False,
) -> dict[str, Any]:
    '''
    磁结构分析的起点：校验输入、精修顺磁相（高温）核结构、建立低温核基线并出图。

    按谱仪科学家的流程，第一步永远是把顺磁温度下的核结构修好——它是后面所有磁
    模型的基准。本工具同时产出输入数据总览图与高温/低温核拟合图。

    这是全流程里最慢的一步（核结构 Rietveld 精修 + 两侧拟合图），通常需要几分钟。
    它持续把进度行追加到 `<mission>/analysis/progress.log`；等待期间可以读该文件，
    把真实进展（如"步骤 4/6: 精修顺磁相核结构作为基线"）讲给用户，而不是只说"仍在运行"。

    Args:
        user_id: 用户ID，字符串，标识当前用户身份
        session_id: 线程ID，字符串，标识当前对话session
        mission_name: 大任务名称。同一次磁结构分析的所有步骤必须使用同一个
                  mission_name；工具用它和 user_id/session_id 定位该任务的文件目录
        task_id: mission 内的子任务ID，必须指定
        initial_cif: 初始核结构 CIF 文件绝对路径（低温相的核结构，不是磁结构）
        high_temperature: 高温（顺磁相）衍射数据，可以是目录、通配符、逗号分隔的多个
                  路径，或文件列表
        low_temperature: 低温（待分析）衍射数据，同上
        instruments: 仪器参数文件（instprm），同上；按文件名标识与高低温数据配对
        magnetic_elements: 候选磁性元素列表或逗号分隔字符串；留空则从 CIF 化学组成
                   自动推断（只按元素周期表判断，不使用材料专属先验）
        cycles: 每轮 GSAS-II 精修的迭代周期数，默认 24
        background_coeffs: 背景项数，默认 6

    Returns:
        dict:
            "status": "completed" 或 "failure"，
            "task_id": 当前子任务ID，
            "mission": 任务目录绝对路径，
            "magnetic_elements": 本次认定的候选磁性元素，
            "banks": 配对成功的 bank 标识列表，
            "unpaired_banks": 未能三方配对的数据文件（不会被使用），
            "input_dir": 整理后的输入目录，
            "high_gpx_path"/"low_gpx_path": 高温/低温核结构精修工程文件，
            "baseline_report_path": 核基线报告 JSON，
            "overview_figure": 输入数据总览图 PNG 路径（每个 bank 高温/低温叠放），
            "high_fit_figure": 高温核拟合图 PNG 路径。蓝线实验、红线计算、绿线背景、
                        黑线为 obs-calc 差值，底部竖线是各相布拉格峰位，
                        虚线灰色为对照模型，
            "low_fit_figure": 低温纯核拟合图 PNG 路径（同上配色），
            "high_reduced_chisq"/"low_nuclear_reduced_chisq": 高/低温核模型 reduced χ²，
            "summary": 中文小结，
            "execution_time_sec": 本步骤耗时（秒），
            "error": 出错信息（仅失败时存在）
    '''
    _limit_threads()
    from magnetic_analysis.mission import initialize

    return initialize(user_id, session_id, mission_name, task_id, initial_cif,
                      high_temperature, low_temperature, instruments,
                      magnetic_elements=_elements(magnetic_elements),
                      cycles=cycles, background_coeffs=background_coeffs,
                      profile_comparison=profile_comparison)


def search_propagation_vector(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    k_vectors: Any = None,
    max_index: int = 8,
    indexing_tolerance: float = 0.01,
    search_option: int = 0,
) -> dict[str, Any]:
    '''
    传播矢（k）分析：找出母相晶胞无法解释的峰，并用 GSAS-II k-vector search 求候选 k。

    k=000 表示磁胞与晶胞同大——此时不会有额外峰出现，磁散射只改变已有峰的强度，
    所以这一步的输入是"强度对不上的峰"，而不是"多出来的峰"。

    Args:
        user_id: 用户ID
        session_id: 线程ID
        mission_name: 大任务名称，必须与初始化时一致
        task_id: 子任务ID
        k_vectors: 已知的候选传播矢列表（一般留空；留空时完全由实验证据搜索决定）
        max_index: 母相整数指标搜索上限（hkl 各自 0..max_index），默认 8
        indexing_tolerance: 母相指标化容差（相对 d 偏差），默认 0.01
        search_option: 传给 GSAS-II k-vector search 的搜索模式；0=高对称点，1=高对称点加路径，
                   2=全网格（需要编译扩展 kvec_general，缺失时会报错而不是静默降级）

    Returns:
        dict:
            "status": "completed" 或 "failure"，
            "task_id"、"report_path": 搜索证据报告 JSON，
            "k_candidates": 候选传播矢列表，每项含 k、指标距离与 d 偏差，
            "chosen_k": 当前选用的 k，
            "evidence_peaks": 参与搜索的峰（bank、TOF、d、相对偏差），
            "propagation_figure": 证据图 PNG 路径（左：母相晶胞解释不了的峰；右：各候选 k 的指标距离），
            "summary": 中文小结，
            "execution_time_sec"、"error"
    '''
    _limit_threads()
    from magnetic_analysis.mission import propagation

    return propagation(user_id, session_id, mission_name, task_id,
                       k_vectors=k_vectors, max_index=max_index,
                       indexing_tolerance=indexing_tolerance,
                       search_option=search_option)


def enumerate_magnetic_candidates(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    magnetic_elements: Any = None,
    k_vectors: Any = None,
    max_candidates: int = 256,
    candidate_scope: str = "magnetic_site",
) -> dict[str, Any]:
    '''
    磁空间群候选枚举：按实验得到的 k 向 Bilbao k-SUBGROUPSMAG 查询磁子群，
    并为每个候选构建带磁矩约束的 GSAS-II 磁相。

    这一步就是把谱仪科学家手工在 Bilbao 上做的操作自动化：输入母空间群与 k，
    预先指定磁性位点，得到候选磁空间群（含磁胞变换、原点移动与每个位点的
    磁矩约束，例如 Mx=My、Mz=0）。候选必须整体保留，不能只取磁群名字。

    `candidate_scope` 决定查询条件，它同时决定物理范围与总耗时：
    - `"magnetic_site"`（默认）：只保留**让磁性离子留在母相 Wyckoff 轨道上**的设置，
      等价于手工流程里"预选磁性位点（如 2a）"这一步。参考案例实测 **131 → 14 个候选**，
      磁空间群 64 → 14；精修总耗时随之从约 100 分钟降到约 12 分钟。
    - `"all_subgroups"`：向 Bilbao 要全量 k-子群（131 个）。当磁有序可能涉及非磁性位点、
      或磁性离子可能离开原轨道时才用。若精确轨道筛选在本环境不可用（缺 ase/spglib），
      工具会自动回退到这个范围并在结果里写明。

    Args:
        user_id: 用户ID
        session_id: 线程ID
        mission_name: 大任务名称
        task_id: 子任务ID
        magnetic_elements: 候选磁性元素；留空则用初始化阶段认定的元素
        k_vectors: 传播矢；留空则用传播矢搜索阶段选定的 k
        max_candidates: 候选数量上限，默认 256
        candidate_scope: "magnetic_site"（默认，预选磁性位点；参考案例 131→14）或 "all_subgroups"（全量子群）

    Returns:
        dict:
            "status"、"task_id"、"report_path"：候选报告 JSON，
            "candidate_count": 候选总数，
            "candidates": 候选表，每项含 id、magnetic_space_group、bns_number、
                       free_moment_components（独立磁矩参数个数）、
                       allowed_moment_direction_parent_basis（母相基下的允许磁矩方向）、
                       magnetic_site_symmetry、phase_file，
            "candidates_figure": 候选概览图 PNG 路径，
            "summary"、"execution_time_sec"、"error"
    '''
    _limit_threads()
    from magnetic_analysis.mission import candidates

    return candidates(user_id, session_id, mission_name, task_id,
                      magnetic_elements=_elements(magnetic_elements),
                      k_vectors=k_vectors, max_candidates=max_candidates,
                      candidate_scope=candidate_scope)


def refine_magnetic_candidates(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    screen_cycles: int = 6,
    final_cycles: int = 24,
    shortlist: int | None = None,
    screen_time_limit_seconds: float = 900.0,
    final_time_limit_seconds: float = 5400.0,
    native_solver: str = "analytic Jacobian",
    models: Any = None,
    refine_profile: bool = True,
    refine_cell: bool = True,
    background_coeffs: int = 6,
) -> dict[str, Any]:
    '''
    磁 Rietveld 精修与模型比较：保留磁空间群约束，对所有候选做带证据的竞争。

    两种策略由 `shortlist` 选择：
    - 默认（整数，如 12）：先用很小的迭代预算对**全部**候选预筛排序，再对短名单
      （最优若干 + 所有 ΔBIC<6 的近简并磁群）做全量精修。更快，但排序依赖预筛收敛。
    - 传 `null`：跳过预筛，对**全部**候选做一次全量精修。更慢，但排序不依赖粗筛。
      若预筛最优模型的 reduced χ² 仍差于纯核对照（说明预筛没收敛）或候选集不大，用这个。

    精修始终保留候选磁空间群自身的磁矩约束（例如 Cmcm 要求 Mx=My、Mz=0）——
    这是谱仪科学家特别强调的一点，磁矩方向不能被自由放开。墙钟上限到达时不会丢弃
    已完成的工作，而是按 BIC 选出已评出的最优模型并标注未完成。

    Args:
        user_id: 用户ID
        session_id: 线程ID
        mission_name: 大任务名称
        task_id: 子任务ID
        screen_cycles: 预筛每轮迭代周期数，默认 6
        final_cycles: 全量精修每轮迭代周期数，默认 24
        shortlist: 进入全量精修的候选个数；默认 null = 单次全量精修全部候选（可靠优先）。
                   候选集很大且预筛确认已收敛时才传整数走两段式
        screen_time_limit_seconds: 预筛阶段的墙钟上限（秒），默认 900
        final_time_limit_seconds: 全量精修阶段的墙钟上限（秒），默认 5400
        native_solver: GSAS-II 求解器，可选 analytic Jacobian / analytic Hessian / Hessian SVD
        models: 直接指定要精修的候选 id 列表；留空则按 shortlist 策略
        refine_profile: 全量精修时是否放开峰型参数，默认 True
        refine_cell: 精修时是否放开晶胞，默认 True
        background_coeffs: 本底项数，默认 6。**若所有候选的 reduced χ² 都偏高（例如 >10），
                   说明是全体拟合的系统性问题：加大本底项数（如 6→9）或调 refine_profile/
                   refine_cell，然后重跑整个比较（对所有候选一视同仁），不要在深修里只给
                   单个模型改参数

    Returns:
        dict:
            "status"、"task_id"、"report_path"：精修报告 JSON，
            "best_model": 最优候选 id，
            "best_mcif_path"/"best_gpx_path": 最优磁结构的 MCIF 与工程文件，
            "complete": 本次精修范围内的比较是否跑完，
            "time_bound_reached": 是否因墙钟上限中断，
            "models_evaluated"/"models_total": 已精修/本次范围内候选数，
            "shortlist"/"strategy": 本次采用的策略与短名单，
            "ranking": 排名表（id、磁空间群、BNS、reduced χ²、BIC、磁矩大小、MCIF），
            "comparison_figure": 模型对比图 PNG 路径（ΔBIC、磁矩大小、reduced χ²），
            "fit_figure": 最优模型的多 bank 拟合图 PNG 路径（实验/计算/背景/差值/布拉格峰位），
            "scientific_status": 完整度、可辨识性与共线性诊断，
            "summary"、"execution_time_sec"、"error"
    '''
    _limit_threads()
    from magnetic_analysis.mission import refine

    return refine(user_id, session_id, mission_name, task_id,
                  screen_cycles=screen_cycles, final_cycles=final_cycles,
                  shortlist=shortlist, refine_cell=refine_cell,
                  background_coeffs=background_coeffs,
                  screen_time_limit_seconds=screen_time_limit_seconds,
                  final_time_limit_seconds=final_time_limit_seconds,
                  native_solver=native_solver, models=models,
                  refine_profile=refine_profile)


def refine_best_magnetic_model(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    model: Any = None,
    cycles: int = 24,
    deep_cycles: int | None = None,
    deep_rounds: int = 3,
    time_limit_seconds: float = 1800.0,
    native_solver: str = "analytic Jacobian",
) -> dict[str, Any]:
    '''
    对**已选定**的磁模型做深修：放开晶胞、原子位移参数与峰型，多轮迭代直到收敛。

    为什么要单独一步：候选比较阶段必须让**每个**候选受到完全相同对待，BIC 排序才公平，
    所以比较用的阶梯是固定的；模型选定之后才可以对它下更重的功夫，深修就是这一步。

    使用时机：`RefineMagneticCandidates` 给出排名后，若最优模型 reduced χ² 仍明显大于 1
    （还有未被吸收的系统性偏差），或用户要求把这个磁结构修到最好，就调用它。可以传 `model`
    指定候选 id，默认深修比较阶段选出的最优者。

    诚实标注：深修指标属**比较后处理**，不能与其他候选直接比较（报告里带 `deepening.note`
    与 `deepened_models`）；若深修没有改善拟合，工具会**回退**到比较阶段的磁结构文件并报
    `improved=false`。

    Args:
        user_id / session_id / mission_name / task_id: 与其他工具相同，mission_name 必须一致
        model: 要深修的候选 id；留空则用比较阶段的最优模型
        cycles: 常规迭代周期基数，默认 24
        deep_cycles: 深修每轮周期数，默认 max(2×cycles, 24)
        deep_rounds: 深修策略轮数，默认 4（依次：放开晶胞/ADP/峰型 → 增加本底项 →
                   换求解器 analytic Hessian → 扰动磁矩重启）
        time_limit_seconds: 本步墙钟上限（秒），默认 1800
        native_solver: GSAS-II 求解器，默认 analytic Jacobian

    Returns:
        dict: "status"、"model"、"improved"、"before_reduced_chisq"/"after_reduced_chisq"、
            "rounds"、"mcif_path"、"comparison_report_path"、"deepening_figure"、"fit_figure"、
            "scientific_status"、"summary"、"execution_time_sec"、"error"
    '''
    _limit_threads()
    from magnetic_analysis.mission import deepen

    return deepen(user_id, session_id, mission_name, task_id, model=model, cycles=cycles,
                  deep_cycles=deep_cycles, deep_rounds=deep_rounds,
                  time_limit_seconds=time_limit_seconds, native_solver=native_solver)


def export_magnetic_structure(
    user_id: str,
    session_id: str,
    mission_name: str,
    task_id: str,
    destination: str | None = None,
    gpx_path: str | None = None,
) -> dict[str, Any]:
    '''
    导出最终磁结构 MCIF：把精修选中的最优模型写到指定路径。

    Args:
        user_id: 用户ID
        session_id: 线程ID
        mission_name: 大任务名称
        task_id: 子任务ID
        destination: 目标 MCIF 绝对路径；留空则写到 mission 的 submission/final.mcif

    Returns:
        dict:
            "status"、"task_id"，
            "mcif_path": 最终 MCIF 路径，
            "source_mcif_path": 引擎导出的原始 MCIF 路径，
            "best_model": 选中的候选 id，
            "complete"/"scope": 精修阶段的范围信息，
            "summary"、"execution_time_sec"、"error"
    '''
    _limit_threads()
    from magnetic_analysis.mission import export

    return export(user_id, session_id, mission_name, task_id, destination=destination,
                  gpx_path=(str(gpx_path) if gpx_path else None))


_SAFE_IDENTIFIER = re.compile(r"^[A-Za-z0-9._:\-]{1,128}$")


def _progress_log_path(tool_kwargs: dict):
    """Where a mission publishes its step lines, for the polling runner to relay.

    A long stage must not look silent: the runner attaches the newest lines of this file to
    every poll, so the model can tell the user what is happening instead of only that
    something is still running.
    """
    user = tool_kwargs.get("user_id")
    session = tool_kwargs.get("session_id")
    mission = tool_kwargs.get("mission_name")
    workdir = os.getenv("WORKDIR")
    if not workdir or not all(isinstance(v, str) and _SAFE_IDENTIFIER.match(v)
                              for v in (user, session, mission)):
        return None
    return Path(workdir) / user / session / mission / "analysis" / "progress.log"


def build_server() -> FastMCP:
    """Build the MCP server with one polling runner per tool."""
    mcp = FastMCP(
        name="MagneticStructureTools",
        instructions=(
            "Magnetic structure analysis from neutron powder diffraction: nuclear baselines, "
            "propagation-vector search, Bilbao magnetic subgroups, symmetry-constrained magnetic "
            "Rietveld comparison and MCIF export. "
            "Mission/task rule: mission_name identifies one high-level mission and should stay the "
            "same across every call for one magnetic structure determination; task_id identifies one "
            "step in that mission. To continue polling the same unfinished task, pass the same "
            "mission_name and task_id."
        ),
        port=int(os.getenv("MCP_MAGNETIC_STRUCTURE_PORT", "42511")),
    )
    runners = (
        (initialize_magnetic_mission, "InitializeMagneticMission", 60.0),
        (search_propagation_vector, "SearchPropagationVector", 60.0),
        (enumerate_magnetic_candidates, "EnumerateMagneticCandidates", 60.0),
        (refine_magnetic_candidates, "RefineMagneticCandidates", 60.0),
        (refine_best_magnetic_model, "RefineBestMagneticModel", 60.0),
        (export_magnetic_structure, "ExportMagneticStructure", 60.0),
    )
    for target, name, timeout in runners:
        runner = ProcessPollingMcpToolBase(target_func=target, timeout_sec=timeout,
                                           poll_interval=1.0,
                                           progress_file=_progress_log_path)
        runner.register_mcp_tool(mcp, name=name)
    return mcp


if __name__ == '__main__':
    build_server().run(transport="sse")
