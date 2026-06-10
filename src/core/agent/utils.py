from typing import (
    List,
    Any
    )
import re,json,os,base64
from pathlib import Path
from drsai.modules.managers.messages import (
    FileInfo,
    FilesContent,
    FilesEvent
    )


def _extract_existing_image_paths_from_content(content: str) -> List[str]:
    """Extract existing local image file paths from tool result text.

    The content can be plain text or nested JSON serialized as text.
    """
    image_suffixes = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tif", ".tiff"}
    quoted_path_pattern = re.compile(
        r"[\"']([^\"']+\.(?:png|jpg|jpeg|gif|webp|bmp|tif|tiff))[\"']",
        re.IGNORECASE,
    )
    unquoted_path_pattern = re.compile(
        r"((?:/|\.?\.?/|[A-Za-z0-9_.@-]+/)[^\n\"'<>]+?\.(?:png|jpg|jpeg|gif|webp|bmp|tif|tiff))",
        re.IGNORECASE,
    )
    # Common trailing punctuation when paths appear in natural text.
    trailing_chars = ".,;:!?)]}>'\""

    def _append_if_valid_path(raw_path: str, output: List[str]):
        candidate = raw_path.strip().strip('"\'').rstrip(trailing_chars)
        if not candidate:
            return
        p = Path(candidate)
        if p.exists() and p.is_file() and p.suffix.lower() in image_suffixes:
            output.append(str(p))

    def _collect_strings(value: Any) -> List[str]:
        if isinstance(value, str):
            return [value]
        if isinstance(value, list):
            out: List[str] = []
            for item in value:
                out.extend(_collect_strings(item))
            return out
        if isinstance(value, dict):
            out = []
            for item in value.values():
                out.extend(_collect_strings(item))
            return out
        return []

    def _extract_from_text(text: str) -> List[str]:
        found: List[str] = []

        for match in quoted_path_pattern.findall(text):
            _append_if_valid_path(match, found)

        for match in unquoted_path_pattern.findall(text):
            _append_if_valid_path(match, found)

        return found

    pending_texts = [content]
    visited = set()
    candidates: List[str] = []

    while pending_texts:
        text = pending_texts.pop()
        if text in visited:
            continue
        visited.add(text)

        candidates.extend(_extract_from_text(text))

        try:
            parsed = json.loads(text)
        except Exception:
            continue

        nested_strings = _collect_strings(parsed)
        pending_texts.extend(nested_strings)

    # Keep order and remove duplicates.
    deduped: List[str] = []
    seen = set()
    for path in candidates:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def show_file_to_user(model_client, file_path: str, purpose: str = "user_data") -> str:
        """上传文件到图床接口
        Args:
            file_path (str): 文件路径
            purpose (str): 上传用途，默认为"user_data"

        Returns:
            str: 上传后的URL
        """
        from openai import OpenAI
        client = OpenAI(
            api_key=model_client._client.api_key,
            base_url="https://aiapi.ihep.ac.cn/apiv2"
        )

        file_obj = client.files.create(
                file=open(file_path, "rb"),
                purpose=purpose
            )
        return f"https://aiapi.ihep.ac.cn/apiv2/files/{file_obj.id}/preview"

def get_operator_funcs_rongzai(source_name: str) -> list[callable]:
    
    def get_file_base64(file_path: str):
        """获取知识库文件的base64编码"""
        try:
            if os.path.exists(file_path):
                with open(file_path, 'rb') as file:
                    file_base64 = base64.b64encode(file.read()).decode('utf-8')
                # file_obj = upload_to_hepai_filesystem(file_path=file_path)
                return file_base64
            else:
                print(f"警告: 文件 {file_path} 不存在")
                return None
        except Exception as e:
            print(f"读取文件时出错: {e}")
            return None
    
    def serve_files_for_user(files_list: list[str], descriptions_list: list[str]):
        """
        Serve the files to the user for downloading by showing it to the frontend.
        if you need to serve downloadable artifacts to user, you can call this function with the local paths of the files and their corresponding descriptions.
        The files will be shown to user in the frontend and user can download them.

        Important:
            - Do NOT use this tool to send image files during intermediate steps.
            - Image files produced by tool calls are already auto-displayed to the user.
            - Prefer this tool for downloadable deliverables requested by the user.

        args:
            files_list (list[str]): The local paths of the files to serve.
            descriptions_list (list[str]): The corresponding descriptions of the files to serve.
        """
        try:
            base64_files = []
            for file_path, description in zip(files_list, descriptions_list):
                file_base64 = get_file_base64(file_path)
                if not file_base64:
                    continue
                    
                base64_file = FileInfo(
                    name = os.path.basename(file_path),
                    base64_content = file_base64,
                    description=description,
                    download_method="base64"
                    )
                base64_files.append(base64_file)
                
            files=FilesContent(
                files=base64_files,
                title="files for download",
                description="these files are served for user downloading.",
                )

            event = FilesEvent(
                source=source_name,
                content=files.model_dump(),
                )

            return json.dumps({"type": "files_event", "event": event.model_dump()}, ensure_ascii=False)
        except Exception as e:
            return f"Error in serve_files_for_user: {e}"
        
    def get_user_info_from_web():
        """
        从网页端 iCAT / session 获取用户信息。

        当前实现优先从环境变量读取，便于在未接入前端鉴权时联调：
        - RONGZAI_USER_INFO_JSON: JSON 字符串，示例
                    {"username":"alice","password":"***","roles":["user"],"allowed_prefixes":["/data/reduction"]}
        - RONGZAI_USERNAME: 用户名（当未提供 JSON 时使用）
                - RONGZAI_PASSWORD: 密码（当未提供 JSON 时使用）
        """
        env_user_json = os.getenv("RONGZAI_USER_INFO_JSON", "").strip()
        if env_user_json:
            try:
                parsed = json.loads(env_user_json)
                if isinstance(parsed, dict):
                    return parsed
            except Exception:
                pass

        return {
            "username": os.getenv("RONGZAI_USERNAME", "anonymous"),
            "password": os.getenv("RONGZAI_PASSWORD", ""),
            "roles": ["user"],
            "allowed_prefixes": [],
        }
    
    def check_user_permission_for_file(user_info, run_id: str):
        """
        检查用户是否有权限访问指定 run。

        规则：
        - 主路径：通过 icat_permission_provider.py 中的 IcatPermissionProvider 查询真实权限。
        - 兜底：若 ICAT 不可用，回退到环境变量 RONGZAI_ALLOWED_RUNS 的 run 白名单判断（便于本地联调）。
        """
        def _normalize_run(raw_value: str) -> str:
            m = re.search(r"RUN(\d{5,})", str(raw_value or "").upper())
            if not m:
                return ""
            return m.group(1).lstrip("0") or "0"

        query_run = _normalize_run(run_id)
        if not query_run:
            return False

        def _fallback_run_check() -> bool:
            allowed_runs: list[str] = []

            if isinstance(user_info, dict):
                raw_allowed_runs = user_info.get("allowed_runs", [])
                if isinstance(raw_allowed_runs, list):
                    allowed_runs.extend([str(x) for x in raw_allowed_runs if x])

            if not allowed_runs:
                env_runs = os.getenv("RONGZAI_ALLOWED_RUNS", "")
                if env_runs:
                    allowed_runs.extend([x.strip() for x in env_runs.split(",") if x.strip()])

            normalized_allowed = {_normalize_run(x) for x in allowed_runs}
            normalized_allowed.discard("")
            return query_run in normalized_allowed

        try:
            from src.core.auth.icat_permission_provider import IcatPermissionProvider

            username = ""
            password = ""
            role = "user"
            if isinstance(user_info, dict):
                username = str(user_info.get("username", "") or "")
                password = str(user_info.get("password", "") or "")
                role = str(user_info.get("role", "user") or "user")

            if not username or not password:
                # 凭证不完整时，用兜底策略。
                return _fallback_run_check()

            icat_uri = os.getenv("ICAT_URI", "").strip()
            if not icat_uri:
                return _fallback_run_check()

            provider = IcatPermissionProvider(icat_uri=icat_uri)
            perms = provider.get_user_permissions(username=username, password=password, role=role)

            # 基于 run 精确授权匹配。
            for proposal in getattr(perms, "proposals", []) or []:
                for run in getattr(proposal, "runs", []) or []:
                    run_num = str(getattr(run, "run_number", "") or "")
                    if (run_num.lstrip("0") or "0") == query_run:
                        return True
            return False
        except Exception:
            # ICAT 异常时回退，保证工具在离线/测试环境可运行。
            return _fallback_run_check()
        
    def get_file_for_user(file_path: str):
        """
        根据用户提供的 run 号返回可访问的规约目录绝对路径。

        安全约束：
        - 仅接受 RUN 号格式输入（如 RUN0012345），避免路径穿越。
        - 严格做权限检查，不允许绕过。
        - 优先匹配 reduction_RUNxxxxxxx_时间戳 目录，找不到时再尝试 root/RUNxxxxxxx。

        输入参数:
            - file_path: 用户输入的 run 号，例：RUN0012345
        输出结果：
            - 成功：返回可访问路径提示字符串。
            - 失败：返回错误信息字符串。
        
        """
        raw = (file_path or "").strip().upper()
        if not re.fullmatch(r"RUN\d{5,}", raw):
            return "Input Error: file_path must be like RUN0012345."
        
        user_info = get_user_info_from_web()
        # 权限检查只用查询用户有没有访问对应run的权限，不允许直接基于路径做权限判断，避免用户绕过权限访问其他目录。
        is_authorized = check_user_permission_for_file(user_info, raw)

        if not is_authorized:
            return "Permission Error: User does not have permission to access file."

        root_path = os.path.realpath(os.getenv("RONGZAI_DATA_ROOT", "/home/dell/ZJJ/rongzai-agent-data"))
        if not os.path.isdir(root_path):
            return f"Path Error: data root does not exist: {root_path}"

        root = Path(root_path)
        candidates: list[Path] = []

        # 优先匹配 reduction_RUNxxxxxxx_时间戳 目录。
        candidates.extend([p for p in root.glob(f"reduction_{raw}_*") if p.is_dir()])

        # 回退：匹配直接同名目录 root/RUNxxxxxxx。
        direct = root / raw
        if direct.is_dir():
            candidates.append(direct)

        if not candidates:
            return f"File Not Found: no reduction directory found for {raw} under {root_path}."

        # 选择最新目录（按 mtime）。
        target_dir = max(candidates, key=lambda p: p.stat().st_mtime)
        abs_file_path = os.path.realpath(str(target_dir))

        if not os.path.isdir(abs_file_path):
            return f"File Not Found: resolved path is not a directory: {abs_file_path}"

        return f"User has permission to access file in {abs_file_path}."
    
    return [serve_files_for_user, get_file_for_user]


if __name__ == "__main__":
    from tempfile import TemporaryDirectory

    print("[TEST] utils.py - get_file_for_user")

    with TemporaryDirectory(prefix="rongzai_data_root_") as tmp_root:
        # 构造一个可命中的规约目录
        run_ok = "RUN0012345"
        matched_dir = Path(tmp_root) / f"reduction_{run_ok}_20260605120000"
        matched_dir.mkdir(parents=True, exist_ok=True)

        # 注入测试环境变量
        os.environ["RONGZAI_DATA_ROOT"] = tmp_root
        os.environ["RONGZAI_USER_INFO_JSON"] = json.dumps(
            {
                "username": "local_test_user",
                "roles": ["user"],
                "allowed_runs": [run_ok],
            },
            ensure_ascii=False,
        )

        _, get_file_for_user = get_operator_funcs_rongzai("local_test")

        cases = [
            ("非法输入", "abc"),
            ("不存在目录", "RUN9999999"),
            ("命中规约目录", run_ok),
        ]

        for title, run_id in cases:
            result = get_file_for_user(run_id)
            print(f"- {title} | input={run_id} -> {result}")
