import logging
import os
import sys
import traceback
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from mcp.server.fastmcp import FastMCP

try:
    from src.core.tools import ProcessPollingMcpToolBase
except ImportError:
    repo_root = Path(__file__).resolve().parents[3]
    if str(repo_root) not in sys.path:
        sys.path.insert(0, str(repo_root))
    from src.core.tools import ProcessPollingMcpToolBase


# Ensure this repository root is importable so that refineTool can be imported.
REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import refineTool  # noqa: E402


TMP_DIR = REPO_ROOT / "tools" / "tmp"
TMP_DIR.mkdir(parents=True, exist_ok=True)


mcp = FastMCP(
    name="RefineToolsZRietveld",
    instructions=(
        "A tool for Z-Rietveld project generation and refinement. "
        "Supports generating .zrietveldn and manual refinement with cfg."
    ),
    port=42511,
)


def _setup_logging(output_dir: Path) -> logging.Logger:
    output_dir.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("zrietveld_mcp")
    logger.setLevel(logging.INFO)

    # Avoid duplicate handlers if module is reloaded.
    if logger.handlers:
        return logger

    formatter = logging.Formatter("%(asctime)s - %(levelname)s - %(message)s")
    fh = logging.FileHandler(output_dir / "zrietveld_refine.log")
    fh.setFormatter(formatter)
    sh = logging.StreamHandler()
    sh.setFormatter(formatter)
    logger.addHandler(fh)
    logger.addHandler(sh)
    return logger


def _ensure_env(zrt_data_dir: Optional[str], zrt_lib_dir: Optional[str]) -> None:
    # Allow caller override; otherwise use conventional local paths.
    if zrt_data_dir:
        os.environ["ZRT_DATA_DIR"] = zrt_data_dir
    else:
        os.environ.setdefault(
            "ZRT_DATA_DIR",
            str(REPO_ROOT / "Z-Rietveld" / "Z-Rietveld" / "utility_atomic_data"),
        )

    if zrt_lib_dir:
        os.environ["ZRT_LIB_DIR"] = zrt_lib_dir
    else:
        os.environ.setdefault("ZRT_LIB_DIR", str(REPO_ROOT / "Z-Rietveld" / "lib"))


def _normalize_cfg(cfg: Optional[Dict[str, object]]) -> Dict[str, str]:
    if not cfg:
        return {}

    out: Dict[str, str] = {}
    for k, v in cfg.items():
        if isinstance(v, bool):
            out[k] = "true" if v else "false"
        else:
            out[k] = str(v)
    return out


def _run_manual_refine(zrtn_path: str, cfg: Dict[str, str], logger: logging.Logger) -> Dict[str, object]:
    logger.info("Start manual refine: %s", zrtn_path)
    logger.info("cfg keys: %s", sorted(cfg.keys()))
    refineTool.refine(zrtn_path, cfg)
    logger.info("Manual refine done: %s", zrtn_path)
    return {
        "ok": True,
        "zrietveldn": zrtn_path,
        "cfg": cfg,
    }


def zrt_generate_project(
    neutron_data: str,
    neutron_inst: str,
    cif_files: List[str],
    user_id: str = "default",
    mission_name: str = "default",
    task_id: Optional[str] = None,
    zrt_data_dir: Optional[str] = None,
    zrt_lib_dir: Optional[str] = None,
) -> Dict[str, object]:
    """
    生成Z-Rietveld .zrietveldn项目文件，为后续精修做准备。
    
    此工具从中子粉末衍射数据、仪器参数文件和晶体结构文件生成完整的Z-Rietveld项目。
    生成的项目文件可用于后续的手动精修或自动精修。
    
    Args:
        neutron_data: 中子粉末衍射数据文件的绝对路径。
                      支持格式：.histogramIgor（Z-Rietveld专有的中子衍射直方图格式）。
                      文件必须存在，且包含有效的衍射强度数据。
        
        neutron_inst: Z-Rietveld仪器参数文件的绝对路径。
                      文件格式为 .zDiffractometer（Z-Rietveld专有格式）。
                      包含仪器几何、探测器参数等信息。
        
        cif_files: 晶体结构文件列表，每项为绝对路径的字符串。
                   支持标准CIF格式(.cif)，包含晶胞参数、空间群、原子位置等。
                   可传入多个CIF文件用于多相精修。
        
        user_id: 用户标识符，字符串，用于文件管理和日志追踪。
                 默认值为 "default"。
        
        mission_name: 任务名称，字符串，标识当前精修任务。
                      用于组织生成的项目文件和日志。
                      默认值为 "default"。

        task_id: 任务唯一标识（可选）。
             在开启一次工具调用时，这里写None或不传，工具会自动生成一个唯一ID；如果是对已有任务的结果查询，则必须传入之前的task_id
        
        zrt_data_dir: Z-Rietveld原子数据目录的自定义路径（可选）。
                      若不提供，使用默认位置：<REPO_ROOT>/Z-Rietveld/Z-Rietveld/utility_atomic_data
        
        zrt_lib_dir: Z-Rietveld库目录的自定义路径（可选）。
                     若不提供，使用默认位置：<REPO_ROOT>/Z-Rietveld/lib
    
    Returns:
        字典，包含以下键值：
        - 'ok': bool，表示操作成功（True）或失败（False）。
        - 'zrietveldn': str，生成的.zrietveldn项目文件的绝对路径（成功时）。
        - 'run_dir': str，任务运行目录，存放日志和中间文件。
        - 'error': str，错误信息（失败时）。
        - 'traceback': str，完整的异常堆栈（失败时，用于调试）。
    
    使用示例:
        result = zrt_generate_project(
            neutron_data="/data/sample.xye",
            neutron_inst="/data/instrument.zDiffractometer",
            cif_files=["/data/structure1.cif", "/data/structure2.cif"],
            user_id="wanghao96",
            mission_name="LNO_refinement_v1"
        )
        if result['ok']:
            zrtn_file = result['zrietveldn']
            print(f"项目生成成功：{zrtn_file}")
        else:
            print(f"项目生成失败：{result['error']}")
    
    注意事项:
        - 生成过程可能耗时数秒到数十秒，取决于数据量和结构复杂性。
        - 若多个CIF文件指向同一结构，只会加载一次。
        - 生成的.zrietveldn文件可被 zrt_manual_refine 工具加载并精修。
    """
    run_dir = TMP_DIR / user_id / mission_name
    logger = _setup_logging(run_dir)
    _ensure_env(zrt_data_dir, zrt_lib_dir)

    logger.info("Generate project start")
    logger.info("data=%s", neutron_data)
    logger.info("inst=%s", neutron_inst)
    logger.info("cif_count=%d", len(cif_files or []))

    try:
        zrt_path = refineTool.genZRTn(neutron_data, neutron_inst, cif_files)
        logger.info("Generate project done: %s", zrt_path)
        return {
            "ok": True,
            "zrietveldn": zrt_path,
            "run_dir": str(run_dir),
        }
    except Exception as e:
        logger.error("Generate project failed: %s", e)
        logger.error(traceback.format_exc())
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "run_dir": str(run_dir),
        }


def zrt_manual_refine(
    zrtn_path: str,
    cfg: Optional[Dict[str, object]] = None,
    user_id: str = "default",
    mission_name: str = "default",
    task_id: Optional[str] = None,
    zrt_data_dir: Optional[str] = None,
    zrt_lib_dir: Optional[str] = None,
) -> Dict[str, object]:
    """
    对现有Z-Rietveld项目文件执行手动精修，按用户指定的参数配置进行精修。
    
    此工具加载已生成的 .zrietveldn 项目文件，按 cfg 配置的参数精修中子粉末衍射数据。
    支持灵活的精修参数组合，包括标度因子、背景、晶胞、仪器参数、原子坐标等。
    
    Args:
        zrtn_path: 已生成的Z-Rietveld项目文件的绝对路径（.zrietveldn格式）。
                   文件必须由 zrt_generate_project 工具或Z-Rietveld GUI生成。
        
        cfg: 精修配置字典（可选，默认为None表示不进行任何精修）。
             所有配置值必须以字符串形式传入。
             支持的配置键及其值如下表所示：
             
             | 参数 | 类型 | 默认值 | 说明 |
             |------|------|--------|------|
             | `scaleVary` / `refine_scale` | str | "false" | 是否精修整体标度因子。值为"true"或"false"。 |
             | `scaleInit` | str | （无） | 标度因子初值，格式为数字字符串，如"1.0"。 |
             | `varyBackground` / `refine_background` | str | "false" | 是否精修背景。值为"true"或"false"。 |
             | `bgOrder` / `background_coeffs` | str | "6" | 背景多项式项数，如"3"、"6"。 |
             | `bgInit` | str | "1.0,0,0,0,0,0" | 背景初值，逗号分隔的数值列表。 |
             | `bgMode` | str | "legendre" | 背景类型，目前主要支持"legendre"。 |
             | `maxCycle` / `refine_cycle` | str | "10" | 最大迭代次数，如"5"、"20"。 |
             | `refine_cell` / `varyLattice` | str | "false" | 是否精修晶胞参数。根据空间群自动应用约束（立方/四方/六方/正交/单斜/三斜）。 |
             | `varyMassRatio` / `refine_phase_fraction` | str | "false" | 是否精修相分数。多相精修时使用。 |
             | `varyOccupancy` / `refine_occupancy` | str | "false" | 是否精修原子占有率。值为"true"或"false"。 |
             | `refine_position` | str | "false" | 是否精修原子坐标。值为"true"或"false"。 |
             | `refine_atoms` | str | （无） | 针对指定原子的精修控制。格式：'{"Fe1":"XU","O2":"YZF","all":"U"}' 支持标记：X/Y/Z（坐标），U/UISO（热因子），F/OCC/OCCUPANCY（占有率）。 |
             | `space_group` | str | （无） | 修改空间群，格式：'{"phase_name":"203_2"}'。相名来自CIF文件名(去扩展名)。 |
             | `absorption` | str | （无） | 吸收修正配置，格式：'{"DataLabel":{"refine":true,"function":"spherical"}}'。function可选值："none"、"cylindrical"、"spherical"、"plate"、"flatplate"、"lobanov1"、"lobanov2"。 |
             
             典型配置示例:
                cfg = {
                    "refine_scale": "true",
                    "refine_background": "true",
                    "background_coeffs": "6",
                    "bgInit": "1.0,0.1,0,0,0,0",
                    "refine_cycle": "10",
                    "refine_cell": "true",
                    "refine_atoms": "{\"all\":\"U\"}"
                }
        
        user_id: 用户标识符，字符串，用于文件管理和日志追踪。
                 默认值为 "default"。
        
        mission_name: 任务名称，字符串，标识当前精修任务。
                      用于组织日志和输出文件。
                      默认值为 "default"。

        task_id: 任务唯一标识（可选）。
             该参数由 MCP 包装层自动注入，用于任务跟踪。
             业务逻辑中通常无需手动传入。
        
        zrt_data_dir: Z-Rietveld原子数据目录的自定义路径（可选）。
                      若不提供，使用默认位置。
        
        zrt_lib_dir: Z-Rietveld库目录的自定义路径（可选）。
                     若不提供，使用默认位置。
    
    Returns:
        字典，包含以下键值：
        - 'ok': bool，表示操作成功（True）或失败（False）。
        - 'zrietveldn': str，项目文件的绝对路径。
        - 'cfg': dict或str，使用的配置参数（成功时）。
        - 'run_dir': str，任务运行目录，存放日志和中间文件。
        - 'error': str，错误信息（失败时）。
        - 'traceback': str，完整的异常堆栈（失败时，用于调试）。
    
    使用示例:
        # 精修scale和背景
        result = zrt_manual_refine(
            zrtn_path="/output/project.zrietveldn",
            cfg={
                "refine_scale": "true",
                "refine_background": "true",
                "background_coeffs": "6",
                "refine_cycle": "5"
            },
            user_id="wanghao96",
            mission_name="LNO_step1"
        )
        
        # 进一步精修晶胞和原子位置
        result = zrt_manual_refine(
            zrtn_path="/output/project.zrietveldn",
            cfg={
                "refine_cell": "true",
                "refine_atoms": "{\"all\":\"XU\"}",
                "refine_cycle": "10"
            },
            user_id="wanghao96",
            mission_name="LNO_step2"
        )
    
    注意事项:
        - cfg中的所有值都必须是字符串。若要传入布尔值，请使用"true"或"false"。
        - 若cfg为None或为空字典，则不会进行任何精修，仅加载项目。
        - 精修过程通常耗时10秒到数分钟，取决于迭代次数和数据量。
        - 旧参数名（如varyLattice）与新参数名（如refine_cell）通常兼容。
        - refine_atoms中的标记X表示该原子的X坐标（历史原因，实际等价XYZ）；F表示占有率。
    """
    run_dir = TMP_DIR / user_id / mission_name
    logger = _setup_logging(run_dir)
    _ensure_env(zrt_data_dir, zrt_lib_dir)
    cfg_str = _normalize_cfg(cfg)

    try:
        result = _run_manual_refine(zrtn_path, cfg_str, logger)
        result["run_dir"] = str(run_dir)
        return result
    except Exception as e:
        logger.error("Manual refine failed: %s", e)
        logger.error(traceback.format_exc())
        return {
            "ok": False,
            "error": str(e),
            "traceback": traceback.format_exc(),
            "run_dir": str(run_dir),
        }



zrt_generate_runner = ProcessPollingMcpToolBase(
    timeout_sec=1200.0,
    poll_interval=1.0,
    target_func=zrt_generate_project,
)

zrt_manual_runner = ProcessPollingMcpToolBase(
    timeout_sec=1200.0,
    poll_interval=1.0,
    target_func=zrt_manual_refine,
)


zrt_generate_runner.register_mcp_tool(mcp, name="zrt_generate_project")
zrt_manual_runner.register_mcp_tool(mcp, name="zrt_manual_refine")


if __name__ == "__main__":
    mcp.run(transport="sse")
