# opportunity_matcher 文件夹说明书

## 核心功能

承载本地机会匹配 CLI 的源码，实现 SQLite 数据层、飞书快照导入、每日邮箱入库、匹配器、候选人工作流、招聘方合作工作流和邮件草稿模板。

## 输入

来自 CLI 的命令参数、JSON 导入文件、本地 SQLite 数据库、候选人简历结构化信息、岗位信息、招聘方白名单、飞书邮箱招聘合作邮件、候选人投递邮件、候选人附件和候选人回复。

## 输出

对外提供 `opportunity_matcher.cli` 命令入口、飞书快照导入、每日邮箱入库摘要、匹配结果、outbox 草稿、飞书邮箱草稿创建、候选人处理状态、招聘合作状态、跟进提醒和审计日志。

## 定位

这是 V1 本地业务程序的核心源码目录，不会真实发送邮件；每日邮箱入库只读邮箱并保存候选人附件到本地受控目录，招聘方合作流程只创建飞书邮箱草稿和飞书群提醒。

## 依赖

依赖 Python 标准库；内部主要由 `db.py`、`lark_importer.py`、`mail_ingestion.py`、`matcher.py`、`workflow.py`、`recruiting_workflow.py`、`email_templates.py` 和 `cli.py` 组成。CLI 对外命令契约集中维护在项目根目录的 `docs/CLI.md`。

## 维护规则

- 每次新增、删除、移动文件或调整职责后，必须检查并更新本 README。
- 文件夹职责、命令参数、输入 JSON 或输出字段影响项目基础文档时，必须同步更新 `docs/CLI.md`。
