from drsai_ui.run_ui import ui
from pathlib import Path
here = Path(__file__).parent.absolute()
import os,sys
parent_path = os.path.dirname(os.path.abspath(__file__))
appdir = os.path.join(parent_path, "tmp/drsai_ui")
os.makedirs(appdir, exist_ok=True)
from dotenv import load_dotenv
# load_dotenv(dotenv_path=os.path.join(parent_path, ".env"), override=True)
load_dotenv(dotenv_path=os.path.join(parent_path, ".env_zjj"), override=True)


if __name__ == "__main__":
    ui(
        # reload=True,
        port=30004,
        appdir=appdir,
        database_uri= f"sqlite:////{appdir}/drsai_ui.db",
    )
