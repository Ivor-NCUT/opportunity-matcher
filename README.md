# Opportunity Matcher

本地版 AI 招聘机会匹配工作台。它是一个无前端 CLI 程序，用产品自有 SQLite 数据库保存候选人、客户、公司、岗位、招聘方白名单、匹配结果、发送草稿、审计日志和外部来源记录。

V1 只保留 `fanhan@aimanziyi.vip` 作为简历投递入口。当前本地版本不会真实发送邮件，只会生成 outbox 草稿和审计记录，方便回放、检查和后续接入真实邮箱发送能力。

## 快速开始

```bash
cd /Users/fanhan/Documents/Codex/大猎头计划/opportunity_matcher
PYTHONPATH=src python3 -m opportunity_matcher.cli init
PYTHONPATH=src python3 -m opportunity_matcher.cli import-jobs --file examples/jobs.json
PYTHONPATH=src python3 -m opportunity_matcher.cli import-recruiters --file examples/recruiters.json
PYTHONPATH=src python3 -m opportunity_matcher.cli import-candidates --file examples/candidates.json
PYTHONPATH=src python3 -m opportunity_matcher.cli import-lark --dir imports/lark
PYTHONPATH=src python3 -m opportunity_matcher.cli run
PYTHONPATH=src python3 -m opportunity_matcher.cli outbox
```

默认数据库路径是 `data/opportunity_matcher.db`。也可以通过 `--db /path/to/app.db` 指定。

## 主要命令

- `init`：初始化本地数据库。
- `import-candidates`：导入候选人 JSON。主题包含 `简历更新｜xxx` 时会按邮箱或姓名更新旧记录。
- `import-companies`：导入公司 JSON。
- `import-clients`：导入招聘客户 JSON，并自动关联公司库。
- `import-jobs`：导入岗位 JSON。
- `import-recruiters`：导入招聘方联系人 JSON。
- `import-lark`：导入飞书 Base JSON 快照，将 `团队` 写入公司库、`职位` 写入职位库、`候选人` 写入候选人库。
- `match --candidate-id <id>`：只查看某个候选人的匹配结果，不生成 outbox。
- `run`：处理所有待处理候选人，生成候选人回信草稿和白名单招聘方推送草稿。
- `outbox`：查看待发送草稿。
- `audit`：查看审计日志。
- `doctor`：检查数据库、基础数据和配置是否可用。

## 数据边界

- 候选人库：姓名、邮箱、城市、经验等级、工作形态、到岗时间、能力标签、能力证据、简历文本、来源邮件、更新时间、处理状态。
- 客户库：客户名称、关联公司、联系人、招聘邮箱、客户状态、来源和备注。
- 公司库：团队名、slug、官网、招聘页、地点、赛道、阶段、团队人数、创始人、社媒、公司简介、文化、技术栈、融资信息、来源记录。
- 岗位库：公司、岗位、城市/远程、经验等级、工作形态、薪资、赛道、岗位描述、必需能力、加分能力、状态。
- 招聘方白名单：联系人、邮箱、公司、是否白名单、可接收岗位范围。
- 审计日志：来源邮件、候选人、岗位、招聘方、事件、解释、创建时间。
- 外部来源记录：记录飞书 Base 表名、record id、本地表和本地 id，方便追溯和重复导入。

## 后续部署方向

本地 CLI 稳定后，可以把 `run` 放进定时任务或队列 worker；把 outbox 从本地草稿替换成真实邮箱发送器；再把 CLI 契约暴露给你、客户和候选人的 Agent 调用。
