"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List, Optional

from ...config import get_settings
from ...services.execution import get_agent_roster

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Return the static system prompt for the interaction agent."""
    return SYSTEM_PROMPT


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
    active_agents: Optional[List[Dict[str, object]]] = None,
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    rendered_agents = _render_active_agents(latest_text, message_type, active_agents)
    sections.append(f"<active_agents>\n{rendered_agents}\n</active_agents>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


def get_active_agent_details(latest_text: str, message_type: str) -> List[Dict[str, object]]:
    """Return the shortlist, or the complete roster when shortlisting is disabled."""
    roster = get_agent_roster()
    roster.load()
    if get_settings().roster_shortlisting_enabled:
        return roster.shortlist_details(latest_text, message_type)
    return [
        {"name": name, "categories": [], "reasons": ["full_roster"]}
        for name in roster.get_agents()
    ]


# Format currently active execution agents into XML tags for LLM awareness
def _render_active_agents(
    latest_text: str = "",
    message_type: str = "user",
    active_agents: Optional[List[Dict[str, object]]] = None,
) -> str:
    agents = active_agents if active_agents is not None else get_active_agent_details(
        latest_text, message_type
    )

    if not agents:
        if get_settings().roster_shortlisting_enabled:
            return (
                "No existing agents are visible. Create a specifically named agent "
                "for delegated work."
            )
        return "None"

    rendered: List[str] = []
    for agent in agents:
        name = escape(str(agent.get("name") or "agent"), quote=True)
        categories = escape(
            ",".join(str(value) for value in agent.get("categories", [])),
            quote=True,
        )
        reasons = escape(
            ",".join(str(value) for value in agent.get("reasons", [])),
            quote=True,
        )
        attributes = f' name="{name}"'
        if categories:
            attributes += f' categories="{categories}"'
        if reasons and get_settings().roster_shortlisting_enabled:
            attributes += f' visible_because="{reasons}"'
        rendered.append(f'<agent{attributes} />')

    return "\n".join(rendered)


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
