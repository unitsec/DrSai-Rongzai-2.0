from __future__ import annotations

from collections.abc import Awaitable, Callable

from src.tools.file_tools import find_refinement_files
from src.tools.rietveld_tools import run_rietveld_refinement


def build_rietveld_skill_tools(
    *,
    data_dir: str,
    user_upload_dir: str,
    thread_id: str | None,
) -> tuple[Callable[..., dict], Callable[..., Awaitable[dict]]]:
    """Build the tool functions used by the Rietveld refinement skill."""

    def find_refinement_files_tool(
        beamline: str | None = None,
        run_id: str | None = None,
        detector: str | None = None,
        cifs_from_database: list[str] | None = None,
        cifs_from_upload: list[str] | None = None,
        gsa_from_upload: str | None = None,
        inst_from_upload: str | None = None,
    ) -> dict:
        """
        定位 Rietveld 精修所需的衍射数据、仪器参数和 CIF 结构文件绝对路径。
        优先检查用户上传目录；缺失时再按 beamline、run_id、detector 从数据库目录查找。
        """
        return find_refinement_files(
            data_dir=data_dir,
            user_upload_dir=user_upload_dir,
            beamline=beamline,
            run_id=run_id,
            detector=detector,
            cifs_from_database=cifs_from_database,
            cifs_from_upload=cifs_from_upload,
            gsa_from_upload=gsa_from_upload,
            inst_from_upload=inst_from_upload,
        )

    async def run_rietveld_refinement_tool(
        gsa_path: str,
        instprm_path: str,
        cif_paths: list[str],
        refine_params: dict | None = None,
        gpx_path: str = "",
        refine_idx: int = 0,
        use_constraints: bool = True,
    ) -> dict:
        """
        调用 GSAS-II MCP 服务执行一轮 Rietveld 精修，并返回统一结构化结果。
        """
        return await run_rietveld_refinement(
            gsa_path=gsa_path,
            instprm_path=instprm_path,
            cif_paths=cif_paths,
            refine_params=refine_params,
            gpx_path=gpx_path,
            refine_idx=refine_idx,
            thread_id=thread_id or "anonymous",
            use_constraints=use_constraints,
        )

    find_refinement_files_tool.__name__ = "find_refinement_files_tool"
    run_rietveld_refinement_tool.__name__ = "run_rietveld_refinement_tool"
    return find_refinement_files_tool, run_rietveld_refinement_tool