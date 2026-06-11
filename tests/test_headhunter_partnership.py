# ## 核心功能
# 验证猎头合作伙伴候选人简历推送流程。
# ## 输入
# 内存 SQLite 数据库、临时简历附件和模拟 lark-cli 发送器。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 猎头合作自动推送流程回归测试，不访问真实飞书邮箱。
# ## 依赖
# `opportunity_matcher.headhunter_partnership`、`db`、Python 标准库 `unittest`。
# ## 维护规则
# 候选人推送幂等规则、附件参数或 CLI 发送边界变化时同步更新本测试。

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from opportunity_matcher.db import init_db, upsert_candidate, upsert_client, upsert_headhunter
from opportunity_matcher.headhunter_partnership import DEFAULT_HEADHUNTER_BODY, forward_candidates_to_headhunters


class HeadhunterPartnershipTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)
        client_id = upsert_client(
            self.conn,
            {
                "name": "TTC",
                "contact_name": "Kitty Zhou",
                "contact_email": "kitty.zhou@ttcadvisory.com",
                "status": "active",
                "source": "manual",
            },
        )
        upsert_headhunter(
            self.conn,
            {
                "name": "TTC",
                "contact_name": "Kitty Zhou",
                "contact_email": "kitty.zhou@ttcadvisory.com",
                "client_id": client_id,
                "status": "active",
                "source": "manual",
            },
        )

    def tearDown(self) -> None:
        self.conn.close()

    def test_forward_candidates_to_headhunters_sends_with_attachment_and_records_status(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume = root / "resume.pdf"
            portfolio = root / "portfolio.pptx"
            resume.write_bytes(b"%PDF-1.4")
            portfolio.write_bytes(b"pptx")
            candidate_id = upsert_candidate(
                self.conn,
                {
                    "email": "candidate@example.com",
                    "name": "候选人",
                    "resume_uri": "resume.pdf; portfolio.pptx",
                    "source_subject": "投递简历",
                },
            )
            calls = []

            def runner(command: list[str]) -> str:
                calls.append(command)
                return json.dumps({"message_id": "sent-1"})

            result = forward_candidates_to_headhunters(
                self.conn,
                candidate_ids=[candidate_id],
                confirm_send=True,
                runner=runner,
                cwd=root,
            )

            row = self.conn.execute("SELECT * FROM candidate_forwards WHERE candidate_id = ?", (candidate_id,)).fetchone()
            self.assertEqual(result["sent"], 1)
            self.assertEqual(row["status"], "sent")
            self.assertIsNotNone(row["headhunter_id"])
            self.assertEqual(row["message_id"], "sent-1")
            self.assertIn("--confirm-send", calls[0])
            self.assertEqual(calls[0][calls[0].index("--attach") + 1], "resume.pdf")
            self.assertIn(DEFAULT_HEADHUNTER_BODY, calls[0])

            second = forward_candidates_to_headhunters(
                self.conn,
                candidate_ids=[candidate_id],
                confirm_send=True,
                runner=runner,
                cwd=root,
            )
            self.assertEqual(second["skipped"][0]["reason"], "already_sent")
            self.assertEqual(len(calls), 1)


if __name__ == "__main__":
    unittest.main()
