# ## 核心功能
# 通过 CLI 主入口跑通本地最小闭环冒烟测试。
# ## 输入
# 临时 SQLite 数据库和 examples 目录中的候选人、岗位、招聘方 JSON。
# ## 输出
# 单元测试断言结果和本地 outbox/audit 记录数量校验。
# ## 定位
# CLI 级 smoke/e2e 回归入口，覆盖用户实际调用路径。
# ## 依赖
# `opportunity_matcher.cli.main`、Python 标准库 `sqlite3`、`tempfile`、`pathlib`、`unittest`。
# ## 维护规则
# CLI 命令、样例数据格式或本地闭环步骤变化时同步更新本测试。

import sqlite3
import tempfile
import unittest
from pathlib import Path

from opportunity_matcher.cli import main


class CliSmokeTest(unittest.TestCase):
    def test_cli_smoke_processes_examples_and_creates_outbox(self) -> None:
        project = Path(__file__).resolve().parents[1]
        examples = project / "examples"
        with tempfile.TemporaryDirectory() as tmp:
            db = Path(tmp) / "smoke.db"
            commands = [
                ["--db", str(db), "init"],
                ["--db", str(db), "import-jobs", "--file", str(examples / "jobs.json")],
                ["--db", str(db), "import-recruiters", "--file", str(examples / "recruiters.json")],
                ["--db", str(db), "import-candidates", "--file", str(examples / "candidates.json")],
                ["--db", str(db), "run"],
            ]
            for command in commands:
                self.assertEqual(main(command), 0)

            conn = sqlite3.connect(db)
            conn.row_factory = sqlite3.Row
            try:
                candidates = conn.execute("SELECT COUNT(*) AS count FROM candidates WHERE status = 'processed'").fetchone()["count"]
                outbox = conn.execute("SELECT COUNT(*) AS count FROM outbox").fetchone()["count"]
                audit = conn.execute("SELECT COUNT(*) AS count FROM audit_logs").fetchone()["count"]
                blocked_push = conn.execute(
                    "SELECT COUNT(*) AS count FROM outbox WHERE recipient_email = 'recruiter.contentos@example.com'"
                ).fetchone()["count"]
            finally:
                conn.close()

        self.assertEqual(candidates, 2)
        self.assertGreaterEqual(outbox, 2)
        self.assertGreater(audit, 0)
        self.assertEqual(blocked_push, 0)


if __name__ == "__main__":
    unittest.main()
