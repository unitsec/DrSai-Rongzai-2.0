from pathlib import Path
import importlib
import os
import sys

from dotenv import load_dotenv

here = Path(__file__).parent.absolute()
parent_path = os.path.dirname(os.path.abspath(__file__))
load_dotenv(dotenv_path=os.path.join(parent_path, ".env"), override=True)


def resolve_ui_entry():
    mode = os.getenv("DRSAI_UI_MODE", "external").strip().lower()
    internal_src = os.getenv("DRSAI_UI_INTERNAL_SRC", os.path.join(parent_path, "src"))

    if mode == "internal":
        if internal_src not in sys.path:
            sys.path.insert(0, internal_src)

    try:
        return importlib.import_module("drsai_ui.run_ui").ui, mode, internal_src
    except Exception as exc:
        if mode == "internal":
            raise RuntimeError(
                "Failed to load internal drsai_ui. "
                f"Check DRSAI_UI_INTERNAL_SRC={internal_src} and ensure drsai_ui package exists under that path."
            ) from exc
        raise RuntimeError(
            "Failed to load external drsai_ui from current Python environment. "
            "Install drsai_ui in this environment or set DRSAI_UI_MODE=internal."
        ) from exc


ui, ui_mode, ui_internal_src = resolve_ui_entry()
appdir = os.path.join(parent_path, "tmp/drsai_ui")
os.makedirs(appdir, exist_ok=True)
db_file = Path(appdir) / "drsai_ui.db"
db_uri = f"sqlite:///{db_file.as_posix()}"
UI_PORT = int(os.getenv("RONGZAI_UI_PORT", "8081"))

if __name__ == "__main__":
    print(f"[drsai_ui] mode={ui_mode}")
    if ui_mode == "internal":
        print(f"[drsai_ui] internal_src={ui_internal_src}")
    ui(
        # reload=True,
        port=UI_PORT,
        appdir=appdir,
        database_uri=db_uri,
    )