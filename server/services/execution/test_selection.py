"""Unit tests for roster shortlisting and metadata."""

import json
import tempfile
import unittest
from pathlib import Path

from server.services.execution.roster import AgentRoster
from server.services.execution.selection import (
    categories_for,
    is_specific_agent_name,
    select_agent_details,
)


class SelectionTests(unittest.TestCase):
    def test_categories_use_whole_words(self) -> None:
        self.assertEqual(categories_for("Draft an email about my flight"), ["email", "travel"])
        self.assertEqual(categories_for("The triplicate form is ready"), [])

    def test_agent_names_must_include_a_specific_subject(self) -> None:
        self.assertFalse(is_specific_agent_name("Email Assistant"))
        self.assertFalse(is_specific_agent_name("Travel Helper"))
        self.assertTrue(is_specific_agent_name("Alice Meeting Email"))
        self.assertTrue(is_specific_agent_name("Acme Invoice Search"))

    def test_shortlist_includes_recent_matching_and_result_agents(self) -> None:
        names = [f"Agent {index}" for index in range(25)]
        metadata = {
            names[20]: {
                "categories": ["email"],
                "last_used": "2026-09-20T10:00:00+00:00",
            },
            names[21]: {
                "categories": ["travel"],
                "last_used": "2026-09-21T10:00:00+00:00",
            },
        }

        user_details = select_agent_details(names, metadata, "Draft an email")
        self.assertIn(names[20], [entry["name"] for entry in user_details])
        self.assertLessEqual(len(user_details), 20)

        result_details = select_agent_details(
            names,
            metadata,
            f"[SUCCESS] {names[24]}: finished",
            message_type="agent",
        )
        self.assertEqual(result_details[0]["name"], names[24])
        self.assertIn("result_sender", result_details[0]["reasons"])


class RosterMetadataTests(unittest.TestCase):
    def test_metadata_persists_and_clear_removes_it(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            roster_path = Path(directory) / "roster.json"
            roster = AgentRoster(roster_path)
            roster.add_agent("Alice Meeting Email")
            roster.mark_used(
                "Alice Meeting Email",
                "Draft an email for Alice's meeting",
            )

            metadata_path = Path(directory) / "roster_metadata.json"
            metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
            self.assertEqual(
                metadata["Alice Meeting Email"]["categories"],
                ["email", "calendar"],
            )

            reloaded = AgentRoster(roster_path)
            self.assertEqual(
                reloaded.get_categories("Alice Meeting Email"),
                ["email", "calendar"],
            )

            reloaded.clear()
            self.assertFalse(roster_path.exists())
            self.assertFalse(metadata_path.exists())


if __name__ == "__main__":
    unittest.main()
