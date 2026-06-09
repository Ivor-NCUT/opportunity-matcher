# ## 核心功能
# 对候选人和开放岗位做客户优先匹配、硬条件过滤、能力证据打分和 Top 3 排序。
# ## 输入
# SQLite 中的候选人记录、客户库、开放岗位记录、能力标签、能力证据和简历文本。
# ## 输出
# `MatchResult` 列表，优先来自客户公司岗位；客户岗位无正向匹配时才回退全职位库。
# ## 定位
# 纯匹配逻辑层，不写数据库、不生成邮件草稿。
# ## 依赖
# `db.loads` 解析 JSON 字段，Python 标准库 `dataclasses`、`re`、`sqlite3`。
# ## 维护规则
# 新增客户优先策略、硬条件、分数阈值、权重或理由格式时，必须同步更新测试和 PRD 验收描述。

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from .db import loads


LEVEL_ORDER = {
    "intern": 1,
    "junior": 2,
    "mid": 3,
    "senior": 4,
    "lead": 5,
}


@dataclass(frozen=True)
class MatchResult:
    job_id: int
    score: float
    reason: str
    hard_filter_summary: str


def match_candidate(conn: sqlite3.Connection, candidate_id: int, limit: int = 3, min_score: float = 1.0) -> list[MatchResult]:
    candidate = conn.execute("SELECT * FROM candidates WHERE id = ?", (candidate_id,)).fetchone()
    if not candidate:
        raise ValueError(f"Candidate not found: {candidate_id}")

    client_results = score_jobs(candidate, fetch_open_jobs(conn, clients_only=True), min_score)
    if client_results:
        return sorted(client_results, key=lambda item: (-item.score, item.job_id))[:limit]

    jobs = fetch_open_jobs(conn, clients_only=False)
    results = score_jobs(candidate, jobs, min_score)
    return sorted(results, key=lambda item: (-item.score, item.job_id))[:limit]


def match_job_candidates(conn: sqlite3.Connection, job_id: int, limit: int = 3, min_score: float = 1.0) -> list[tuple[sqlite3.Row, MatchResult]]:
    job = conn.execute("SELECT * FROM jobs WHERE id = ? AND status = 'open'", (job_id,)).fetchone()
    if not job:
        raise ValueError(f"Open job not found: {job_id}")

    rows = conn.execute("SELECT * FROM candidates ORDER BY updated_at DESC, id").fetchall()
    results: list[tuple[sqlite3.Row, MatchResult]] = []
    for candidate in rows:
        passed, summary = hard_filter(candidate, job)
        if not passed:
            continue
        score, reason = evidence_score(candidate, job)
        if score < min_score:
            continue
        results.append((candidate, MatchResult(int(job["id"]), score, reason, summary)))

    return sorted(results, key=lambda item: (-item[1].score, int(item[0]["id"])))[:limit]


def fetch_open_jobs(conn: sqlite3.Connection, clients_only: bool) -> list[sqlite3.Row]:
    if clients_only:
        return conn.execute(
            """
            SELECT DISTINCT jobs.*
            FROM jobs
            JOIN clients ON clients.company_id = jobs.company_id
            WHERE jobs.status = 'open' AND clients.status = 'active'
            ORDER BY jobs.id
            """
        ).fetchall()
    return conn.execute("SELECT * FROM jobs WHERE status = 'open' ORDER BY id").fetchall()


def score_jobs(candidate: sqlite3.Row, jobs: list[sqlite3.Row], min_score: float) -> list[MatchResult]:
    results: list[MatchResult] = []
    for job in jobs:
        passed, summary = hard_filter(candidate, job)
        if not passed:
            continue
        score, reason = evidence_score(candidate, job)
        if score < min_score:
            continue
        results.append(
            MatchResult(
                job_id=int(job["id"]),
                score=score,
                reason=reason,
                hard_filter_summary=summary,
            )
        )

    return results


def hard_filter(candidate: sqlite3.Row, job: sqlite3.Row) -> tuple[bool, str]:
    checks: list[str] = []

    city_ok = compatible_city(candidate["city"], job["city"])
    checks.append(f"城市{'通过' if city_ok else '不通过'}: 候选人={candidate['city'] or '未填'}, 岗位={job['city'] or '不限'}")

    work_ok = compatible_work_type(candidate["work_type"], job["work_type"])
    checks.append(f"工作形态{'通过' if work_ok else '不通过'}: 候选人={candidate['work_type'] or '未填'}, 岗位={job['work_type'] or '不限'}")

    level_ok = compatible_level(candidate["level"], job["level_min"], job["level_max"])
    checks.append(f"经验等级{'通过' if level_ok else '不通过'}: 候选人={candidate['level'] or '未填'}, 岗位={job['level_min'] or '不限'}-{job['level_max'] or '不限'}")

    return city_ok and work_ok and level_ok, "；".join(checks)


def compatible_city(candidate_city: str, job_city: str) -> bool:
    job = normalize(job_city)
    candidate = normalize(candidate_city)
    if not job or job in {"any", "不限", "remote", "远程"}:
        return True
    if not candidate:
        return True
    job_parts = split_values(job)
    candidate_parts = split_values(candidate)
    return bool(job_parts & candidate_parts) or "remote" in job_parts or "远程" in job_parts


def compatible_work_type(candidate_work_type: str, job_work_type: str) -> bool:
    job = normalize(job_work_type)
    candidate = normalize(candidate_work_type)
    if not job or job in {"any", "不限"}:
        return True
    if not candidate:
        return True
    return bool(split_values(job) & split_values(candidate))


def compatible_level(candidate_level: str, level_min: str, level_max: str) -> bool:
    current = LEVEL_ORDER.get(normalize(candidate_level), 0)
    low = LEVEL_ORDER.get(normalize(level_min), 0)
    high = LEVEL_ORDER.get(normalize(level_max), max(LEVEL_ORDER.values()))
    if current == 0:
        return True
    return low <= current <= high


def evidence_score(candidate: sqlite3.Row, job: sqlite3.Row) -> tuple[float, str]:
    candidate_skills = {normalize(item) for item in loads(candidate["skills_json"])}
    evidence = [str(item) for item in loads(candidate["evidence_json"])]
    resume_text = candidate["resume_text"] or ""
    searchable = normalize(" ".join(evidence) + " " + resume_text)

    required = [normalize(item) for item in loads(job["required_skills_json"])]
    preferred = [normalize(item) for item in loads(job["preferred_skills_json"])]

    required_hits = [skill for skill in required if has_skill(skill, candidate_skills, searchable)]
    preferred_hits = [skill for skill in preferred if has_skill(skill, candidate_skills, searchable)]
    missing_required = [skill for skill in required if skill not in required_hits]

    score = len(required_hits) * 10 + len(preferred_hits) * 4
    if required and not required_hits:
        score -= 5
    if missing_required:
        score -= len(missing_required) * 2

    reason_parts = []
    if required_hits:
        reason_parts.append("命中必需能力：" + "、".join(required_hits))
    if preferred_hits:
        reason_parts.append("命中加分能力：" + "、".join(preferred_hits))
    if missing_required:
        reason_parts.append("待确认能力：" + "、".join(missing_required))
    if not reason_parts:
        reason_parts.append("硬条件通过，但能力证据较少，建议人工复核")

    return score, "；".join(reason_parts)


def has_skill(skill: str, candidate_skills: set[str], searchable: str) -> bool:
    if not skill:
        return False
    return skill in candidate_skills or skill in searchable


def normalize(value: str | None) -> str:
    return (value or "").strip().lower()


def split_values(value: str) -> set[str]:
    return {part for part in re.split(r"[,/，、\s]+", value) if part}
