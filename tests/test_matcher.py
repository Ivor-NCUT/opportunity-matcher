# ## 核心功能
# 验证匹配器先按客户库优先，再做硬条件过滤、过滤低分岗位，最后返回最多 3 个岗位。
# ## 输入
# 内存 SQLite 数据库、测试候选人和测试岗位。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 匹配逻辑的回归测试，不覆盖 outbox 和审计链路。
# ## 依赖
# `opportunity_matcher.db`、`opportunity_matcher.matcher` 和 Python 标准库 `unittest`。
# ## 维护规则
# 客户优先策略、匹配权重、最低分阈值、硬条件或推荐数量变化时同步更新测试。

import sqlite3
import unittest

from opportunity_matcher.db import init_db, upsert_candidate, upsert_client, upsert_job
from opportunity_matcher.matcher import match_candidate


class MatcherTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_hard_filter_then_top_three(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "a@example.com",
                "name": "候选人A",
                "city": "上海",
                "level": "junior",
                "work_type": "full-time",
                "skills": ["ai operations", "customer success", "saas", "content operations"],
                "evidence": ["AI SaaS 客户成功和内容运营"],
                "resume_text": "ai operations customer success saas content operations",
            },
        )
        for index in range(5):
            upsert_job(
                self.conn,
                {
                    "company": f"Company{index}",
                    "title": f"Job{index}",
                    "city": "上海",
                    "level_min": "intern",
                    "level_max": "mid",
                    "work_type": "full-time",
                    "required_skills": ["ai operations"],
                    "preferred_skills": ["saas"],
                    "status": "open",
                },
            )
        upsert_job(
            self.conn,
            {
                "company": "Blocked",
                "title": "WrongCity",
                "city": "北京",
                "level_min": "intern",
                "level_max": "mid",
                "work_type": "full-time",
                "required_skills": ["ai operations"],
                "status": "open",
            },
        )

        results = match_candidate(self.conn, candidate_id)

        self.assertEqual(len(results), 3)
        self.assertTrue(all(result.score > 0 for result in results))
        self.assertNotIn("Blocked", [self.conn.execute("SELECT company FROM jobs WHERE id = ?", (result.job_id,)).fetchone()["company"] for result in results])

    def test_low_score_jobs_are_not_recommended(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "low@example.com",
                "name": "低匹配候选人",
                "city": "上海",
                "level": "junior",
                "work_type": "full-time",
                "skills": ["database", "teaching"],
                "resume_text": "database teaching computer science",
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

        self.assertEqual(match_candidate(self.conn, candidate_id), [])

    def test_client_jobs_take_priority_over_general_jobs(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "client-first@example.com",
                "name": "客户优先候选人",
                "city": "北京",
                "level": "intern",
                "work_type": "intern",
                "skills": ["ai operations", "market research", "investment research", "growth", "sales"],
                "resume_text": "ai operations market research investment research growth sales",
            },
        )
        upsert_client(
            self.conn,
            {
                "name": "真格基金",
                "slug": "zhenfund",
                "contact_email": "joyce@zhenfund.com",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "真格基金",
                "company_slug": "zhenfund",
                "title": "投资运营实习生",
                "city": "北京",
                "level_min": "intern",
                "level_max": "intern",
                "work_type": "intern",
                "required_skills": ["ai operations", "market research"],
                "status": "open",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "普通职位公司",
                "title": "增长运营",
                "city": "北京",
                "level_min": "intern",
                "level_max": "intern",
                "work_type": "intern",
                "required_skills": ["growth", "sales"],
                "preferred_skills": ["ai operations", "market research"],
                "status": "open",
            },
        )

        results = match_candidate(self.conn, candidate_id)
        companies = [self.company_for(result.job_id) for result in results]
        self.assertEqual(companies, ["真格基金"])

    def test_general_jobs_are_used_when_client_jobs_have_no_positive_match(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "fallback@example.com",
                "name": "回退候选人",
                "city": "北京",
                "level": "intern",
                "work_type": "intern",
                "skills": ["growth", "sales"],
                "resume_text": "growth sales",
            },
        )
        upsert_client(
            self.conn,
            {
                "name": "真格基金",
                "slug": "zhenfund",
                "contact_email": "joyce@zhenfund.com",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "真格基金",
                "company_slug": "zhenfund",
                "title": "投资运营实习生",
                "city": "北京",
                "level_min": "intern",
                "level_max": "intern",
                "work_type": "intern",
                "required_skills": ["investment research", "market research"],
                "status": "open",
            },
        )
        upsert_job(
            self.conn,
            {
                "company": "普通职位公司",
                "title": "增长运营",
                "city": "北京",
                "level_min": "intern",
                "level_max": "intern",
                "work_type": "intern",
                "required_skills": ["growth", "sales"],
                "status": "open",
            },
        )

        results = match_candidate(self.conn, candidate_id)
        companies = [self.company_for(result.job_id) for result in results]
        self.assertEqual(companies, ["普通职位公司"])

    def company_for(self, job_id: int) -> str:
        return self.conn.execute("SELECT company FROM jobs WHERE id = ?", (job_id,)).fetchone()["company"]


if __name__ == "__main__":
    unittest.main()
