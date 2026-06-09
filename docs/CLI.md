# Opportunity Matcher CLI 使用说明

`opportunity-matcher` 是本地机会匹配工作台的命令行入口。它用 SQLite 保存候选人、公司、客户、岗位、招聘方白名单、匹配结果、outbox 草稿、审计日志和飞书来源映射。

当前版本不会真实发送邮件。候选人常规匹配流程仍写本地 outbox；招聘方合作流程会通过 `lark-cli` 创建飞书邮箱草稿，等待人工检查后发送。

## 运行方式

在项目根目录执行：

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli <command>
```

如果已经按 `pyproject.toml` 安装为可执行脚本，也可以执行：

```bash
opportunity-matcher <command>
```

默认数据库路径是 `data/opportunity_matcher.db`。所有命令都可以用全局参数 `--db` 指定其他数据库：

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli --db /tmp/opportunity.db doctor
```

## 推荐闭环

本地最小闭环：

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli init
PYTHONPATH=src python3 -m opportunity_matcher.cli import-jobs --file examples/jobs.json
PYTHONPATH=src python3 -m opportunity_matcher.cli import-recruiters --file examples/recruiters.json
PYTHONPATH=src python3 -m opportunity_matcher.cli import-candidates --file examples/candidates.json
PYTHONPATH=src python3 -m opportunity_matcher.cli run
PYTHONPATH=src python3 -m opportunity_matcher.cli outbox
PYTHONPATH=src python3 -m opportunity_matcher.cli audit
PYTHONPATH=src python3 -m opportunity_matcher.cli doctor
```

使用飞书 Base 导出快照时：

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli init
PYTHONPATH=src python3 -m opportunity_matcher.cli import-lark --dir imports/lark
PYTHONPATH=src python3 -m opportunity_matcher.cli run
PYTHONPATH=src python3 -m opportunity_matcher.cli outbox
```

招聘方合作草稿闭环：

```bash
export OPPORTUNITY_MATCHER_FEISHU_BOT_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/...'
PYTHONPATH=src python3 -m opportunity_matcher.cli sync-recruiting-mails
PYTHONPATH=src python3 -m opportunity_matcher.cli draft-candidate-outreach --request-id 1
PYTHONPATH=src python3 -m opportunity_matcher.cli review-interest
PYTHONPATH=src python3 -m opportunity_matcher.cli mark-interested --outreach-id 1 --reply-text '候选人表示感兴趣'
PYTHONPATH=src python3 -m opportunity_matcher.cli send-due-followups
```

## 命令总览

### `init`

初始化 SQLite 数据库和表结构。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli init
```

输出：

```text
数据库已就绪：data/opportunity_matcher.db
```

### `import-candidates --file <json>`

导入候选人 JSON 列表。候选人按 `email` 去重；如果姓名相同也会更新旧记录。邮件主题包含 `简历更新` 时，旧记录状态会变为 `pending_update`，供 `run` 重新处理。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-candidates --file examples/candidates.json
```

常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `email` | 是 | 候选人邮箱，作为主去重键 |
| `name` | 是 | 候选人姓名 |
| `city` | 否 | 候选人城市，可和岗位城市做硬条件匹配 |
| `level` | 否 | `intern`、`junior`、`mid`、`senior`、`lead` |
| `work_type` | 否 | 工作形态，例如 `full-time`、`intern` |
| `availability` | 否 | 到岗时间 |
| `skills` | 否 | 能力标签数组 |
| `evidence` | 否 | 能力证据数组 |
| `resume_text` | 否 | 简历或邮件正文文本，用于补充能力命中 |
| `source_email_id` | 否 | 来源邮件 ID，用于审计追溯 |
| `source_subject` | 否 | 来源邮件主题 |
| `resume_uri` | 否 | 本地或外部简历地址 |
| `phone_or_wechat` | 否 | 联系方式 |
| `direction` | 否 | 投递方向 |
| `portfolio` | 否 | 作品集或补充材料数组 |
| `referrer_name` | 否 | 推荐人姓名，用于后续跟进提醒 |
| `referral_note` | 否 | 推荐来源备注 |
| `status` | 否 | 默认 `pending` |

### `import-companies --file <json>`

导入公司 JSON 列表。公司按 `name` 去重；非空 `slug` 也必须唯一。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-companies --file companies.json
```

常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 公司或团队名 |
| `slug` | 否 | 公司短标识 |
| `website` | 否 | 官网 |
| `jobs_url` | 否 | 招聘页 |
| `source_url` | 否 | 来源链接 |
| `locations` | 否 | 地点 |
| `track` | 否 | 赛道 |
| `stage` | 否 | 公司阶段 |
| `team_size` | 否 | 团队人数 |
| `founders` | 否 | 创始人 |
| `founder_socials` | 否 | 创始人社媒 |
| `description` | 否 | 公司简介 |
| `values_text` | 否 | 价值观或团队文化 |
| `tech_stack` | 否 | 技术栈 |
| `funding_info` | 否 | 融资信息 |

### `import-clients --file <json>`

导入招聘客户 JSON 列表，并自动尝试关联公司库。客户会影响匹配优先级：候选人先匹配 active 客户公司的开放岗位；客户岗位没有正向结果时，才回退到全职位库。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-clients --file clients.json
```

常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 客户名，通常等于公司名 |
| `company` | 否 | 关联公司名 |
| `contact_name` | 否 | 客户联系人 |
| `contact_email` | 否 | 客户邮箱 |
| `status` | 否 | 默认 `active` |
| `source` | 否 | 来源 |
| `notes` | 否 | 备注 |

### `import-jobs --file <json>`

导入岗位 JSON 列表。岗位按 `company + title` 去重。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-jobs --file examples/jobs.json
```

常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `company` | 是 | 公司名 |
| `title` | 是 | 岗位名 |
| `city` | 否 | 岗位城市，空值或 `远程`、`remote`、`不限` 视为宽松匹配 |
| `level_min` | 否 | 最低经验等级 |
| `level_max` | 否 | 最高经验等级 |
| `work_type` | 否 | 工作形态 |
| `required_skills` | 否 | 必需能力数组 |
| `preferred_skills` | 否 | 加分能力数组 |
| `description` | 否 | 岗位描述 |
| `salary` | 否 | 薪资 |
| `track` | 否 | 赛道 |
| `status` | 否 | 默认 `open`，只有 `open` 会参与匹配 |
| `company_slug` | 否 | 用于辅助关联公司 |

### `import-recruiters --file <json>`

导入招聘方联系人 JSON 列表。招聘方按 `email` 去重。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-recruiters --file examples/recruiters.json
```

常用字段：

| 字段 | 必填 | 说明 |
| --- | --- | --- |
| `name` | 是 | 联系人姓名 |
| `email` | 是 | 联系人邮箱 |
| `company` | 是 | 所属公司 |
| `whitelisted` | 否 | 是否允许生成招聘方推送草稿 |
| `job_ids` | 否 | 可接收的岗位 ID 数组；空数组表示接收本公司所有匹配岗位 |

### `import-lark --dir <directory>`

导入飞书 Base JSON 快照目录。这个命令只读本地 JSON 文件，不直接访问飞书 API。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli import-lark --dir imports/lark
```

目录下可包含：

| 文件 | 对应本地表 | 说明 |
| --- | --- | --- |
| `teams.json` | `companies` | 飞书 `团队` 表快照 |
| `jobs.json` | `jobs` | 飞书 `职位` 表快照 |
| `candidates.json` | `candidates` | 飞书 `候选人` 表快照 |

快照格式应来自 `lark-cli base +record-list --format json`，需要包含 `data.fields`、`data.data` 和可选的 `data.record_id_list`。导入后会写入 `source_records`，方便追溯飞书 record id 和本地记录的映射。

### `match --candidate-id <id> [--json]`

预览单个候选人的 Top 3 匹配结果，不写入 `matches`，也不生成 outbox。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli match --candidate-id 1
PYTHONPATH=src python3 -m opportunity_matcher.cli match --candidate-id 1 --json
```

匹配规则：

- 只匹配 `status = open` 的岗位。
- 先匹配 active 客户公司的岗位；如果没有结果，再匹配全职位库。
- 城市、工作形态、经验等级是硬条件。
- 必需能力命中每项加 10 分，加分能力命中每项加 4 分。
- 必需能力全部缺失会扣分，部分缺失也会扣分。
- 默认最低分是 1 分，最多返回 3 个岗位。

### `run`

处理所有 `pending` 和 `pending_update` 候选人，写入匹配记录、候选人回信草稿、白名单招聘方推送草稿和审计日志。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli run
```

执行结果：

- 清空该候选人的旧 `matches` 后重新写入新匹配。
- 永远为候选人生成一封 `candidate_reply` 草稿。
- 只给 `whitelisted = true` 的招聘方生成 `recruiter_push` 草稿。
- 如果招聘方配置了 `job_ids`，只接收指定岗位；空数组表示接收本公司所有匹配岗位。
- 候选人状态更新为 `processed`。

### `sync-recruiting-mails [--query <text>] [--max <n>] [--mailbox <mailbox>]`

从飞书邮箱同步招聘合作邮件。默认搜索标题/正文里的 `招聘合作`，读取邮件正文后导入为招聘客户、招聘方联系人、岗位和招聘合作请求。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli sync-recruiting-mails
```

招聘方邮件标题格式：

```text
招聘合作｜你的姓名 & 昵称｜公司名称
```

处理规则：

- 标题合规且正文非空：写入 `clients`、`recruiters`、`jobs`、`recruiting_requests`。
- 标题不合规或正文为空：写入 `recruiting_requests`，状态为 `needs_review`。
- 同一 `source_email_id` 重复同步会更新旧记录，不重复创建请求。
- 命令依赖本机 `lark-cli` 邮箱授权。

### `sync-mail-inbox [--mailbox <mailbox>] [--max <n>] [--candidate-query <text>] [--attachment-dir <dir>] [--json]`

每日飞书邮箱入库入口。它只读邮箱，不发送、不删除、不移动邮件；目标库是本地 SQLite。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli sync-mail-inbox
PYTHONPATH=src python3 -m opportunity_matcher.cli sync-mail-inbox --json
```

处理规则：

- 先复用 `sync-recruiting-mails` 搜索 `招聘合作`，导入客户、招聘方、职位和招聘请求。
- 再用候选人关键词搜索邮箱，默认关键词是 `简历`、`resume`、`投递`、`求职`、`CV`、`应聘`、`候选人`、`作品集`。
- 候选人按来源邮件、邮箱、姓名和附件记录去重；同一邮件不会重复下载和重复入库。
- 默认下载候选人附件到 `data/mail_attachments/<message_id>/`，并尽量抽取全文写入候选人的 `resume_text`。
- 无法识别候选人邮箱或姓名的邮件写入 `mail_ingestion_items`，状态为 `needs_review`。
- 执行末尾默认调用 `run`，处理新入库的 `pending` / `pending_update` 候选人，只生成本地 outbox 草稿。

可选参数：

| 参数 | 说明 |
| --- | --- |
| `--candidate-query <text>` | 指定候选人搜索词，可重复传入；传入后不使用默认关键词。 |
| `--no-download-attachments` | 只入库附件元数据，不下载文件。 |
| `--no-extract-text` | 下载附件但不抽取全文。 |
| `--no-run` | 入库后不处理 pending 候选人。 |
| `--json` | 输出定时任务可消费的机器可读摘要。 |

### `draft-candidate-outreach --request-id <id> [--limit <n>] [--mailbox <mailbox>]`

为某个招聘合作请求匹配候选人，并在飞书邮箱创建发给候选人的真实草稿。命令不会发送邮件。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli draft-candidate-outreach --request-id 1 --limit 3
```

执行结果：

- 按招聘需求对应岗位，从候选人库里反向匹配候选人。
- 调用 `lark-cli mail +send` 创建候选人触达草稿。
- 写入 `candidate_outreach`，保存候选人、岗位、招聘请求、招聘方和飞书草稿 ID。
- 已创建过草稿的 `request_id + candidate_id` 不重复创建。

### `review-interest [--outreach-id <id>] [--reply-text <text> | --reply-file <file>] [--json]`

查看或记录候选人回复，进入人工兴趣判断队列。V1 预留语义识别接口，但不接真实大模型，默认都需要人工确认。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli review-interest
PYTHONPATH=src python3 -m opportunity_matcher.cli review-interest --outreach-id 1 --reply-text '我对这个机会感兴趣'
```

### `mark-interested --outreach-id <id> [--reply-text <text> | --reply-file <file>] [--mailbox <mailbox>]`

人工确认候选人感兴趣后，创建发给招聘方的飞书邮箱转发草稿，并安排 4 天后跟进提醒。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli mark-interested --outreach-id 1 --reply-text '候选人确认感兴趣'
```

执行结果：

- 标记 `candidate_outreach.interest_status = interested`。
- 如果候选人有原始投递邮件 ID，优先调用 `lark-cli mail +forward` 转发原邮件。
- 如果没有原始投递邮件 ID，调用 `lark-cli mail +send` 创建普通草稿。
- 草稿正文会说明候选人来自 `fanhan@aimanziyi.vip` 推荐，方便后续内推费归属。
- 创建 `followups` 记录，默认 4 天后到期。

### `send-due-followups [--webhook-url <url>]`

发送已到期的招聘跟进提醒到飞书群机器人。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli send-due-followups
```

配置方式：

```bash
export OPPORTUNITY_MATCHER_FEISHU_BOT_WEBHOOK='https://open.feishu.cn/open-apis/bot/v2/hook/...'
```

提醒内容包括候选人、公司、岗位、招聘方邮箱、推荐人，以及三件事：询问候选人面试进展；如果入职，提醒招聘方结算内推费；如果有推荐人，提醒发微信红包。

### `outbox [--json]`

查看本地待发送草稿。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli outbox
PYTHONPATH=src python3 -m opportunity_matcher.cli outbox --json
```

普通输出只展示摘要；`--json` 会输出包含 `id`、`kind`、`recipient_email`、`recipient_name`、`subject`、`status`、`candidate_id`、`job_id`、`recruiter_id`、`created_at` 的数组。

### `audit [--limit <n>] [--json]`

查看审计日志，默认显示最近 20 条。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli audit
PYTHONPATH=src python3 -m opportunity_matcher.cli audit --limit 50 --json
```

审计日志用于追踪候选人创建/更新、匹配创建、候选人回信草稿、招聘方推送草稿和处理完成事件。

### `doctor`

检查当前数据库的基础数据状态。

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli doctor
```

输出每张核心表的记录数，并在没有开放岗位或没有白名单招聘方时给出提醒。

`doctor` 还会检查：

- `lark-cli` 是否可用。
- 是否配置 `OPPORTUNITY_MATCHER_FEISHU_BOT_WEBHOOK`。

## 状态和输出边界

候选人状态：

| 状态 | 含义 |
| --- | --- |
| `pending` | 新候选人，等待处理 |
| `pending_update` | 已存在候选人的简历更新，等待重新处理 |
| `processed` | 已由 `run` 处理完成 |

邮箱入库状态：

| 状态 | 含义 |
| --- | --- |
| `imported` | 新邮件已创建本地记录 |
| `updated` | 邮件对应的已有记录已更新 |
| `duplicate` | 邮件已处理，本次跳过 |
| `needs_review` | 邮件信息不足，需要人工复核 |

招聘合作请求状态：

| 状态 | 含义 |
| --- | --- |
| `parsed` | 邮件已解析并入库为招聘需求 |
| `needs_review` | 邮件标题或正文不合规，需要人工复核 |

候选人触达兴趣状态：

| 状态 | 含义 |
| --- | --- |
| `needs_manual_review` | 候选人回复需要人工判断 |
| `interested` | 已人工确认候选人感兴趣 |

outbox 类型：

| 类型 | 含义 |
| --- | --- |
| `candidate_reply` | 发给候选人的机会反馈草稿 |
| `recruiter_push` | 发给白名单招聘方的候选人推荐草稿 |

当前 CLI 不会真实发送邮件。`outbox` 是本地草稿和审计依据；招聘方合作流程创建的是飞书邮箱草稿，也需要人工检查后发送。

## 验证

运行单元测试：

```bash
PYTHONPATH=src python3 -m unittest discover -s tests
```

快速检查 CLI 是否可用：

```bash
PYTHONPATH=src python3 -m opportunity_matcher.cli --help
PYTHONPATH=src python3 -m opportunity_matcher.cli doctor
```

## 维护规则

新增命令、参数、输出字段或 JSON 输入契约时，需要同步更新：

- `docs/CLI.md`
- `README.md` 的快速开始和命令总览
- `src/opportunity_matcher/-_opportunity_matcher_README.md` 的目录职责说明
- 相关 `tests/` 回归测试
