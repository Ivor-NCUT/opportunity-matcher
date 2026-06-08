---
name: candidate-location-filter
description: Filter recruiting candidates by geography. Use this skill whenever the user asks whether candidates are in Guangzhou, Shenzhen, Hong Kong, Beijing, Shanghai, nearby areas, commutable regions, on-site locations, remote eligibility, or any location-sensitive hiring constraint. Always provide evidence for keep/reject decisions and never infer location without support.
---

# 候选人地域过滤

## Purpose

Apply location constraints to a candidate shortlist and produce keep/reject tables with evidence.

## Inputs

- Opportunity city or region rule.
- Candidate shortlist with resumes, email body excerpts, or attachment text.
- User's strictness preference if provided.

If no strictness is specified, use this default:

- Keep candidates with explicit matching or nearby-region evidence.
- Reject candidates with explicit incompatible location or expectation.
- Mark unknown as `需确认` when the role might allow remote; reject unknown when the user asks for strict local filtering.

## Evidence Hierarchy

Prefer evidence in this order:

1. Resume current city, expected city, or base location.
2. Email body stating current city, target city, relocation, or remote preference.
3. Portfolio/profile page with current base.
4. Recent work/education location only if it clearly implies current availability.
5. Weak signals such as school city, hometown, phone area code, or old internship location are not enough.

Never infer location from accent, name, school alone, or a single old project location.

## Region Normalization

Normalize city language into a clear rule:

- "广州/深圳附近" usually means Guangzhou, Shenzhen, Foshan, Dongguan, Zhuhai, Zhongshan, Huizhou, Hong Kong, Macau, and candidates explicitly willing to relocate to the Greater Bay Area.
- "北京可线下" means Beijing-based, near Beijing, or explicitly willing to work on-site in Beijing.
- "远程可接受" means location is lower priority, but still preserve timezone and travel constraints.

If the user's wording is stricter than these defaults, follow the user's wording.

## Output Format

```markdown
# 地域过滤结果

## 地域规则
- 岗位城市/区域：
- 是否严格：
- 采用规则：

## 保留
| 候选人 | 邮箱 | 地域证据 | 判断 | 备注 |

## 淘汰
| 候选人 | 邮箱 | 地域证据 | 淘汰原因 | 备注 |

## 需确认
| 候选人 | 邮箱 | 缺失信息 | 建议确认话术 |
```

Every row must include a concrete evidence sentence or state `未找到明确证据`.

## Decision Rules

- If a resume says expected city is incompatible, reject unless there is explicit relocation flexibility.
- If a candidate is highly relevant but location is unknown, preserve them in `需确认` unless the user requested strict elimination.
- If the evidence conflicts, keep the conflict visible and choose the conservative decision.
- Do not use this skill to judge role fit except where location affects feasibility.
