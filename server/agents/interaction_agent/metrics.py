"""Optional, append-only counts of interaction turns, model requests, and provider token usage.

Enable with OPENPOKE_INTERACTION_METRICS_PATH. No prompts or replies are stored.
"""

import json
import logging
import os
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal


TurnType = Literal["user", "agent"]
_lock = threading.Lock()


def _record(event_type: str, turn_type: TurnType, **fields) -> None:
    path = os.environ.get("OPENPOKE_INTERACTION_METRICS_PATH")
    if not path:
        return
    event = {
        "run_id": os.environ.get("OPENPOKE_BENCHMARK_RUN_ID", "manual"),
        "turn_type": turn_type,
        "event_type": event_type,
        **fields,
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }
    try:
        with _lock:
            with Path(path).open("a", encoding="utf-8") as output:
                output.write(json.dumps(event) + "\n")
    except OSError:
        # Measurement must not prevent a user request from running.
        logging.getLogger("openpoke.server").warning(
            "Could not write interaction LLM metrics", exc_info=True
        )


def record_turn(turn_type: TurnType) -> None:
    """Count one invocation, even when it makes zero or several LLM calls."""
    _record("turn", turn_type)


def record_llm_call(turn_type: TurnType) -> None:
    """Count an attempted request, including requests that later fail."""
    _record("llm_call", turn_type)


def record_llm_usage(turn_type: TurnType, response: dict) -> None:
    """Use provider counts; missing usage remains unknown, never estimated."""
    usage = response.get("usage")
    if not isinstance(usage, dict):
        usage = {}

    def token_count(key):
        value = usage.get(key)
        return value if type(value) is int and value >= 0 else None

    _record("llm_usage", turn_type,
            input_tokens=token_count("prompt_tokens"),
            output_tokens=token_count("completion_tokens"))
