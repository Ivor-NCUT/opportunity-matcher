# ## 核心功能
# 编排招聘方合作邮件同步、候选人触达草稿、兴趣确认、简历转发草稿和跟进提醒。
# ## 输入
# 飞书邮箱邮件摘要/正文、本地候选人库、招聘需求 ID、候选人回复文本和飞书群机器人 webhook。
# ## 输出
# 招聘需求记录、飞书邮箱草稿、候选人触达状态、4 天跟进提醒和审计日志。
# ## 定位
# 招聘方合作流程层，隔离外部 `lark-cli` 和 webhook 调用，不改变旧候选人闭环。
# ## 依赖
# `db`、`matcher`、`email_templates`、Python 标准库 `subprocess`、`urllib`。
# ## 维护规则
# 邮件标题协议、外部命令参数、触达状态或提醒规则变化时，同步更新 CLI 文档和测试。

from __future__ import annotations

import json
import os
import re
import sqlite3
import subprocess
import urllib.request
from collections.abc import Callable
from datetime import datetime, timedelta, timezone
from typing import Any

from .db import (
    create_candidate_outreach_record,
    fetch_all,
    log_event,
    mark_followup_failed,
    mark_followup_sent,
    mark_outreach_interested,
    set_outreach_forward_draft,
    upsert_client,
    upsert_followup,
    upsert_job,
    upsert_recruiter,
    upsert_recruiting_request,
)
from .email_templates import candidate_outreach_body, candidate_outreach_subject, followup_message, recruiter_forward_body, recruiter_forward_subject
from .matcher import match_job_candidates


ENTRY_EMAIL = "fanhan@aimanziyi.vip"
RECRUITING_SUBJECT_PREFIX = "招聘合作"
WEBHOOK_ENV = "OPPORTUNITY_MATCHER_FEISHU_BOT_WEBHOOK"
CommandRunner = Callable[[list[str]], str]
WebhookPoster = Callable[[str, str], None]


def run_command(command: list[str]) -> str:
    completed = subprocess.run(command, check=True, text=True, capture_output=True)
    return completed.stdout


def parse_recruiting_subject(subject: str) -> tuple[str, str] | None:
    parts = [part.strip() for part in subject.split("｜")]
    if len(parts) != 3 or parts[0] != RECRUITING_SUBJECT_PREFIX:
        return None
    recruiter_name, company = parts[1], parts[2]
    if not recruiter_name or not company:
        return None
    return recruiter_name, company


def sync_recruiting_mails(
    conn: sqlite3.Connection,
    query: str = RECRUITING_SUBJECT_PREFIX,
    max_messages: int = 100,
    mailbox: str = "me",
    runner: CommandRunner = run_command,
) -> dict[str, int]:
    triage = runner(
        [
            "lark-cli",
            "mail",
            "+triage",
            "--as",
            "user",
            "--query",
            query,
            "--max",
            str(max_messages),
            "--format",
            "json",
            "--mailbox",
            mailbox,
        ]
    )
    summaries = extract_mail_items(json.loads(triage or "[]"))
    message_ids = [message_id_for(item) for item in summaries if message_id_for(item)]
    if not message_ids:
        return {"seen": 0, "parsed": 0, "needs_review": 0}

    details_raw = runner(
        [
            "lark-cli",
            "mail",
            "+messages",
            "--as",
            "user",
            "--message-ids",
            ",".join(message_ids),
            "--html=false",
            "--format",
            "json",
            "--mailbox",
            mailbox,
        ]
    )
    details_by_id = {message_id_for(item): item for item in extract_mail_items(json.loads(details_raw or "[]")) if message_id_for(item)}

    counts = {"seen": 0, "parsed": 0, "needs_review": 0}
    for summary in summaries:
        message_id = message_id_for(summary)
        if not message_id:
            continue
        detail = details_by_id.get(message_id, summary)
        status = import_recruiting_mail(conn, {**summary, **detail, "message_id": message_id})
        counts["seen"] += 1
        counts[status] += 1
    return counts


def import_recruiting_mail(conn: sqlite3.Connection, message: dict[str, Any]) -> str:
    subject = text_value(first_present(message, "subject", "title"))
    message_id = text_value(first_present(message, "message_id", "id", "mail_id"))
    sender_email = extract_email(text_value(first_present(message, "from", "sender", "from_email", "sender_email")))
    parsed = parse_recruiting_subject(subject)
    body = text_value(first_present(message, "body", "text", "plain_text", "content", "body_text"))

    if not parsed:
        upsert_recruiting_request(
            conn,
            {
                "source_email_id": message_id,
                "source_subject": subject,
                "sender_email": sender_email,
                "status": "needs_review",
                "error_text": "邮件标题不符合：招聘合作｜你的姓名 & 昵称｜公司名称",
                "raw": message,
            },
        )
        return "needs_review"

    recruiter_name, company = parsed
    if not body.strip():
        upsert_recruiting_request(
            conn,
            {
                "source_email_id": message_id,
                "source_subject": subject,
                "sender_email": sender_email,
                "recruiter_name": recruiter_name,
                "company": company,
                "status": "needs_review",
                "error_text": "邮件正文为空，无法提取 JD 和招人偏好",
                "raw": message,
            },
        )
        return "needs_review"

    client_id = upsert_client(
        conn,
        {
            "name": company,
            "contact_name": recruiter_name,
            "contact_email": sender_email,
            "source": "recruiting_email",
            "notes": f"来源邮件：{subject}",
            "raw": message,
        },
    )
    job_id = upsert_job(
        conn,
        {
            "company": company,
            "title": infer_job_title(body),
            "description": body,
            "required_skills": infer_request_skills(body),
            "preferred_skills": [],
            "source_url": f"mail:{message_id}",
            "source_record_id": message_id,
            "raw": message,
            "status": "open",
        },
    )
    recruiter_id = upsert_recruiter(
        conn,
        {
            "name": recruiter_name,
            "email": sender_email or f"unknown-{message_id}@example.invalid",
            "company": company,
            "whitelisted": True,
            "job_ids": [job_id],
        },
    )
    upsert_recruiting_request(
        conn,
        {
            "source_email_id": message_id,
            "source_subject": subject,
            "sender_email": sender_email,
            "recruiter_name": recruiter_name,
            "company": company,
            "jd_text": body,
            "preference_text": body,
            "client_id": client_id,
            "recruiter_id": recruiter_id,
            "job_id": job_id,
            "status": "parsed",
            "raw": message,
        },
    )
    log_event(conn, "recruiting_request_imported", None, job_id, recruiter_id, message_id, subject)
    conn.commit()
    return "parsed"


def draft_candidate_outreach(
    conn: sqlite3.Connection,
    request_id: int,
    limit: int = 3,
    mailbox: str = "me",
    runner: CommandRunner = run_command,
) -> list[int]:
    request = conn.execute("SELECT * FROM recruiting_requests WHERE id = ?", (request_id,)).fetchone()
    if not request:
        raise ValueError(f"Recruiting request not found: {request_id}")
    if request["status"] != "parsed" or not request["job_id"]:
        raise ValueError(f"Recruiting request is not ready for outreach: {request_id}")

    drafted: list[int] = []
    for candidate, result in match_job_candidates(conn, int(request["job_id"]), limit=limit):
        existing = conn.execute(
            "SELECT id FROM candidate_outreach WHERE request_id = ? AND candidate_id = ? AND candidate_draft_id != ''",
            (request_id, int(candidate["id"])),
        ).fetchone()
        if existing:
            continue
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
        subject = candidate_outreach_subject(job)
        body = candidate_outreach_body(candidate, job, result.reason)
        try:
            output = runner(
                [
                    "lark-cli",
                    "mail",
                    "+send",
                    "--as",
                    "user",
                    "--mailbox",
                    mailbox,
                    "--to",
                    candidate["email"],
                    "--subject",
                    subject,
                    "--body",
                    body,
                    "--plain-text",
                    "--format",
                    "json",
                ]
            )
            draft_id = draft_id_from_output(output)
            status, error = "drafted", ""
        except (subprocess.CalledProcessError, OSError) as exc:
            draft_id = ""
            status, error = "draft_failed", str(exc)
        outreach_id = create_candidate_outreach_record(
            conn,
            request_id,
            int(candidate["id"]),
            int(request["job_id"]),
            int(request["recruiter_id"]) if request["recruiter_id"] else None,
            draft_id,
            status,
            error,
        )
        log_event(conn, "candidate_outreach_drafted", int(candidate["id"]), int(request["job_id"]), request["recruiter_id"], request["source_email_id"], result.reason)
        drafted.append(outreach_id)
    return drafted


def review_interest(
    conn: sqlite3.Connection,
    outreach_id: int | None = None,
    reply_text: str = "",
    source_reply_email_id: str = "",
) -> list[dict[str, Any]]:
    if outreach_id is not None:
        conn.execute(
            """
            UPDATE candidate_outreach
            SET reply_text = ?, source_reply_email_id = ?, interest_status = 'needs_manual_review',
                status = 'needs_manual_review', updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (reply_text, source_reply_email_id, outreach_id),
        )
        conn.commit()
    rows = fetch_all(
        conn,
        """
        SELECT candidate_outreach.id, candidate_outreach.reply_text, candidates.name AS candidate_name,
               jobs.company, jobs.title
        FROM candidate_outreach
        JOIN candidates ON candidates.id = candidate_outreach.candidate_id
        LEFT JOIN jobs ON jobs.id = candidate_outreach.job_id
        WHERE candidate_outreach.interest_status = 'needs_manual_review'
        ORDER BY candidate_outreach.updated_at DESC, candidate_outreach.id
        """,
    )
    return [dict(row) for row in rows]


def mark_interested_and_draft_forward(
    conn: sqlite3.Connection,
    outreach_id: int,
    reply_text: str = "",
    source_reply_email_id: str = "",
    mailbox: str = "me",
    runner: CommandRunner = run_command,
    now: datetime | None = None,
) -> int:
    row = conn.execute(
        """
        SELECT candidate_outreach.*, candidates.source_email_id AS candidate_source_email_id
        FROM candidate_outreach
        JOIN candidates ON candidates.id = candidate_outreach.candidate_id
        WHERE candidate_outreach.id = ?
        """,
        (outreach_id,),
    ).fetchone()
    if not row:
        raise ValueError(f"Candidate outreach not found: {outreach_id}")

    mark_outreach_interested(conn, outreach_id, reply_text, source_reply_email_id)
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (row["candidate_id"],)).fetchone()
    job = conn.execute("SELECT * FROM jobs WHERE id = ?", (row["job_id"],)).fetchone()
    recruiter = conn.execute("SELECT * FROM recruiters WHERE id = ?", (row["recruiter_id"],)).fetchone() if row["recruiter_id"] else None
    reason = match_reason_for(conn, int(row["candidate_id"]), int(row["job_id"]))
    subject = recruiter_forward_subject(candidate, job)
    body = recruiter_forward_body(candidate, job, reason)
    recipient = recruiter["email"] if recruiter else ""
    if not recipient:
        raise ValueError("Recruiter email is required to draft forward.")

    try:
        if candidate["source_email_id"]:
            command = [
                "lark-cli",
                "mail",
                "+forward",
                "--as",
                "user",
                "--mailbox",
                mailbox,
                "--message-id",
                candidate["source_email_id"],
                "--to",
                recipient,
                "--subject",
                subject,
                "--body",
                body,
                "--plain-text",
                "--format",
                "json",
            ]
        else:
            command = [
                "lark-cli",
                "mail",
                "+send",
                "--as",
                "user",
                "--mailbox",
                mailbox,
                "--to",
                recipient,
                "--subject",
                subject,
                "--body",
                body,
                "--plain-text",
                "--format",
                "json",
            ]
        output = runner(command)
        draft_id = draft_id_from_output(output)
        error = ""
    except (subprocess.CalledProcessError, OSError) as exc:
        draft_id = ""
        error = str(exc)

    set_outreach_forward_draft(conn, outreach_id, draft_id, error)
    due_at = ((now or datetime.now(timezone.utc)) + timedelta(days=4)).replace(microsecond=0).isoformat()
    message = followup_message(candidate, job, recruiter)
    followup_id = upsert_followup(conn, outreach_id, int(candidate["id"]), int(job["id"]), int(recruiter["id"]) if recruiter else None, due_at, message)
    log_event(conn, "recruiter_forward_drafted", int(candidate["id"]), int(job["id"]), int(recruiter["id"]) if recruiter else None, candidate["source_email_id"], f"draft={draft_id}; followup={followup_id}")
    return followup_id


def send_due_followups(
    conn: sqlite3.Connection,
    webhook_url: str | None = None,
    now: datetime | None = None,
    poster: WebhookPoster | None = None,
) -> int:
    url = webhook_url or os.environ.get(WEBHOOK_ENV, "")
    if not url:
        raise ValueError(f"Missing webhook URL. Set {WEBHOOK_ENV}.")
    current = (now or datetime.now(timezone.utc)).replace(microsecond=0).isoformat()
    rows = fetch_all(conn, "SELECT * FROM followups WHERE status = 'pending' AND due_at <= ? ORDER BY due_at, id", (current,))
    count = 0
    for row in rows:
        try:
            (poster or post_feishu_bot)(url, row["message"])
            mark_followup_sent(conn, int(row["id"]), current)
            count += 1
        except Exception as exc:  # pragma: no cover - tested through injected poster.
            mark_followup_failed(conn, int(row["id"]), str(exc))
    return count


def post_feishu_bot(webhook_url: str, message: str) -> None:
    payload = json.dumps({"msg_type": "text", "content": {"text": message}}, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(request, timeout=10) as response:
        if response.status >= 400:
            raise RuntimeError(f"Webhook failed: HTTP {response.status}")


def extract_mail_items(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [item for item in payload if isinstance(item, dict)]
    if not isinstance(payload, dict):
        return []
    for key in ("items", "messages", "message_list", "data"):
        value = payload.get(key)
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        if isinstance(value, dict):
            nested = extract_mail_items(value)
            if nested:
                return nested
    return [payload] if message_id_for(payload) else []


def message_id_for(item: dict[str, Any]) -> str:
    return text_value(first_present(item, "message_id", "id", "mail_id"))


def first_present(item: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in item and item[key] not in (None, ""):
            return item[key]
    return ""


def text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("email", "address", "text", "name", "content"):
            if value.get(key):
                return text_value(value[key])
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, list):
        return "；".join(text_value(item) for item in value if text_value(item))
    return str(value).strip()


def extract_email(value: str) -> str:
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
    return match.group(0).lower() if match else ""


def infer_job_title(body: str) -> str:
    for line in body.splitlines():
        clean = line.strip(" -:\t")
        if not clean:
            continue
        if "岗位" in clean or "职位" in clean or "JD" in clean.upper():
            pieces = re.split(r"[:：]", clean, maxsplit=1)
            return (pieces[-1] if len(pieces) > 1 else clean)[:80]
    return "招聘合作岗位"


def infer_request_skills(body: str) -> list[str]:
    lower = body.lower()
    mapping = {
        "ai operations": ["ai运营", "ai 运营", "ai-native", "ai native", "ai工具", "ai 工具"],
        "ai agent": ["agent", "智能体", "cursor", "claude code"],
        "customer success": ["客户成功", "用户成功", "onboarding", "客户"],
        "content operations": ["内容", "社媒", "小红书", "公众号", "品牌", "传播", "社区"],
        "growth": ["增长", "获客", "转化", "growth"],
        "sales": ["销售", "商务", "bd", "商业化"],
        "design": ["设计", "figma", "ui", "交互"],
        "frontend": ["前端", "react", "vue", "typescript"],
        "backend": ["后端", "python", "node.js", "golang", "docker"],
    }
    return [skill for skill, keywords in mapping.items() if any(keyword.lower() in lower for keyword in keywords)]


def draft_id_from_output(output: str) -> str:
    if not output.strip():
        return ""
    try:
        payload = json.loads(output)
    except json.JSONDecodeError:
        return output.strip().splitlines()[-1]
    return text_value(find_key(payload, {"draft_id", "id", "message_id"}))


def find_key(value: Any, keys: set[str]) -> Any:
    if isinstance(value, dict):
        for key in keys:
            if key in value and value[key]:
                return value[key]
        for item in value.values():
            found = find_key(item, keys)
            if found:
                return found
    if isinstance(value, list):
        for item in value:
            found = find_key(item, keys)
            if found:
                return found
    return ""


def match_reason_for(conn: sqlite3.Connection, candidate_id: int, job_id: int) -> str:
    rows = match_job_candidates(conn, job_id, limit=100)
    for candidate, result in rows:
        if int(candidate["id"]) == candidate_id:
            return result.reason
    row = conn.execute("SELECT reason FROM matches WHERE candidate_id = ? AND job_id = ? ORDER BY id DESC LIMIT 1", (candidate_id, job_id)).fetchone()
    return row["reason"] if row else "候选人已确认对该岗位感兴趣"
