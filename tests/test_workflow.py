# ## 核心功能
# 验证候选人处理工作流、白名单推送边界和简历更新入库。
# ## 输入
# 内存 SQLite 数据库、测试候选人、岗位和招聘方联系人。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 业务闭环回归测试，覆盖 outbox、审计前置事件和候选人更新状态。
# ## 依赖
# `opportunity_matcher.db`、`opportunity_matcher.workflow` 和 Python 标准库 `unittest`。
# ## 维护规则
# 候选人状态、白名单规则或 outbox 类型变化时同步更新测试。

import sqlite3
import unittest

from opportunity_matcher.db import init_db, upsert_candidate, upsert_job, upsert_recruiter
from opportunity_matcher.workflow import process_candidate


class WorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_process_candidate_drafts_candidate_and_whitelist_only_recruiter_outbox(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "candidate@example.com",
                "name": "候选人",
                "city": "上海",
                "level": "junior",
                "work_type": "full-time",
                "skills": ["ai operations", "customer success"],
                "resume_text": "ai operations customer success",
                "source_email_id": "mail-1",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "WhitelistedCo",
                "title": "AI 客户运营",
                "city": "上海",
                "level_min": "intern",
                "level_max": "mid",
                "work_type": "full-time",
                "required_skills": ["ai operations"],
                "status": "open",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "NotWhitelistedCo",
                "title": "AI 内容运营",
                "city": "上海",
                "level_min": "intern",
                "level_max": "mid",
                "work_type": "full-time",
                "required_skills": ["customer success"],
                "status": "open",
            },
        )
        upsert_recruiter(
            self.conn,
            {
                "name": "白名单招聘方",
                "email": "white@example.com",
                "company": "WhitelistedCo",
                "whitelisted": True,
            },
        )
        upsert_recruiter(
            self.conn,
            {
                "name": "非白名单招聘方",
                "email": "blocked@example.com",
                "company": "NotWhitelistedCo",
                "whitelisted": False,
            },
        )

        process_candidate(self.conn, candidate_id)

        outbox = self.conn.execute("SELECT kind, recipient_email FROM outbox ORDER BY id").fetchall()
        self.assertEqual([row["kind"] for row in outbox], ["candidate_reply", "recruiter_push"])
        self.assertEqual(outbox[1]["recipient_email"], "white@example.com")

    def test_resume_update_reuses_candidate_record(self) -> None:
        first_id = upsert_candidate(
            self.conn,
            {
                "email": "same@example.com",
                "name": "同一候选人",
                "city": "上海",
                "level": "junior",
                "work_type": "full-time",
                "resume_text": "old",
                "source_subject": "投递｜同一候选人",
            },
        )
        second_id = upsert_candidate(
            self.conn,
            {
                "email": "same@example.com",
                "name": "同一候选人",
                "city": "北京",
                "level": "mid",
                "work_type": "full-time",
                "resume_text": "new",
                "source_subject": "简历更新｜同一候选人",
            },
        )

        row = self.conn.execute("SELECT city, level, resume_text, status FROM candidates WHERE id = ?", (first_id,)).fetchone()
        self.assertEqual(first_id, second_id)
        self.assertEqual(row["city"], "北京")
        self.assertEqual(row["level"], "mid")
        self.assertEqual(row["resume_text"], "new")
        self.assertEqual(row["status"], "pending_update")

    def test_no_recruiter_push_when_no_positive_match(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "weak@example.com",
                "name": "弱匹配候选人",
                "city": "上海",
                "level": "junior",
                "work_type": "full-time",
                "skills": ["database", "teaching"],
                "resume_text": "database teaching",
                "source_email_id": "mail-weak",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "Openmart",
                "title": "AI 客户运营",
                "city": "上海",
                "level_min": "intern",
                "level_max": "mid",
                "work_type": "full-time",
                "required_skills": ["ai operations", "customer success"],
                "status": "open",
            },
        )
        upsert_recruiter(
            self.conn,
            {
                "name": "白名单招聘方",
                "email": "white@example.com",
                "company": "Openmart",
                "whitelisted": True,
            },
        )

        process_candidate(self.conn, candidate_id)

        outbox = self.conn.execute("SELECT kind, recipient_email FROM outbox ORDER BY id").fetchall()
        self.assertEqual(len(outbox), 1)
        self.assertEqual(outbox[0]["kind"], "candidate_reply")


if __name__ == "__main__":
    unittest.main()
