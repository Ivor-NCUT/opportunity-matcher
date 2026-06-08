---
name: job-post-to-opportunity-brief
description: Convert a recruitment source into a structured opportunity brief. Use this skill whenever the user provides a Xiaohongshu recruitment note, webpage, screenshots, images, OCR text, JD text, hiring poster, or social post and wants candidate matching, role extraction, location filtering, or recruiting email preparation.
---

# 招聘内容转岗位画像

## Purpose

Turn noisy recruitment material into a compact brief that can drive candidate search and matching. The source may be a Xiaohongshu note, webpage, image carousel, pasted JD, email, PDF, or screenshot.

## Source Handling

- For web or Xiaohongshu links, use `web-access:web-access` when live access, image download, screenshots, or visual extraction is needed.
- For image-heavy posts, identify each job image and extract text from the image. Ignore likes, comments, hashtags, note title, and marketing copy unless they contain concrete hiring requirements.
- Preserve source links and evidence snippets. If a field is uncertain, mark it as unknown instead of guessing.
- If the job post contains multiple roles, keep roles separate.

## Extraction Fields

Always output these fields:

```markdown
# 岗位画像

## 基本信息
- 公司：
- 品牌/别名：
- 来源：
- 招聘邮箱/联系渠道：
- 城市/办公方式：
- 地域要求：
- 发布时间/有效性：

## 岗位列表
### [岗位名]
- 角色定位：
- 主要职责：
- 硬性要求：
- 加分项：
- 作品/项目要求：
- AI/工具要求：
- 关键词：
- 匹配时优先看的证据：

## 不确定信息
- ...
```

## Matching-Oriented Normalization

Convert vague post language into searchable signals:

- "网感好" -> content sense, social media, Xiaohongshu, trend research, community interaction.
- "AI 工作流" -> AI-assisted production, image/video generation, automation, prompt workflow.
- "作品集" -> portfolio, case study, visual work, video reel, design collection.
- "高情商建联" -> KOL/media operations, creator outreach, partnership communication.
- "数据强迫症" -> spreadsheet, funnel analysis, campaign metrics, CRM tracking.

Keep the original wording as evidence, but add normalized keywords for search.

## Quality Rules

- Separate facts from inference. Label inferred keywords as "匹配关键词", not as original requirements.
- Do not evaluate candidates in this skill. Only produce the opportunity brief.
- Do not fabricate a recruitment email. If the email comes from the user rather than the post, mark it as "用户提供".
- If the source is stale or may have changed, verify it live when possible.

## Output Checklist

Before finishing, make sure the brief contains:

- Search keywords broad enough for mailbox retrieval.
- Role-specific evidence needs.
- A clear city/location rule or "未说明".
- Hiring contact if available.
- Source link(s) for traceability.
