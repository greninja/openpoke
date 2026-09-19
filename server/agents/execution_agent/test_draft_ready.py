"""Tests for structured draft result creation and routing."""

from __future__ import annotations

import unittest
from unittest.mock import AsyncMock, patch

from .batch_manager import ExecutionBatchManager
from .runtime import ExecutionAgentRuntime, ExecutionResult


class DraftReadyResultTests(unittest.TestCase):
    def test_disabled_delivery_forwards_exact_draft_and_other_results(self):
        from types import SimpleNamespace
        result = ExecutionResult("Email Alice", True, "Draft stored.", structured_results=[{
            "type": "draft_ready", "draft_id": "D1", "task_id": "T1",
            "to": "alice@example.test", "subject": "Exact subject", "body": "Exact body",
        }], draft_only=True)
        failure = ExecutionResult("Search", False, "Lookup failed")
        with patch("server.agents.execution_agent.batch_manager.get_settings",
                   return_value=SimpleNamespace(draft_ready_enabled=False)), patch(
                       "server.agents.execution_agent.batch_manager.display_draft_for_review") as display:
            manager = ExecutionBatchManager()
            self.assertEqual(manager._deliver_structured_results([result, failure]), [result, failure])
            display.assert_not_called()
            payload = manager._format_batch_payload([result, failure])
            for text in ('"draft_id": "D1"', '"agent_name": "Email Alice"',
                         '"subject": "Exact subject"', '"body": "Exact body"', "Lookup failed"):
                self.assertIn(text, payload)

    def test_builds_draft_ready_from_successful_tool_call(self) -> None:
        result = ExecutionAgentRuntime._build_structured_result(
            "gmail_create_draft",
            True,
            {"data": {"draft_id": "gmail-draft-123"}},
            {
                "recipient_email": "alice@example.com",
                "subject": "Re: Q3 budget",
                "body": "I will send the numbers Monday.",
            },
        )

        self.assertEqual(
            result,
            {
                "type": "draft_ready",
                "draft_id": "gmail-draft-123",
                "to": "alice@example.com",
                "subject": "Re: Q3 budget",
                "body": "I will send the numbers Monday.",
            },
        )

    def test_does_not_structure_failed_draft(self) -> None:
        result = ExecutionAgentRuntime._build_structured_result(
            "gmail_create_draft",
            True,
            {"error": "Gmail unavailable"},
            {
                "recipient_email": "alice@example.com",
                "subject": "Subject",
                "body": "Body",
            },
        )

        self.assertIsNone(result)

    @patch("server.agents.execution_agent.batch_manager.display_draft_for_review")
    def test_draft_result_bypasses_interaction_llm(self, display_draft) -> None:
        manager = ExecutionBatchManager()
        result = ExecutionResult(
            agent_name="Email Alice",
            success=True,
            response="Draft created.",
            draft_only=True,
            structured_results=[
                {
                    "type": "draft_ready",
                    "task_id": "task-17",
                    "draft_id": "gmail-draft-123",
                    "to": "alice@example.com",
                    "subject": "Re: Q3 budget",
                    "body": "I will send the numbers Monday.",
                }
            ],
        )

        remaining = manager._deliver_structured_results([result])

        self.assertEqual(remaining, [])
        display_draft.assert_called_once_with(
            agent_name="Email Alice",
            task_id="task-17",
            draft_id="gmail-draft-123",
            to="alice@example.com",
            subject="Re: Q3 budget",
            body="I will send the numbers Monday.",
        )

    def test_plain_result_still_uses_interaction_llm(self) -> None:
        manager = ExecutionBatchManager()
        result = ExecutionResult(
            agent_name="Flight search",
            success=True,
            response="Found two flights.",
        )

        self.assertEqual(manager._deliver_structured_results([result]), [result])


class DraftReadyBatchTests(unittest.IsolatedAsyncioTestCase):
    @patch("server.agents.execution_agent.batch_manager.display_draft_for_review")
    async def test_completed_draft_batch_does_not_dispatch_to_interaction_llm(
        self,
        display_draft,
    ) -> None:
        manager = ExecutionBatchManager()
        batch_id = await manager._register_pending_execution(
            "Email Alice",
            "Draft a reply",
            "task-17",
        )
        result = ExecutionResult(
            agent_name="Email Alice",
            success=True,
            response="Draft created.",
            draft_only=True,
            structured_results=[
                {
                    "type": "draft_ready",
                    "task_id": "task-17",
                    "draft_id": "gmail-draft-123",
                    "to": "alice@example.com",
                    "subject": "Re: Q3 budget",
                    "body": "I will send the numbers Monday.",
                }
            ],
        )

        manager._dispatch_to_interaction_agent = AsyncMock()
        await manager._complete_execution(batch_id, result, "Email Alice")

        display_draft.assert_called_once()
        manager._dispatch_to_interaction_agent.assert_not_awaited()


if __name__ == "__main__":
    unittest.main()
