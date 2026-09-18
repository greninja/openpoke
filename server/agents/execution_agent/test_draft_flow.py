"""Regression checks for duplicate work and draft-only delivery."""

import json
import tempfile
from pathlib import Path
import unittest
from unittest.mock import AsyncMock, Mock, patch

from .runtime import ExecutionAgentRuntime, ExecutionResult
from .batch_manager import ExecutionBatchManager
from ..interaction_agent.runtime import InteractionAgentRuntime
from ..interaction_agent.tools import ToolResult
from ...services.draft_delivery import display_draft_for_review
from ...services.conversation.log import ConversationLog


def response(*calls):
    return {"choices": [{"message": {"content": "", "tool_calls": [
        {"id": str(i), "type": "function", "function": {
            "name": name, "arguments": json.dumps(args)}}
        for i, (name, args) in enumerate(calls)
    ]}}]}


class InteractionTests(unittest.IsolatedAsyncioTestCase):
    def runtime(self, replies):
        runtime = InteractionAgentRuntime.__new__(InteractionAgentRuntime)
        runtime._make_llm_call = AsyncMock(side_effect=replies)
        runtime._execute_tool = Mock(return_value=ToolResult(success=True, payload={}))
        return runtime

    async def test_successful_delegation_finishes_without_another_model_call(self):
        args = {"agent_name": "Erin", "instructions": "Send approved D10"}
        runtime = self.runtime([response(("send_message_to_agent", args),
                                         ("send_message_to_agent", args))])
        await runtime._run_interaction_loop("", [])
        self.assertEqual(runtime._make_llm_call.await_count, 1)
        self.assertEqual(runtime._execute_tool.call_count, 1)

    async def test_acknowledgement_alone_does_not_drop_pending_work(self):
        runtime = self.runtime([
            response(("send_message_to_user", {"message": "I will draft it."})),
            response(("send_message_to_agent", {"agent_name": "Erin", "instructions": "Draft"})),
        ])
        await runtime._run_interaction_loop("", [])
        self.assertEqual(runtime._make_llm_call.await_count, 2)

    async def test_error_can_be_corrected_without_repeating_successful_work(self):
        reply = response(("send_message_to_user", {"message": "Working"}),
                         ("send_message_to_agent", {"agent_name": "Erin", "instructions": "Draft"}))
        runtime = self.runtime([reply, reply])
        runtime._execute_tool.side_effect = [ToolResult(True), ToolResult(False), ToolResult(True)]
        await runtime._run_interaction_loop("", [])
        self.assertEqual(runtime._make_llm_call.await_count, 2)
        self.assertEqual(runtime._execute_tool.call_count, 3)

    async def test_printed_tool_call_is_repaired_not_shown_as_completed_work(self):
        runtime = self.runtime([
            {"choices": [{"message": {"content": "print(send_message_to_agent(agent_name='Erin'))"}}]},
            response(("send_message_to_agent", {"agent_name": "Erin", "instructions": "Draft"})),
        ])
        result = await runtime._run_interaction_loop("", [])
        self.assertEqual(runtime._make_llm_call.await_count, 2)
        self.assertEqual(runtime._execute_tool.call_count, 1)
        self.assertEqual(result.last_assistant_text, "")


class ExecutionTests(unittest.IsolatedAsyncioTestCase):
    async def test_error_dictionary_is_not_reported_as_success(self):
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.tool_registry = {"send": lambda: {"error": "Draft not found"}}
        success, _ = await runtime._execute_tool("send", {})
        self.assertFalse(success)

    async def test_repeated_create_does_not_create_or_display_two_drafts(self):
        runtime = ExecutionAgentRuntime.__new__(ExecutionAgentRuntime)
        runtime.agent = Mock(name="agent")
        runtime.agent.name = "Erin"
        create = Mock(return_value={"draft_id": "D10"})
        runtime.tool_registry = {"gmail_create_draft": create}
        args = {"recipient_email": "erin@example.test", "subject": "PR", "body": "Review PR"}
        call = response(("gmail_create_draft", args))
        runtime._make_llm_call = AsyncMock(side_effect=[call, call,
            {"choices": [{"message": {"content": "Draft ready."}}]}])
        result = await runtime.execute("Draft only")
        self.assertTrue(result.success)
        self.assertTrue(result.draft_only)
        self.assertEqual(create.call_count, 1)
        self.assertEqual(len(result.structured_results), 1)


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def draft(self):
        return {"type": "draft_ready", "task_id": "T1", "draft_id": "D10",
                "to": "erin@example.test", "subject": "PR", "body": "Review PR"}

    async def test_persisted_multiline_draft_is_not_displayed_twice(self):
        with tempfile.TemporaryDirectory() as directory:
            log = ConversationLog(Path(directory) / "conversation.log")
            log._notify_summarization = Mock()
            log._working_memory_log = Mock()
            args = self.draft()
            args.pop("type")
            args["body"] = "Hi Erin,\nPlease review PR #94."
            with patch("server.services.draft_delivery.get_conversation_log", return_value=log):
                display_draft_for_review(agent_name="Erin", **args)
                display_draft_for_review(agent_name="Erin", **args)
            self.assertEqual(sum(tag == "poke_reply" for tag, _, _ in log.iter_entries()), 1)

    @patch("server.agents.execution_agent.batch_manager.display_draft_for_review")
    async def test_draft_only_result_skips_callback_but_mixed_result_does_not(self, display):
        for draft_only in [True, False]:
            manager = ExecutionBatchManager()
            batch = await manager._register_pending_execution("Erin", "Draft", "T1")
            manager._dispatch_to_interaction_agent = AsyncMock()
            result = ExecutionResult("Erin", True, "Done", structured_results=[self.draft()],
                                     draft_only=draft_only)
            await manager._complete_execution(batch, result, "Erin")
            self.assertEqual(manager._dispatch_to_interaction_agent.await_count, int(not draft_only))

    @patch("server.agents.execution_agent.batch_manager.display_draft_for_review", side_effect=OSError("disk"))
    async def test_delivery_error_falls_back_to_interaction(self, display):
        manager = ExecutionBatchManager()
        result = ExecutionResult("Erin", True, "Draft", structured_results=[self.draft()], draft_only=True)
        self.assertEqual(manager._deliver_structured_results([result]), [result])

    @patch("server.services.draft_delivery.get_conversation_log")
    async def test_same_draft_is_shown_once_without_repeated_approval_prompts(self, get_log):
        entries = []
        log = Mock()
        log.iter_entries.side_effect = lambda: iter(entries)
        log.record_agent_message.side_effect = lambda text: entries.append(("agent_message", "", text))
        get_log.return_value = log
        args = self.draft()
        args.pop("type")
        display_draft_for_review(agent_name="Erin", **args)
        display_draft_for_review(agent_name="Erin", **args)
        args["draft_id"] = "D11"
        display_draft_for_review(agent_name="Erin", **args)
        log.record_reply.assert_called_once_with("To: erin@example.test\nSubject: PR\n\nReview PR")
