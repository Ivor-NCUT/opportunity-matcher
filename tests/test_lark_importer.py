# ## 核心功能
# 验证飞书 Base JSON 快照导入器能写入公司、岗位、候选人和来源映射。
# ## 输入
# 测试临时生成的飞书 record-list JSON 结构。
# ## 输出
# 单元测试断言结果。
# ## 定位
# 外部数据导入回归测试，不访问真实飞书 API。
# ## 依赖
# `opportunity_matcher.lark_importer`、`opportunity_matcher.db` 和 Python 标准库 `unittest`。
# ## 维护规则
# 飞书导出格式或本地库字段变化时同步更新测试。

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from opportunity_matcher.db import init_db, upsert_client
from opportunity_matcher.lark_importer import import_lark_dir


class LarkImporterTest(unittest.TestCase):
    def setUp(self) -> None:
        self.conn = sqlite3.connect(":memory:")
        self.conn.row_factory = sqlite3.Row
        init_db(self.conn)

    def tearDown(self) -> None:
        self.conn.close()

    def test_import_lark_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            write_snapshot(
                root / "teams.json",
                ["团队名", "团队Slug", "官网", "公司简介"],
                [["Acme AI", "acme-ai", "https://acme.example", "AI 工具公司"]],
                ["team-rec-1"],
            )
            write_snapshot(
                root / "jobs.json",
                ["职位", "团队名", "团队Slug", "地点", "经验", "状态", "岗位描述", "职位ID"],
                [["AI 运营", "Acme AI", "acme-ai", "上海", "Junior", "published", "负责 AI 工具运营和客户成功", "job-1"]],
                ["job-rec-1"],
            )
            write_snapshot(
                root / "candidates.json",
                ["姓名", "邮箱", "邮件主题", "投递方向", "邮件正文摘要"],
                [["候选人", "[candidate@example.com](mailto:candidate@example.com)", "投递 AI 运营", "AI 运营", "有客户成功经验"]],
                ["candidate-rec-1"],
            )

            counts = import_lark_dir(self.conn, root)

        self.assertEqual(counts["companies"], 1)
        self.assertEqual(counts["jobs"], 1)
        self.assertEqual(counts["candidates"], 1)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) AS count FROM source_records").fetchone()["count"], 3)
        job = self.conn.execute("SELECT company_id, required_skills_json FROM jobs").fetchone()
        self.assertIsNotNone(job["company_id"])
        self.assertIn("ai operations", job["required_skills_json"])

    def test_upsert_client_creates_company_link(self) -> None:
        client_id = upsert_client(
            self.conn,
            {
                "name": "真格基金",
                "slug": "zhenfund",
                "contact_name": "Joyce",
                "contact_email": "joyce@zhenfund.com",
                "status": "active",
                "description": "早期投资机构",
            },
        )

        client = self.conn.execute("SELECT company_id, contact_email FROM clients WHERE id = ?", (client_id,)).fetchone()
        company = self.conn.execute("SELECT name, slug FROM companies WHERE id = ?", (client["company_id"],)).fetchone()
        self.assertEqual(client["contact_email"], "joyce@zhenfund.com")
        self.assertEqual(company["name"], "真格基金")
        self.assertEqual(company["slug"], "zhenfund")


def write_snapshot(path: Path, fields: list[str], rows: list[list[object]], record_ids: list[str]) -> None:
    path.write_text(
        json.dumps(
            {
                "ok": True,
                "data": {
                    "fields": fields,
                    "field_id_list": fields,
                    "record_id_list": record_ids,
                    "data": rows,
                    "has_more": False,
                },
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


if __name__ == "__main__":
    unittest.main()
