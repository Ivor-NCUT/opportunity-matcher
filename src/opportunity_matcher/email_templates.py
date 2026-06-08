# ## 核心功能
# 生成候选人回信和招聘方推荐邮件的本地 outbox 文案。
# ## 输入
# 候选人记录、岗位记录、匹配理由和投递入口邮箱。
# ## 输出
# 邮件主题和邮件正文字符串。
# ## 定位
# 邮件内容模板层，不负责匹配、不负责真实发送。
# ## 依赖
# Python 标准库 `sqlite3`。
# ## 维护规则
# 普通用户可见文案、发信边界或署名变化时，同步检查产品流程和发送规则。

from __future__ import annotations

import sqlite3


ENTRY_EMAIL = "fanhan@aimanziyi.vip"


def candidate_subject(candidate: sqlite3.Row) -> str:
    return f"{candidate['name']}，这里是与你匹配的 3 个机会"


def candidate_body(candidate: sqlite3.Row, matched_jobs: list[tuple[sqlite3.Row, str]]) -> str:
    lines = [
        f"{candidate['name']}你好，",
        "",
        f"我已经收到你投递到 {ENTRY_EMAIL} 的简历。基于你目前的简历信息，先为你匹配下面这些机会：",
        "",
    ]
    if not matched_jobs:
        lines.extend(
            [
                "这次暂时没有找到足够匹配的开放岗位。我会保留你的最新简历，后续有更合适机会再同步你。",
                "",
                "泛函",
            ]
        )
        return "\n".join(lines)

    for index, (job, reason) in enumerate(matched_jobs, start=1):
        lines.extend(
            [
                f"{index}. {job['company']} - {job['title']}",
                f"   匹配理由：{reason}",
                "",
            ]
        )
    lines.extend(["如果你对其中岗位感兴趣，可以直接回复这封邮件说明优先级。", "", "泛函"])
    return "\n".join(lines)


def recruiter_subject(candidate: sqlite3.Row, job: sqlite3.Row) -> str:
    return f"候选人推荐：{candidate['name']} - {job['title']}"


def recruiter_body(candidate: sqlite3.Row, job: sqlite3.Row, reason: str) -> str:
    return "\n".join(
        [
            "你好，",
            "",
            f"这里有一位候选人可能适合 {job['company']} 的 {job['title']}：",
            "",
            f"- 姓名：{candidate['name']}",
            f"- 邮箱：{candidate['email']}",
            f"- 城市：{candidate['city'] or '未填'}",
            f"- 经验等级：{candidate['level'] or '未填'}",
            f"- 工作形态：{candidate['work_type'] or '未填'}",
            f"- 到岗时间：{candidate['availability'] or '未填'}",
            f"- 匹配理由：{reason}",
            f"- 简历位置：{candidate['resume_uri'] or '见原始投递邮件/候选人库'}",
            "",
            "这封推荐来自本地机会匹配工作流，仅发送给已确认白名单联系人。",
            "",
            "泛函",
        ]
    )
