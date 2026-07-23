#!/usr/bin/env python3
"""Export one user's chat history from drsai SQLite DB into per-session JSON files."""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Export all chat sessions for a specified user into JSON files.",
    )
    parser.add_argument(
        "--db",
        default="tmp/drsai.db",
        help="Path to drsai sqlite database (default: tmp/drsai.db)",
    )
    parser.add_argument("--user-id", required=True, help="Target user_id to export")
    parser.add_argument(
        "--output-dir",
        default="tmp/exported_sessions",
        help="Directory to save exported JSON files",
    )
    return parser.parse_args()


def table_exists(conn: sqlite3.Connection, table_name: str) -> bool:
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?",
        (table_name,),
    ).fetchone()
    return row is not None


def detect_storage_mode(conn: sqlite3.Connection, user_id: str) -> str:
    has_sessionmessage = table_exists(conn, "sessionmessage")
    if has_sessionmessage:
        row = conn.execute(
            "SELECT COUNT(*) FROM sessionmessage WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if row and row[0] > 0:
            return "sessionmessage"

    has_thread = table_exists(conn, "thread")
    if has_thread:
        row = conn.execute(
            """
            SELECT COUNT(*)
            FROM thread
            WHERE user_id=?
              AND messages IS NOT NULL
              AND messages<>''
            """,
            (user_id,),
        ).fetchone()
        if row and row[0] > 0:
            return "thread"

    return "none"


def safe_json_loads(raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def normalize_thread_id(thread_id: Any, fallback: str) -> str:
    """Return a safe, non-empty session identifier."""
    if thread_id is None:
        return fallback
    text = str(thread_id).strip()
    return text or fallback


def export_from_sessionmessage(
    conn: sqlite3.Connection, user_id: str, output_dir: Path
) -> int:
    rows = conn.execute(
        """
        SELECT id, created_at, updated_at, thread_id, user_id, message_type, source,
               raw_message, content, tool_name, tool_call_id, version
        FROM sessionmessage
        WHERE user_id=?
        ORDER BY thread_id, id
        """,
        (user_id,),
    ).fetchall()

    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        (
            msg_id,
            created_at,
            updated_at,
            thread_id,
            row_user_id,
            message_type,
            source,
            raw_message,
            content,
            tool_name,
            tool_call_id,
            version,
        ) = row

        normalized_thread_id = normalize_thread_id(thread_id, fallback=f"no_thread_{msg_id}")

        session = sessions.setdefault(
            normalized_thread_id,
            {
                "user_id": row_user_id,
                "thread_id": normalized_thread_id,
                "original_thread_id": thread_id,
                "source_table": "sessionmessage",
                "messages": [],
            },
        )
        session["messages"].append(
            {
                "id": msg_id,
                "created_at": created_at,
                "updated_at": updated_at,
                "message_type": message_type,
                "source": source,
                "content": content,
                "tool_name": tool_name,
                "tool_call_id": tool_call_id,
                "version": version,
                "raw_message": safe_json_loads(raw_message),
            }
        )

    return write_session_files(user_id, output_dir, sessions)


def export_from_thread(conn: sqlite3.Connection, user_id: str, output_dir: Path) -> int:
    rows = conn.execute(
        """
        SELECT id, created_at, updated_at, thread_id, status, messages, user_input, meta
        FROM thread
        WHERE user_id=?
          AND messages IS NOT NULL
          AND messages<>''
        ORDER BY id
        """,
        (user_id,),
    ).fetchall()

    sessions: dict[str, dict[str, Any]] = {}
    for row in rows:
        thread_pk, created_at, updated_at, thread_id, status, messages, user_input, meta = row
        normalized_thread_id = normalize_thread_id(thread_id, fallback=f"no_thread_{thread_pk}")
        parsed_messages = safe_json_loads(messages)
        message_count = len(parsed_messages) if isinstance(parsed_messages, list) else None
        sessions[normalized_thread_id] = {
            "thread_pk": thread_pk,
            "user_id": user_id,
            "thread_id": normalized_thread_id,
            "original_thread_id": thread_id,
            "source_table": "thread",
            "created_at": created_at,
            "updated_at": updated_at,
            "status": status,
            "message_count": message_count,
            "messages": parsed_messages,
            "user_input": safe_json_loads(user_input),
            "meta": safe_json_loads(meta),
        }

    return write_session_files(user_id, output_dir, sessions)


def sanitize_filename(value: str) -> str:
    keep = "-_."
    return "".join(ch if ch.isalnum() or ch in keep else "_" for ch in value)


def write_session_files(user_id: str, output_dir: Path, sessions: dict[str, dict[str, Any]]) -> int:
    output_dir.mkdir(parents=True, exist_ok=True)
    user_dir = output_dir / sanitize_filename(user_id)
    user_dir.mkdir(parents=True, exist_ok=True)

    index: list[dict[str, Any]] = []
    for idx, thread_id in enumerate(sorted(sessions.keys()), start=1):
        session_data = sessions[thread_id]
        filename = f"{idx:04d}_{sanitize_filename(thread_id)}.json"
        file_path = user_dir / filename
        with file_path.open("w", encoding="utf-8") as f:
            json.dump(session_data, f, ensure_ascii=False, indent=2)

        index.append(
            {
                "thread_id": thread_id,
                "file": filename,
                "message_count": session_data.get("message_count"),
                "created_at": session_data.get("created_at"),
                "updated_at": session_data.get("updated_at"),
            }
        )

    with (user_dir / "sessions_index.json").open("w", encoding="utf-8") as f:
        json.dump(index, f, ensure_ascii=False, indent=2)

    return len(sessions)


def main() -> int:
    args = parse_args()
    db_path = Path(args.db)
    out_dir = Path(args.output_dir)

    if not db_path.exists():
        print(f"[ERROR] Database not found: {db_path}")
        return 1

    conn = sqlite3.connect(str(db_path))
    try:
        mode = detect_storage_mode(conn, args.user_id)
        if mode == "none":
            print(f"[INFO] No chat history found for user_id={args.user_id}")
            return 0

        if mode == "sessionmessage":
            session_count = export_from_sessionmessage(conn, args.user_id, out_dir)
        else:
            session_count = export_from_thread(conn, args.user_id, out_dir)

        print(f"[OK] Exported {session_count} sessions for user_id={args.user_id}")
        print(f"[OK] Output directory: {out_dir / sanitize_filename(args.user_id)}")
        print(f"[OK] Source table: {mode}")
        return 0
    finally:
        conn.close()


if __name__ == "__main__":

    """
    python export_user_chat_sessions.py --db drsai.db --user-id wanghao96@ihep.ac.cn --output-dir tmp/exported_sessions
    """
    raise SystemExit(main())
