"""Execute sample events against real agent loops and an isolated fake mailbox."""

import argparse
import asyncio
from contextvars import ContextVar
from dataclasses import asdict
from datetime import timedelta
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time

from sample_tools import Mailbox, current_event
from check_outcomes import check as check_outcomes

ROOT = Path(__file__).resolve().parents[2]


def instrument_unmetered_runtime(module, record_call):
    """Use the same call boundary for worktrees without built-in counters."""
    if hasattr(module, "record_llm_call"):
        return
    turn = ContextVar("interaction_turn_type")
    runtime = module.InteractionAgentRuntime

    def wrap_entry(method, source):
        async def entry(self, *args, **kwargs):
            token = turn.set(source)
            try:
                return await method(self, *args, **kwargs)
            finally:
                turn.reset(token)
        return entry

    original_call = runtime._make_llm_call

    async def call(self, *args, **kwargs):
        record_call(turn.get())
        return await original_call(self, *args, **kwargs)

    runtime.execute = wrap_entry(runtime.execute, "user")
    runtime.handle_agent_message = wrap_entry(runtime.handle_agent_message, "agent")
    runtime._make_llm_call = call


def load_model_environment(path):
    """Load only model credentials/settings; explicit environment values win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() in {"OPENROUTER_API_KEY", "OPENROUTER_MODEL"}:
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


async def replay(events, invoke, mailbox, fixture, timeout, records=None):
    """Release timed overlaps; use drained work, not an acknowledgement, as completion.

    Run in a dedicated event loop: all other tasks belong to this workload.
    Shared batches mean completion is conservatively marked at global idle.
    """
    remaining = list(events)
    started, completed = {}, set()
    if records is None:
        records = {}
    main_task = asyncio.current_task()
    beginning = time.monotonic()
    previous_tick = beginning

    async def deliver(event):
        token = current_event.set(event["id"])
        try:
            if event["kind"] == "background":
                mailbox.deliver(event, fixture)
            else:
                mailbox.observe(event["text"])
            records[event["id"]]["turn_result"] = await invoke(event)
        except Exception as exc:
            records[event["id"]]["error"] = str(exc)
        finally:
            current_event.reset(token)

    while remaining or (asyncio.all_tasks() - {main_task}):
        tick = time.monotonic()
        mailbox.now += timedelta(seconds=tick - previous_tick)
        previous_tick = tick
        if time.monotonic() - beginning > timeout:
            raise TimeoutError(f"Workload exceeded {timeout} seconds")
        for event in list(remaining):
            release = event["release"]
            dependencies = release.get("after_completed", []) + release.get("after_delivered", [])
            if not set(dependencies) <= completed:
                continue
            parent = release.get("while_pending")
            if parent and (parent not in started or
                           time.monotonic() - started[parent] < release.get("offset_seconds", 0)):
                continue
            started[event["id"]] = time.monotonic()
            records[event["id"]] = {
                "id": event["id"], "kind": event["kind"], "text": event["text"],
                "started_seconds": started[event["id"]] - beginning,
                "received_at": mailbox.now.isoformat(),
                "expected": event.get("outcomes", event.get("check")),
                "overlap_met": parent not in completed if parent else None,
            }
            print(f"Starting {event['id']}", flush=True)
            remaining.remove(event)
            asyncio.create_task(deliver(event))
        await asyncio.sleep(0.02)
        if not (asyncio.all_tasks() - {main_task}):
            for event_id in started.keys() - completed:
                records[event_id]["drained_seconds"] = time.monotonic() - beginning
            completed.update(started)
            # A scheduled overlap may still be waiting for its offset.
            if remaining and not any(
                (e["release"].get("while_pending") in started) or
                set(e["release"].get("after_completed", []) +
                    e["release"].get("after_delivered", [])) <= completed
                for e in remaining
            ):
                raise RuntimeError("Unresolved workload dependencies")
    return list(records.values())


async def check_draft_delivery(args, mailbox):
    """Hold execution work constant: both versions receive the same finished draft."""
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager
    from server.agents.execution_agent.runtime import ExecutionResult
    from server.services.conversation import get_conversation_log
    from server.services.execution import get_agent_roster

    log = get_conversation_log()
    name = "Email to Erin"
    get_agent_roster().add_agent(name)
    log.record_user_message("Draft an email to erin@example.test asking her to review PR #94. Leave it unsent.")
    draft = mailbox.call("gmail_create_draft", name, recipient_email="erin@example.test",
                         subject="Review PR #94", body="Hi Erin, please review PR #94. Thanks!")
    text = (f"Draft ready for review. Draft ID: {draft['draft_id']}\n"
            f"To: {draft['recipient_email']}\nSubject: {draft['subject']}\n\n{draft['body']}")
    kwargs = {}
    if "structured_results" in ExecutionResult.__dataclass_fields__:
        kwargs["structured_results"] = [{"type": "draft_ready", "task_id": "paired-draft",
            "draft_id": draft["draft_id"], "to": draft["recipient_email"],
            "subject": draft["subject"], "body": draft["body"]}]
    if "draft_only" in ExecutionResult.__dataclass_fields__:
        kwargs["draft_only"] = True
    result = ExecutionResult(name, True, text, tools_executed=["gmail_create_draft"], **kwargs)
    manager = ExecutionBatchManager()
    batch = await manager._register_pending_execution(name, "Prepare draft", "paired-draft")
    await manager._complete_execution(batch, result, name)
    main = asyncio.current_task()
    start = time.monotonic()
    while asyncio.all_tasks() - {main}:
        if time.monotonic() - start > args.timeout:
            raise TimeoutError("Draft delivery check timed out")
        await asyncio.sleep(0.02)
    expected = f"To: {draft['recipient_email']}\nSubject: {draft['subject']}\n\n{draft['body']}"
    replies = [body for tag, _, body in log.iter_entries() if tag == "poke_reply"]
    checks = {"exact_draft_displayed_once": replies.count(expected) == 1,
              "email_not_sent": not mailbox.sent, "one_stored_draft": len(mailbox.drafts) == 1}
    output = {"mode": "paired_draft_delivery", "server_root": str(args.server_root.resolve()),
              "task_outcomes": "passed" if all(checks.values()) else "failed",
              "checks": checks, "transcript": log.load_transcript(), "mailbox": mailbox.snapshot()}
    args.report.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(checks))
    return 0 if all(checks.values()) else 1


async def run_worker(args):
    from server.agents.interaction_agent import runtime as interaction
    from server.agents.interaction_agent.metrics import record_llm_call
    from server.agents.execution_agent import runtime as execution
    from server.config import get_settings
    from server.services.timezone_store import get_timezone_store
    from server.services.conversation import get_conversation_log
    from server.services.conversation import log as conversation_log
    from server.services.execution import log_store
    from server.services.conversation.summarization import working_memory_log

    fixture = json.loads((ROOT / "benchmarks/sample_inbox.json").read_text())
    instrument_unmetered_runtime(interaction, record_llm_call)
    events = json.loads((ROOT / "benchmarks/sample_workload.json").read_text())["events"]
    if args.limit:
        events = events[:args.limit]
    mailbox = Mailbox(fixture)
    for module in (conversation_log, log_store, working_memory_log):
        module.now_in_user_timezone = lambda fmt: mailbox.now.strftime(fmt)
    get_settings().conversation_summary_threshold = 0
    get_timezone_store().set_timezone(fixture["timezone"])
    schemas = execution.get_tool_schemas()
    execution.get_tool_registry = lambda agent_name: mailbox.registry(agent_name, schemas)

    # Give both LLMs the fixture clock without modifying production prompts.
    original_interaction = interaction.build_system_prompt
    interaction.build_system_prompt = lambda: (
        original_interaction() + f"\nCurrent benchmark time: {mailbox.now.isoformat()}"
    )
    original_execution = execution.ExecutionAgent.build_system_prompt
    execution.ExecutionAgent.build_system_prompt = lambda self: (
        original_execution(self) + f"\nCurrent benchmark time: {mailbox.now.isoformat()}"
    )

    if args.smoke:
        async def fake_interaction(**kwargs):
            if any(m["role"] == "tool" for m in kwargs["messages"]):
                message = {"content": "Smoke: delegation submitted."}
            elif "<new_agent_message>" in kwargs["messages"][0]["content"]:
                message = {"content": "Smoke: execution result received."}
            else:
                message = {"content": "", "tool_calls": [{"id": "smoke", "type": "function",
                    "function": {"name": "send_message_to_agent", "arguments": json.dumps({
                        "agent_name": "Smoke agent", "instructions": "Inspect the sample inbox."
                    })}}]}
            return {"choices": [{"message": message}]}

        async def fake_execution(**kwargs):
            if any(m["role"] == "tool" for m in kwargs["messages"]):
                message = {"content": "Smoke: sample inbox inspected."}
            else:
                message = {"content": "", "tool_calls": [{"id": "search", "type": "function",
                    "function": {"name": "task_email_search", "arguments": '{"search_query":"inbox"}'}}]}
            return {"choices": [{"message": message}]}

        interaction.request_chat_completion = fake_interaction
        execution.request_chat_completion = fake_execution

    async def invoke(event):
        runtime = interaction.InteractionAgentRuntime()
        result = await (runtime.execute(event["text"]) if event["kind"] == "user"
                        else runtime.handle_agent_message(event["text"]))
        return asdict(result)

    if args.draft_check:
        return await check_draft_delivery(args, mailbox)

    output = {"mode": "smoke_fake_models" if args.smoke else "live_models_fake_mailbox",
              "server_root": str(args.server_root.resolve()),
              "task_outcomes": "manual_review_required", "events": []}
    records = {}
    try:
        output["events"] = await replay(events, invoke, mailbox, fixture, args.timeout, records)
        failed = any(e.get("error") or not e.get("turn_result", {}).get("success")
                     for e in output["events"])
        output["status"] = "turn_errors" if failed else "finished"
    except Exception as exc:
        output.update(status="failed", error=str(exc))
    finally:
        # Stop outstanding work before taking the final snapshot.
        pending = asyncio.all_tasks() - {asyncio.current_task()}
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
        output["events"] = list(records.values())
        output["mailbox"] = mailbox.snapshot()
        output["transcript"] = get_conversation_log().load_transcript()
        output["send_and_reminder_checks"] = check_outcomes(output)
        args.report.write_text(json.dumps(output, indent=2) + "\n")
    return 0 if output["status"] == "finished" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="fake models; not a baseline")
    parser.add_argument("--draft-check", action="store_true", help="compare delivery of one identical finished draft")
    parser.add_argument("--limit", type=int, help="run only the first N fixture events")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--server-root", type=Path, default=ROOT,
                        help="worktree to benchmark; fixtures and model settings remain unchanged")
    args = parser.parse_args()
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.smoke and args.limit is None:
        args.limit = 3
    if not args.smoke and not args.workspace:
        load_model_environment(ROOT / ".env")
    if not args.smoke and not os.environ.get("OPENROUTER_API_KEY"):
        parser.error("Set OPENROUTER_API_KEY in the environment or the project root .env")
    if args.workspace:
        sys.path.insert(0, str(args.workspace))
        return asyncio.run(run_worker(args))

    metrics_path = os.environ.get("OPENPOKE_INTERACTION_METRICS_PATH")
    if args.report is None:
        args.report = (Path(metrics_path).parent if metrics_path else
                       ROOT / "benchmarks/results") / "workload.json"
    args.report = args.report.resolve()
    args.report.parent.mkdir(parents=True, exist_ok=True)
    # Import the server from a clean copy: existing logs, roster, credentials,
    # and .env cannot enter the benchmark and production data stays untouched.
    with tempfile.TemporaryDirectory(prefix="openpoke-benchmark-") as directory:
        shutil.copytree(args.server_root / "server", Path(directory) / "server", ignore=shutil.ignore_patterns(
            "data", "__pycache__", ".env*", "roster.json", "venv", "*.log"
        ))
        metrics_file = Path(directory) / "server/agents/interaction_agent/metrics.py"
        if not metrics_file.exists():
            shutil.copyfile(ROOT / "server/agents/interaction_agent/metrics.py", metrics_file)
        command = [sys.executable, str(Path(__file__).resolve()), "--workspace", directory,
                   "--server-root", str(args.server_root.resolve()),
                   "--report", str(args.report), "--timeout", str(args.timeout)]
        if args.limit:
            command.extend(["--limit", str(args.limit)])
        if args.smoke:
            command.append("--smoke")
        if args.draft_check:
            command.append("--draft-check")
        env = dict(os.environ)
        if args.smoke:
            env["OPENROUTER_API_KEY"] = "fake-smoke-key"
        return subprocess.run(command, env=env, cwd=directory).returncode


if __name__ == "__main__":
    sys.exit(main())
