"""Standalone stdlib-only helper, executed inside the analysis sandbox."""

import base64
import json
from pathlib import Path
import sys
import subprocess


def main() -> None:
    operation = sys.argv[1]
    payload = json.load(sys.stdin)
    path = Path(payload["path"])
    if operation == "read":
        selected = []
        count = 0
        truncated = False
        with path.open("r", encoding=payload["encoding"], errors="ignore") as stream:
            for number, line in enumerate(stream, 1):
                if number < payload["start_line"]:
                    continue
                if payload["end_line"] is not None and number > payload["end_line"]:
                    break
                chunk = ("\n" if selected else "") + line.rstrip("\n")
                remaining = payload["max_chars"] - count
                if len(chunk) > remaining:
                    selected.append(chunk[:remaining])
                    truncated = True
                    break
                selected.append(chunk)
                count += len(chunk)
        text = "".join(selected)
        if truncated:
            text += (
                f"\n\n[Truncated: The requested file content exceeds the {payload['max_chars']}-character limit, "
                f"so only the first {payload['max_chars']} characters are shown. To continue reading, "
                "strongly prefer using start_line and end_line to read the file in chunks.]"
            )
        print(text, end="")
    elif operation == "write":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(payload["content"], encoding="utf-8")
    elif operation == "edit":
        text = path.read_text(encoding="utf-8")
        if payload["old_text"] not in text:
            raise ValueError("Text not found")
        path.write_text(text.replace(payload["old_text"], payload["new_text"], 1), encoding="utf-8")
    elif operation == "base64":
        print(base64.b64encode(path.read_bytes()).decode("ascii"), end="")
    elif operation == "grep":
        result = subprocess.run(payload["command"], check=False)
        if result.returncode not in (0, 1):
            raise SystemExit(result.returncode)
    else:
        raise ValueError(f"Unknown file operation: {operation}")


if __name__ == "__main__":
    main()
