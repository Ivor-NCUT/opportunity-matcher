# ## 核心功能
# 从飞书邮箱读取招聘相关新邮件，分类导入本地 SQLite，并下载/抽取候选人附件文本。
# ## 输入
# 飞书邮箱摘要/正文/附件元数据、本地附件目录、SQLite 连接。
# ## 输出
# 客户/职位/招聘请求/候选人入库结果、附件清单、待复核项和 JSON 摘要。
# ## 定位
# 每日邮箱巡检入库流程层，供 CLI 和定时任务调用，不发送、不删除、不移动邮件。
# ## 依赖
# `db`、`recruiting_workflow`、Python 标准库 `subprocess`、`urllib`、`zipfile`。
# ## 维护规则
# 邮箱搜索关键词、附件抽文策略或入库摘要字段变化时，同步更新 CLI 文档和测试。

from __future__ import annotations

import html
import json
import re
import sqlite3
import subprocess
import urllib.request
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any
from xml.etree import ElementTree

from .db import log_event, upsert_candidate, upsert_mail_ingestion_item
from .lark_importer import infer_skills
from .recruiting_workflow import (
    CommandRunner,
    extract_email,
    extract_mail_items,
    first_present,
    import_recruiting_mail,
    message_id_for,
    run_command,
    sync_recruiting_mails,
    text_value,
)
from .workflow import process_pending


CANDIDATE_QUERIES = ["简历", "resume", "投递", "求职", "CV", "应聘", "候选人", "作品集"]
RECRUITING_QUERY = "招聘合作"
Downloader = Callable[[str, Path], None]
TextExtractor = Callable[[Path], str]


def sync_mail_inbox(
    conn: sqlite3.Connection,
    mailbox: str = "me",
    max_messages: int = 100,
    candidate_queries: list[str] | None = None,
    attachment_dir: str | Path = "data/mail_attachments",
    download_attachments: bool = True,
    extract_text: bool = True,
    run_pending: bool = True,
    runner: CommandRunner = run_command,
    downloader: Downloader | None = None,
    text_extractor: TextExtractor | None = None,
) -> dict[str, Any]:
    before = table_counts(conn)
    recruiting = sync_recruiting_mails(conn, query=RECRUITING_QUERY, max_messages=max_messages, mailbox=mailbox, runner=runner)
    candidate_result = sync_candidate_mails(
        conn,
        mailbox=mailbox,
        max_messages=max_messages,
        queries=candidate_queries or CANDIDATE_QUERIES,
        attachment_dir=Path(attachment_dir),
        download_attachments=download_attachments,
        extract_text=extract_text,
        runner=runner,
        downloader=downloader or download_url_to_file,
        text_extractor=text_extractor or extract_text_from_file,
    )
    processed = process_pending(conn) if run_pending else 0
    after = table_counts(conn)
    return {
        "recruiting": recruiting,
        "candidates": candidate_result,
        "processed_pending_candidates": processed,
        "new_records": {
            "clients": after["clients"] - before["clients"],
            "jobs": after["jobs"] - before["jobs"],
            "recruiting_requests": after["recruiting_requests"] - before["recruiting_requests"],
            "candidates": after["candidates"] - before["candidates"],
        },
    }


def sync_candidate_mails(
    conn: sqlite3.Connection,
    mailbox: str,
    max_messages: int,
    queries: list[str],
    attachment_dir: Path,
    download_attachments: bool,
    extract_text: bool,
    runner: CommandRunner,
    downloader: Downloader,
    text_extractor: TextExtractor,
) -> dict[str, Any]:
    summaries = collect_candidate_summaries(mailbox, max_messages, queries, runner)
    message_ids = [message_id_for(item) for item in summaries if message_id_for(item)]
    details = fetch_message_details(mailbox, message_ids, runner)
    result: dict[str, Any] = {
        "queries": queries,
        "seen": len(message_ids),
        "created": 0,
        "updated": 0,
        "duplicates": 0,
        "needs_review": [],
        "attachments_downloaded": 0,
        "attachment_errors": [],
        "text_extraction_errors": [],
    }

    for summary in summaries:
        message_id = message_id_for(summary)
        if not message_id:
            continue
        message = {**summary, **details.get(message_id, {}), "message_id": message_id}
        subject = text_value(first_present(message, "subject", "title"))
        if subject.startswith(RECRUITING_QUERY):
            continue
        existing = conn.execute(
            "SELECT local_id FROM mail_ingestion_items WHERE source_email_id = ? AND classification = 'candidate' AND status IN ('imported', 'updated', 'duplicate')",
            (message_id,),
        ).fetchone()
        if existing:
            result["duplicates"] += 1
            continue

        candidate = candidate_from_mail(message)
        if not candidate.get("email") or not candidate.get("name"):
            reason = "缺少可稳定去重的候选人邮箱或姓名"
            record_review(conn, message, reason)
            result["needs_review"].append(review_item(message, reason))
            continue

        attachment_result = handle_attachments(
            message,
            mailbox,
            attachment_dir,
            download_attachments,
            extract_text,
            runner,
            downloader,
            text_extractor,
        )
        result["attachments_downloaded"] += attachment_result["downloaded"]
        result["attachment_errors"].extend(attachment_result["download_errors"])
        result["text_extraction_errors"].extend(attachment_result["extract_errors"])

        if attachment_result["paths"]:
            candidate["resume_uri"] = "; ".join(attachment_result["paths"])
            candidate["portfolio"] = attachment_result["paths"]
        if attachment_result["text"]:
            candidate["resume_text"] = "\n\n".join([candidate.get("resume_text", ""), attachment_result["text"]]).strip()
            candidate["skills"] = sorted(set(candidate.get("skills", []) + infer_skills(candidate["resume_text"])))
            candidate["evidence"] = [item for item in candidate.get("evidence", []) if item] + [attachment_result["text"][:1200]]

        existed = candidate_exists(conn, candidate["email"], candidate["name"])
        candidate_id = upsert_candidate(conn, candidate)
        status = "updated" if existed else "imported"
        result["updated" if existed else "created"] += 1
        upsert_mail_ingestion_item(
            conn,
            {
                "source_email_id": message_id,
                "source_subject": subject,
                "sender_email": candidate["email"],
                "classification": "candidate",
                "status": status,
                "local_table": "candidates",
                "local_id": candidate_id,
                "attachments": attachment_result["manifest"],
                "raw": message,
            },
        )
        log_event(conn, f"mail_candidate_{status}", candidate_id, None, None, message_id, subject)
        conn.commit()

    return result


def collect_candidate_summaries(mailbox: str, max_messages: int, queries: list[str], runner: CommandRunner) -> list[dict[str, Any]]:
    by_id: dict[str, dict[str, Any]] = {}
    for query in queries:
        raw = runner(
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
        for item in extract_mail_items(json.loads(raw or "[]")):
            message_id = message_id_for(item)
            if message_id:
                by_id[message_id] = {**by_id.get(message_id, {}), **item}
    return list(by_id.values())


def fetch_message_details(mailbox: str, message_ids: list[str], runner: CommandRunner) -> dict[str, dict[str, Any]]:
    if not message_ids:
        return {}
    by_id: dict[str, dict[str, Any]] = {}
    for start in range(0, len(message_ids), 50):
        chunk = message_ids[start : start + 50]
        raw = runner(
            [
                "lark-cli",
                "mail",
                "+messages",
                "--as",
                "user",
                "--message-ids",
                ",".join(chunk),
                "--html=false",
                "--format",
                "json",
                "--mailbox",
                mailbox,
            ]
        )
        for item in extract_mail_items(json.loads(raw or "[]")):
            message_id = message_id_for(item)
            if message_id:
                by_id[message_id] = item
    return by_id


def candidate_from_mail(message: dict[str, Any]) -> dict[str, Any]:
    subject = text_value(first_present(message, "subject", "title"))
    body = text_value(first_present(message, "body", "text", "plain_text", "content", "body_text"))
    sender = first_present(message, "from", "sender", "from_email", "sender_email")
    sender_text = text_value(sender)
    email = extract_email(sender_text) or extract_email(body)
    sender_name = sender_name_from(sender)
    name = infer_candidate_name(subject, sender_name, email)
    attachments = normalize_attachments(message)
    attachment_names = [item["name"] for item in attachments if item.get("name")]
    combined = "\n".join([subject, body, "；".join(attachment_names)])
    return {
        "email": email,
        "name": name,
        "sender_name": sender_name,
        "direction": infer_direction(subject, body),
        "source_subject": subject,
        "source_email_id": message_id_for(message),
        "resume_uri": "；".join(attachment_names),
        "portfolio": attachment_names,
        "resume_text": body,
        "evidence": [body[:1200], "；".join(attachment_names)],
        "skills": infer_skills(combined),
        "status": "pending",
        "raw": message,
    }


def sender_name_from(value: Any) -> str:
    if isinstance(value, dict):
        return text_value(first_present(value, "name", "display_name", "text"))
    text = text_value(value)
    if "<" in text:
        return text.split("<", 1)[0].strip().strip('"')
    if "@" in text:
        return ""
    return text


def infer_candidate_name(subject: str, sender_name: str, email: str) -> str:
    for pattern in [
        r"简历\s*[-—｜|:：]\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·\s]{1,30})",
        r"投递\s*[-—｜|:：]\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·\s]{1,30})",
        r"应聘\s*[-—｜|:：]\s*([\u4e00-\u9fffA-Za-z][\u4e00-\u9fffA-Za-z·\s]{1,30})",
    ]:
        match = re.search(pattern, subject)
        if match:
            return clean_name(match.group(1))
    if sender_name:
        return clean_name(sender_name)
    return email.split("@", 1)[0] if email else ""


def clean_name(value: str) -> str:
    value = re.split(r"[-—｜|:：,，/（）()]", value.strip(), maxsplit=1)[0].strip()
    return re.sub(r"\s+", " ", value)[:40]


def infer_direction(subject: str, body: str) -> str:
    for line in (subject + "\n" + body).splitlines():
        if any(word in line for word in ("投递", "应聘", "岗位", "方向", "职位")):
            return line.strip()[:120]
    return ""


def normalize_attachments(message: dict[str, Any]) -> list[dict[str, str]]:
    raw = first_present(message, "attachments", "attachment_list", "files")
    if not isinstance(raw, list):
        return []
    attachments = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        attachment_id = text_value(first_present(item, "attachment_id", "id", "file_token", "token"))
        name = text_value(first_present(item, "name", "filename", "file_name"))
        if attachment_id or name:
            attachments.append({"attachment_id": attachment_id, "name": name or attachment_id})
    return attachments


def handle_attachments(
    message: dict[str, Any],
    mailbox: str,
    attachment_dir: Path,
    download_attachments: bool,
    extract_text: bool,
    runner: CommandRunner,
    downloader: Downloader,
    text_extractor: TextExtractor,
) -> dict[str, Any]:
    attachments = normalize_attachments(message)
    result: dict[str, Any] = {"downloaded": 0, "paths": [], "text": "", "manifest": [], "download_errors": [], "extract_errors": []}
    if not attachments or not download_attachments:
        result["manifest"] = attachments
        return result

    message_id = message_id_for(message)
    urls = attachment_download_urls(mailbox, message_id, attachments, runner)
    target_dir = attachment_dir / safe_segment(message_id)
    target_dir.mkdir(parents=True, exist_ok=True)
    texts = []
    for attachment in attachments:
        attachment_id = attachment.get("attachment_id", "")
        name = attachment.get("name", attachment_id)
        manifest_item = {"attachment_id": attachment_id, "name": name}
        url = urls.get(attachment_id)
        if not url:
            error = {"message_id": message_id, "attachment_id": attachment_id, "name": name, "error": "missing download_url"}
            result["download_errors"].append(error)
            manifest_item["error"] = error["error"]
            result["manifest"].append(manifest_item)
            continue
        path = unique_path(target_dir / safe_filename(name or attachment_id or "attachment"))
        try:
            downloader(url, path)
            result["downloaded"] += 1
            result["paths"].append(str(path))
            manifest_item["path"] = str(path)
        except Exception as exc:
            error = {"message_id": message_id, "attachment_id": attachment_id, "name": name, "error": str(exc)}
            result["download_errors"].append(error)
            manifest_item["error"] = str(exc)
            result["manifest"].append(manifest_item)
            continue
        if extract_text:
            try:
                text = text_extractor(path)
                if text:
                    texts.append(f"# {name}\n{text}")
                    manifest_item["text_chars"] = len(text)
            except Exception as exc:
                error = {"message_id": message_id, "attachment_id": attachment_id, "name": name, "path": str(path), "error": str(exc)}
                result["extract_errors"].append(error)
                manifest_item["extract_error"] = str(exc)
        result["manifest"].append(manifest_item)
    result["text"] = "\n\n".join(texts)
    return result


def attachment_download_urls(mailbox: str, message_id: str, attachments: list[dict[str, str]], runner: CommandRunner) -> dict[str, str]:
    ids = [item["attachment_id"] for item in attachments if item.get("attachment_id")]
    if not ids:
        return {}
    params = json.dumps({"user_mailbox_id": mailbox, "message_id": message_id, "attachment_ids": ids}, ensure_ascii=False)
    raw = runner(
        [
            "lark-cli",
            "mail",
            "user_mailbox.message.attachments",
            "download_url",
            "--as",
            "user",
            "--params",
            params,
            "--format",
            "json",
        ]
    )
    payload = json.loads(raw or "{}")
    if isinstance(payload, dict) and isinstance(payload.get("data"), dict):
        payload = payload["data"]
    urls = {}
    for item in payload.get("download_urls", []) if isinstance(payload, dict) else []:
        if isinstance(item, dict) and item.get("attachment_id") and item.get("download_url"):
            urls[text_value(item["attachment_id"])] = text_value(item["download_url"])
    return urls


def download_url_to_file(url: str, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=60) as response:
        path.write_bytes(response.read())


def extract_text_from_file(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        completed = subprocess.run(["pdftotext", "-layout", str(path), "-"], check=True, text=True, capture_output=True)
        return completed.stdout.strip()
    if suffix in {".doc", ".docx", ".rtf"}:
        completed = subprocess.run(["textutil", "-convert", "txt", "-stdout", str(path)], check=True, text=True, capture_output=True)
        return completed.stdout.strip()
    if suffix == ".pptx":
        return extract_pptx_text(path)
    if suffix in {".txt", ".md", ".csv"}:
        return path.read_text(encoding="utf-8", errors="ignore").strip()
    return ""


def extract_pptx_text(path: Path) -> str:
    chunks = []
    with zipfile.ZipFile(path) as archive:
        for name in sorted(item for item in archive.namelist() if item.startswith("ppt/slides/slide") and item.endswith(".xml")):
            root = ElementTree.fromstring(archive.read(name))
            for node in root.iter():
                if node.tag.endswith("}t") and node.text:
                    chunks.append(html.unescape(node.text))
    return "\n".join(chunks).strip()


def candidate_exists(conn: sqlite3.Connection, email: str, name: str) -> bool:
    row = conn.execute("SELECT id FROM candidates WHERE email = ? OR (? != '' AND name = ?) ORDER BY id LIMIT 1", (email, name, name)).fetchone()
    return row is not None


def record_review(conn: sqlite3.Connection, message: dict[str, Any], reason: str) -> None:
    upsert_mail_ingestion_item(
        conn,
        {
            "source_email_id": message_id_for(message),
            "source_subject": text_value(first_present(message, "subject", "title")),
            "sender_email": extract_email(text_value(first_present(message, "from", "sender", "from_email", "sender_email"))),
            "classification": "candidate",
            "status": "needs_review",
            "error_text": reason,
            "raw": message,
        },
    )


def review_item(message: dict[str, Any], reason: str) -> dict[str, str]:
    return {
        "message_id": message_id_for(message),
        "subject": text_value(first_present(message, "subject", "title")),
        "reason": reason,
    }


def table_counts(conn: sqlite3.Connection) -> dict[str, int]:
    return {
        table: int(conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"])
        for table in ("clients", "jobs", "recruiting_requests", "candidates")
    }


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for index in range(2, 1000):
        candidate = path.with_name(f"{stem}-{index}{suffix}")
        if not candidate.exists():
            return candidate
    raise RuntimeError(f"Cannot allocate unique path for {path}")


def safe_segment(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value).strip("._")[:120] or "mail"


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[\\/:*?\"<>|\x00-\x1f]+", "_", value).strip(" ._")
    return cleaned[:160] or "attachment"
