"""Simplified Execution Agent Runtime."""

import inspect
import json
from typing import Dict, Any, List, Optional, Tuple
from dataclasses import dataclass, field

from .agent import ExecutionAgent
from .tools import get_tool_schemas, get_tool_registry
from ..tool_protocol import describes_tool_call
from ...config import get_settings
from ...openrouter_client import request_chat_completion
from ...logging_config import logger


@dataclass
class ExecutionResult:
    """Result from an execution agent."""
    agent_name: str
    success: bool
    response: str
    error: Optional[str] = None
    tools_executed: List[str] = None
    structured_results: List[Dict[str, Any]] = field(default_factory=list)
    draft_only: bool = False


class ExecutionAgentRuntime:
    """Manages the execution of a single agent request."""

    MAX_TOOL_ITERATIONS = 8

    # Initialize execution agent runtime with settings, tools, and agent instance
    def __init__(self, agent_name: str):
        settings = get_settings()
        self.agent = ExecutionAgent(agent_name)
        self.api_key = settings.openrouter_api_key
        self.model = settings.execution_agent_model
        self.tool_registry = get_tool_registry(agent_name=agent_name)
        self.tool_schemas = get_tool_schemas()

        if not self.api_key:
            raise ValueError("OpenRouter API key not configured. Set OPENROUTER_API_KEY environment variable.")

    # Main execution loop for running agent with LLM calls and tool execution
    async def execute(self, instructions: str) -> ExecutionResult:
        """Execute the agent with given instructions."""
        try:
            # Build system prompt with history
            system_prompt = self.agent.build_system_prompt_with_history()

            # Start conversation with the instruction
            messages = [{"role": "user", "content": instructions}]
            tools_executed: List[str] = []
            structured_results: List[Dict[str, Any]] = []
            successful_writes = {}
            had_tool_error = False
            final_response: Optional[str] = None

            for iteration in range(self.MAX_TOOL_ITERATIONS):
                logger.info(
                    f"[{self.agent.name}] Requesting plan (iteration {iteration + 1})"
                )
                response = await self._make_llm_call(system_prompt, messages, with_tools=True)
                assistant_message = response.get("choices", [{}])[0].get("message", {})

                if not assistant_message:
                    raise RuntimeError("LLM response did not include an assistant message")

                raw_tool_calls = assistant_message.get("tool_calls", []) or []
                parsed_tool_calls = self._extract_tool_calls(raw_tool_calls)

                assistant_entry: Dict[str, Any] = {
                    "role": "assistant",
                    "content": assistant_message.get("content", "") or "",
                }
                if raw_tool_calls:
                    assistant_entry["tool_calls"] = raw_tool_calls
                messages.append(assistant_entry)

                if not parsed_tool_calls:
                    if describes_tool_call(assistant_entry["content"], self.tool_registry) or not assistant_entry["content"].strip():
                        messages.append({"role": "user", "content":
                            "No tool was invoked in that response. Use actual API tool calls "
                            "instead of describing or printing them. If the task is already "
                            "complete, report the observed result accurately."})
                        continue
                    final_response = assistant_entry["content"]
                    break

                for tool_call in parsed_tool_calls:
                    tool_name = tool_call.get("name", "")
                    tool_args = tool_call.get("arguments", {})
                    call_id = tool_call.get("id")

                    if not tool_name:
                        logger.warning("Tool call missing name: %s", tool_call)
                        failure = {"error": "Tool call missing name; unable to execute."}
                        tool_message = {
                            "role": "tool",
                            "tool_call_id": call_id or "unknown_tool",
                            "content": self._format_tool_result(
                                tool_name or "<unknown>", False, failure, tool_args
                            ),
                        }
                        messages.append(tool_message)
                        continue

                    tools_executed.append(tool_name)
                    logger.info(f"[{self.agent.name}] Executing tool: {tool_name}")

                    key = (tool_name, json.dumps(tool_args, sort_keys=True))
                    if key in successful_writes:
                        success, result = True, successful_writes[key]
                    else:
                        success, result = await self._execute_tool(tool_name, tool_args)
                        if success and tool_name in {
                            "gmail_create_draft", "gmail_execute_draft", "gmail_delete_draft"
                        }:
                            successful_writes[key] = result
                    had_tool_error = had_tool_error or not success

                    structured_result = self._build_structured_result(
                        tool_name,
                        success,
                        result,
                        tool_args,
                    )
                    if structured_result is not None and not any(
                        item["draft_id"] == structured_result["draft_id"]
                        for item in structured_results
                    ):
                        structured_results.append(structured_result)
                    if success and tool_name in {"gmail_execute_draft", "gmail_delete_draft"}:
                        structured_results = [item for item in structured_results
                                              if item["draft_id"] != str(tool_args.get("draft_id"))]
                        successful_writes = {
                            cached_key: cached_result for cached_key, cached_result in successful_writes.items()
                            if not (cached_key[0] == "gmail_create_draft" and
                                    self._find_draft_id(cached_result) == str(tool_args.get("draft_id")))
                        }

                    if success:
                        logger.info(f"[{self.agent.name}] Tool {tool_name} completed successfully")
                        record_payload = self._safe_json_dump(result)
                    else:
                        error_detail = result.get("error") if isinstance(result, dict) else str(result)
                        logger.warning(f"[{self.agent.name}] Tool {tool_name} failed: {error_detail}")
                        record_payload = error_detail

                    self.agent.record_tool_execution(
                        tool_name,
                        self._safe_json_dump(tool_args),
                        record_payload
                    )

                    tool_message = {
                        "role": "tool",
                        "tool_call_id": call_id or tool_name,
                        "content": self._format_tool_result(tool_name, success, result, tool_args),
                    }
                    messages.append(tool_message)

            else:
                raise RuntimeError("Reached tool iteration limit without final response")

            if final_response is None:
                raise RuntimeError("LLM did not return a final response")

            self.agent.record_response(final_response)

            return ExecutionResult(
                agent_name=self.agent.name,
                success=True,
                response=final_response,
                tools_executed=tools_executed,
                structured_results=structured_results,
                draft_only=bool(structured_results) and not had_tool_error and
                    set(tools_executed) <= {"gmail_create_draft", "gmail_delete_draft"},
            )

        except Exception as e:
            logger.error(f"[{self.agent.name}] Execution failed: {e}")
            error_msg = str(e)
            failure_text = f"Failed to complete task: {error_msg}"
            self.agent.record_response(f"Error: {error_msg}")

            return ExecutionResult(
                agent_name=self.agent.name,
                success=False,
                response=failure_text,
                error=error_msg
            )

    @classmethod
    def _build_structured_result(
        cls,
        tool_name: str,
        success: bool,
        result: Any,
        arguments: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Create backend-routable results from successful tool calls."""

        if tool_name != "gmail_create_draft" or not success:
            return None
        if isinstance(result, dict) and result.get("error"):
            return None

        draft_id = cls._find_draft_id(result)
        required_fields = ("recipient_email", "subject", "body")
        if not draft_id or any(field not in arguments for field in required_fields):
            return None

        return {
            "type": "draft_ready",
            "draft_id": draft_id,
            "to": arguments["recipient_email"],
            "subject": arguments["subject"],
            "body": arguments["body"],
        }

    @classmethod
    def _find_draft_id(cls, value: Any) -> Optional[str]:
        """Find the draft identifier in provider responses with nested envelopes."""

        if not isinstance(value, dict):
            return None

        for key in ("draft_id", "draftId"):
            candidate = value.get(key)
            if candidate is not None:
                return str(candidate)

        for key in ("draft", "data", "result", "response_data"):
            candidate = cls._find_draft_id(value.get(key))
            if candidate:
                return candidate

        candidate = value.get("id")
        if candidate is not None:
            return str(candidate)

        return None

    # Execute OpenRouter API call with system prompt, messages, and optional tool schemas
    async def _make_llm_call(self, system_prompt: str, messages: List[Dict], with_tools: bool) -> Dict:
        """Make an LLM call."""
        tools_to_send = self.tool_schemas if with_tools else None
        logger.info(f"[{self.agent.name}] Calling LLM with model: {self.model}, tools: {len(tools_to_send) if tools_to_send else 0}")
        return await request_chat_completion(
            model=self.model,
            messages=messages,
            system=system_prompt,
            api_key=self.api_key,
            tools=tools_to_send
        )

    # Parse and validate tool calls from LLM response into structured format
    def _extract_tool_calls(self, raw_tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        """Extract tool calls from an assistant message."""
        tool_calls: List[Dict[str, Any]] = []

        for tool in raw_tools:
            function = tool.get("function", {})
            name = function.get("name", "")
            args = function.get("arguments", "")

            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {}

            if name:
                tool_calls.append({
                    "id": tool.get("id"),
                    "name": name,
                    "arguments": args,
                })

        return tool_calls

    # Safely convert objects to JSON with fallback to string representation
    def _safe_json_dump(self, payload: Any) -> str:
        """Serialize payload to JSON, falling back to string representation."""
        try:
            return json.dumps(payload, default=str)
        except TypeError:
            return str(payload)

    # Format tool execution results into JSON structure for LLM consumption
    def _format_tool_result(
        self,
        tool_name: str,
        success: bool,
        result: Any,
        arguments: Dict[str, Any],
    ) -> str:
        """Build a structured string for tool responses."""
        if success:
            payload: Dict[str, Any] = {
                "tool": tool_name,
                "status": "success",
                "arguments": arguments,
                "result": result,
            }
        else:
            error_detail = result.get("error") if isinstance(result, dict) else str(result)
            payload = {
                "tool": tool_name,
                "status": "error",
                "arguments": arguments,
                "error": error_detail,
            }
        return self._safe_json_dump(payload)

    # Execute tool function from registry with error handling and async support
    async def _execute_tool(self, tool_name: str, arguments: Dict) -> Tuple[bool, Any]:
        """Execute a tool. Returns (success, result)."""
        tool_func = self.tool_registry.get(tool_name)
        if not tool_func:
            return False, {"error": f"Unknown tool: {tool_name}"}

        try:
            result = tool_func(**arguments)
            if inspect.isawaitable(result):
                result = await result
            if isinstance(result, dict) and (
                result.get("error") or result.get("success") is False or
                result.get("successful") is False or result.get("status") == "error"
            ):
                return False, result
            return True, result
        except Exception as e:
            return False, {"error": str(e)}
