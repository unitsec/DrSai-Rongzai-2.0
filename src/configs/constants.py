from pathlib import Path
import os
here = Path(__file__).parent.resolve()

# file save path
files_dir = str(here.parent.parent /'files')
# print(files_dir)

tools_dir = str(here.parent.parent/'tools')


def get_datasets_dir() -> Path:
    """Resolve the administrator-configured shared dataset directory."""
    configured = os.getenv("RONGZAI_DATASETS_DIR", "").strip()
    path = Path(configured) if configured else here.parent.parent / "datasets"
    if not path.is_absolute():
        raise ValueError("RONGZAI_DATASETS_DIR must be an absolute directory path")
    path = path.resolve()
    if not path.is_dir():
        raise ValueError(f"RONGZAI_DATASETS_DIR is not an existing directory: {path}")
    return path