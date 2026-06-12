from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Any, Callable


PROVIDER = "ark"
DEFAULT_BASE_URL = os.environ.get("OPPORTUNITY_MATCHER_MAIL_CLASSIFIER_BASE_URL", "https://ark.cn-beijing.volces.com/api/v3")
DEFAULT_MODEL = os.environ.get("OPPORTUNITY_MATCHER_MAIL_CLASSIFIER_MODEL", "ep-20260611005702-gw2qc")
DEFAULT_TIMEOUT = float(os.environ.get("OPPORTUNITY_MATCHER_MAIL_CLASSIFIER_TIMEOUT", "60"))
ARK_API_KEY_ENVS = ("OPPORTUNITY_MATCHER_ARK_API_KEY", "ARK_API_KEY")
ALLOWED_LABELS = {"candidate", "recruiting", "review"}


@dataclass(frozen=True)
class MailClassification:
    label: str
    confidence: str
    reason: str
    provider: str
    model: str


MailClassifier = Callable[[dict[str, Any]], MailClassification]


def build_mail_classifier(
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> MailClassifier | None:
    client = ArkMailClassifier(model=model, base_url=base_url, timeout=timeout)
    return client.classify


def inspect_mail_classifier(
    model: str = DEFAULT_MODEL,
    base_url: str = DEFAULT_BASE_URL,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    payload = {
        "provider": PROVIDER,
        "model": model,
        "base_url": base_url,
        "enabled": True,
        "api_key_env": " or ".join(ARK_API_KEY_ENVS),
    }
    try:
        ark_healthcheck(model=model, base_url=base_url, timeout=timeout)
    except Exception as exc:
        payload["available"] = False
        payload["reason"] = str(exc)
        return payload
    payload["available"] = True
    payload["reason"] = ""
    return payload


class ArkMailClassifier:
    def __init__(self, model: str, base_url: str, timeout: float) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def classify(self, message: dict[str, Any]) -> MailClassification:
        completion = ark_chat_completion(
            model=self.model,
            base_url=self.base_url,
            timeout=self.timeout,
            messages=classifier_messages(message),
        )
        content = completion_text(completion)
        parsed = parse_classifier_json(content)
        return MailClassification(
            label=normalize_label(parsed.get("label", "review")),
            confidence=text_value(parsed.get("confidence")) or "low",
            reason=text_value(parsed.get("reason")) or "模型未给出原因",
            provider="ark",
            model=self.model,
        )


def classifier_messages(message: dict[str, Any]) -> list[dict[str, str]]:
    return [
        {
            "role": "system",
            "content": (
                "你是邮件路由器。"
                "只输出 JSON，字段必须是 label、confidence、reason。"
                "label 只能是 candidate、recruiting、review。"
                "candidate 表示候选人向我投递简历/作品集/求职。"
                "recruiting 表示公司或招聘方希望我帮忙招人/推荐候选人/JD 合作。"
                "review 表示无法确定、和招聘无关，或正文与标题冲突。"
            ),
        },
        {"role": "user", "content": build_prompt(message)},
    ]


def build_prompt(message: dict[str, Any]) -> str:
    subject = text_value(first_present(message, "subject", "title"))
    body = text_value(first_present(message, "body", "text", "plain_text", "content", "body_text"))
    sender = text_value(first_present(message, "from", "sender", "from_email", "sender_email"))
    attachment_names = ", ".join(normalize_attachment_names(message))
    return (
        "请判断这封邮件应该进入哪类库。\n"
        "判断规则：如果是候选人向我投递简历、求职、发作品集，返回 candidate；"
        "如果是公司、HR、猎头、招聘负责人希望我帮忙招人、给 JD、推荐人选，返回 recruiting；"
        "如果无法确定或与招聘流程无关，返回 review。\n\n"
        f"发件人: {sender or '未知'}\n"
        f"标题: {subject or '空'}\n"
        f"正文: {body[:4000] or '空'}\n"
        f"附件名: {attachment_names or '无'}\n\n"
        '只输出 JSON，例如 {"label":"candidate","confidence":"high","reason":"标题和正文都在投递简历"}'
    )


def ark_healthcheck(model: str, base_url: str, timeout: float) -> None:
    completion = ark_chat_completion(
        model=model,
        base_url=base_url,
        timeout=timeout,
        messages=[
            {"role": "system", "content": "你是健康检查助手，只回复 OK。"},
            {"role": "user", "content": "OK"},
        ],
        max_tokens=8,
    )
    if not completion_text(completion):
        raise RuntimeError("Ark returned an empty response")


def ark_chat_completion(
    model: str,
    base_url: str,
    timeout: float,
    messages: list[dict[str, str]],
    max_tokens: int | None = None,
) -> Any:
    api_key = ark_api_key()
    if not api_key:
        raise RuntimeError(f"{' or '.join(ARK_API_KEY_ENVS)} is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError("openai package is not installed; run `python3 -m pip install --upgrade openai>=1.0`") from exc
    client = OpenAI(base_url=base_url.rstrip("/"), api_key=api_key, timeout=timeout)
    kwargs: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": 0,
    }
    if max_tokens is not None:
        kwargs["max_tokens"] = max_tokens
    try:
        return client.chat.completions.create(**kwargs)
    except Exception as exc:
        raise RuntimeError(ark_actionable_error_message(exc, model=model, base_url=base_url)) from exc


def ark_api_key() -> str:
    for env_name in ARK_API_KEY_ENVS:
        value = os.environ.get(env_name)
        if value:
            return value
    return ""


def ark_actionable_error_message(exc: Exception, model: str, base_url: str) -> str:
    raw = str(exc)
    if "NoAvailableModel" in raw:
        return (
            "Volcengine Ark endpoint has no available model instance. "
            f"Endpoint ID: {model}; base URL: {base_url.rstrip('/')}. "
            "Open the Ark console online inference page, find this inference endpoint, and check that it is in cn-beijing, "
            "status is healthy, bound model is present, and quota or instances are available. "
            "Copy the API key from this endpoint's API access page and set OPPORTUNITY_MATCHER_ARK_API_KEY; "
            "also confirm that the key belongs to the endpoint's project. "
            "You can also call GetEndpoint on open.volcengineapi.com with Action=GetEndpoint, Version=2024-01-01, "
            f"and Id={model} to inspect Status and ModelReference. "
            "If the endpoint looks healthy but chat still returns NoAvailableModel, enable it again in the console or call StartEndpoint. "
            "If the endpoint was recreated, update OPPORTUNITY_MATCHER_MAIL_CLASSIFIER_MODEL to the new ep-* ID. "
            f"Raw error: {raw}"
        )
    if "ModelNotFound" in raw or "model" in raw and "not found" in raw.lower():
        return (
            "Volcengine Ark model/endpoint was not found. "
            f"Configured model endpoint ID: {model}; base URL: {base_url.rstrip('/')}. "
            "Verify the endpoint ID and region in the Ark console. "
            f"Raw error: {raw}"
        )
    return raw


def completion_text(completion: Any) -> str:
    try:
        return text_value(completion.choices[0].message.content)
    except (AttributeError, IndexError, TypeError):
        return ""


def parse_classifier_json(raw: str) -> dict[str, Any]:
    raw = raw.strip()
    if not raw:
        return {"label": "review", "confidence": "low", "reason": "模型返回为空"}
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = raw.find("{")
    end = raw.rfind("}")
    if start >= 0 and end > start:
        try:
            parsed = json.loads(raw[start : end + 1])
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            pass
    return {"label": "review", "confidence": "low", "reason": f"无法解析模型输出: {raw[:200]}"}


def normalize_label(value: str) -> str:
    normalized = text_value(value).strip().lower()
    mapping = {
        "candidate_application": "candidate",
        "candidate_mail": "candidate",
        "application": "candidate",
        "recruiting_request": "recruiting",
        "recruiter": "recruiting",
        "client": "recruiting",
        "manual_review": "review",
        "unknown": "review",
        "other": "review",
    }
    label = mapping.get(normalized, normalized)
    return label if label in ALLOWED_LABELS else "review"


def normalize_attachment_names(message: dict[str, Any]) -> list[str]:
    raw = first_present(message, "attachments", "attachment_list", "files")
    if not isinstance(raw, list):
        return []
    names: list[str] = []
    for item in raw:
        if isinstance(item, dict):
            name = text_value(first_present(item, "name", "filename", "file_name", "title"))
            if name:
                names.append(name)
    return names


def first_present(data: Any, *keys: str) -> Any:
    current = data
    for key in keys:
        if isinstance(current, dict) and key in current and current[key] not in (None, ""):
            return current[key]
    return ""


def text_value(value: Any) -> str:
    if isinstance(value, dict):
        for key in ("text", "name", "email", "display_name", "address", "content"):
            nested = value.get(key)
            if nested not in (None, ""):
                return text_value(nested)
        return ""
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        return " ".join(text_value(item) for item in value if text_value(item))
    return str(value).strip()
