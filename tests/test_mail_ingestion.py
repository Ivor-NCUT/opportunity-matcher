# ## 核心功能
# 验证每日飞书邮箱入库能同步招聘邮件、候选人邮件、附件文本和幂等状态。
# ## 输入
# 内存 SQLite 数据库、模拟 lark-cli 输出和模拟附件下载器。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 邮箱入库流程回归测试，不访问真实飞书邮箱、不下载真实附件。
# ## 依赖
# `opportunity_matcher.mail_ingestion`、`cli`、`db`、Python 标准库 `unittest`。
# ## 维护规则
# 入库摘要字段、附件处理策略或 CLI 参数变化时同步更新本测试。

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from opportunity_matcher.cli import main
from opportunity_matcher.db import init_db
from opportunity_matcher.mail_ingestion import sync_mail_inbox


class MailIngestionTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_sync_mail_inbox_imports_candidate_with_attachment_text(self) -> None:
        calls = []

        def runner(command: list[str]) -> str:
            calls.append(command)
            if "+triage" in command and "招聘合作" in command:
                return json.dumps([{"message_id": "mail-recruiting-1", "subject": "招聘合作｜张三｜Openmart"}], ensure_ascii=False)
            if "+messages" in command and "mail-recruiting-1" in ",".join(command):
                return json.dumps(
                    [{"message_id": "mail-recruiting-1", "from": "san@example.com", "body": "岗位：AI 客户运营\n需要 AI 运营。"}],
                    ensure_ascii=False,
                )
            if "+triage" in command:
                query = command[command.index("--query") + 1]
                if query == "简历":
                    return json.dumps(
                        [
                            {
                                "message_id": "mail-candidate-1",
                                "subject": "投递｜李四｜AI 运营",
                                "from": {"name": "李四", "email": "lisi@example.com"},
                            }
                        ],
                        ensure_ascii=False,
                    )
                return "[]"
            if "+messages" in command and "mail-candidate-1" in ",".join(command):
                return json.dumps(
                    [
                        {
                            "message_id": "mail-candidate-1",
                            "subject": "投递｜李四｜AI 运营",
                            "from": {"name": "李四", "email": "lisi@example.com"},
                            "body": "我想投递 AI 运营，有客户成功经验。",
                            "attachments": [{"attachment_id": "att-1", "name": "李四简历.txt"}],
                        }
                    ],
                    ensure_ascii=False,
                )
            if "download_url" in command:
                return json.dumps({"download_urls": [{"attachment_id": "att-1", "download_url": "https://example.com/resume.txt"}]}, ensure_ascii=False)
            raise AssertionError(f"Unexpected command: {command}")

        def downloader(url: str, path: Path) -> None:
            path.write_text("AI 运营\n客户成功\n作品集项目", encoding="utf-8")

        with tempfile.TemporaryDirectory() as tmp:
            result = sync_mail_inbox(
                self.conn,
                max_messages=20,
                candidate_queries=["简历"],
                attachment_dir=tmp,
                run_pending=False,
                runner=runner,
                downloader=downloader,
            )

        candidate = self.conn.execute("SELECT * FROM candidates WHERE email = 'lisi@example.com'").fetchone()
        ingestion = self.conn.execute("SELECT * FROM mail_ingestion_items WHERE source_email_id = 'mail-candidate-1'").fetchone()

        self.assertEqual(result["new_records"]["clients"], 1)
        self.assertEqual(result["new_records"]["jobs"], 1)
        self.assertEqual(result["candidates"]["created"], 1)
        self.assertEqual(result["candidates"]["attachments_downloaded"], 1)
        self.assertIn("客户成功", candidate["resume_text"])
        self.assertIn("李四简历.txt", candidate["resume_uri"])
        self.assertEqual(ingestion["status"], "imported")
        self.assertTrue(any("download_url" in command for command in calls))

    def test_sync_mail_inbox_skips_already_processed_candidate_message(self) -> None:
        def runner(command: list[str]) -> str:
            if "+triage" in command and "招聘合作" in command:
                return "[]"
            if "+triage" in command:
                return json.dumps([{"message_id": "mail-candidate-1", "subject": "投递｜李四", "from": "李四 <lisi@example.com>"}], ensure_ascii=False)
            if "+messages" in command:
                return json.dumps(
                    [{"message_id": "mail-candidate-1", "subject": "投递｜李四", "from": "李四 <lisi@example.com>", "body": "AI 运营"}],
                    ensure_ascii=False,
                )
            return "{}"

        first = sync_mail_inbox(self.conn, candidate_queries=["简历"], run_pending=False, runner=runner)
        second = sync_mail_inbox(self.conn, candidate_queries=["简历"], run_pending=False, runner=runner)
        count = self.conn.execute("SELECT COUNT(*) AS count FROM candidates").fetchone()["count"]

        self.assertEqual(first["candidates"]["created"], 1)
        self.assertEqual(second["candidates"]["duplicates"], 1)
        self.assertEqual(count, 1)

    def test_sync_mail_inbox_records_needs_review_without_stable_email(self) -> None:
        def runner(command: list[str]) -> str:
            if "+triage" in command and "招聘合作" in command:
                return "[]"
            if "+triage" in command:
                return json.dumps([{"message_id": "mail-unknown", "subject": "简历投递", "from": "未知发件人"}], ensure_ascii=False)
            if "+messages" in command:
                return json.dumps([{"message_id": "mail-unknown", "subject": "简历投递", "from": "未知发件人", "body": "请看附件"}], ensure_ascii=False)
            return "{}"

        result = sync_mail_inbox(self.conn, candidate_queries=["简历"], run_pending=False, runner=runner)
        row = self.conn.execute("SELECT status, error_text FROM mail_ingestion_items WHERE source_email_id = 'mail-unknown'").fetchone()

        self.assertEqual(result["candidates"]["created"], 0)
        self.assertEqual(len(result["candidates"]["needs_review"]), 1)
        self.assertEqual(row["status"], "needs_review")
        self.assertIn("邮箱", row["error_text"])

    def test_cli_sync_mail_inbox_prints_json_summary(self) -> None:
        payload = {
            "recruiting": {"seen": 0, "parsed": 0, "needs_review": 0},
            "candidates": {
                "queries": ["简历"],
                "seen": 0,
                "created": 0,
                "updated": 0,
                "duplicates": 0,
                "needs_review": [],
                "attachments_downloaded": 0,
                "attachment_errors": [],
                "text_extraction_errors": [],
            },
            "processed_pending_candidates": 0,
            "new_records": {"clients": 0, "jobs": 0, "recruiting_requests": 0, "candidates": 0},
        }
        with tempfile.TemporaryDirectory() as tmp, patch("opportunity_matcher.cli.sync_mail_inbox", return_value=payload) as mocked:
            code = main(["--db", str(Path(tmp) / "cli.db"), "sync-mail-inbox", "--json", "--candidate-query", "简历"])

        self.assertEqual(code, 0)
        mocked.assert_called_once()
        self.assertEqual(mocked.call_args.kwargs["candidate_queries"], ["简历"])


if __name__ == "__main__":
    unittest.main()
