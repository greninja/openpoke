"""Interaction agent helpers for prompt construction."""

from html import escape
from pathlib import Path
from typing import Dict, List

from ...services.execution import get_agent_roster
from ...config import get_settings

_prompt_path = Path(__file__).parent / "system_prompt.md"
SYSTEM_PROMPT = _prompt_path.read_text(encoding="utf-8").strip()


# Load and return the pre-defined system prompt from markdown file
def build_system_prompt() -> str:
    """Build the shared prompt with instructions for the selected draft display path."""
    direct = get_settings().draft_ready_enabled
    result_instructions = (
        "An agent_message with type draft_ready means the backend has already displayed "
        "that exact draft. Do not call send_draft or ask for approval again for that result."
        if direct else
        "An agent_message with type draft_ready contains a stored draft that has NOT been "
        "displayed. Call send_draft with its exact to, subject, and body to show it for review. "
        "When asking for approval, call send_message_to_user in the same tool-call response "
        "as send_draft, since successful draft display ends the turn."
    )
    result_instructions += (
        " Reuse its agent_name and draft_id when the user requests a revision or approves "
        "sending. A draft_ready result is not approval to send. Do not send without the "
        "user's approval, and never claim a send succeeded before its execution result."
    )
    display_instructions = (
        "- Execution agents create stored drafts. "
        + ("The backend displays their exact contents." if direct else
           "Use send_draft to display their exact contents; this tool only displays an existing draft.")
        + " Do not invent a draft ID or treat displayed text as a stored draft."
    )
    mode_instructions = (
        "Draft-ready messages are already displayed by the backend; do not repeat them."
        if direct else
        "Show completed drafts through send_draft unless the same draft has already been shown."
    )
    return (SYSTEM_PROMPT.replace("{{DRAFT_RESULT_INSTRUCTIONS}}", result_instructions)
            .replace("{{DRAFT_DISPLAY_INSTRUCTIONS}}", display_instructions)
            .replace("{{DRAFT_MODE_INSTRUCTIONS}}", mode_instructions))


# Build structured message with conversation history, active agents, and current turn
def prepare_message_with_history(
    latest_text: str,
    transcript: str,
    message_type: str = "user",
) -> List[Dict[str, str]]:
    """Compose a message that bundles history, roster, and the latest turn."""
    sections: List[str] = []

    sections.append(_render_conversation_history(transcript))
    sections.append(f"<active_agents>\n{_render_active_agents()}\n</active_agents>")
    sections.append(_render_current_turn(latest_text, message_type))

    content = "\n\n".join(sections)
    return [{"role": "user", "content": content}]


# Format conversation transcript into XML tags for LLM context
def _render_conversation_history(transcript: str) -> str:
    history = transcript.strip()
    if not history:
        history = "None"
    return f"<conversation_history>\n{history}\n</conversation_history>"


# Format currently active execution agents into XML tags for LLM awareness
def _render_active_agents() -> str:
    roster = get_agent_roster()
    roster.load()
    agents = roster.get_agents()

    if not agents:
        return "None"

    rendered: List[str] = []
    for agent_name in agents:
        name = escape(agent_name or "agent", quote=True)
        rendered.append(f'<agent name="{name}" />')

    return "\n".join(rendered)


# Wrap the current message in appropriate XML tags based on sender type
def _render_current_turn(latest_text: str, message_type: str) -> str:
    tag = "new_agent_message" if message_type == "agent" else "new_user_message"
    body = latest_text.strip()
    return f"<{tag}>\n{body}\n</{tag}>"
