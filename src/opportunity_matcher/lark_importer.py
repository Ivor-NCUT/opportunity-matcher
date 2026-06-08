# ## 核心功能
# 把飞书 Base 导出的团队、职位、候选人 JSON 快照导入本地产品数据库。
# ## 输入
# `lark-cli base +record-list --format json` 生成的 teams/jobs/candidates JSON 文件。
# ## 输出
# 本地 companies、jobs、candidates 和 source_records 写入结果统计。
# ## 定位
# 一次性外部数据导入适配层，只处理飞书快照格式，不直接访问飞书 API。
# ## 依赖
# `db.upsert_company`、`db.upsert_job`、`db.upsert_candidate`、`db.upsert_source_record`。
# ## 维护规则
# 飞书字段名、导出格式或本地表结构变化时，同步更新字段映射和测试。

from __future__ import annotations

import json
import re
import sqlite3
from pathlib import Path
from typing import Any

from .db import upsert_candidate, upsert_company, upsert_job, upsert_source_record


LARK_SOURCE = "lark_base:A80Xb9jOnaexcKswFkacPBoEnAf"


def import_lark_dir(conn: sqlite3.Connection, directory: str | Path) -> dict[str, int]:
    path = Path(directory)
    counts = {"companies": 0, "jobs": 0, "candidates": 0, "source_records": 0}
    teams_file = path / "teams.json"
    jobs_file = path / "jobs.json"
    candidates_file = path / "candidates.json"

    if teams_file.exists():
        for record in read_records(teams_file):
            company_id = upsert_company(conn, team_to_company(record))
            upsert_source_record(conn, LARK_SOURCE, "团队", record["_record_id"], "companies", company_id, record)
            counts["companies"] += 1
            counts["source_records"] += 1

    if jobs_file.exists():
        for record in read_records(jobs_file):
            job_id = upsert_job(conn, job_record_to_job(record))
            upsert_source_record(conn, LARK_SOURCE, "职位", record["_record_id"], "jobs", job_id, record)
            counts["jobs"] += 1
            counts["source_records"] += 1

    if candidates_file.exists():
        for record in read_records(candidates_file):
            candidate = candidate_record_to_candidate(record)
            if not candidate.get("email"):
                continue
            candidate_id = upsert_candidate(conn, candidate)
            upsert_source_record(conn, LARK_SOURCE, "候选人", record["_record_id"], "candidates", candidate_id, record)
            counts["candidates"] += 1
            counts["source_records"] += 1

    return counts


def read_records(path: Path) -> list[dict[str, Any]]:
    envelope = json.loads(path.read_text(encoding="utf-8"))
    data = envelope["data"]
    fields = data["fields"]
    record_ids = data.get("record_id_list", [])
    rows = data.get("data", [])
    records = []
    for index, row in enumerate(rows):
        record = {field: row[position] if position < len(row) else None for position, field in enumerate(fields)}
        record["_record_id"] = record_ids[index] if index < len(record_ids) else ""
        records.append(record)
    return records


def team_to_company(record: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": text(record.get("团队名")),
        "slug": text(record.get("团队Slug")),
        "website": text(record.get("官网")),
        "jobs_url": text(record.get("招聘页")),
        "source_url": text(record.get("来源链接")),
        "locations": text(record.get("地点")),
        "track": text(record.get("赛道")),
        "stage": text(record.get("公司阶段")),
        "team_size": text(record.get("团队人数")),
        "founders": text(record.get("创始人")),
        "founder_socials": text(record.get("创始人社媒")),
        "description": text(record.get("公司简介")),
        "values_text": text(record.get("价值观/团队文化")),
        "interesting_info": text(record.get("有趣信息")),
        "tech_stack": text(record.get("技术栈")),
        "funding_info": text(record.get("融资信息")),
        "logo_url": text(record.get("Logo")),
        "source_record_id": record["_record_id"],
        "raw": record,
    }


def job_record_to_job(record: dict[str, Any]) -> dict[str, Any]:
    level_min, level_max = parse_level_range(text(record.get("经验")))
    title = text(record.get("职位"))
    description = text(record.get("岗位描述"))
    return {
        "company": text(record.get("团队名")),
        "company_slug": text(record.get("团队Slug")),
        "title": title,
        "city": text(record.get("地点")),
        "level_min": level_min,
        "level_max": level_max,
        "work_type": infer_work_type(text(record.get("地点")), description),
        "required_skills": infer_skills(title + "\n" + description),
        "preferred_skills": infer_preferred_skills(title + "\n" + description + "\n" + text(record.get("赛道"))),
        "description": description,
        "salary": text(record.get("薪资")),
        "track": text(record.get("赛道")),
        "stage": text(record.get("公司阶段")),
        "team_size": text(record.get("团队人数")),
        "founder": text(record.get("创始人")),
        "founder_socials": text(record.get("创始人社媒")),
        "source_url": text(record.get("来源链接")),
        "external_job_id": text(record.get("职位ID")),
        "source_record_id": record["_record_id"],
        "published_at": text(record.get("发布时间")),
        "source_updated_at": text(record.get("更新时间")),
        "is_hot": bool(record.get("热门")),
        "status": normalize_status(text(record.get("状态"))),
        "company_description": text(record.get("公司简介")),
        "raw": record,
    }


def candidate_record_to_candidate(record: dict[str, Any]) -> dict[str, Any]:
    email = extract_email(text(record.get("邮箱")))
    resume_files = attachments(record.get("简历"))
    portfolio_files = attachments(record.get("作品集/补充材料"))
    return {
        "email": email,
        "name": text(record.get("姓名")) or text(record.get("发件人显示名")) or email,
        "phone_or_wechat": text(record.get("手机号/微信/其他联系方式")),
        "sender_name": text(record.get("发件人显示名")),
        "direction": text(record.get("投递方向")),
        "source_subject": text(record.get("邮件主题")),
        "source_email_id": text(record.get("来源邮件ID")),
        "source_record_id": record["_record_id"],
        "first_seen_at": text(record.get("首次来信时间")),
        "last_seen_at": text(record.get("最近来信时间")),
        "resume_uri": "; ".join(resume_files),
        "portfolio": portfolio_files,
        "resume_text": text(record.get("邮件正文摘要")),
        "evidence": [text(record.get("邮件正文摘要")), text(record.get("备注")), text(record.get("投递方向"))],
        "skills": infer_skills(text(record.get("投递方向")) + "\n" + text(record.get("邮件正文摘要"))),
        "status": "pending",
        "raw": record,
    }


def text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, str):
        return value.replace("<br>", "\n").strip()
    if isinstance(value, list):
        if value and isinstance(value[0], dict) and "name" in value[0]:
            return "；".join(str(item.get("name", "")) for item in value)
        return "；".join(text(item) for item in value if text(item))
    if isinstance(value, dict):
        return value.get("text") or value.get("name") or json.dumps(value, ensure_ascii=False)
    return str(value)


def attachments(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        if isinstance(item, dict):
            names.append(item.get("name") or item.get("file_token") or "")
        else:
            names.append(text(item))
    return [name for name in names if name]


def extract_email(value: str) -> str:
    match = re.search(r"mailto:([^)\s]+)", value)
    if match:
        return match.group(1).strip().lower()
    match = re.search(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", value)
    return match.group(0).strip().lower() if match else ""


def parse_level_range(value: str) -> tuple[str, str]:
    normalized = value.lower()
    levels = []
    for key in ["intern", "junior", "mid", "senior", "lead"]:
        if key in normalized:
            levels.append(key)
    if "实习" in value and "intern" not in levels:
        levels.insert(0, "intern")
    if "c-level" in normalized:
        levels.append("lead")
    if not levels:
        return "", ""
    return levels[0], levels[-1]


def infer_work_type(city: str, description: str) -> str:
    combined = city + "\n" + description
    if "实习" in combined:
        return "intern"
    return "full-time"


def normalize_status(value: str) -> str:
    return "open" if value in {"", "published"} else value


def infer_skills(text_value: str) -> list[str]:
    lower = text_value.lower()
    mapping = {
        "ai operations": ["ai运营", "ai 运营", "ai-native", "ai native", "ai工具", "ai 工具", "ai workflow", "ai 工作流"],
        "ai agent": ["agent", "智能体", "openclaw", "manus", "cursor", "claude code"],
        "customer success": ["客户成功", "用户成功", "onboarding", "用户访谈", "客户", "用户反馈"],
        "content operations": ["内容", "社媒", "小红书", "公众号", "kol", "品牌", "传播", "社区运营"],
        "growth": ["增长", "获客", "转化", "growth", "流量", "营销"],
        "sales": ["销售", "商务", "bd", "商业化", "营收"],
        "crm": ["crm", "私域", "sop", "线索"],
        "frontend": ["前端", "react", "vue", "typescript", "webgl"],
        "backend": ["后端", "python", "golang", "go ", "node.js", "k8s", "docker"],
        "machine learning": ["机器学习", "深度学习", "pytorch", "transformer", "rag", "llm", "多模态"],
        "hardware": ["嵌入式", "硬件", "linux驱动", "c/c++", "pcba"],
        "design": ["设计", "product designer", "ui", "交互", "figma"],
    }
    hits = []
    for skill, keywords in mapping.items():
        if any(keyword.lower() in lower for keyword in keywords):
            hits.append(skill)
    return hits


def infer_preferred_skills(text_value: str) -> list[str]:
    skills = infer_skills(text_value)
    return [skill for skill in skills if skill not in {"frontend", "backend", "machine learning", "hardware"}]
