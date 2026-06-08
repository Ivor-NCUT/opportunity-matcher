# ## 核心功能
# 编排候选人处理闭环，生成匹配记录、outbox 草稿和审计日志。
# ## 输入
# 待处理候选人 ID、本地数据库中的岗位库和招聘方白名单。
# ## 输出
# 匹配结果、候选人回信草稿、白名单招聘方推送草稿、候选人处理状态和审计日志。
# ## 定位
# 业务工作流层，连接匹配器、模板和数据库，不直接处理 CLI 参数。
# ## 依赖
# `db.log_event`、`email_templates`、`matcher.match_candidate`。
# ## 维护规则
# 处理状态、outbox 生成规则或白名单策略变化时，同步更新测试和基础架构文档。

from __future__ import annotations

import sqlite3

from .db import log_event, loads
from .email_templates import candidate_body, candidate_subject, recruiter_body, recruiter_subject
from .matcher import MatchResult, match_candidate


def process_candidate(conn: sqlite3.Connection, candidate_id: int) -> list[MatchResult]:
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    if not candidate:
        raise ValueError(f"Candidate not found: {candidate_id}")

    conn.execute("DELETE FROM matches WHERE candidate_id = ?", (candidate_id,))
    results = match_candidate(conn, candidate_id)
    for result in results:
        conn.execute(
            """
            INSERT INTO matches (candidate_id, job_id, score, reason, hard_filter_summary)
            VALUES (?, ?, ?, ?, ?)
            """,
            (candidate_id, result.job_id, result.score, result.reason, result.hard_filter_summary),
        )
        log_event(
            conn,
            "match_created",
            candidate_id,
            result.job_id,
            None,
            candidate["source_email_id"],
            f"score={result.score}; {result.reason}; {result.hard_filter_summary}",
        )

    create_candidate_outbox(conn, candidate_id, results)
    create_recruiter_outbox(conn, candidate_id, results)
    conn.execute("UPDATE candidates SET status = 'processed', updated_at = CURRENT_TIMESTAMP WHERE id = ?", (candidate_id,))
    log_event(conn, "candidate_processed", candidate_id, None, None, candidate["source_email_id"], f"matches={len(results)}")
    conn.commit()
    return results


def process_pending(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        "SELECT id FROM candidates WHERE status IN ('pending', 'pending_update') ORDER BY updated_at, id"
    ).fetchall()
    for row in rows:
        process_candidate(conn, int(row["id"]))
    return len(rows)


def create_candidate_outbox(conn: sqlite3.Connection, candidate_id: int, results: list[MatchResult]) -> None:
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    matched_jobs = []
    for result in results:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
        if job:
            matched_jobs.append((job, result.reason))

    conn.execute(
        """
        INSERT INTO outbox (kind, recipient_email, recipient_name, subject, body, candidate_id)
        VALUES ('candidate_reply', ?, ?, ?, ?, ?)
        """,
        (
            candidate["email"],
            candidate["name"],
            candidate_subject(candidate),
            candidate_body(candidate, matched_jobs),
            candidate_id,
        ),
    )
    log_event(conn, "candidate_reply_drafted", candidate_id, None, None, candidate["source_email_id"], f"jobs={len(matched_jobs)}")


def create_recruiter_outbox(conn: sqlite3.Connection, candidate_id: int, results: list[MatchResult]) -> None:
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    for result in results:
        job = conn.execute("SELECT * FROM jobs WHERE id = ?", (result.job_id,)).fetchone()
        if not job:
            continue
        recruiters = whitelisted_recruiters_for_job(conn, job)
        for recruiter in recruiters:
            conn.execute(
                """
                INSERT INTO outbox (
                    kind, recipient_email, recipient_name, subject, body,
                    candidate_id, job_id, recruiter_id
                ) VALUES ('recruiter_push', ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    recruiter["email"],
                    recruiter["name"],
                    recruiter_subject(candidate, job),
                    recruiter_body(candidate, job, result.reason),
                    candidate_id,
                    result.job_id,
                    int(recruiter["id"]),
                ),
            )
            log_event(
                conn,
                "recruiter_push_drafted",
                candidate_id,
                result.job_id,
                int(recruiter["id"]),
                candidate["source_email_id"],
                result.reason,
            )


def whitelisted_recruiters_for_job(conn: sqlite3.Connection, job: sqlite3.Row) -> list[sqlite3.Row]:
    rows = conn.execute(
        "SELECT * FROM recruiters WHERE whitelisted = 1 AND company = ? ORDER BY id",
        (job["company"],),
    ).fetchall()
    scoped: list[sqlite3.Row] = []
    for row in rows:
        job_ids = {int(value) for value in loads(row["job_ids_json"]) if str(value).isdigit()}
        if not job_ids or int(job["id"]) in job_ids:
            scoped.append(row)
    return scoped
