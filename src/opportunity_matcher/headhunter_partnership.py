# ## 核心功能
# 将候选人简历按猎头合作伙伴自动推送，并记录发送幂等状态。
# ## 输入
# SQLite 猎头库、候选人记录、候选人排除列表和 lark-cli 执行器。
# ## 输出
# 发送/草稿/跳过/失败统计，以及 candidate_forwards 审计记录。
# ## 定位
# 猎头合作推送流程层，供 CLI 和邮箱入库自动化调用。
# ## 依赖
# `db`、`recruiting_workflow`、Python 标准库 `pathlib`、`sqlite3`。
# ## 维护规则
# 发送正文、附件选择、幂等状态或外部命令参数变化时，同步更新 CLI 文档和测试。

from __future__ import annotations

import re
import sqlite3
import subprocess
from pathlib import Path
from typing import Any

from .db import active_headhunters, candidate_forward_exists, log_event, upsert_candidate_forward
from .recruiting_workflow import CommandRunner, draft_id_from_output, run_command


DEFAULT_HEADHUNTER_BODY = "你好，我是泛函，擅长用流量手段获得优质候选人线索，这是我手里不错的候选人，请查收"


def forward_candidates_to_headhunters(
    conn: sqlite3.Connection,
    exclude_candidate_ids: set[int] | None = None,
    candidate_ids: list[int] | None = None,
    recipient_email: str = "",
    recipient_name: str = "",
    body: str = DEFAULT_HEADHUNTER_BODY,
    mailbox: str = "me",
    confirm_send: bool = False,
    runner: CommandRunner = run_command,
    cwd: Path | None = None,
) -> dict[str, Any]:
    rows = candidate_rows(conn, candidate_ids, exclude_candidate_ids or set())
    partners = target_headhunters(conn, recipient_email, recipient_name)
    result: dict[str, Any] = {"seen": len(rows), "partners": len(partners), "sent": 0, "drafted": 0, "skipped": [], "failed": []}
    for partner in partners:
        for candidate in rows:
            item = forward_candidate_to_recipient(
                conn,
                candidate,
                partner=partner,
                body=body,
                mailbox=mailbox,
                confirm_send=confirm_send,
                runner=runner,
                cwd=cwd or Path.cwd(),
            )
            if item["status"] == "sent":
                result["sent"] += 1
            elif item["status"] == "drafted":
                result["drafted"] += 1
            elif item["status"] == "skipped":
                result["skipped"].append(item)
            else:
                result["failed"].append(item)
    return result


def forward_candidate_to_recipient(
    conn: sqlite3.Connection,
    candidate: sqlite3.Row,
    partner: dict[str, Any],
    body: str,
    mailbox: str,
    confirm_send: bool,
    runner: CommandRunner,
    cwd: Path,
) -> dict[str, Any]:
    candidate_id = int(candidate["id"])
    recipient_email = partner["contact_email"].strip().lower()
    recipient_name = partner.get("contact_name", "")
    if candidate_forward_exists(conn, candidate_id, recipient_email):
        return {"candidate_id": candidate_id, "name": candidate["name"], "headhunter": partner["name"], "status": "skipped", "reason": "already_sent"}

    attachments = attachment_paths(candidate["resume_uri"], cwd)
    if not attachments:
        upsert_candidate_forward(
            conn,
            {
                "candidate_id": candidate_id,
                "recipient_email": recipient_email,
                "recipient_name": recipient_name,
                "headhunter_id": partner.get("id"),
                "client_id": partner.get("client_id"),
                "subject": forward_subject(candidate),
                "body": body,
                "attachment_paths": [],
                "status": "skipped",
                "error_text": "no_existing_resume_attachment",
            },
        )
        return {"candidate_id": candidate_id, "name": candidate["name"], "headhunter": partner["name"], "status": "skipped", "reason": "no_existing_resume_attachment"}

    subject = forward_subject(candidate)
    command = [
        "lark-cli",
        "mail",
        "+send",
        "--as",
        "user",
        "--mailbox",
        mailbox,
        "--to",
        recipient_email,
        "--subject",
        subject,
        "--body",
        body,
        "--plain-text",
        "--attach",
        ",".join(attachments),
        "--format",
        "json",
    ]
    if confirm_send:
        command.append("--confirm-send")

    try:
        output = runner(command)
        message_id = draft_id_from_output(output)
        status = "sent" if confirm_send else "drafted"
        error = ""
    except (subprocess.CalledProcessError, OSError) as exc:
        message_id = ""
        status = "failed"
        stderr = getattr(exc, "stderr", "") or ""
        error = (stderr or str(exc)).strip()

    forward_id = upsert_candidate_forward(
        conn,
        {
            "candidate_id": candidate_id,
            "recipient_email": recipient_email,
            "recipient_name": recipient_name,
            "headhunter_id": partner.get("id"),
            "client_id": partner.get("client_id"),
            "subject": subject,
            "body": body,
            "attachment_paths": attachments,
            "message_id": message_id,
            "status": status,
            "error_text": error,
        },
    )
    log_event(conn, f"candidate_forward_{status}", candidate_id, None, None, message_id, f"headhunter={partner['name']}; to={recipient_email}; forward_id={forward_id}")
    conn.commit()
    return {"candidate_id": candidate_id, "name": candidate["name"], "headhunter": partner["name"], "status": status, "message_id": message_id, "attachments": attachments, "error": error}


def candidate_rows(conn: sqlite3.Connection, candidate_ids: list[int] | None, exclude_candidate_ids: set[int]) -> list[sqlite3.Row]:
    if candidate_ids:
        placeholders = ",".join("?" for _ in candidate_ids)
        rows = conn.execute(f"SELECT * FROM candidates WHERE id IN ({placeholders}) ORDER BY id", tuple(candidate_ids)).fetchall()
        return [row for row in rows if int(row["id"]) not in exclude_candidate_ids]
    return conn.execute(
        """
        SELECT * FROM candidates
        WHERE id NOT IN (%s)
        ORDER BY updated_at DESC, id
        """
        % ",".join("?" for _ in exclude_candidate_ids),
        tuple(exclude_candidate_ids),
    ).fetchall() if exclude_candidate_ids else conn.execute("SELECT * FROM candidates ORDER BY updated_at DESC, id").fetchall()


def target_headhunters(conn: sqlite3.Connection, recipient_email: str = "", recipient_name: str = "") -> list[dict[str, Any]]:
    if recipient_email:
        return [
            {
                "id": None,
                "name": recipient_name or recipient_email,
                "contact_name": recipient_name,
                "contact_email": recipient_email,
                "client_id": None,
            }
        ]
    return [dict(row) for row in active_headhunters(conn)]


def forward_subject(candidate: sqlite3.Row) -> str:
    name = candidate["name"] or candidate["email"] or f"candidate-{candidate['id']}"
    return f"候选人简历推荐 - {name}"


def attachment_paths(value: str, cwd: Path) -> list[str]:
    paths: list[str] = []
    for raw in re.split(r"\s*[;；]\s*", value or ""):
        item = raw.strip()
        if not item or "://" in item:
            continue
        path = Path(item)
        if not path.is_absolute():
            path = cwd / path
        try:
            resolved = path.resolve()
            relative = resolved.relative_to(cwd.resolve())
        except ValueError:
            continue
        if resolved.is_file():
            paths.append(str(relative))
    resume_like = [item for item in paths if is_resume_attachment(item)]
    return resume_like or paths


def is_resume_attachment(path: str) -> bool:
    name = Path(path).name.lower()
    return any(token in name for token in ("简历", "resume", "cv"))
