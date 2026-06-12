# ## 核心功能
# 提供本地机会匹配工作台的命令行入口。
# ## 输入
# 命令行参数、JSON 导入文件、本地 SQLite 数据库路径。
# ## 输出
# 数据库初始化、数据导入、匹配预览、批处理、outbox 查看、审计查看和健康检查结果。
# ## 定位
# 业务 CLI 适配层，只做参数解析和结果展示，核心逻辑委托给数据层和工作流层。
# ## 依赖
# `db`、`matcher`、`workflow`、`lark_importer` 和 Python 标准库 `argparse`、`json`、`sqlite3`。
# ## 维护规则
# 新增命令、参数或 JSON 输出契约时，同步更新 README、基础架构文档和测试。

from __future__ import annotations

import argparse
import json
import os
import shutil
import sqlite3
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, fetch_all, init_db, upsert_candidate, upsert_client, upsert_company, upsert_headhunter, upsert_job, upsert_recruiter
from .headhunter_partnership import DEFAULT_HEADHUNTER_BODY, forward_candidates_to_headhunters
from .lark_importer import import_lark_dir
from .mail_classifier import DEFAULT_BASE_URL, DEFAULT_MODEL, DEFAULT_TIMEOUT, build_mail_classifier, inspect_mail_classifier
from .mail_ingestion import CANDIDATE_QUERIES, sync_mail_inbox
from .matcher import match_candidate
from .recruiting_workflow import WEBHOOK_ENV, draft_candidate_outreach, mark_interested_and_draft_forward, review_interest, send_due_followups, sync_recruiting_mails
from .workflow import process_pending


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    conn = connect(args.db)
    init_db(conn)
    try:
        return int(args.func(conn, args) or 0)
    finally:
        conn.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="opportunity-matcher")
    parser.add_argument("--db", default=str(DEFAULT_DB), help="SQLite database path.")
    subparsers = parser.add_subparsers(required=True)

    command = subparsers.add_parser("init", help="Initialize local database.")
    command.set_defaults(func=cmd_init)

    command = subparsers.add_parser("import-candidates", help="Import candidates from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_candidates)

    command = subparsers.add_parser("import-companies", help="Import companies from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_companies)

    command = subparsers.add_parser("import-clients", help="Import recruiting clients from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_clients)

    command = subparsers.add_parser("import-jobs", help="Import jobs from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_jobs)

    command = subparsers.add_parser("import-recruiters", help="Import recruiters from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_recruiters)

    command = subparsers.add_parser("import-headhunters", help="Import headhunter partners from JSON.")
    command.add_argument("--file", required=True)
    command.set_defaults(func=cmd_import_headhunters)

    command = subparsers.add_parser("import-lark", help="Import exported Lark Base JSON snapshots.")
    command.add_argument("--dir", required=True)
    command.set_defaults(func=cmd_import_lark)

    command = subparsers.add_parser("match", help="Preview matches for one candidate.")
    command.add_argument("--candidate-id", type=int, required=True)
    command.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    command.set_defaults(func=cmd_match)

    command = subparsers.add_parser("run", help="Process pending candidates and draft outbox messages.")
    command.set_defaults(func=cmd_run)

    command = subparsers.add_parser("sync-recruiting-mails", help="Sync recruiting cooperation emails from Lark Mail.")
    command.add_argument("--query", default="招聘合作")
    command.add_argument("--max", type=int, default=100)
    command.add_argument("--mailbox", default="me")
    add_mail_classifier_args(command)
    command.set_defaults(func=cmd_sync_recruiting_mails)

    command = subparsers.add_parser("sync-mail-inbox", help="Daily Lark Mail ingestion for recruiting clients, jobs, and candidates.")
    command.add_argument("--mailbox", default="me")
    command.add_argument("--max", type=int, default=100)
    command.add_argument("--candidate-query", action="append", dest="candidate_queries", help="Candidate search query. Can be repeated.")
    command.add_argument("--attachment-dir", default="data/mail_attachments")
    command.add_argument("--no-download-attachments", action="store_true")
    command.add_argument("--no-extract-text", action="store_true")
    command.add_argument("--no-run", action="store_true", help="Do not process pending candidates after ingestion.")
    command.add_argument("--forward-new-candidates-to-headhunters", action="store_true", help="Forward newly imported/updated candidate resumes to active headhunter partners.")
    command.add_argument("--confirm-headhunter-send", action="store_true", help="Send headhunter forwards immediately. Without this flag, forwards are saved as drafts.")
    command.add_argument("--json", action="store_true")
    add_mail_classifier_args(command)
    command.set_defaults(func=cmd_sync_mail_inbox)

    command = subparsers.add_parser("forward-candidates-to-headhunters", help="Forward candidate resume attachments to active headhunter partners.")
    command.add_argument("--exclude-candidate-id", type=int, action="append", default=[])
    command.add_argument("--candidate-id", type=int, action="append", dest="candidate_ids")
    command.add_argument("--recipient-email", default="", help="Override active headhunter partners and send to one email.")
    command.add_argument("--recipient-name", default="")
    command.add_argument("--body", default=DEFAULT_HEADHUNTER_BODY)
    command.add_argument("--mailbox", default="me")
    command.add_argument("--confirm-send", action="store_true", help="Send immediately. Without this flag, create drafts.")
    command.add_argument("--json", action="store_true")
    command.set_defaults(func=cmd_forward_candidates_to_headhunters)

    command = subparsers.add_parser("draft-candidate-outreach", help="Create Lark Mail drafts for candidates matched to one recruiting request.")
    command.add_argument("--request-id", type=int, required=True)
    command.add_argument("--limit", type=int, default=3)
    command.add_argument("--mailbox", default="me")
    command.set_defaults(func=cmd_draft_candidate_outreach)

    command = subparsers.add_parser("review-interest", help="List or record candidate replies that need manual interest review.")
    command.add_argument("--outreach-id", type=int)
    command.add_argument("--reply-text", default="")
    command.add_argument("--reply-file")
    command.add_argument("--source-reply-email-id", default="")
    command.add_argument("--json", action="store_true")
    command.set_defaults(func=cmd_review_interest)

    command = subparsers.add_parser("mark-interested", help="Mark one outreach as interested and create a recruiter forward draft.")
    command.add_argument("--outreach-id", type=int, required=True)
    command.add_argument("--reply-text", default="")
    command.add_argument("--reply-file")
    command.add_argument("--source-reply-email-id", default="")
    command.add_argument("--mailbox", default="me")
    command.set_defaults(func=cmd_mark_interested)

    command = subparsers.add_parser("send-due-followups", help="Send due recruiting followup reminders to Feishu bot.")
    command.add_argument("--webhook-url", default="")
    command.set_defaults(func=cmd_send_due_followups)

    command = subparsers.add_parser("outbox", help="Show outbox drafts.")
    command.add_argument("--json", action="store_true")
    command.set_defaults(func=cmd_outbox)

    command = subparsers.add_parser("audit", help="Show audit logs.")
    command.add_argument("--limit", type=int, default=20)
    command.add_argument("--json", action="store_true")
    command.set_defaults(func=cmd_audit)

    command = subparsers.add_parser("doctor", help="Check local setup.")
    command.set_defaults(func=cmd_doctor)

    return parser


def cmd_init(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    print(f"数据库已就绪：{args.db}")
    return 0


def cmd_import_candidates(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_candidate(conn, item) for item in items]
    print(f"已导入候选人：{len(ids)}")
    print("候选人 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_import_companies(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_company(conn, item) for item in items]
    print(f"已导入公司：{len(ids)}")
    print("公司 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_import_clients(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_client(conn, item) for item in items]
    print(f"已导入客户：{len(ids)}")
    print("客户 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_import_jobs(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_job(conn, item) for item in items]
    print(f"已导入岗位：{len(ids)}")
    print("岗位 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_import_lark(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    counts = import_lark_dir(conn, args.dir)
    print(f"已导入公司：{counts['companies']}")
    print(f"已导入岗位：{counts['jobs']}")
    print(f"已导入候选人：{counts['candidates']}")
    print(f"已记录来源映射：{counts['source_records']}")
    return 0


def cmd_import_recruiters(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_recruiter(conn, item) for item in items]
    print(f"已导入招聘方联系人：{len(ids)}")
    print("联系人 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_import_headhunters(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    items = read_json_list(args.file)
    ids = [upsert_headhunter(conn, item) for item in items]
    print(f"已导入猎头合作伙伴：{len(ids)}")
    print("猎头 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_match(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    results = match_candidate(conn, args.candidate_id)
    rows = []
    for result in results:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
        rows.append(
            {
                "job_id": result.job_id,
                "company": job["company"],
                "title": job["title"],
                "score": result.score,
                "reason": result.reason,
                "hard_filter_summary": result.hard_filter_summary,
            }
        )
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        for row in rows:
            print(f"#{row['job_id']} {row['company']} - {row['title']}｜score={row['score']}")
            print(f"  {row['reason']}")
    return 0


def cmd_run(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    count = process_pending(conn)
    print(f"已处理待处理候选人：{count}")
    return 0


def cmd_sync_recruiting_mails(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    counts = sync_recruiting_mails(
        conn,
        query=args.query,
        max_messages=args.max,
        mailbox=args.mailbox,
        classifier=resolve_mail_classifier(args),
    )
    print(f"已扫描招聘合作邮件：{counts['seen']}")
    print(f"已解析入库：{counts['parsed']}")
    print(f"待人工复核：{counts['needs_review']}")
    return 0


def cmd_sync_mail_inbox(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    result = sync_mail_inbox(
        conn,
        mailbox=args.mailbox,
        max_messages=args.max,
        candidate_queries=args.candidate_queries or CANDIDATE_QUERIES,
        attachment_dir=args.attachment_dir,
        download_attachments=not args.no_download_attachments,
        extract_text=not args.no_extract_text,
        run_pending=not args.no_run,
        classifier=resolve_mail_classifier(args),
        forward_new_candidates_to_headhunters=args.forward_new_candidates_to_headhunters,
        confirm_headhunter_send=args.confirm_headhunter_send,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"招聘合作邮件扫描：{result['recruiting']['seen']}")
    print(f"招聘合作已解析：{result['recruiting']['parsed']}")
    print(f"招聘合作待复核：{result['recruiting']['needs_review']}")
    print(f"候选人邮件扫描：{result['candidates']['seen']}")
    print(f"新增候选人：{result['candidates']['created']}")
    print(f"更新候选人：{result['candidates']['updated']}")
    print(f"重复跳过：{result['candidates']['duplicates']}")
    print(f"附件下载：{result['candidates']['attachments_downloaded']}")
    print(f"待复核候选人邮件：{len(result['candidates']['needs_review'])}")
    print(f"已处理 pending 候选人：{result['processed_pending_candidates']}")
    print(
        "新增记录："
        f"客户 {result['new_records']['clients']}，"
        f"职位 {result['new_records']['jobs']}，"
        f"招聘请求 {result['new_records']['recruiting_requests']}，"
        f"候选人 {result['new_records']['candidates']}"
    )
    if result["candidates"]["attachment_errors"]:
        print(f"附件下载失败：{len(result['candidates']['attachment_errors'])}")
    if result["candidates"]["text_extraction_errors"]:
        print(f"附件抽文失败：{len(result['candidates']['text_extraction_errors'])}")
    if result["candidates"]["headhunter_forward"]["seen"]:
        print(
            "猎头合作推送："
            f"合作伙伴 {result['candidates']['headhunter_forward']['partners']}，"
            f"已发送 {result['candidates']['headhunter_forward']['sent']}，"
            f"草稿 {result['candidates']['headhunter_forward']['drafted']}，"
            f"失败 {len(result['candidates']['headhunter_forward']['failed'])}"
        )
    return 0


def cmd_forward_candidates_to_headhunters(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    result = forward_candidates_to_headhunters(
        conn,
        exclude_candidate_ids=set(args.exclude_candidate_id or []),
        candidate_ids=args.candidate_ids,
        recipient_email=args.recipient_email,
        recipient_name=args.recipient_name,
        body=args.body,
        mailbox=args.mailbox,
        confirm_send=args.confirm_send,
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    print(f"候选人扫描：{result['seen']}")
    print(f"猎头合作伙伴：{result['partners']}")
    print(f"已发送：{result['sent']}")
    print(f"已创建草稿：{result['drafted']}")
    print(f"已跳过：{len(result['skipped'])}")
    print(f"失败：{len(result['failed'])}")
    return 0


def cmd_draft_candidate_outreach(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    ids = draft_candidate_outreach(conn, args.request_id, limit=args.limit, mailbox=args.mailbox)
    print(f"已创建候选人触达草稿：{len(ids)}")
    if ids:
        print("触达记录 ID：" + ", ".join(str(item) for item in ids))
    return 0


def cmd_review_interest(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    reply_text = read_text_arg(args.reply_text, args.reply_file)
    rows = review_interest(conn, outreach_id=args.outreach_id, reply_text=reply_text, source_reply_email_id=args.source_reply_email_id)
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    for row in rows:
        print(f"#{row['id']} {row['candidate_name']}｜{row['company'] or '未关联公司'} - {row['title'] or '未关联岗位'}")
        if row["reply_text"]:
            print(f"  回复：{row['reply_text']}")
    if not rows:
        print("当前没有待人工判断的候选人回复。")
    return 0


def cmd_mark_interested(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    reply_text = read_text_arg(args.reply_text, args.reply_file)
    followup_id = mark_interested_and_draft_forward(
        conn,
        args.outreach_id,
        reply_text=reply_text,
        source_reply_email_id=args.source_reply_email_id,
        mailbox=args.mailbox,
    )
    print(f"已确认候选人感兴趣，并创建招聘方转发草稿。跟进提醒 ID：{followup_id}")
    return 0


def cmd_send_due_followups(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    count = send_due_followups(conn, webhook_url=args.webhook_url or None)
    print(f"已发送到期跟进提醒：{count}")
    return 0


def cmd_outbox(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rows = fetch_all(
        conn,
        """
        SELECT id, kind, recipient_email, recipient_name, subject, status, candidate_id, job_id, recruiter_id, created_at
        FROM outbox
        ORDER BY id
        """,
    )
    payload = [dict(row) for row in rows]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for row in payload:
            print(f"#{row['id']} [{row['kind']}/{row['status']}] {row['recipient_email']}｜{row['subject']}")
    return 0


def cmd_audit(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    rows = fetch_all(
        conn,
        """
        SELECT id, event, candidate_id, job_id, recruiter_id, source_email_id, explanation, created_at
        FROM audit_logs
        ORDER BY id DESC
        LIMIT ?
        """,
        (args.limit,),
    )
    payload = [dict(row) for row in rows]
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    else:
        for row in payload:
            print(f"#{row['id']} [{row['event']}] candidate={row['candidate_id']} job={row['job_id']} recruiter={row['recruiter_id']}")
            if row["explanation"]:
                print(f"  {row['explanation']}")
    return 0


def cmd_doctor(conn: sqlite3.Connection, args: argparse.Namespace) -> int:
    tables = {
        "clients": "客户",
        "companies": "公司",
        "candidates": "候选人",
        "jobs": "岗位",
        "recruiters": "招聘方联系人",
        "headhunters": "猎头合作伙伴",
        "matches": "匹配记录",
        "outbox": "outbox 草稿",
        "candidate_forwards": "候选人外部推送记录",
        "audit_logs": "审计日志",
        "source_records": "外部来源记录",
        "recruiting_requests": "招聘合作请求",
        "candidate_outreach": "候选人触达记录",
        "followups": "跟进提醒",
        "mail_ingestion_items": "邮箱入库记录",
    }
    for table, label in tables.items():
        count = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
        print(f"{label}: {count}")
    open_jobs = conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = 'open'").fetchone()["count"]
    whitelisted = conn.execute("SELECT COUNT(*) AS count FROM recruiters WHERE whitelisted = 1").fetchone()["count"]
    active_headhunter_count = conn.execute("SELECT COUNT(*) AS count FROM headhunters WHERE status = 'active'").fetchone()["count"]
    if open_jobs == 0:
        print("提醒：当前没有开放岗位。")
    if whitelisted == 0:
        print("提醒：当前没有招聘方白名单联系人。")
    if active_headhunter_count == 0:
        print("提醒：当前没有 active 猎头合作伙伴，新候选人不会自动抄送猎头。")
    if shutil.which("lark-cli"):
        print("lark-cli: 可用")
    else:
        print("提醒：当前找不到 lark-cli，招聘邮件同步和飞书邮箱草稿创建不可用。")
    classifier = inspect_mail_classifier()
    if classifier["enabled"] and classifier["available"]:
        print(f"邮件分类模型: {classifier['provider']} {classifier['model']} @ {classifier['base_url']} 可用")
    elif classifier["enabled"]:
        print(f"提醒：邮件分类模型不可用：{classifier['provider']} {classifier['model']} @ {classifier['base_url']} ({classifier['reason']})")
    else:
        print("提醒：邮件分类模型已禁用，邮件分类将回退到规则判断。")
    if os.environ.get(WEBHOOK_ENV):
        print("飞书群机器人 webhook: 已配置")
    else:
        print(f"提醒：未配置 {WEBHOOK_ENV}，到期跟进提醒不会发送到飞书群。")
    return 0


def add_mail_classifier_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--mail-classifier-model",
        default=DEFAULT_MODEL,
        help="Volcengine Ark endpoint model ID.",
    )
    parser.add_argument(
        "--mail-classifier-base-url",
        default=DEFAULT_BASE_URL,
        help="Volcengine Ark OpenAI-compatible base URL.",
    )
    parser.add_argument(
        "--mail-classifier-timeout",
        default=DEFAULT_TIMEOUT,
        type=float,
        help="Mail classifier request timeout in seconds.",
    )


def resolve_mail_classifier(args: argparse.Namespace):
    return build_mail_classifier(
        model=args.mail_classifier_model,
        base_url=args.mail_classifier_base_url,
        timeout=args.mail_classifier_timeout,
    )


def read_json_list(path: str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("JSON file must contain a list.")
    return raw


def read_text_arg(value: str, file_path: str | None) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8")
    return value


if __name__ == "__main__":
    raise SystemExit(main())
