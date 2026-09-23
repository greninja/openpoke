"""Cheap, deterministic roster selection. No LLM calls or private agent logs."""

import re
from typing import Dict, List, Mapping, Sequence

CATEGORY_KEYWORDS = {
    "email": {"email", "mail", "inbox", "reply", "draft", "gmail"},
    "travel": {"flight", "airport", "airline", "hotel", "travel", "trip"},
    "finance": {"budget", "invoice", "payment", "refund", "expense", "finance"},
    "calendar": {"meeting", "schedule", "calendar", "appointment", "reminder"},
}

_GENERIC_NAME_WORDS = {
    "agent", "assistant", "email", "emails", "general", "helper", "inbox",
    "latest", "mail", "mailbox", "manager", "message", "messages", "new",
    "search", "specialist", "summary", "summarizer", "task", "tasks", "today",
    "travel", "finance", "calendar", "draft", "drafting", "handler", "worker",
}


def categories_for(text: str) -> list[str]:
    """Return the supported categories whose keywords occur as whole words."""
    words = set(re.findall(r"[a-z]+", text.lower()))
    return [
        category
        for category, keywords in CATEGORY_KEYWORDS.items()
        if words & keywords
    ]


def is_specific_agent_name(name: object) -> bool:
    """Require a concrete subject, person, thread, or job in a new agent name."""
    if not isinstance(name, str):
        return False
    # Ignore possessive fragments such as the ``s`` in "Today's".
    words = {
        word
        for word in re.findall(r"[a-z0-9]+", name.lower())
        if len(word) > 1
    }
    return bool(words - _GENERIC_NAME_WORDS)


def select_agent_details(
    names: Sequence[str],
    metadata: Mapping[str, Mapping[str, object]],
    latest_text: str,
    message_type: str = "user",
    limit: int = 20,
    recent_limit: int = 5,
) -> List[Dict[str, object]]:
    """Return shortlisted names with the reasons each one was made visible."""
    result_names = (
        {
            name
            for name in names
            if any(
                line.startswith(f"[{status}] {name}:")
                for line in latest_text.splitlines()
                for status in ("SUCCESS", "FAILED")
            )
        }
        if message_type == "agent"
        else set()
    )
    pinned = [name for name in names if name in result_names]
    recent = sorted(
        (name for name in names if metadata.get(name, {}).get("last_used")),
        key=lambda name: str(metadata[name]["last_used"]),
        reverse=True,
    )
    categories = set(categories_for(latest_text))
    # Recent matching agents rank first; insertion order resolves remaining ties.
    ranked = list(dict.fromkeys(recent + names))
    matched = [
        name
        for name in ranked
        if categories.intersection(metadata.get(name, {}).get("categories", []))
    ]
    candidates = list(dict.fromkeys(pinned + recent[:recent_limit] + matched))
    selected = candidates[:max(limit, len(pinned))]
    details = []
    for name in selected:
        reasons = []
        if name in result_names:
            reasons.append("result_sender")
        if name in recent[:recent_limit]:
            reasons.append("recent")
        matched_categories = sorted(
            categories.intersection(metadata.get(name, {}).get("categories", []))
        )
        if matched_categories:
            reasons.extend(f"category:{category}" for category in matched_categories)
        details.append({
            "name": name,
            "categories": list(metadata.get(name, {}).get("categories", [])),
            "reasons": reasons,
        })
    return details


def select_agents(
    names: Sequence[str],
    metadata: Mapping[str, Mapping[str, object]],
    latest_text: str,
    message_type: str = "user",
    limit: int = 20,
    recent_limit: int = 5,
) -> List[str]:
    """Preserve the original name-only API for callers that do not need reasons."""
    return [
        str(entry["name"])
        for entry in select_agent_details(
            names, metadata, latest_text, message_type, limit, recent_limit
        )
    ]
