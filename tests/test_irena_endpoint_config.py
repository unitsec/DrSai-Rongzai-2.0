import os
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch

from dotenv import load_dotenv


REPO_ROOT = Path(__file__).resolve().parents[1]


class IrenaEndpointConfigTests(unittest.TestCase):
    def test_port_configuration(self):
        cases = (
            ("MCP_IRENA_REFINE_PORT=43519\n", None, 43519),
            ("MCP_IRENA_REFINE_PORT=43519\n", "43509", 43519),
            ("", None, 42509),
            ("", "43529", 43529),
        )
        for env_content, inherited_port, expected_port in cases:
            with self.subTest(
                env_content=env_content, inherited_port=inherited_port
            ), tempfile.TemporaryDirectory() as directory:
                env_file = Path(directory) / ".env"
                env_file.write_text(env_content, encoding="utf-8")

                def load_test_env(dotenv_path, override=False):
                    self.assertEqual(Path(dotenv_path), REPO_ROOT / ".env")
                    return load_dotenv(env_file, override=override)

                with patch.dict(os.environ), patch(
                    "dotenv.load_dotenv", side_effect=load_test_env
                ) as loader:
                    os.environ.pop("MCP_IRENA_REFINE_PORT", None)
                    if inherited_port is not None:
                        os.environ["MCP_IRENA_REFINE_PORT"] = inherited_port
                    namespace = runpy.run_path(
                        str(REPO_ROOT / "tools/Irena/0Irena_fit.py"),
                        run_name="irena_config_test",
                    )
                    self.assertEqual(namespace["mcp"].settings.port, expected_port)
                    loader.assert_called_once()


if __name__ == "__main__":
    unittest.main()
