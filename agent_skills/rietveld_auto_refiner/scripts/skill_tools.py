from __future__ import annotations

import asyncio
import glob
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from typing import Any, Dict, Optional

from drsai import CancellationToken
from drsai.modules.components.tool import SseServerParams, mcp_server_tools


DEFAULT_GSASII_MCP_URL = "http://127.0.0.1:42501/sse"
_TOOLS_CACHE: dict[str, list[Any]] = {}


def find_refinement_files(
    data_dir: str = "",
    user_upload_dir: str = "",
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
    """

    data_dir = data_dir or os.getenv("WORKER_SPACE", "")
    user_upload_dir = user_upload_dir or os.getenv("USER_UPLOAD_DIR", "")

    if not data_dir:
        return {
            "status": "error",
            "message": "未提供 data_dir，且环境变量 WORKER_SPACE 未设置。",
        }

    def _find_in_upload(name: str) -> str:
        if not name or not user_upload_dir:
            return ""
        target = os.path.basename(name).lower()
        for root, _, files in os.walk(user_upload_dir):
            for filename in files:
                if filename.lower() == target:
                    return os.path.join(root, filename)
        return ""

    if run_id:
        digits = "".join(ch for ch in run_id if ch.isdigit())
        if digits:
            run_id = f"RUN{digits.zfill(7)}"

    cifs_from_database = cifs_from_database or []
    cifs_from_upload = cifs_from_upload or []

    need_run_lookup = not gsa_from_upload or bool(cifs_from_database)
    need_inst_lookup = not inst_from_upload

    run_dir = None
    cif_dir = ""

    if need_run_lookup:
        if not beamline:
            return {"status": "error", "message": "请告诉我谱仪名称，例如 BL16、BL18、BL09、BL15。"}
        if not run_id:
            return {"status": "error", "message": "请告诉我 RUN 号，例如 RUN0021905。"}

        run_data_dir = os.path.join(data_dir, beamline, "RUNdataFiles")
        if os.path.exists(run_data_dir):
            for folder in os.listdir(run_data_dir):
                if run_id.lower() in folder.lower():
                    run_dir = os.path.join(run_data_dir, folder)
                    break

        if not run_dir:
            return {
                "status": "error",
                "message": f"在 {beamline} 数据库中未找到 {run_id}，请确认谱仪名称和 RUN 号是否正确，或直接上传数据文件。",
            }
        cif_dir = os.path.join(run_dir, "cifFiles")

    if need_inst_lookup:
        if not beamline:
            return {"status": "error", "message": "请告诉我谱仪名称，例如 BL16、BL18、BL09、BL15。"}
        if not detector:
            return {"status": "error", "message": "请告诉我探测器组名，例如 group5、bank14。"}

    gsa_path = _find_in_upload(gsa_from_upload) if gsa_from_upload else ""
    if not gsa_path:
        if not run_dir:
            return {"status": "error", "message": "需要提供正确的 RUN 号，才能从数据库定位衍射数据文件。"}
        run_base = os.path.basename(run_dir).upper()
        candidate = os.path.join(run_dir, f"{run_base}_{detector}.gsa")
        if not os.path.exists(candidate):
            return {
                "status": "error",
                "message": f"在 {run_id} 下未找到 {run_base}_{detector}.gsa，请确认探测器编号是否正确。",
            }
        gsa_path = candidate

    instprm_path = _find_in_upload(inst_from_upload) if inst_from_upload else ""
    if not instprm_path:
        pattern = os.path.join(data_dir, beamline, "instrumentFiles", f"*{detector}*.instprm")
        matches = glob.glob(pattern)
        if not matches:
            return {
                "status": "error",
                "message": f"未找到 {detector} 对应的仪器参数文件（.instprm），请上传或确认探测器编号。",
            }
        instprm_path = matches[0]

    cif_paths = []
    for name in cifs_from_upload:
        path = _find_in_upload(name)
        if not path:
            return {"status": "error", "message": f"上传的 CIF 文件 {name} 未找到，请确认是否已上传。"}
        cif_paths.append(path)

    for name in cifs_from_database:
        path = os.path.join(cif_dir, name)
        if not os.path.exists(path):
            return {"status": "error", "message": f"数据库中未找到 CIF 文件 {name}。"}
        cif_paths.append(path)

    if not cif_paths:
        return {"status": "error", "message": "至少需要一个 CIF 结构文件，请告诉我文件名或上传文件。"}

    return {
        "status": "success",
        "gsa_path": gsa_path,
        "instprm_path": instprm_path,
        "cif_paths": cif_paths,
    }


def _to_bool(value: Any, default: bool = False) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return bool(value)
    if isinstance(value, str):
        normalized = value.strip().lower()
        if normalized in {"true", "1", "yes", "y"}:
            return True
        if normalized in {"false", "0", "no", "n"}:
            return False
    return default


def _extract_text_payload(content_item: Any) -> Any:
    if isinstance(content_item, dict) and content_item.get("type") == "text":
        text = content_item.get("text", "")
        if isinstance(text, str):
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except Exception:
                    return text
        return text

    if getattr(content_item, "type", None) == "text" and hasattr(content_item, "text"):
        text = getattr(content_item, "text", "")
        if isinstance(text, str):
            stripped = text.strip()
            if stripped.startswith("{") or stripped.startswith("["):
                try:
                    return json.loads(stripped)
                except Exception:
                    return text
        return text

    if hasattr(content_item, "text") and isinstance(getattr(content_item, "text", None), str):
        text = getattr(content_item, "text")
        stripped = text.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            try:
                return json.loads(stripped)
            except Exception:
                return text
        return text

    return content_item


def _parse_mcp_output(raw_result: Any) -> Dict[str, Any]:
    try:
        if isinstance(raw_result, list) and raw_result:
            payload = _extract_text_payload(raw_result[0])
            if isinstance(payload, dict):
                return payload

            if isinstance(payload, str):
                stripped = payload.strip()
                if stripped.startswith("{") or stripped.startswith("["):
                    try:
                        parsed = json.loads(stripped)
                        if isinstance(parsed, dict):
                            return parsed
                    except Exception:
                        pass

            return {
                "state": "completed",
                "status": "error",
                "result": str(payload),
            }
    except Exception as exc:
        return {
            "state": "completed",
            "status": "error",
            "result": f"parse_error: {exc}",
        }
    return {
        "state": "completed",
        "status": "error",
        "result": f"unexpected_result: {raw_result}",
    }


async def _get_write_info_tool(mcp_url: str):
    tools = _TOOLS_CACHE.get(mcp_url)
    if tools is None:
        tools = await mcp_server_tools(
            SseServerParams(
                url=mcp_url,
                timeout=20.0,
                sse_read_timeout=300.0,
            )
        )
        _TOOLS_CACHE[mcp_url] = tools

    for tool in tools:
        if getattr(tool, "name", "") == "write_info":
            return tool
    raise RuntimeError("未找到 MCP 工具 write_info，请确认 0neutron_gsas_refine.py 已启动")


def _derive_basic_switches(refine_params: Dict[str, Any]) -> Dict[str, Any]:
    refine_atoms = refine_params.get("refine_atoms", {})
    return {
        "refine_background": _to_bool(refine_params.get("refine_background", False)),
        "refine_cell": _to_bool(refine_params.get("refine_cell", False)),
        "refine_scale": _to_bool(refine_params.get("refine_scale", False)),
        "refine_instrument": _to_bool(refine_params.get("refine_instrument", False)),
        "refine_absorption": _to_bool(refine_params.get("refine_absorption", False)),
        "refine_atoms": refine_atoms if isinstance(refine_atoms, dict) else {},
    }


async def run_rietveld_refinement(
    gsa_path: str,
    instprm_path: str,
    cif_paths: list[str],
    refine_params: Optional[Dict[str, Any]] = None,
    gpx_path: str = "",
    refine_idx: int = 0,
    thread_id: str = "anonymous",
    use_constraints: bool = True,
    mcp_url: str = DEFAULT_GSASII_MCP_URL,
    poll_interval_sec: float = 2.0,
    timeout_sec: int = 1800,
) -> Dict[str, Any]:
    """
    通过 GSAS-II MCP 工具 write_info 执行一次 Rietveld 精修，并轮询直到任务完成。
    """
    refine_params = refine_params or {}
    task_id = str(uuid.uuid4())
    write_info_tool = await _get_write_info_tool(mcp_url)
    cancellation_token = CancellationToken()

    basic = _derive_basic_switches(refine_params)
    arguments = {
        "gsa_path": gsa_path,
        "instprm_path": instprm_path,
        "cif_paths": cif_paths,
        "gpx_path": gpx_path or "",
        "refine_idx": int(refine_idx),
        "refine_params": refine_params,
        "refine_background": basic["refine_background"],
        "refine_cell": basic["refine_cell"],
        "refine_scale": basic["refine_scale"],
        "refine_instrument": basic["refine_instrument"],
        "refine_absorption": basic["refine_absorption"],
        "refine_atoms": basic["refine_atoms"],
        "use_constraints": bool(use_constraints),
        "task_id": task_id,
        "thread_id": thread_id,
    }

    first_raw = await write_info_tool.run_json(arguments, cancellation_token)
    first = _parse_mcp_output(first_raw)
    state = str(first.get("state", "")).lower()
    if state == "completed":
        ok = str(first.get("status", "")).lower() == "success"
        return {
            "success": ok,
            "status": "success" if ok else "error",
            "message": "refinement completed" if ok else "refinement failed",
            "state": "completed",
            "task_id": task_id,
            "data": first.get("result"),
            "raw": first,
        }

    start_time = asyncio.get_running_loop().time()
    last = first
    while True:
        if asyncio.get_running_loop().time() - start_time > timeout_sec:
            return {
                "success": False,
                "status": "timeout",
                "message": f"refinement timeout after {timeout_sec}s",
                "state": "timeout",
                "task_id": task_id,
                "data": last.get("result"),
                "raw": last,
            }

        await asyncio.sleep(poll_interval_sec)
        poll_raw = await write_info_tool.run_json(arguments, cancellation_token)
        poll = _parse_mcp_output(poll_raw)
        last = poll
        state = str(poll.get("state", "")).lower()
        if state == "completed":
            ok = str(poll.get("status", "")).lower() == "success"
            return {
                "success": ok,
                "status": "success" if ok else "error",
                "message": "refinement completed" if ok else "refinement failed",
                "state": "completed",
                "task_id": task_id,
                "data": poll.get("result"),
                "raw": poll,
            }


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

    find_refinement_files_tool.__name__ = "find_refinement_files"
    run_rietveld_refinement_tool.__name__ = "run_rietveld_refinement"
    return find_refinement_files_tool, run_rietveld_refinement_tool