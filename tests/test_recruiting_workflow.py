# ## 核心功能
# 验证招聘方合作邮件同步、候选人触达、兴趣确认、简历转发和跟进提醒。
# ## 输入
# 内存 SQLite 数据库、模拟 lark-cli 输出、模拟飞书群机器人发送器。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 招聘方合作流程回归测试，不访问真实飞书邮箱和真实 webhook。
# ## 依赖
# `opportunity_matcher.recruiting_workflow`、`db`、Python 标准库 `unittest`。
# ## 维护规则
# 招聘合作邮件格式、草稿状态、跟进提醒或外部命令参数变化时同步更新本测试。

import json
import sqlite3
import unittest
from datetime import datetime, timezone

from opportunity_matcher.db import init_db, upsert_candidate
from opportunity_matcher.mail_classifier import MailClassification
from opportunity_matcher.recruiting_workflow import (
    draft_candidate_outreach,
    import_recruiting_mail,
    mark_interested_and_draft_forward,
    review_interest,
    send_due_followups,
    sync_recruiting_mails,
)


class RecruitingWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    @staticmethod
    def classifier(label: str = "recruiting", reason: str = "test"):
        def _classify(message: dict) -> MailClassification:
            return MailClassification(label=label, confidence="high", reason=reason, provider="test", model="fake")

        return _classify

    def test_import_recruiting_mail_creates_request_client_recruiter_and_job(self) -> None:
        status = import_recruiting_mail(
            self.conn,
            {
                "message_id": "mail-recruiting-1",
                "subject": "招聘合作｜张三 & San｜Openmart",
                "from": "san@example.com",
                "body": "岗位：AI 客户运营\n需要 AI 运营、客户成功和内容运营经验。",
            },
        )

        request = self.conn.execute("SELECT * FROM recruiting_requests").fetchone()
        job = self.conn.execute("SELECT * FROM jobs").fetchone()
        recruiter = self.conn.execute("SELECT * FROM recruiters").fetchone()

        self.assertEqual(status, "parsed")
        self.assertEqual(request["company"], "Openmart")
        self.assertEqual(request["status"], "parsed")
        self.assertEqual(job["title"], "AI 客户运营")
        self.assertEqual(recruiter["email"], "san@example.com")
        self.assertEqual(recruiter["whitelisted"], 1)

    def test_import_invalid_subject_goes_to_manual_review(self) -> None:
        status = import_recruiting_mail(
            self.conn,
            {
                "message_id": "mail-bad",
                "subject": "想招人",
                "from": "bad@example.com",
                "body": "JD",
            },
        )

        request = self.conn.execute("SELECT status, error_text FROM recruiting_requests WHERE source_email_id = 'mail-bad'").fetchone()
        self.assertEqual(status, "needs_review")
        self.assertEqual(request["status"], "needs_review")
        self.assertIn("标题", request["error_text"])

    def test_sync_recruiting_mails_uses_lark_cli_runner(self) -> None:
        calls = []

        def runner(command: list[str]) -> str:
            calls.append(command)
            if "+triage" in command:
                return json.dumps([{"message_id": "mail-1", "subject": "招聘合作｜张三｜Openmart"}], ensure_ascii=False)
            return json.dumps([{"message_id": "mail-1", "from": "san@example.com", "body": "岗位：AI 客户运营\n需要 AI 运营。"}], ensure_ascii=False)

        counts = sync_recruiting_mails(self.conn, runner=runner, classifier=self.classifier())

        self.assertEqual(counts, {"seen": 1, "parsed": 1, "needs_review": 0})
        self.assertIn("+triage", calls[0])
        self.assertIn("+messages", calls[1])

    def test_sync_recruiting_mails_respects_classifier_review(self) -> None:
        def runner(command: list[str]) -> str:
            if "+triage" in command:
                return json.dumps([{"message_id": "mail-1", "subject": "招聘合作｜张三｜Openmart"}], ensure_ascii=False)
            return json.dumps([{"message_id": "mail-1", "from": "san@example.com", "body": "这里其实是在约面试，不是委托招聘。"}], ensure_ascii=False)

        counts = sync_recruiting_mails(
            self.conn,
            runner=runner,
            classifier=self.classifier(label="review", reason="内容像沟通邮件，不像正式招聘委托"),
        )
        request = self.conn.execute("SELECT status, error_text FROM recruiting_requests WHERE source_email_id = 'mail-1'").fetchone()

        self.assertEqual(counts, {"seen": 1, "parsed": 0, "needs_review": 1})
        self.assertEqual(request["status"], "needs_review")
        self.assertIn("方舟模型未将该邮件判定为招聘合作", request["error_text"])

    def test_draft_outreach_then_mark_interested_creates_forward_and_followup(self) -> None:
        candidate_id = upsert_candidate(
            self.conn,
            {
                "email": "candidate@example.com",
                "name": "候选人",
                "city": "",
                "level": "junior",
                "work_type": "",
                "skills": ["ai operations", "customer success"],
                "resume_text": "ai operations customer success",
                "source_email_id": "mail-candidate-1",
                "resume_uri": "local://resume.pdf",
                "referrer_name": "推荐朋友",
            },
        )
        import_recruiting_mail(
            self.conn,
            {
                "message_id": "mail-recruiting-1",
                "subject": "招聘合作｜张三｜Openmart",
                "from": "san@example.com",
                "body": "岗位：AI 客户运营\n需要 AI 运营、客户成功。",
            },
        )
        request_id = self.conn.execute("SELECT id FROM recruiting_requests").fetchone()["id"]
        runner_calls = []

        def runner(command: list[str]) -> str:
            runner_calls.append(command)
            if "+forward" in command:
                return json.dumps({"draft_id": "forward-draft-1"})
            return json.dumps({"draft_id": "candidate-draft-1"})

        outreach_ids = draft_candidate_outreach(self.conn, request_id, runner=runner)
        pending = review_interest(self.conn, outreach_ids[0], reply_text="我感兴趣，想看看。")
        followup_id = mark_interested_and_draft_forward(
            self.conn,
            outreach_ids[0],
            reply_text="我感兴趣，想看看。",
            runner=runner,
            now=datetime(2026, 6, 8, tzinfo=timezone.utc),
        )

        outreach = self.conn.execute("SELECT * FROM candidate_outreach WHERE candidate_id = ?", (candidate_id,)).fetchone()
        followup = self.conn.execute("SELECT * FROM followups WHERE id = ?", (followup_id,)).fetchone()

        self.assertEqual(len(outreach_ids), 1)
        self.assertEqual(pending[0]["id"], outreach_ids[0])
        self.assertEqual(outreach["candidate_draft_id"], "candidate-draft-1")
        self.assertEqual(outreach["forward_draft_id"], "forward-draft-1")
        self.assertEqual(outreach["interest_status"], "interested")
        self.assertIn("+send", runner_calls[0])
        self.assertIn("+forward", runner_calls[1])
        self.assertEqual(followup["status"], "pending")
        self.assertTrue(followup["due_at"].startswith("2026-06-12"))
        self.assertIn("推荐朋友", followup["message"])

    def test_send_due_followups_posts_and_marks_sent(self) -> None:
        self.conn.execute(
            """
            INSERT INTO followups (outreach_id, due_at, message, status)
            VALUES (1, '2026-06-08T00:00:00+00:00', '提醒内容', 'pending')
            """
        )
        self.conn.commit()
        sent = []

        def poster(url: str, message: str) -> None:
            sent.append((url, message))

        count = send_due_followups(
            self.conn,
            webhook_url="https://example.com/hook",
            now=datetime(2026, 6, 8, 1, tzinfo=timezone.utc),
            poster=poster,
        )
        row = self.conn.execute("SELECT status FROM followups").fetchone()

        self.assertEqual(count, 1)
        self.assertEqual(sent, [("https://example.com/hook", "提醒内容")])
        self.assertEqual(row["status"], "sent")


if __name__ == "__main__":
    unittest.main()
