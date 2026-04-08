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
    image_pattern = re.compile(
        r"(/[^\s\"'<>\]\[\(\){}]+\.(?:png|jpg|jpeg|gif|webp|bmp|tif|tiff))",
        re.IGNORECASE,
    )
    # Common trailing punctuation when paths appear in natural text.
    trailing_chars = ".,;:!?)]}>'\""

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
        for match in image_pattern.findall(text):
            candidate = match.rstrip(trailing_chars)
            p = Path(candidate)
            if p.exists() and p.is_file():
                found.append(str(p))
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
        if you need to serve files to user, you can call this function with the local paths of the files and their corresponding descriptions, then the files will be shown to user in the frontend and user can download them.

        args:
            files_list (list[str]): The local paths of the files to serve.
            descriptions_list (list[str]): The corresponding descriptions of the files to serve.
        """
        events = []
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
            
            files=FilesContent(
                files=[base64_file],
                title="files for download",
                description="these files are served for user downloading.",
                )

            event = FilesEvent(
                source=source_name,
                content=files.model_dump(),
                )
            events.append(event.model_dump(mode="json"))

        return json.dumps({"type": "files_events", "events": events}, ensure_ascii=False)

    return [serve_files_for_user]
