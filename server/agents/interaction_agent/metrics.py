"""Optional, append-only counts of interaction model requests.

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


def record_llm_call(turn_type: TurnType) -> None:
    path = os.environ.get("OPENPOKE_INTERACTION_METRICS_PATH")
    if not path:
        return
    event = {
        "run_id": os.environ.get("OPENPOKE_BENCHMARK_RUN_ID", "manual"),
        "turn_type": turn_type,
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
