# Agent 工作流资产

这里存放可沉淀到 Codex/Agent 的 AI 猎头工作流 skill。它们来自真实的 CANDYSIGN 推荐任务，覆盖招聘内容解析、飞书邮箱候选人筛选、地域过滤、附件整理和推荐邮件草稿生成。

入口 skill 是 [opportunity-recommendation-workflow](opportunity-recommendation-workflow/SKILL.md)，它会按步骤编排 4 个子流程：

- [job-post-to-opportunity-brief](job-post-to-opportunity-brief/SKILL.md)：招聘图、网页招聘贴或 JD 转岗位画像。
- [mailbox-candidate-shortlist](mailbox-candidate-shortlist/SKILL.md)：从飞书邮箱和候选人材料中筛短名单。
- [candidate-location-filter](candidate-location-filter/SKILL.md)：按城市、附近区域、远程规则做地域过滤。
- [candidate-recommendation-delivery](candidate-recommendation-delivery/SKILL.md)：整理附件命名，生成 HR 推荐邮件和候选人征询邮件草稿。

每个 skill 目录都包含：

- `SKILL.md`：给 Agent 使用的流程说明。
- `evals/evals.json`：后续做 with-skill / baseline 对比时使用的测试提示。
