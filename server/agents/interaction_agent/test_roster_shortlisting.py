"""Unit tests for interaction-agent roster shortlisting behavior."""

import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from server.agents.interaction_agent.agent import _render_active_agents
from server.agents.interaction_agent.runtime import (
    InteractionAgentRuntime,
    _ToolCall,
)
from server.agents.interaction_agent.tools import TOOL_SCHEMAS


class PromptRenderingTests(unittest.TestCase):
    @patch("server.agents.interaction_agent.agent.get_settings")
    def test_shortlist_details_are_rendered_as_agent_attributes(
        self, get_settings: Mock
    ) -> None:
        get_settings.return_value = SimpleNamespace(
            roster_shortlisting_enabled=True
        )

        rendered = _render_active_agents(
            active_agents=[
                {
                    "name": "Alice Meeting Email",
                    "categories": ["email", "calendar"],
                    "reasons": ["recent", "category:email"],
                }
            ]
        )

        self.assertIn('name="Alice Meeting Email"', rendered)
        self.assertIn('categories="email,calendar"', rendered)
        self.assertIn('visible_because="recent,category:email"', rendered)


class ToolSchemaTests(unittest.TestCase):
    def test_roster_tools_are_exposed_with_category_validation(self) -> None:
        functions = {
            schema["function"]["name"]: schema["function"]
            for schema in TOOL_SCHEMAS
        }

        self.assertIn("get_full_roster", functions)
        category_schema = functions["send_message_to_agent"]["parameters"][
            "properties"
        ]["categories"]
        self.assertEqual(
            category_schema["items"]["enum"],
            ["email", "travel", "finance", "calendar"],
        )


class RuntimeValidationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        self.runtime.settings = SimpleNamespace(roster_shortlisting_enabled=True)

    @patch("server.agents.interaction_agent.runtime.get_agent_roster")
    def test_broad_new_agent_name_is_rejected(self, get_roster: Mock) -> None:
        get_roster.return_value = SimpleNamespace(
            load=Mock(),
            get_agents=Mock(return_value=[]),
        )

        result = self.runtime._execute_tool(
            _ToolCall(
                identifier="call-1",
                name="send_message_to_agent",
                arguments={
                    "agent_name": "Email Assistant",
                    "instructions": "Draft an email",
                },
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.payload["error"], "New agent name is too broad.")

    @patch("server.agents.interaction_agent.runtime.get_agent_roster")
    def test_existing_agent_rejects_unrelated_category(
        self, get_roster: Mock
    ) -> None:
        get_roster.return_value = SimpleNamespace(
            load=Mock(),
            get_agents=Mock(return_value=["Alice Meeting Email"]),
            get_categories=Mock(return_value=["email", "calendar"]),
        )

        result = self.runtime._execute_tool(
            _ToolCall(
                identifier="call-2",
                name="send_message_to_agent",
                arguments={
                    "agent_name": "Alice Meeting Email",
                    "instructions": "Find a flight to Delhi",
                },
            )
        )

        self.assertFalse(result.success)
        self.assertEqual(result.payload["missing_categories"], ["travel"])


if __name__ == "__main__":
    unittest.main()
