"""Coordinate execution agents and batch their results for the interaction agent."""

from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from .runtime import ExecutionAgentRuntime, ExecutionResult
from ...logging_config import logger
from ...config import get_settings
from ...services.draft_delivery import display_draft_for_review


@dataclass
class PendingExecution:
    """Track a pending execution request."""

    request_id: str
    agent_name: str
    instructions: str
    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)


@dataclass
class _BatchState:
    """Collect results for a single interaction-agent turn."""

    batch_id: str
    created_at: datetime = field(default_factory=datetime.now)
    pending: int = 0
    results: List[ExecutionResult] = field(default_factory=list)


class ExecutionBatchManager:
    """Run execution agents and deliver their combined outcome."""

    # Initialize batch manager with timeout and coordination state for execution agents
    def __init__(self, timeout_seconds: int = 90) -> None:
        self.timeout_seconds = timeout_seconds
        self._pending: Dict[str, PendingExecution] = {}
        self._batch_lock = asyncio.Lock()
        self._batch_state: Optional[_BatchState] = None

    # Run execution agent with timeout handling and batch coordination for interaction agent
    async def execute_agent(
        self,
        agent_name: str,
        instructions: str,
        request_id: Optional[str] = None,
    ) -> ExecutionResult:
        """Execute an agent asynchronously and buffer the result for batch dispatch."""

        if not request_id:
            request_id = str(uuid.uuid4())

        batch_id = await self._register_pending_execution(agent_name, instructions, request_id)

        try:
            logger.info(f"[{agent_name}] Execution started")
            runtime = ExecutionAgentRuntime(agent_name=agent_name)
            result = await asyncio.wait_for(
                runtime.execute(instructions),
                timeout=self.timeout_seconds,
            )
            status = "SUCCESS" if result.success else "FAILED"
            logger.info(f"[{agent_name}] Execution finished: {status}")
        except asyncio.TimeoutError:
            logger.error(f"[{agent_name}] Execution timed out after {self.timeout_seconds}s")
            result = ExecutionResult(
                agent_name=agent_name,
                success=False,
                response=f"Execution timed out after {self.timeout_seconds} seconds",
                error="Timeout",
            )
        except Exception as exc:  # pragma: no cover - defensive
            logger.exception(f"[{agent_name}] Execution failed unexpectedly")
            result = ExecutionResult(
                agent_name=agent_name,
                success=False,
                response=f"Execution failed: {exc}",
                error=str(exc),
            )
        finally:
            self._pending.pop(request_id, None)

        for structured_result in result.structured_results:
            structured_result.setdefault("task_id", request_id)

        await self._complete_execution(batch_id, result, agent_name)
        return result

    # Add execution request to current batch or create new batch if none exists
    async def _register_pending_execution(
        self,
        agent_name: str,
        instructions: str,
        request_id: str,
    ) -> str:
        """Attach a new execution to the active batch, opening one when required."""

        async with self._batch_lock:
            if self._batch_state is None:
                batch_id = str(uuid.uuid4())
                self._batch_state = _BatchState(batch_id=batch_id)
            else:
                batch_id = self._batch_state.batch_id

            self._batch_state.pending += 1
            self._pending[request_id] = PendingExecution(
                request_id=request_id,
                agent_name=agent_name,
                instructions=instructions,
                batch_id=batch_id,
            )

            return batch_id

    # Store execution result and send combined batch to interaction agent when complete
    async def _complete_execution(
        self,
        batch_id: str,
        result: ExecutionResult,
        agent_name: str,
    ) -> None:
        """Record the execution result and dispatch when the batch drains."""

        completed_results: Optional[List[ExecutionResult]] = None

        async with self._batch_lock:
            state = self._batch_state
            if state is None or state.batch_id != batch_id:
                logger.warning(f"[{agent_name}] Dropping result for unknown batch")
                return

            state.results.append(result)
            state.pending -= 1

            if state.pending == 0:
                completed_results = list(state.results)
                agent_names = [entry.agent_name for entry in state.results]
                logger.info(f"Execution batch completed: {', '.join(agent_names)}")
                self._batch_state = None

        if completed_results is None:
            return

        interaction_results = self._deliver_structured_results(completed_results)
        if interaction_results:
            await self._dispatch_to_interaction_agent(
                self._format_batch_payload(interaction_results)
            )

    # Return list of currently pending execution requests for monitoring purposes
    def get_pending_executions(self) -> List[Dict[str, str]]:
        """Expose pending executions for observability."""

        return [
            {
                "request_id": pending.request_id,
                "agent_name": pending.agent_name,
                "batch_id": pending.batch_id,
                "created_at": pending.created_at.isoformat(),
                "elapsed_seconds": (datetime.now() - pending.created_at).total_seconds(),
            }
            for pending in self._pending.values()
        ]

    # Clean up all pending executions and batch state on shutdown
    async def shutdown(self) -> None:
        """Clear pending bookkeeping (no background work remains)."""

        self._pending.clear()
        async with self._batch_lock:
            self._batch_state = None

    # Format multiple execution results into single message for interaction agent
    def _format_batch_payload(self, results: List[ExecutionResult]) -> str:
        """Render execution results into the interaction-agent format."""

        entries: List[str] = []
        for result in results:
            status = "SUCCESS" if result.success else "FAILED"
            response_text = (result.response or "(no response provided)").strip()
            entries.append(f"[{status}] {result.agent_name}: {response_text}")
            if not get_settings().draft_ready_enabled:
                for item in result.structured_results:
                    if item.get("type") == "draft_ready":
                        entries.append(json.dumps({**item, "agent_name": result.agent_name}, ensure_ascii=False))
        return "\n".join(entries)

    def _deliver_structured_results(
        self,
        results: List[ExecutionResult],
    ) -> List[ExecutionResult]:
        """Display known result types and return results that still need LLM judgment."""

        if not get_settings().draft_ready_enabled:
            return list(results)

        interaction_results: List[ExecutionResult] = []

        for result in results:
            handled, unknown = self._deliver_result(result)
            # A draft must not hide other outcomes or failures from a mixed task.
            if not handled or unknown or not result.success or not result.draft_only:
                interaction_results.append(result)

        return interaction_results

    def _deliver_result(self, result: ExecutionResult) -> Tuple[bool, bool]:
        handled = False
        unknown = False

        for structured_result in result.structured_results:
            if structured_result.get("type") != "draft_ready":
                unknown = True
                continue

            if not self._valid_draft_ready(structured_result):
                unknown = True
                continue

            try:
                display_draft_for_review(
                    agent_name=result.agent_name,
                    task_id=structured_result["task_id"],
                    draft_id=structured_result["draft_id"],
                    to=structured_result["to"],
                    subject=structured_result["subject"],
                    body=structured_result["body"],
                )
            except Exception:
                logger.exception("Draft delivery failed; forwarding result to interaction agent")
                unknown = True
                continue
            handled = True

        return handled, unknown

    @staticmethod
    def _valid_draft_ready(result: Dict[str, Any]) -> bool:
        required = ("task_id", "draft_id", "to", "subject", "body")
        return all(isinstance(result.get(field), str) and result[field].strip() for field in required)

    # Forward combined execution results to interaction agent for user response generation
    async def _dispatch_to_interaction_agent(self, payload: str) -> None:
        """Send the aggregated execution summary to the interaction agent."""

        from ..interaction_agent.runtime import InteractionAgentRuntime

        runtime = InteractionAgentRuntime()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            asyncio.run(runtime.handle_agent_message(payload))
            return

        loop.create_task(runtime.handle_agent_message(payload))
