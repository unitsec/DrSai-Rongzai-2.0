import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from drsai import CancellationToken
from drsai.modules.components.tool import SseServerParams, mcp_server_tools


DEFAULT_GSASII_MCP_URL = "http://127.0.0.1:42501/sse"

_TOOLS_CACHE: dict[str, list[Any]] = {}


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

    # MCP SDK may return typed objects like TextContent(type='text', text='...').
    # Handle these objects the same way as dict text payloads.
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

    # Some tool wrappers may return a plain object with `text` but no `type`.
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

            # If payload is a JSON string, parse it again as a final fallback.
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

    参数:
        gsa_path: 中子衍射数据文件绝对路径（通常为 .gsa）
        instprm_path: 仪器参数文件绝对路径（.instprm）
        cif_paths: CIF 结构文件绝对路径列表，支持多相
        refine_params: GSAS-II 高级精修参数字典，建议按旧架构 write_info 的格式直接传入
        gpx_path: 已有 gpx 工程文件路径；为空时创建新工程，非空时在已有工程上继续精修
        refine_idx: 当前精修轮次索引，用于生成轮次化输出文件
        thread_id: 线程标识，用于隔离输出目录
        use_constraints: 是否启用约束
        mcp_url: GSAS-II MCP SSE 服务地址
        poll_interval_sec: 轮询间隔秒数
        timeout_sec: 超时时间，默认 1800 秒

    返回:
        {
            "success": bool,
            "status": "success" | "error" | "timeout",
            "message": str,
            "state": "completed" | "running" | "created" | "timeout",
            "task_id": str,
            "data": dict | None,
            "raw": dict,
        }
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