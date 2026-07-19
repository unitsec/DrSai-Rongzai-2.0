#!/usr/bin/env python3
"""Monitor chat sessions and record those unrelated to neutron topics.

Default behavior:
- Scan messages from the last 2 days in tmp/drsai_ui/drsai_ui.db.
- Consider only user TextMessage records.
- If no user message in a session matches neutron-related keywords, record it.
- Append one JSON report per run to an output JSONL file.
"""

from __future__ import annotations

import argparse
import json
import smtplib
import sqlite3
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.mime.text import MIMEText
from pathlib import Path
from typing import Any

# Includes Chinese and English neutron-domain terms.
NEUTRON_KEYWORDS = [
    "中子",
    "散裂",
    "衍射",
    "小角",
    "pdf",
    "rietveld",
    "gsas",
    "sasview",
    "neutron",
    "diffraction",
    "scattering",
    "refine",
    "refinement",
    "qmax",
    "gr",
]

USER_SOURCES = {"user", "user_proxy"}

# =========================
# User Configuration Region
# =========================
# 建议只改这里：邮箱配置、检测窗口、循环周期、输出路径等。
APP_CONFIG: dict[str, Any] = {
    # 数据源与扫描范围
    "db_path": "../.drsai_ui/drsai_ui.db",
    "window_days": 2,
    "output": "tmp/monitor/non_neutron_sessions.jsonl",
    "max_samples": 2,
    # 执行模式
    "loop": False,
    "interval_days": 2.0,
    # 邮件告警
    "email_to": ["wanghao96@ihep.ac.cn"],
    "email_subject_prefix": "[monitor_non_neutron_chats]",
    "email_dry_run": False,
    "email_max_sessions_in_alert": 20,
    # SMTP 参数（按你们邮件系统修改）
    "smtp_host": "smtp.ihep.ac.cn",
    "smtp_port": 465,
    "smtp_user": "wanghao96@ihep.ac.cn",
    "smtp_password": "wPWrfSUnRUx4s3cu",
    "smtp_from": "wanghao96@ihep.ac.cn",
    "smtp_ssl": True,
    "smtp_no_starttls": True,
    "smtp_timeout_sec": 90,
    "smtp_retry": 3,
}


@dataclass
class SessionAccumulator:
    user_id: str = ""
    start_at: str | None = None
    end_at: str | None = None
    user_messages: int = 0
    neutron_related: bool = False
    samples: list[str] = field(default_factory=list)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Record sessions unrelated to neutron topics")
    parser.add_argument(
        "--db-path",
        default=APP_CONFIG["db_path"],
        help="Path to sqlite database file",
    )
    parser.add_argument(
        "--window-days",
        type=int,
        default=APP_CONFIG["window_days"],
        help="Scan window in days (default: 2)",
    )
    parser.add_argument(
        "--output",
        default=APP_CONFIG["output"],
        help="Output JSONL path",
    )
    parser.add_argument(
        "--max-samples",
        type=int,
        default=APP_CONFIG["max_samples"],
        help="Max text samples per session in output",
    )
    parser.add_argument(
        "--email-to",
        action="append",
        default=list(APP_CONFIG["email_to"]),
        help="Alert recipients. Repeat this option or pass comma-separated emails",
    )
    parser.add_argument(
        "--smtp-host",
        default=APP_CONFIG["smtp_host"],
        help="SMTP host for email alerts",
    )
    parser.add_argument(
        "--smtp-port",
        type=int,
        default=APP_CONFIG["smtp_port"],
        help="SMTP port (default: 587)",
    )
    parser.add_argument(
        "--smtp-user",
        default=APP_CONFIG["smtp_user"],
        help="SMTP username (optional)",
    )
    parser.add_argument(
        "--smtp-password",
        default=APP_CONFIG["smtp_password"],
        help="SMTP password (optional)",
    )
    parser.add_argument(
        "--smtp-from",
        default=APP_CONFIG["smtp_from"],
        help="From email address for alert emails",
    )
    parser.add_argument(
        "--smtp-ssl",
        action=argparse.BooleanOptionalAction,
        default=APP_CONFIG["smtp_ssl"],
        help="Use SMTP over SSL (smtplib.SMTP_SSL)",
    )
    parser.add_argument(
        "--smtp-no-starttls",
        action=argparse.BooleanOptionalAction,
        default=APP_CONFIG["smtp_no_starttls"],
        help="Disable STARTTLS when using plain SMTP",
    )
    parser.add_argument(
        "--smtp-timeout-sec",
        type=int,
        default=APP_CONFIG["smtp_timeout_sec"],
        help="SMTP socket timeout in seconds",
    )
    parser.add_argument(
        "--smtp-retry",
        type=int,
        default=APP_CONFIG["smtp_retry"],
        help="SMTP send retries on transient failures",
    )
    parser.add_argument(
        "--email-subject-prefix",
        default=APP_CONFIG["email_subject_prefix"],
        help="Subject prefix for alert emails",
    )
    parser.add_argument(
        "--email-max-sessions-in-alert",
        type=int,
        default=APP_CONFIG["email_max_sessions_in_alert"],
        help="Max number of sessions included in alert email body",
    )
    parser.add_argument(
        "--email-dry-run",
        action="store_true",
        default=APP_CONFIG["email_dry_run"],
        help="Do not send email; print the email body preview instead",
    )
    parser.add_argument(
        "--loop",
        action="store_true",
        default=APP_CONFIG["loop"],
        help="Run continuously with a fixed interval",
    )
    parser.add_argument(
        "--interval-days",
        type=float,
        default=APP_CONFIG["interval_days"],
        help="Interval days between scans when --loop is enabled (default: 2)",
    )
    return parser.parse_args()


def try_parse_json(text: str) -> Any:
    try:
        return json.loads(text)
    except Exception:
        return None


def extract_text(content: Any) -> str:
    """Extract plain text from message content that can be nested JSON."""
    if content is None:
        return ""

    if isinstance(content, str):
        stripped = content.strip()
        if stripped.startswith("{") or stripped.startswith("["):
            parsed = try_parse_json(stripped)
            if parsed is not None:
                return extract_text(parsed)
        return stripped

    if isinstance(content, dict):
        for key in ("content", "text", "message", "title"):
            if key in content:
                text = extract_text(content[key])
                if text:
                    return text
        parts = content.get("parts")
        if isinstance(parts, list):
            joined = " ".join(extract_text(p) for p in parts)
            return joined.strip()
        return ""

    if isinstance(content, list):
        joined = " ".join(extract_text(item) for item in content)
        return joined.strip()

    return str(content).strip()


def contains_neutron_keyword(text: str) -> bool:
    low = text.lower()
    return any(keyword in low for keyword in NEUTRON_KEYWORDS)


def normalize_preview(text: str, max_len: int = 160) -> str:
    clean = " ".join(text.split())
    if len(clean) <= max_len:
        return clean
    return clean[: max_len - 3] + "..."


def fetch_recent_text_messages(conn: sqlite3.Connection, cutoff: datetime) -> list[tuple[Any, ...]]:
    cutoff_str = cutoff.strftime("%Y-%m-%d %H:%M:%S")
    query = """
    SELECT
        m.id,
        m.created_at,
        s.uuid AS session_uuid,
        s.user_id,
        m.config
    FROM message AS m
    JOIN session AS s ON s.id = m.session_id
    WHERE m.created_at >= ?
    ORDER BY m.created_at ASC, m.id ASC
    """
    cur = conn.cursor()
    cur.execute(query, (cutoff_str,))
    return cur.fetchall()


def parse_recipients(values: list[str]) -> list[str]:
    recipients: list[str] = []
    for item in values:
        parts = [p.strip() for p in item.split(",")]
        recipients.extend([p for p in parts if p])
    unique: list[str] = []
    seen = set()
    for addr in recipients:
        low = addr.lower()
        if low in seen:
            continue
        seen.add(low)
        unique.append(addr)
    return unique


def build_report(rows: list[tuple[Any, ...]], max_samples: int, window_days: int, db_path: str) -> dict[str, Any]:
    sessions: dict[str, SessionAccumulator] = defaultdict(SessionAccumulator)
    parsed_user_messages = 0

    for _msg_id, created_at, session_uuid, user_id, raw_config in rows:
        if not raw_config:
            continue

        config = try_parse_json(raw_config)
        if not isinstance(config, dict):
            continue

        source = str(config.get("source", "")).strip()
        msg_type = str(config.get("type", "")).strip()
        if msg_type != "TextMessage":
            continue
        if source not in USER_SOURCES:
            continue

        text = extract_text(config.get("content"))
        if not text:
            continue

        acc = sessions[session_uuid]
        acc.user_id = user_id or acc.user_id
        acc.start_at = created_at if acc.start_at is None else min(acc.start_at, created_at)
        acc.end_at = created_at if acc.end_at is None else max(acc.end_at, created_at)
        acc.user_messages += 1

        if contains_neutron_keyword(text):
            acc.neutron_related = True

        if len(acc.samples) < max_samples:
            acc.samples.append(f"[{source}] {normalize_preview(text)}")

        parsed_user_messages += 1

    non_neutron_sessions = []
    for session_uuid, acc in sessions.items():
        if acc.user_messages == 0:
            continue
        if acc.neutron_related:
            continue
        non_neutron_sessions.append(
            {
                "session_uuid": session_uuid,
                "user_id": acc.user_id,
                "start_at": acc.start_at,
                "end_at": acc.end_at,
                "checked_user_messages": acc.user_messages,
                "samples": acc.samples,
            }
        )

    non_neutron_sessions.sort(key=lambda item: (item["start_at"] or "", item["session_uuid"]))

    return {
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "window_days": window_days,
        "db_path": str(db_path),
        "scanned_rows": len(rows),
        "parsed_user_text_messages": parsed_user_messages,
        "scanned_sessions": len(sessions),
        "non_neutron_sessions_count": len(non_neutron_sessions),
        "non_neutron_sessions": non_neutron_sessions,
    }


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(payload, ensure_ascii=False) + "\n")


def build_email_body(report: dict[str, Any], recipients: list[str], max_sessions: int) -> str:
    lines = [
        "发现非中子会话告警",
        "",
        f"运行时间: {report['run_at']}",
        f"扫描窗口(天): {report['window_days']}",
        f"数据库: {report['db_path']}",
        f"扫描原始行数: {report['scanned_rows']}",
        f"解析用户文本条数: {report['parsed_user_text_messages']}",
        f"扫描会话数(按用户消息): {report['scanned_sessions']}",
        f"命中非中子会话数: {report['non_neutron_sessions_count']}",
        f"收件人: {', '.join(recipients)}",
        "",
    ]

    included = report["non_neutron_sessions"][: max(max_sessions, 1)]
    for idx, item in enumerate(included, start=1):
        lines.append(f"[{idx}] session={item['session_uuid']} user={item['user_id']}")
        lines.append(f"    start={item['start_at']} end={item['end_at']}")
        lines.append(f"    checked_user_messages={item['checked_user_messages']}")
        for sample in item.get("samples", []):
            lines.append(f"    sample={sample}")
        lines.append("")

    omitted = len(report["non_neutron_sessions"]) - len(included)
    if omitted > 0:
        lines.append(f"... 其余 {omitted} 个会话已省略")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"


def send_alert_email(args: argparse.Namespace, report: dict[str, Any]) -> None:
    recipients = parse_recipients(args.email_to)
    if not recipients:
        return
    if report["non_neutron_sessions_count"] <= 0:
        return

    body = build_email_body(report, recipients, args.email_max_sessions_in_alert)
    subject = (
        f"{args.email_subject_prefix} "
        f"non-neutron sessions: {report['non_neutron_sessions_count']}"
    )

    if args.email_dry_run:
        print("email_dry_run=on")
        print(f"email_to={','.join(recipients)}")
        print(f"email_subject={subject}")
        print("email_body_preview=")
        print(body[:1200])
        return

    if not args.smtp_host:
        print("email_skipped=missing_smtp_host")
        return

    from_addr = args.smtp_from or args.smtp_user
    if not from_addr:
        print("email_skipped=missing_smtp_from")
        return

    msg = MIMEText(body, _subtype="plain", _charset="utf-8")
    msg["Subject"] = subject
    msg["From"] = from_addr
    msg["To"] = ", ".join(recipients)

    retries = max(args.smtp_retry, 1)
    timeout_sec = max(args.smtp_timeout_sec, 10)
    for attempt in range(1, retries + 1):
        try:
            if args.smtp_ssl:
                server = smtplib.SMTP_SSL(args.smtp_host, args.smtp_port, timeout=timeout_sec)
            else:
                server = smtplib.SMTP(args.smtp_host, args.smtp_port, timeout=timeout_sec)

            with server:
                if (not args.smtp_ssl) and (not args.smtp_no_starttls):
                    server.starttls()
                if args.smtp_user:
                    server.login(args.smtp_user, args.smtp_password)
                server.sendmail(from_addr, recipients, msg.as_string())

            print(f"email_sent=1 recipients={len(recipients)} attempt={attempt}")
            return
        except (smtplib.SMTPException, TimeoutError, OSError) as e:
            # 邮件是附加能力，失败时不应阻断主监控流程。
            print(f"email_send_failed attempt={attempt}/{retries} {type(e).__name__}: {e}")
            if attempt < retries:
                time.sleep(2)


def run_once(args: argparse.Namespace) -> dict[str, Any]:
    db_path = Path(args.db_path)
    if not db_path.exists():
        raise FileNotFoundError(f"Database file not found: {db_path}")

    cutoff = datetime.now() - timedelta(days=args.window_days)
    conn = sqlite3.connect(db_path)
    try:
        rows = fetch_recent_text_messages(conn, cutoff)
    finally:
        conn.close()

    report = build_report(rows, args.max_samples, args.window_days, str(db_path))
    append_jsonl(Path(args.output), report)
    send_alert_email(args, report)

    print(f"run_at={report['run_at']}")
    print(f"window_days={report['window_days']}")
    print(f"scanned_rows={report['scanned_rows']}")
    print(f"parsed_user_text_messages={report['parsed_user_text_messages']}")
    print(f"scanned_sessions={report['scanned_sessions']}")
    print(f"non_neutron_sessions_count={report['non_neutron_sessions_count']}")
    print(f"output={args.output}\n")

    return report


def main() -> int:
    args = parse_args()

    if not args.loop:
        run_once(args)
        return 0

    interval_seconds = max(args.interval_days, 0.01) * 24 * 3600
    print(
        f"loop_mode=on interval_days={args.interval_days} "
        f"window_days={args.window_days} db={args.db_path} output={args.output}"
    )

    while True:
        run_once(args)
        time.sleep(interval_seconds)

    return 0



"""
推荐用法（先改 APP_CONFIG，再直接运行）

1) 单次执行（使用 APP_CONFIG）
cd /home/wangh/rongzai-agent && python monitor_non_neutron_chats.py

2) 切换为真实发信
- 在 APP_CONFIG 中设置:
    - email_dry_run = False
    - smtp_host / smtp_port / smtp_user / smtp_password / smtp_from

3) 常驻每2天执行一次（使用 APP_CONFIG）
cd /home/wangh/rongzai-agent && nohup python monitor_non_neutron_chats.py --loop >> tmp/monitor/runner.log 2>&1 &

"""

if __name__ == "__main__":
    raise SystemExit(main())
