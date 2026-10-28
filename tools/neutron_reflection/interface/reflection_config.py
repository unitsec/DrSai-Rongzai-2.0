"""Load reduction templates without treating data-source mappings as path strings."""
import json
from pathlib import Path


def load_reflection_config(base_json, specific_json):
    with Path(base_json).open(encoding="utf-8") as f:
        config = json.load(f)
    with Path(specific_json).open(encoding="utf-8") as f:
        specific = json.load(f)
    # Match rongzai 4.0 load_config's one-level dictionary merge semantics.
    for key, value in specific.items():
        if isinstance(config.get(key), dict) and isinstance(value, dict):
            config[key] = {**config[key], **value}
        else:
            config[key] = value
    base_dir = Path(base_json).parent
    for folder in (base_dir, base_dir.parent):
        candidate = folder / "machine_paths.json"
        if candidate.is_file():
            with candidate.open(encoding="utf-8") as f:
                paths = json.load(f)
            config = _resolve(config, paths)
            break
    return config


def _resolve(value, paths):
    if isinstance(value, str):
        for key, replacement in paths.items():
            token = "{" + key + "}"
            if token in value:
                if not isinstance(replacement, str):
                    raise ValueError(f"路径模板{token}必须引用字符串，不能引用字典等结构")
                value = value.replace(token, replacement)
        return value
    if isinstance(value, dict):
        return {key: _resolve(item, paths) for key, item in value.items()}
    if isinstance(value, list):
        return [_resolve(item, paths) for item in value]
    return value
