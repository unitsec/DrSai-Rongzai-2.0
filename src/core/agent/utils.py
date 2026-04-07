from typing import (
    List,
    Any
    )
import re,json
from pathlib import Path

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