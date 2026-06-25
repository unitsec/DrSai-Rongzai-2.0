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
    
    
    return [serve_files_for_user]


if __name__ == "__main__":
    [_,check_user_permission_for_file] = get_operator_funcs_rongzai("test_source")
    checkinfo = check_user_permission_for_file("durong", "BL16", "RUN0028987")
    print(f"checkinfo: {checkinfo}")
