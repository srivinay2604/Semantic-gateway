"""Request and text normalisation for caching.

Spec §9.2:
- Lowercase and trim whitespace for the embedding text only
  (never alter what is sent to the provider).
- Collapse repeated whitespace.
- Canonical JSON for the exact key: sorted keys, only fields that affect output
  (model, messages, temperature, top_p, max_tokens, stop, tools, response_format, seed).
- Exclude fields that do not affect output (user, stream, request metadata).
"""

from __future__ import annotations

import json
import re
from typing import Any

from cachegate.schemas.internal import InternalMessage, InternalRequest
from cachegate.schemas.openai import ChatCompletionRequest

_WHITESPACE_RE = re.compile(r"\s+")


def normalize_text(text: str) -> str:
    """Normalize text for embedding computation.

    Lowercases text, trims leading/trailing whitespace, and collapses
    multiple consecutive whitespace characters into a single space.
    """
    return _WHITESPACE_RE.sub(" ", text).strip().lower()


def canonical_request_dict(request: InternalRequest | ChatCompletionRequest) -> dict[str, Any]:
    """Build a canonical dictionary containing only output-affecting fields.

    Fields excluded: user, stream, request_id, client headers, timestamps.
    Fields included: model, messages (role, content, name), temperature, top_p,
    max_tokens, stop.
    """
    if isinstance(request, InternalRequest):
        model = request.model_alias
        raw_messages = request.messages
        temperature = request.temperature
        top_p = request.top_p
        max_tokens = request.max_tokens
        stop = request.stop
    else:
        model = request.model
        raw_messages = [
            InternalMessage(role=m.role, content=m.content, name=m.name) for m in request.messages
        ]
        temperature = request.temperature
        top_p = request.top_p
        max_tokens = request.max_tokens
        stop = request.stop

    canonical_messages: list[dict[str, Any]] = []
    for msg in raw_messages:
        content = msg.content
        if content:
            content = (
                content.replace("’", "'")
                .replace("‘", "'")
                .replace("“", '"')
                .replace("”", '"')
            )
            content = _WHITESPACE_RE.sub(" ", content).strip()
        m_dict: dict[str, Any] = {"role": msg.role, "content": content}
        if msg.name is not None:
            m_dict["name"] = msg.name
        canonical_messages.append(m_dict)

    result: dict[str, Any] = {
        "model": model,
        "messages": canonical_messages,
    }
    if temperature is not None:
        result["temperature"] = round(temperature, 4)
    if top_p is not None:
        result["top_p"] = round(top_p, 4)
    if max_tokens is not None:
        result["max_tokens"] = max_tokens
    if stop is not None:
        result["stop"] = stop

    return result


def canonical_request_json(request: InternalRequest | ChatCompletionRequest) -> str:
    """Serialize canonical request dictionary to deterministic JSON with sorted keys."""
    d = canonical_request_dict(request)
    return json.dumps(d, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def extract_last_user_message(messages: list[InternalMessage]) -> str | None:
    """Extract the content of the final user message in the conversation.

    Returns None if no user message exists.
    """
    for msg in reversed(messages):
        if msg.role == "user" and msg.content:
            return msg.content
    return None


def extract_system_prompt(messages: list[InternalMessage]) -> str | None:
    """Extract and concatenate all system message contents in order."""
    system_parts = [msg.content for msg in messages if msg.role == "system" and msg.content]
    if not system_parts:
        return None
    return "\n".join(system_parts)


def extract_context_messages(messages: list[InternalMessage]) -> list[dict[str, Any]]:
    """Extract all messages appearing *before* the final user message.

    System messages are excluded (they are captured in system_prompt_hash).
    Single-turn chats return an empty list.
    """
    conversation_msgs = [m for m in messages if m.role != "system"]
    last_user_idx = -1
    for i in range(len(conversation_msgs) - 1, -1, -1):
        if conversation_msgs[i].role == "user":
            last_user_idx = i
            break

    if last_user_idx <= 0:
        return []

    context: list[dict[str, Any]] = []
    for msg in conversation_msgs[:last_user_idx]:
        m: dict[str, Any] = {"role": msg.role, "content": msg.content}
        if msg.name is not None:
            m["name"] = msg.name
        context.append(m)
    return context
