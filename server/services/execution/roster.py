"""Simple agent roster management - just a list of agent names."""

import json
import fcntl
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from ...logging_config import logger
from .selection import categories_for, select_agent_details, select_agents


class AgentRoster:
    """Simple roster that stores agent names in a JSON file."""

    def __init__(self, roster_path: Path):
        self._roster_path = roster_path
        self._agents: list[str] = []
        self._metadata_path = roster_path.with_name(roster_path.stem + "_metadata.json")
        self._metadata: Dict[str, Dict[str, object]] = {}
        self.load()

    def load(self) -> None:
        """Load agent names from roster.json."""
        self._load_metadata()
        if self._roster_path.exists():
            try:
                with open(self._roster_path, 'r') as f:
                    data = json.load(f)
                    if isinstance(data, list):
                        self._agents = [str(name) for name in data]
            except Exception as exc:
                logger.warning(f"Failed to load roster.json: {exc}")
                self._agents = []
        else:
            self._agents = []
            self.save()

    def save(self) -> None:
        """Save agent names to roster.json with file locking."""
        max_retries = 5
        retry_delay = 0.1

        for attempt in range(max_retries):
            try:
                self._roster_path.parent.mkdir(parents=True, exist_ok=True)

                # Open file and acquire exclusive lock
                with open(self._roster_path, 'w') as f:
                    fcntl.flock(f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    try:
                        json.dump(self._agents, f, indent=2)
                        return
                    finally:
                        fcntl.flock(f.fileno(), fcntl.LOCK_UN)

            except BlockingIOError:
                # Lock is held by another process
                if attempt < max_retries - 1:
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.warning("Failed to acquire lock on roster.json after retries")
            except Exception as exc:
                logger.warning(f"Failed to save roster.json: {exc}")
                break

    def add_agent(self, agent_name: str) -> None:
        """Add an agent to the roster if not already present."""
        if agent_name not in self._agents:
            self._agents.append(agent_name)
            self.save()

    def _load_metadata(self) -> None:
        """Load valid shortlist metadata while tolerating old or corrupt files."""
        try:
            data = json.loads(self._metadata_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            self._metadata = {}
            return

        if not isinstance(data, dict):
            self._metadata = {}
            return

        self._metadata = {
            name: entry
            for name, entry in data.items()
            if isinstance(name, str)
            and isinstance(entry, dict)
            and isinstance(entry.get("categories"), list)
            and all(isinstance(category, str) for category in entry["categories"])
            and isinstance(entry.get("last_used", ""), str)
        }

    def mark_used(
        self,
        agent_name: str,
        instructions: str = "",
        categories: Optional[List[str]] = None,
    ) -> None:
        """Keep the existing name-only roster compatible; metadata lives alongside it."""
        if agent_name not in self._agents:
            return
        previous = self._metadata.get(agent_name, {}).get("categories", [])
        # An agent's purpose is stable. Infer categories when it is first used,
        # but do not turn repeated unrelated work into permanent new categories.
        assigned = previous or (
            categories or categories_for(f"{agent_name} {instructions}")
        )
        self._metadata[agent_name] = {
            "categories": list(dict.fromkeys(assigned)),
            "last_used": datetime.now(timezone.utc).isoformat(),
        }
        # Atomic replacement prevents readers from observing a partial JSON document.
        temp_path: Optional[Path] = None
        try:
            self._metadata_path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self._metadata_path.parent,
                delete=False,
                encoding="utf-8",
            ) as output:
                temp_path = Path(output.name)
                json.dump(self._metadata, output, indent=2)
            os.replace(temp_path, self._metadata_path)
        except OSError as exc:
            logger.warning(f"Failed to save roster metadata: {exc}")
        finally:
            if temp_path is not None:
                try:
                    temp_path.unlink(missing_ok=True)
                except OSError:
                    pass

    def shortlist(self, latest_text: str, message_type: str = "user") -> list[str]:
        return select_agents(self._agents, self._metadata, latest_text, message_type)

    def shortlist_details(self, latest_text: str, message_type: str = "user") -> list[dict]:
        return select_agent_details(
            self._agents, self._metadata, latest_text, message_type
        )

    def get_categories(self, agent_name: str) -> list[str]:
        """Return the stable categories assigned to an existing agent."""
        return list(self._metadata.get(agent_name, {}).get("categories", []))

    def get_agents(self) -> list[str]:
        """Get list of all agent names."""
        return list(self._agents)

    def clear(self) -> None:
        """Clear the agent roster."""
        self._agents = []
        try:
            if self._metadata_path.exists():
                self._metadata_path.unlink()
            self._metadata = {}
            if self._roster_path.exists():
                self._roster_path.unlink()
            logger.info("Cleared agent roster")
        except Exception as exc:
            logger.warning(f"Failed to clear roster.json: {exc}")


_DATA_DIR = Path(__file__).resolve().parent.parent.parent / "data"
_ROSTER_PATH = _DATA_DIR / "execution_agents" / "roster.json"

_agent_roster = AgentRoster(_ROSTER_PATH)


def get_agent_roster() -> AgentRoster:
    """Get the singleton roster instance."""
    return _agent_roster
