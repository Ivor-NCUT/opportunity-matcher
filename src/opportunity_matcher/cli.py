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
import sqlite3
from pathlib import Path
from typing import Any

from .db import DEFAULT_DB, connect, fetch_all, init_db, upsert_candidate, upsert_client, upsert_company, upsert_job, upsert_recruiter
from .lark_importer import import_lark_dir
from .matcher import match_candidate
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

    command = subparsers.add_parser("import-lark", help="Import exported Lark Base JSON snapshots.")
    command.add_argument("--dir", required=True)
    command.set_defaults(func=cmd_import_lark)

    command = subparsers.add_parser("match", help="Preview matches for one candidate.")
    command.add_argument("--candidate-id", type=int, required=True)
    command.add_argument("--json", action="store_true", help="Print machine-readable JSON.")
    command.set_defaults(func=cmd_match)

    command = subparsers.add_parser("run", help="Process pending candidates and draft outbox messages.")
    command.set_defaults(func=cmd_run)

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
        "matches": "匹配记录",
        "outbox": "outbox 草稿",
        "audit_logs": "审计日志",
        "source_records": "外部来源记录",
    }
    for table, label in tables.items():
        count = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()["count"]
        print(f"{label}: {count}")
    open_jobs = conn.execute("SELECT COUNT(*) AS count FROM jobs WHERE status = 'open'").fetchone()["count"]
    whitelisted = conn.execute("SELECT COUNT(*) AS count FROM recruiters WHERE whitelisted = 1").fetchone()["count"]
    if open_jobs == 0:
        print("提醒：当前没有开放岗位。")
    if whitelisted == 0:
        print("提醒：当前没有招聘方白名单联系人。")
    return 0


def read_json_list(path: str) -> list[dict[str, Any]]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(raw, list):
        raise ValueError("JSON file must contain a list.")
    return raw


if __name__ == "__main__":
    raise SystemExit(main())
