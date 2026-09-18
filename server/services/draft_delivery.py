"""Display structured email drafts without invoking the interaction LLM."""

from __future__ import annotations

import json

from .conversation import get_conversation_log
from ..logging_config import logger


def display_draft_for_review(
    *,
    agent_name: str,
    task_id: str,
    draft_id: str,
    to: str,
    subject: str,
    body: str,
) -> None:
    """Record approval context and show the exact draft to the user."""

    log = get_conversation_log()
    # Persist delivery identity in the same conversation used on subsequent
    # turns; restarting the runtime must not display the same draft again.
    for tag, _, content in log.iter_entries():
        if tag != "agent_message":
            continue
        try:
            # ConversationLog decodes escaped newlines, including those inside
            # JSON strings. Accept these when reading its own stored records.
            previous = json.loads(content, strict=False)
        except (ValueError, TypeError):
            continue
        if isinstance(previous, dict) and previous.get("type") == "draft_ready" and (
            previous.get("draft_id") == draft_id or
            (previous.get("task_id") == task_id and all(
                previous.get(key) == value for key, value in
                (("to", to), ("subject", subject), ("body", body))
            ))
        ):
            return
    result = {
        "type": "draft_ready",
        "agent_name": agent_name,
        "task_id": task_id,
        "draft_id": draft_id,
        "to": to,
        "subject": subject,
        "body": body,
    }

    # This internal entry preserves the context needed to route a later approval.
    log.record_agent_message(json.dumps(result, ensure_ascii=False))
    log.record_reply(f"To: {to}\nSubject: {subject}\n\n{body}")
    logger.info("Draft displayed for review: %s", to)
