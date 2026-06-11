# ## 核心功能
# 管理本地 SQLite 数据库连接、表结构和候选人/客户/公司/岗位/招聘方写入。
# ## 输入
# CLI 或测试传入的数据库路径、JSON 导入记录、外部来源记录和审计事件。
# ## 输出
# 初始化后的数据库、upsert 后的记录 ID、查询结果、客户记录、来源记录和审计日志。
# ## 定位
# 产品自有数据层，负责持久化和轻量迁移，不负责匹配排序和邮件内容生成。
# ## 依赖
# Python 标准库 `sqlite3`、`json`、`pathlib`。
# ## 维护规则
# 表结构、字段含义、迁移或写入契约变化时，同步更新 README、基础架构文档和测试。

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable


DEFAULT_DB = Path("data/opportunity_matcher.db")


def connect(db_path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT NOT NULL DEFAULT '',
            website TEXT NOT NULL DEFAULT '',
            jobs_url TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            locations TEXT NOT NULL DEFAULT '',
            track TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            team_size TEXT NOT NULL DEFAULT '',
            founders TEXT NOT NULL DEFAULT '',
            founder_socials TEXT NOT NULL DEFAULT '',
            description TEXT NOT NULL DEFAULT '',
            values_text TEXT NOT NULL DEFAULT '',
            interesting_info TEXT NOT NULL DEFAULT '',
            tech_stack TEXT NOT NULL DEFAULT '',
            funding_info TEXT NOT NULL DEFAULT '',
            logo_url TEXT NOT NULL DEFAULT '',
            source_record_id TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_name ON companies(name);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_companies_slug_nonempty ON companies(slug) WHERE slug != '';

        CREATE TABLE IF NOT EXISTS clients (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            contact_name TEXT NOT NULL DEFAULT '',
            contact_email TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(name)
        );

        CREATE TABLE IF NOT EXISTS headhunters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            contact_name TEXT NOT NULL DEFAULT '',
            contact_email TEXT NOT NULL,
            company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'active',
            source TEXT NOT NULL DEFAULT '',
            notes TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(contact_email)
        );

        CREATE TABLE IF NOT EXISTS candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL,
            name TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            level TEXT NOT NULL DEFAULT '',
            work_type TEXT NOT NULL DEFAULT '',
            availability TEXT NOT NULL DEFAULT '',
            skills_json TEXT NOT NULL DEFAULT '[]',
            evidence_json TEXT NOT NULL DEFAULT '[]',
            resume_text TEXT NOT NULL DEFAULT '',
            source_email_id TEXT NOT NULL DEFAULT '',
            source_subject TEXT NOT NULL DEFAULT '',
            resume_uri TEXT NOT NULL DEFAULT '',
            phone_or_wechat TEXT NOT NULL DEFAULT '',
            sender_name TEXT NOT NULL DEFAULT '',
            direction TEXT NOT NULL DEFAULT '',
            first_seen_at TEXT NOT NULL DEFAULT '',
            last_seen_at TEXT NOT NULL DEFAULT '',
            portfolio_json TEXT NOT NULL DEFAULT '[]',
            referrer_name TEXT NOT NULL DEFAULT '',
            referral_note TEXT NOT NULL DEFAULT '',
            source_record_id TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            status TEXT NOT NULL DEFAULT 'pending',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(email)
        );

        CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
            company TEXT NOT NULL,
            title TEXT NOT NULL,
            city TEXT NOT NULL DEFAULT '',
            level_min TEXT NOT NULL DEFAULT '',
            level_max TEXT NOT NULL DEFAULT '',
            work_type TEXT NOT NULL DEFAULT '',
            required_skills_json TEXT NOT NULL DEFAULT '[]',
            preferred_skills_json TEXT NOT NULL DEFAULT '[]',
            description TEXT NOT NULL DEFAULT '',
            salary TEXT NOT NULL DEFAULT '',
            track TEXT NOT NULL DEFAULT '',
            stage TEXT NOT NULL DEFAULT '',
            team_size TEXT NOT NULL DEFAULT '',
            founder TEXT NOT NULL DEFAULT '',
            founder_socials TEXT NOT NULL DEFAULT '',
            source_url TEXT NOT NULL DEFAULT '',
            external_job_id TEXT NOT NULL DEFAULT '',
            source_record_id TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            source_updated_at TEXT NOT NULL DEFAULT '',
            is_hot INTEGER NOT NULL DEFAULT 0,
            raw_json TEXT NOT NULL DEFAULT '{}',
            recruiter_scope TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'open',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(company, title)
        );

        CREATE TABLE IF NOT EXISTS recruiters (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER REFERENCES companies(id) ON DELETE SET NULL,
            name TEXT NOT NULL,
            email TEXT NOT NULL,
            company TEXT NOT NULL,
            whitelisted INTEGER NOT NULL DEFAULT 0,
            job_ids_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(email)
        );

        CREATE TABLE IF NOT EXISTS matches (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            job_id INTEGER NOT NULL REFERENCES jobs(id) ON DELETE CASCADE,
            score REAL NOT NULL,
            reason TEXT NOT NULL,
            hard_filter_summary TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS outbox (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            recipient_email TEXT NOT NULL,
            recipient_name TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL,
            body TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'draft',
            candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            recruiter_id INTEGER REFERENCES recruiters(id) ON DELETE SET NULL,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS audit_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT NOT NULL,
            candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            recruiter_id INTEGER REFERENCES recruiters(id) ON DELETE SET NULL,
            source_email_id TEXT NOT NULL DEFAULT '',
            explanation TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS source_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source TEXT NOT NULL,
            source_table TEXT NOT NULL,
            source_record_id TEXT NOT NULL,
            local_table TEXT NOT NULL,
            local_id INTEGER NOT NULL,
            raw_json TEXT NOT NULL DEFAULT '{}',
            imported_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source, source_table, source_record_id, local_table)
        );

        CREATE TABLE IF NOT EXISTS recruiting_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_email_id TEXT NOT NULL,
            source_subject TEXT NOT NULL DEFAULT '',
            sender_email TEXT NOT NULL DEFAULT '',
            recruiter_name TEXT NOT NULL DEFAULT '',
            company TEXT NOT NULL DEFAULT '',
            jd_text TEXT NOT NULL DEFAULT '',
            preference_text TEXT NOT NULL DEFAULT '',
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            recruiter_id INTEGER REFERENCES recruiters(id) ON DELETE SET NULL,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            status TEXT NOT NULL DEFAULT 'parsed',
            error_text TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_email_id)
        );

        CREATE TABLE IF NOT EXISTS candidate_outreach (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER NOT NULL REFERENCES recruiting_requests(id) ON DELETE CASCADE,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            recruiter_id INTEGER REFERENCES recruiters(id) ON DELETE SET NULL,
            candidate_draft_id TEXT NOT NULL DEFAULT '',
            forward_draft_id TEXT NOT NULL DEFAULT '',
            interest_status TEXT NOT NULL DEFAULT 'needs_manual_review',
            status TEXT NOT NULL DEFAULT 'drafted',
            source_reply_email_id TEXT NOT NULL DEFAULT '',
            reply_text TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(request_id, candidate_id)
        );

        CREATE TABLE IF NOT EXISTS followups (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            outreach_id INTEGER NOT NULL REFERENCES candidate_outreach(id) ON DELETE CASCADE,
            candidate_id INTEGER REFERENCES candidates(id) ON DELETE SET NULL,
            job_id INTEGER REFERENCES jobs(id) ON DELETE SET NULL,
            recruiter_id INTEGER REFERENCES recruiters(id) ON DELETE SET NULL,
            due_at TEXT NOT NULL,
            message TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            sent_at TEXT NOT NULL DEFAULT '',
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(outreach_id)
        );

        CREATE TABLE IF NOT EXISTS mail_ingestion_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_email_id TEXT NOT NULL,
            source_subject TEXT NOT NULL DEFAULT '',
            sender_email TEXT NOT NULL DEFAULT '',
            classification TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT '',
            local_table TEXT NOT NULL DEFAULT '',
            local_id INTEGER,
            attachment_manifest_json TEXT NOT NULL DEFAULT '[]',
            error_text TEXT NOT NULL DEFAULT '',
            raw_json TEXT NOT NULL DEFAULT '{}',
            processed_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(source_email_id, classification)
        );

        CREATE TABLE IF NOT EXISTS candidate_forwards (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id INTEGER NOT NULL REFERENCES candidates(id) ON DELETE CASCADE,
            recipient_email TEXT NOT NULL,
            recipient_name TEXT NOT NULL DEFAULT '',
            headhunter_id INTEGER REFERENCES headhunters(id) ON DELETE SET NULL,
            client_id INTEGER REFERENCES clients(id) ON DELETE SET NULL,
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            attachment_paths_json TEXT NOT NULL DEFAULT '[]',
            message_id TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            error_text TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            UNIQUE(candidate_id, recipient_email)
        );
        """
    )
    ensure_columns(conn)
    conn.commit()


def ensure_columns(conn: sqlite3.Connection) -> None:
    migrations = {
        "candidates": {
            "phone_or_wechat": "TEXT NOT NULL DEFAULT ''",
            "sender_name": "TEXT NOT NULL DEFAULT ''",
            "direction": "TEXT NOT NULL DEFAULT ''",
            "first_seen_at": "TEXT NOT NULL DEFAULT ''",
            "last_seen_at": "TEXT NOT NULL DEFAULT ''",
            "portfolio_json": "TEXT NOT NULL DEFAULT '[]'",
            "referrer_name": "TEXT NOT NULL DEFAULT ''",
            "referral_note": "TEXT NOT NULL DEFAULT ''",
            "source_record_id": "TEXT NOT NULL DEFAULT ''",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "headhunters": {
            "contact_name": "TEXT NOT NULL DEFAULT ''",
            "company_id": "INTEGER REFERENCES companies(id) ON DELETE SET NULL",
            "client_id": "INTEGER REFERENCES clients(id) ON DELETE SET NULL",
            "status": "TEXT NOT NULL DEFAULT 'active'",
            "source": "TEXT NOT NULL DEFAULT ''",
            "notes": "TEXT NOT NULL DEFAULT ''",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
        "jobs": {
            "company_id": "INTEGER REFERENCES companies(id) ON DELETE SET NULL",
            "description": "TEXT NOT NULL DEFAULT ''",
            "salary": "TEXT NOT NULL DEFAULT ''",
            "track": "TEXT NOT NULL DEFAULT ''",
            "stage": "TEXT NOT NULL DEFAULT ''",
            "team_size": "TEXT NOT NULL DEFAULT ''",
            "founder": "TEXT NOT NULL DEFAULT ''",
            "founder_socials": "TEXT NOT NULL DEFAULT ''",
            "source_url": "TEXT NOT NULL DEFAULT ''",
            "external_job_id": "TEXT NOT NULL DEFAULT ''",
            "source_record_id": "TEXT NOT NULL DEFAULT ''",
            "published_at": "TEXT NOT NULL DEFAULT ''",
            "source_updated_at": "TEXT NOT NULL DEFAULT ''",
            "is_hot": "INTEGER NOT NULL DEFAULT 0",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
        },
        "recruiters": {
            "company_id": "INTEGER REFERENCES companies(id) ON DELETE SET NULL",
        },
        "recruiting_requests": {
            "preference_text": "TEXT NOT NULL DEFAULT ''",
        },
        "candidate_outreach": {
            "reply_text": "TEXT NOT NULL DEFAULT ''",
        },
        "followups": {
            "error_text": "TEXT NOT NULL DEFAULT ''",
        },
        "mail_ingestion_items": {
            "source_subject": "TEXT NOT NULL DEFAULT ''",
            "sender_email": "TEXT NOT NULL DEFAULT ''",
            "classification": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT ''",
            "local_table": "TEXT NOT NULL DEFAULT ''",
            "local_id": "INTEGER",
            "attachment_manifest_json": "TEXT NOT NULL DEFAULT '[]'",
            "error_text": "TEXT NOT NULL DEFAULT ''",
            "raw_json": "TEXT NOT NULL DEFAULT '{}'",
            "processed_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
        "candidate_forwards": {
            "recipient_name": "TEXT NOT NULL DEFAULT ''",
            "headhunter_id": "INTEGER REFERENCES headhunters(id) ON DELETE SET NULL",
            "client_id": "INTEGER REFERENCES clients(id) ON DELETE SET NULL",
            "subject": "TEXT NOT NULL DEFAULT ''",
            "body": "TEXT NOT NULL DEFAULT ''",
            "attachment_paths_json": "TEXT NOT NULL DEFAULT '[]'",
            "message_id": "TEXT NOT NULL DEFAULT ''",
            "status": "TEXT NOT NULL DEFAULT 'pending'",
            "error_text": "TEXT NOT NULL DEFAULT ''",
            "created_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
            "updated_at": "TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP",
        },
    }
    for table, columns in migrations.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})")}
        for column, definition in columns.items():
            if column not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def dumps(value: Any) -> str:
    return json.dumps(value or [], ensure_ascii=False)


def loads(value: str | None) -> Any:
    if not value:
        return []
    return json.loads(value)


def find_existing_candidate(conn: sqlite3.Connection, email: str, name: str) -> sqlite3.Row | None:
    email = email.strip().lower()
    name = name.strip()
    if email:
        row = conn.execute(
            "SELECT id FROM candidates WHERE email = ? ORDER BY id LIMIT 1",
            (email,),
        ).fetchone()
        if row:
            return row
    if name:
        return conn.execute(
            "SELECT id FROM candidates WHERE name = ? ORDER BY id LIMIT 1",
            (name,),
        ).fetchone()
    return None


def upsert_candidate(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    subject = item.get("source_subject", "")
    is_update = "简历更新" in subject
    email = item["email"].strip().lower()
    name = item["name"].strip()

    existing = find_existing_candidate(conn, email, name)
    status = "pending_update" if existing and is_update else item.get("status", "pending")

    fields = {
        "email": email,
        "name": name,
        "city": item.get("city", ""),
        "level": item.get("level", ""),
        "work_type": item.get("work_type", ""),
        "availability": item.get("availability", ""),
        "skills_json": dumps(item.get("skills", [])),
        "evidence_json": dumps(item.get("evidence", [])),
        "resume_text": item.get("resume_text", ""),
        "source_email_id": item.get("source_email_id", ""),
        "source_subject": subject,
        "resume_uri": item.get("resume_uri", ""),
        "phone_or_wechat": item.get("phone_or_wechat", ""),
        "sender_name": item.get("sender_name", ""),
        "direction": item.get("direction", ""),
        "first_seen_at": item.get("first_seen_at", ""),
        "last_seen_at": item.get("last_seen_at", ""),
        "portfolio_json": dumps(item.get("portfolio", [])),
        "referrer_name": item.get("referrer_name", ""),
        "referral_note": item.get("referral_note", ""),
        "source_record_id": item.get("source_record_id", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
        "status": status,
    }

    if existing:
        conn.execute(
            """
            UPDATE candidates
            SET email = :email, name = :name, city = :city, level = :level,
                work_type = :work_type, availability = :availability,
                skills_json = :skills_json, evidence_json = :evidence_json,
                resume_text = :resume_text, source_email_id = :source_email_id,
                source_subject = :source_subject, resume_uri = :resume_uri,
                phone_or_wechat = :phone_or_wechat, sender_name = :sender_name,
                direction = :direction, first_seen_at = :first_seen_at,
                last_seen_at = :last_seen_at, portfolio_json = :portfolio_json,
                referrer_name = :referrer_name, referral_note = :referral_note,
                source_record_id = :source_record_id, raw_json = :raw_json,
                status = :status, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """,
            {**fields, "id": existing["id"]},
        )
        candidate_id = int(existing["id"])
        log_event(conn, "candidate_updated" if is_update else "candidate_upserted", candidate_id, None, None, fields["source_email_id"], subject)
    else:
        cur = conn.execute(
            """
            INSERT INTO candidates (
                email, name, city, level, work_type, availability, skills_json,
                evidence_json, resume_text, source_email_id, source_subject,
                resume_uri, phone_or_wechat, sender_name, direction,
                first_seen_at, last_seen_at, portfolio_json, referrer_name,
                referral_note, source_record_id, raw_json, status
            ) VALUES (
                :email, :name, :city, :level, :work_type, :availability,
                :skills_json, :evidence_json, :resume_text, :source_email_id,
                :source_subject, :resume_uri, :phone_or_wechat, :sender_name,
                :direction, :first_seen_at, :last_seen_at, :portfolio_json,
                :referrer_name, :referral_note, :source_record_id, :raw_json,
                :status
            )
            """,
            fields,
        )
        candidate_id = int(cur.lastrowid)
        log_event(conn, "candidate_created", candidate_id, None, None, fields["source_email_id"], subject)

    conn.commit()
    return candidate_id


def upsert_company(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    name = item["name"].strip()
    slug = item.get("slug", "").strip()
    fields = {
        "name": name,
        "slug": slug,
        "website": item.get("website", ""),
        "jobs_url": item.get("jobs_url", ""),
        "source_url": item.get("source_url", ""),
        "locations": item.get("locations", ""),
        "track": item.get("track", ""),
        "stage": item.get("stage", ""),
        "team_size": item.get("team_size", ""),
        "founders": item.get("founders", ""),
        "founder_socials": item.get("founder_socials", ""),
        "description": item.get("description", ""),
        "values_text": item.get("values_text", ""),
        "interesting_info": item.get("interesting_info", ""),
        "tech_stack": item.get("tech_stack", ""),
        "funding_info": item.get("funding_info", ""),
        "logo_url": item.get("logo_url", ""),
        "source_record_id": item.get("source_record_id", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
    }
    existing = conn.execute(
        "SELECT id FROM companies WHERE name = ? OR (? != '' AND slug = ?) ORDER BY id LIMIT 1",
        (name, slug, slug),
    ).fetchone()
    if existing:
        conn.execute(
            """
            UPDATE companies
            SET name = :name, slug = :slug, website = :website, jobs_url = :jobs_url,
                source_url = :source_url, locations = :locations, track = :track,
                stage = :stage, team_size = :team_size, founders = :founders,
                founder_socials = :founder_socials, description = :description,
                values_text = :values_text, interesting_info = :interesting_info,
                tech_stack = :tech_stack, funding_info = :funding_info,
                logo_url = :logo_url, source_record_id = :source_record_id,
                raw_json = :raw_json, updated_at = CURRENT_TIMESTAMP
            WHERE id = :id
            """,
            {**fields, "id": existing["id"]},
        )
        company_id = int(existing["id"])
    else:
        cur = conn.execute(
            """
            INSERT INTO companies (
                name, slug, website, jobs_url, source_url, locations, track,
                stage, team_size, founders, founder_socials, description,
                values_text, interesting_info, tech_stack, funding_info,
                logo_url, source_record_id, raw_json
            ) VALUES (
                :name, :slug, :website, :jobs_url, :source_url, :locations,
                :track, :stage, :team_size, :founders, :founder_socials,
                :description, :values_text, :interesting_info, :tech_stack,
                :funding_info, :logo_url, :source_record_id, :raw_json
            )
            """,
            fields,
        )
        company_id = int(cur.lastrowid)
    conn.commit()
    return company_id


def upsert_client(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    company_id = item.get("company_id")
    if not company_id:
        company_id = upsert_company(
            conn,
            {
                "name": item["name"],
                "slug": item.get("slug", ""),
                "website": item.get("website", ""),
                "source_url": item.get("source_url", ""),
                "locations": item.get("locations", ""),
                "track": item.get("track", ""),
                "stage": item.get("stage", ""),
                "description": item.get("description", ""),
                "raw": item.get("raw", {}),
            },
        )
    fields = {
        "company_id": company_id,
        "name": item["name"].strip(),
        "contact_name": item.get("contact_name", ""),
        "contact_email": item.get("contact_email", "").strip().lower(),
        "status": item.get("status", "active"),
        "source": item.get("source", ""),
        "notes": item.get("notes", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
    }
    cur = conn.execute(
        """
        INSERT INTO clients (company_id, name, contact_name, contact_email, status, source, notes, raw_json)
        VALUES (:company_id, :name, :contact_name, :contact_email, :status, :source, :notes, :raw_json)
        ON CONFLICT(name) DO UPDATE SET
            company_id = excluded.company_id,
            contact_name = excluded.contact_name,
            contact_email = excluded.contact_email,
            status = excluded.status,
            source = excluded.source,
            notes = excluded.notes,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    client_id = int(cur.fetchone()["id"])
    conn.commit()
    return client_id


def upsert_headhunter(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    fields = {
        "name": item["name"].strip(),
        "contact_name": item.get("contact_name", ""),
        "contact_email": item["contact_email"].strip().lower(),
        "company_id": item.get("company_id"),
        "client_id": item.get("client_id"),
        "status": item.get("status", "active"),
        "source": item.get("source", ""),
        "notes": item.get("notes", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
    }
    cur = conn.execute(
        """
        INSERT INTO headhunters (
            name, contact_name, contact_email, company_id, client_id, status,
            source, notes, raw_json
        ) VALUES (
            :name, :contact_name, :contact_email, :company_id, :client_id,
            :status, :source, :notes, :raw_json
        )
        ON CONFLICT(contact_email) DO UPDATE SET
            name = excluded.name,
            contact_name = excluded.contact_name,
            company_id = excluded.company_id,
            client_id = excluded.client_id,
            status = excluded.status,
            source = excluded.source,
            notes = excluded.notes,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    headhunter_id = int(cur.fetchone()["id"])
    conn.commit()
    return headhunter_id


def active_headhunters(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT * FROM headhunters
        WHERE status = 'active'
        ORDER BY updated_at DESC, id
        """
    ).fetchall()


def upsert_job(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    company_id = item.get("company_id") or upsert_company(
        conn,
        {
            "name": item["company"],
            "slug": item.get("company_slug", ""),
            "source_url": item.get("source_url", ""),
            "track": item.get("track", ""),
            "stage": item.get("stage", ""),
            "team_size": item.get("team_size", ""),
            "founders": item.get("founder", ""),
            "founder_socials": item.get("founder_socials", ""),
            "description": item.get("company_description", ""),
        },
    )
    fields = {
        "company_id": company_id,
        "company": item["company"].strip(),
        "title": item["title"].strip(),
        "city": item.get("city", ""),
        "level_min": item.get("level_min", ""),
        "level_max": item.get("level_max", ""),
        "work_type": item.get("work_type", ""),
        "required_skills_json": dumps(item.get("required_skills", [])),
        "preferred_skills_json": dumps(item.get("preferred_skills", [])),
        "description": item.get("description", ""),
        "salary": item.get("salary", ""),
        "track": item.get("track", ""),
        "stage": item.get("stage", ""),
        "team_size": item.get("team_size", ""),
        "founder": item.get("founder", ""),
        "founder_socials": item.get("founder_socials", ""),
        "source_url": item.get("source_url", ""),
        "external_job_id": item.get("external_job_id", ""),
        "source_record_id": item.get("source_record_id", ""),
        "published_at": item.get("published_at", ""),
        "source_updated_at": item.get("source_updated_at", ""),
        "is_hot": 1 if item.get("is_hot") else 0,
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
        "recruiter_scope": item.get("recruiter_scope", ""),
        "status": item.get("status", "open"),
    }
    cur = conn.execute(
        """
        INSERT INTO jobs (
            company_id, company, title, city, level_min, level_max, work_type,
            required_skills_json, preferred_skills_json, description, salary,
            track, stage, team_size, founder, founder_socials, source_url,
            external_job_id, source_record_id, published_at, source_updated_at,
            is_hot, raw_json, recruiter_scope, status
        ) VALUES (
            :company_id, :company, :title, :city, :level_min, :level_max,
            :work_type, :required_skills_json, :preferred_skills_json,
            :description, :salary, :track, :stage, :team_size, :founder,
            :founder_socials, :source_url, :external_job_id, :source_record_id,
            :published_at, :source_updated_at, :is_hot, :raw_json,
            :recruiter_scope, :status
        )
        ON CONFLICT(company, title) DO UPDATE SET
            company_id = excluded.company_id,
            city = excluded.city,
            level_min = excluded.level_min,
            level_max = excluded.level_max,
            work_type = excluded.work_type,
            required_skills_json = excluded.required_skills_json,
            preferred_skills_json = excluded.preferred_skills_json,
            description = excluded.description,
            salary = excluded.salary,
            track = excluded.track,
            stage = excluded.stage,
            team_size = excluded.team_size,
            founder = excluded.founder,
            founder_socials = excluded.founder_socials,
            source_url = excluded.source_url,
            external_job_id = excluded.external_job_id,
            source_record_id = excluded.source_record_id,
            published_at = excluded.published_at,
            source_updated_at = excluded.source_updated_at,
            is_hot = excluded.is_hot,
            raw_json = excluded.raw_json,
            recruiter_scope = excluded.recruiter_scope,
            status = excluded.status,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    job_id = int(cur.fetchone()["id"])
    conn.commit()
    return job_id


def upsert_recruiter(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    company_id = item.get("company_id")
    if not company_id and item.get("company"):
        row = conn.execute(
            "SELECT id FROM companies WHERE name = ? OR slug = ? ORDER BY id LIMIT 1",
            (item["company"], item["company"]),
        ).fetchone()
        company_id = int(row["id"]) if row else None
    fields = {
        "company_id": company_id,
        "name": item["name"].strip(),
        "email": item["email"].strip().lower(),
        "company": item["company"].strip(),
        "whitelisted": 1 if item.get("whitelisted") else 0,
        "job_ids_json": dumps(item.get("job_ids", [])),
    }
    cur = conn.execute(
        """
        INSERT INTO recruiters (company_id, name, email, company, whitelisted, job_ids_json)
        VALUES (:company_id, :name, :email, :company, :whitelisted, :job_ids_json)
        ON CONFLICT(email) DO UPDATE SET
            company_id = excluded.company_id,
            name = excluded.name,
            company = excluded.company,
            whitelisted = excluded.whitelisted,
            job_ids_json = excluded.job_ids_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    recruiter_id = int(cur.fetchone()["id"])
    conn.commit()
    return recruiter_id


def upsert_source_record(
    conn: sqlite3.Connection,
    source: str,
    source_table: str,
    source_record_id: str,
    local_table: str,
    local_id: int,
    raw: dict[str, Any],
) -> int:
    cur = conn.execute(
        """
        INSERT INTO source_records (source, source_table, source_record_id, local_table, local_id, raw_json)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(source, source_table, source_record_id, local_table) DO UPDATE SET
            local_id = excluded.local_id,
            raw_json = excluded.raw_json,
            imported_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (source, source_table, source_record_id, local_table, local_id, json.dumps(raw, ensure_ascii=False)),
    )
    source_id = int(cur.fetchone()["id"])
    conn.commit()
    return source_id


def upsert_recruiting_request(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    fields = {
        "source_email_id": item["source_email_id"],
        "source_subject": item.get("source_subject", ""),
        "sender_email": item.get("sender_email", "").strip().lower(),
        "recruiter_name": item.get("recruiter_name", ""),
        "company": item.get("company", ""),
        "jd_text": item.get("jd_text", ""),
        "preference_text": item.get("preference_text", ""),
        "client_id": item.get("client_id"),
        "recruiter_id": item.get("recruiter_id"),
        "job_id": item.get("job_id"),
        "status": item.get("status", "parsed"),
        "error_text": item.get("error_text", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
    }
    cur = conn.execute(
        """
        INSERT INTO recruiting_requests (
            source_email_id, source_subject, sender_email, recruiter_name,
            company, jd_text, preference_text, client_id, recruiter_id, job_id,
            status, error_text, raw_json
        ) VALUES (
            :source_email_id, :source_subject, :sender_email, :recruiter_name,
            :company, :jd_text, :preference_text, :client_id, :recruiter_id,
            :job_id, :status, :error_text, :raw_json
        )
        ON CONFLICT(source_email_id) DO UPDATE SET
            source_subject = excluded.source_subject,
            sender_email = excluded.sender_email,
            recruiter_name = excluded.recruiter_name,
            company = excluded.company,
            jd_text = excluded.jd_text,
            preference_text = excluded.preference_text,
            client_id = excluded.client_id,
            recruiter_id = excluded.recruiter_id,
            job_id = excluded.job_id,
            status = excluded.status,
            error_text = excluded.error_text,
            raw_json = excluded.raw_json,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    request_id = int(cur.fetchone()["id"])
    conn.commit()
    return request_id


def create_candidate_outreach_record(
    conn: sqlite3.Connection,
    request_id: int,
    candidate_id: int,
    job_id: int | None,
    recruiter_id: int | None,
    candidate_draft_id: str,
    status: str = "drafted",
    error_text: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO candidate_outreach (
            request_id, candidate_id, job_id, recruiter_id, candidate_draft_id,
            status, error_text
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(request_id, candidate_id) DO UPDATE SET
            job_id = excluded.job_id,
            recruiter_id = excluded.recruiter_id,
            candidate_draft_id = CASE
                WHEN excluded.candidate_draft_id != '' THEN excluded.candidate_draft_id
                ELSE candidate_outreach.candidate_draft_id
            END,
            status = excluded.status,
            error_text = excluded.error_text,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (request_id, candidate_id, job_id, recruiter_id, candidate_draft_id, status, error_text),
    )
    outreach_id = int(cur.fetchone()["id"])
    conn.commit()
    return outreach_id


def mark_outreach_interested(conn: sqlite3.Connection, outreach_id: int, reply_text: str = "", source_reply_email_id: str = "") -> None:
    conn.execute(
        """
        UPDATE candidate_outreach
        SET interest_status = 'interested',
            status = 'interested',
            reply_text = ?,
            source_reply_email_id = ?,
            error_text = '',
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (reply_text, source_reply_email_id, outreach_id),
    )
    conn.commit()


def set_outreach_forward_draft(conn: sqlite3.Connection, outreach_id: int, draft_id: str, error_text: str = "") -> None:
    status = "forward_drafted" if draft_id else "forward_failed"
    conn.execute(
        """
        UPDATE candidate_outreach
        SET forward_draft_id = ?,
            status = ?,
            error_text = ?,
            updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (draft_id, status, error_text, outreach_id),
    )
    conn.commit()


def upsert_followup(
    conn: sqlite3.Connection,
    outreach_id: int,
    candidate_id: int | None,
    job_id: int | None,
    recruiter_id: int | None,
    due_at: str,
    message: str,
) -> int:
    cur = conn.execute(
        """
        INSERT INTO followups (outreach_id, candidate_id, job_id, recruiter_id, due_at, message)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(outreach_id) DO UPDATE SET
            candidate_id = excluded.candidate_id,
            job_id = excluded.job_id,
            recruiter_id = excluded.recruiter_id,
            due_at = excluded.due_at,
            message = excluded.message,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        (outreach_id, candidate_id, job_id, recruiter_id, due_at, message),
    )
    followup_id = int(cur.fetchone()["id"])
    conn.commit()
    return followup_id


def mark_followup_sent(conn: sqlite3.Connection, followup_id: int, sent_at: str) -> None:
    conn.execute(
        """
        UPDATE followups
        SET status = 'sent', sent_at = ?, error_text = '', updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (sent_at, followup_id),
    )
    conn.commit()


def mark_followup_failed(conn: sqlite3.Connection, followup_id: int, error_text: str) -> None:
    conn.execute(
        """
        UPDATE followups
        SET status = 'failed', error_text = ?, updated_at = CURRENT_TIMESTAMP
        WHERE id = ?
        """,
        (error_text, followup_id),
    )
    conn.commit()


def fetch_all(conn: sqlite3.Connection, query: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
    return list(conn.execute(query, tuple(params)).fetchall())


def log_event(
    conn: sqlite3.Connection,
    event: str,
    candidate_id: int | None,
    job_id: int | None,
    recruiter_id: int | None,
    source_email_id: str = "",
    explanation: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO audit_logs (event, candidate_id, job_id, recruiter_id, source_email_id, explanation)
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (event, candidate_id, job_id, recruiter_id, source_email_id, explanation),
    )


def candidate_forward_exists(conn: sqlite3.Connection, candidate_id: int, recipient_email: str) -> bool:
    row = conn.execute(
        """
        SELECT id FROM candidate_forwards
        WHERE candidate_id = ? AND recipient_email = ? AND status = 'sent'
        ORDER BY id LIMIT 1
        """,
        (candidate_id, recipient_email.strip().lower()),
    ).fetchone()
    return row is not None


def upsert_candidate_forward(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    fields = {
        "candidate_id": item["candidate_id"],
        "recipient_email": item["recipient_email"].strip().lower(),
        "recipient_name": item.get("recipient_name", ""),
        "headhunter_id": item.get("headhunter_id"),
        "client_id": item.get("client_id"),
        "subject": item.get("subject", ""),
        "body": item.get("body", ""),
        "attachment_paths_json": json.dumps(item.get("attachment_paths", []), ensure_ascii=False),
        "message_id": item.get("message_id", ""),
        "status": item.get("status", "pending"),
        "error_text": item.get("error_text", ""),
    }
    cur = conn.execute(
        """
        INSERT INTO candidate_forwards (
            candidate_id, recipient_email, recipient_name, headhunter_id,
            client_id, subject, body, attachment_paths_json, message_id,
            status, error_text
        ) VALUES (
            :candidate_id, :recipient_email, :recipient_name, :headhunter_id,
            :client_id, :subject, :body, :attachment_paths_json, :message_id,
            :status, :error_text
        )
        ON CONFLICT(candidate_id, recipient_email) DO UPDATE SET
            recipient_name = excluded.recipient_name,
            headhunter_id = excluded.headhunter_id,
            client_id = excluded.client_id,
            subject = excluded.subject,
            body = excluded.body,
            attachment_paths_json = excluded.attachment_paths_json,
            message_id = excluded.message_id,
            status = excluded.status,
            error_text = excluded.error_text,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    forward_id = int(cur.fetchone()["id"])
    conn.commit()
    return forward_id


def upsert_mail_ingestion_item(conn: sqlite3.Connection, item: dict[str, Any]) -> int:
    fields = {
        "source_email_id": item["source_email_id"],
        "source_subject": item.get("source_subject", ""),
        "sender_email": item.get("sender_email", "").strip().lower(),
        "classification": item.get("classification", ""),
        "status": item.get("status", ""),
        "local_table": item.get("local_table", ""),
        "local_id": item.get("local_id"),
        "attachment_manifest_json": json.dumps(item.get("attachments", []), ensure_ascii=False),
        "error_text": item.get("error_text", ""),
        "raw_json": json.dumps(item.get("raw", {}), ensure_ascii=False),
    }
    cur = conn.execute(
        """
        INSERT INTO mail_ingestion_items (
            source_email_id, source_subject, sender_email, classification,
            status, local_table, local_id, attachment_manifest_json,
            error_text, raw_json
        ) VALUES (
            :source_email_id, :source_subject, :sender_email, :classification,
            :status, :local_table, :local_id, :attachment_manifest_json,
            :error_text, :raw_json
        )
        ON CONFLICT(source_email_id, classification) DO UPDATE SET
            source_subject = excluded.source_subject,
            sender_email = excluded.sender_email,
            status = excluded.status,
            local_table = excluded.local_table,
            local_id = excluded.local_id,
            attachment_manifest_json = excluded.attachment_manifest_json,
            error_text = excluded.error_text,
            raw_json = excluded.raw_json,
            processed_at = CURRENT_TIMESTAMP,
            updated_at = CURRENT_TIMESTAMP
        RETURNING id
        """,
        fields,
    )
    item_id = int(cur.fetchone()["id"])
    conn.commit()
    return item_id
