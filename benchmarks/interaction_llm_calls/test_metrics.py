"""Exercise real runtime methods with fake dependencies, without loading .env."""

import ast
import asyncio
from dataclasses import dataclass, field
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Set
import unittest
from unittest.mock import AsyncMock, Mock, patch


ROOT = Path(__file__).resolve().parents[2]


def load_file(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


metrics = load_file("metrics", ROOT / "server/agents/interaction_agent/metrics.py")
runner = load_file("runner", Path(__file__).with_name("runner.py"))


def runtime_namespace():
    # Production imports initialize configuration and persistent services. Load
    # the unchanged runtime definitions with fake dependencies instead.
    path = ROOT / "server/agents/interaction_agent/runtime.py"
    tree = ast.parse(path.read_text(), filename=str(path))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    namespace = dict(
        json=json, dataclass=dataclass, field=field, Any=Any, Dict=Dict,
        List=List, Optional=Optional, Set=Set, ToolResult=SimpleNamespace,
        TurnType=metrics.TurnType, record_llm_call=metrics.record_llm_call,
        describes_tool_call=load_file("tool_protocol", ROOT / "server/agents/tool_protocol.py").describes_tool_call,
        logger=Mock(), build_system_prompt=Mock(return_value="system"),
        prepare_message_with_history=Mock(return_value=[]),
        request_chat_completion=AsyncMock(),
    )
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace


class MetricsTests(unittest.TestCase):
    def test_turn_labels_multiple_rounds_and_failed_call(self):
        namespace = runtime_namespace()
        runtime = namespace["InteractionAgentRuntime"].__new__(namespace["InteractionAgentRuntime"])
        runtime.api_key = "fake"
        runtime.model = "fake"
        runtime.tool_schemas = []
        runtime.conversation_log = Mock()
        runtime._load_conversation_transcript = Mock(return_value="")
        runtime._execute_tool = Mock(return_value=SimpleNamespace(success=True, user_message=None))
        runtime._format_tool_result = Mock(return_value="{}")
        namespace["request_chat_completion"].side_effect = [
            {"choices": [{"message": {"tool_calls": [{"id": "1", "function": {
                "name": "send_message_to_user", "arguments": '{"message":"Working on it."}'
            }}]}}]},
            {"choices": [{"message": {"content": "done"}}]},
            {"choices": [{"message": {"content": "agent result"}}]},
            RuntimeError("simulated model failure"),
        ]

        async def exercise():
            self.assertTrue((await runtime.execute("hello")).success)
            self.assertTrue((await runtime.handle_agent_message("result")).success)
            self.assertFalse((await runtime.execute("failure")).success)

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "calls.jsonl"
            with patch.dict(os.environ, {
                "OPENPOKE_INTERACTION_METRICS_PATH": str(path),
                "OPENPOKE_BENCHMARK_RUN_ID": "test",
            }):
                asyncio.run(exercise())
            self.assertEqual(runner.summarize(path, "test"), {
                "total_calls": 4, "user_turn_calls": 3, "agent_turn_calls": 1,
            })
            self.assertEqual(runner.summarize(path, "other")["total_calls"], 0)

    def test_disabled_and_unwritable_metrics_do_not_break_requests(self):
        with patch.dict(os.environ, {"OPENPOKE_INTERACTION_METRICS_PATH": ""}):
            metrics.record_llm_call("user")
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"OPENPOKE_INTERACTION_METRICS_PATH": directory}):
                with self.assertLogs("openpoke.server", level="WARNING"):
                    metrics.record_llm_call("agent")

    def test_runner_inherits_run_labels_and_preserves_failure_status(self):
        script = (
            "import runpy; "
            "m = runpy.run_path('server/agents/interaction_agent/metrics.py'); "
            "m['record_llm_call']('user'); m['record_llm_call']('agent'); "
            "raise SystemExit(3)"
        )
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run([
                sys.executable, str(Path(__file__).with_name("runner.py")),
                "--output-dir", directory, "--", sys.executable, "-c", script,
            ], cwd=ROOT, capture_output=True, text=True)
            self.assertEqual(result.returncode, 3, result.stderr)
            report = json.loads(next(Path(directory).glob("*/report.json")).read_text())
            self.assertEqual(report["total_calls"], 2)
            self.assertEqual(report["user_turn_calls"], 1)
            self.assertEqual(report["agent_turn_calls"], 1)
            self.assertEqual(report["command_exit_code"], 3)
            self.assertEqual(report["task_outcomes"], "not_evaluated")


if __name__ == "__main__":
    unittest.main()
