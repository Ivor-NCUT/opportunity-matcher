# opportunity_matcher 文件夹说明书

## 核心功能

承载本地机会匹配 CLI 的源码，实现 SQLite 数据层、飞书快照导入、匹配器、工作流和邮件草稿模板。

## 输入

来自 CLI 的命令参数、JSON 导入文件、本地 SQLite 数据库、候选人简历结构化信息、岗位信息和招聘方白名单。

## 输出

对外提供 `opportunity_matcher.cli` 命令入口、飞书快照导入、匹配结果、outbox 草稿、候选人处理状态和审计日志。

## 定位

这是 V1 本地业务程序的核心源码目录，不负责保存历史简历附件，也不直接接入真实邮箱发送。

## 依赖

依赖 Python 标准库；内部主要由 `db.py`、`lark_importer.py`、`matcher.py`、`workflow.py`、`email_templates.py` 和 `cli.py` 组成。

## 维护规则

- 每次新增、删除、移动文件或调整职责后，必须检查并更新本 README。
- 文件夹职责影响项目基础文档时，必须同步更新 `docs/basic/`。
