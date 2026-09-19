"""Execute sample events against real agent loops and an isolated fake mailbox."""

import argparse
import asyncio
from copy import deepcopy
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
from check_outcomes import evaluate as check_outcomes

ROOT = Path(__file__).resolve().parents[2]
BENCHMARK_MODEL = "google/gemini-2.5-flash"


def select_server_root(draft_ready):
    """Both flag values intentionally select this checkout."""
    return ROOT


def instrument_unmetered_runtime(module, record_call, record_usage, record_turn):
    """Measure original code at the same boundaries as the current runtime."""
    if hasattr(module, "record_llm_usage"):
        return
    turn = ContextVar("interaction_turn_type")
    runtime = module.InteractionAgentRuntime

    def wrap_entry(method, source):
        async def entry(self, *args, **kwargs):
            token = turn.set(source)
            record_turn(source)
            try:
                return await method(self, *args, **kwargs)
            finally:
                turn.reset(token)
        return entry

    original_request = module.request_chat_completion

    async def request(**kwargs):
        source = turn.get()
        if not hasattr(module, "record_llm_call"):
            record_call(source)
        response = await original_request(**kwargs)
        record_usage(source, response)
        return response

    runtime.execute = wrap_entry(runtime.execute, "user")
    runtime.handle_agent_message = wrap_entry(runtime.handle_agent_message, "agent")
    module.request_chat_completion = request


def load_model_environment(path):
    """Load only model credentials/settings; explicit environment values win."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.strip().partition("=")
        if separator and key.strip() in {"OPENROUTER_API_KEY", "OPENROUTER_MODEL"}:
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


def target_metadata(args):
    root = args.server_root.resolve()
    def git(*arguments):
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments], text=True
        ).strip()
    return {"server_root": str(root), "server_commit": git("rev-parse", "HEAD"),
            "server_dirty": bool(git("status", "--porcelain", "--untracked-files=no")),
            "model": args.model, "draft_delivery": "direct" if args.draft_ready else "interaction",
            "draft_ready": args.draft_ready, "comparison": "same_repo_draft_ready_toggle"}


async def replay(events, invoke, mailbox, fixture, timeout, records=None, capture_evidence=None):
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
            if capture_evidence:
                snapshot = capture_evidence()
                records[event["id"]]["_conversation_start"] = len(snapshot["conversation"])
                records[event["id"]]["_action_start"] = len(snapshot["actions"])
            print(f"Starting {event['id']}", flush=True)
            remaining.remove(event)
            asyncio.create_task(deliver(event))
        await asyncio.sleep(0.02)
        if not (asyncio.all_tasks() - {main_task}):
            newly_completed = started.keys() - completed
            snapshot = capture_evidence() if capture_evidence else None
            for event_id in newly_completed:
                records[event_id]["drained_seconds"] = time.monotonic() - beginning
                if snapshot:
                    record = records[event_id]
                    conversation_start = record.pop("_conversation_start")
                    action_start = record.pop("_action_start")
                    record["evidence"] = {
                        "conversation": snapshot["conversation"][conversation_start:],
                        "actions": snapshot["actions"][action_start:],
                        "drafts": snapshot["drafts"],
                        "sent": snapshot["sent"],
                        "reminders": snapshot["reminders"],
                    }
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

    from server.agents.interaction_agent.runtime import InteractionAgentRuntime
    from server.agents.interaction_agent.agent import build_system_prompt

    runtime = InteractionAgentRuntime()
    advertised = {tool["function"]["name"] for tool in runtime.tool_schemas}
    prompt = build_system_prompt()
    log = get_conversation_log()
    name = "Email to Erin"
    get_agent_roster().add_agent(name)
    log.record_user_message("Draft an email to erin@example.test asking her to review PR #94. Leave it unsent.")
    draft = mailbox.call("gmail_create_draft", name, recipient_email="erin@example.test",
                         subject="Review PR #94", body="Hi Erin, please review PR #94. Thanks!")
    text = "Draft stored successfully."  # Exact content must come from the structured result.
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
              "email_not_sent": not mailbox.sent, "one_stored_draft": len(mailbox.drafts) == 1,
              "send_draft_available_only_when_needed": ("send_draft" in advertised) == (not args.draft_ready),
              "prompt_matches_delivery": (
                  "backend has already displayed" in prompt if args.draft_ready else
                  "has NOT been displayed" in prompt and "backend has already displayed" not in prompt
              ),
              "draft_context_retained": any(draft["draft_id"] in body and name in body
                                            for tag, _, body in log.iter_entries() if tag == "agent_message")}
    output = {"mode": "smoke_paired_draft_delivery" if args.smoke else "paired_draft_delivery", **target_metadata(args),
              "task_outcomes": "passed" if all(checks.values()) else "failed",
              "checks": checks, "transcript": log.load_transcript(), "mailbox": mailbox.snapshot()}
    args.report.write_text(json.dumps(output, indent=2) + "\n")
    print(json.dumps(checks))
    return 0 if all(checks.values()) else 1


async def run_worker(args):
    from server.agents.interaction_agent import runtime as interaction
    from server.agents.interaction_agent.metrics import record_llm_call, record_llm_usage, record_turn
    from server.agents.execution_agent import runtime as execution
    from server.agents.execution_agent.batch_manager import ExecutionBatchManager
    from server.config import get_settings
    from server.services.timezone_store import get_timezone_store
    from server.services.conversation import get_conversation_log
    from server.services.conversation import log as conversation_log
    from server.services.execution import log_store
    from server.services.conversation.summarization import working_memory_log

    fixture = json.loads((ROOT / "benchmarks/sample_inbox.json").read_text())
    events = json.loads((ROOT / "benchmarks/sample_workload.json").read_text())["events"]
    if args.limit:
        events = events[:args.limit]
    mailbox = Mailbox(fixture)
    for module in (conversation_log, log_store, working_memory_log):
        module.now_in_user_timezone = lambda fmt: mailbox.now.strftime(fmt)
    settings = get_settings()
    settings.draft_ready_enabled = args.draft_ready
    # Both modes use identical model settings in this temporary worker.
    for field in ("interaction_agent_model", "execution_agent_model",
                  "execution_agent_search_model", "summarizer_model", "email_classifier_model"):
        setattr(settings, field, args.model)
    settings.conversation_summary_threshold = 0
    get_timezone_store().set_timezone(fixture["timezone"])
    schemas = execution.get_tool_schemas()
    execution.get_tool_registry = lambda agent_name: mailbox.registry(agent_name, schemas)

    # Give both LLMs the fixture clock without modifying production prompts.
    original_interaction = interaction.build_system_prompt
    interaction.build_system_prompt = lambda: original_interaction() + f"\nCurrent benchmark time: {mailbox.now.isoformat()}"
    original_execution = execution.ExecutionAgent.build_system_prompt
    execution.ExecutionAgent.build_system_prompt = lambda self: (
        original_execution(self) + f"\nCurrent benchmark time: {mailbox.now.isoformat()}"
    )

    if args.smoke:
        async def fake_interaction(**kwargs):
            assert kwargs["model"] == args.model, "Runtime did not use the selected model"
            if args.draft_check:
                advertised = {tool["function"]["name"] for tool in kwargs["tools"]}
                assert "send_draft" in advertised, "Interaction display mode must advertise send_draft"
                assert any('"draft_id": "D1"' in m.get("content", "") and
                           '"body": "Hi Erin, please review PR #94. Thanks!"' in m.get("content", "")
                           for m in kwargs["messages"]), "Completed draft content must reach the model"
                if any(m["role"] == "tool" for m in kwargs["messages"]):
                    message = {"content": ""}
                else:
                    message = {"content": "", "tool_calls": [{"id": "display-draft", "type": "function", "function": {
                        "name": "send_draft", "arguments": json.dumps({
                            "to": "erin@example.test", "subject": "Review PR #94",
                            "body": "Hi Erin, please review PR #94. Thanks!"})}}]}
                return {"choices": [{"message": message}]}
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
            assert kwargs["model"] == args.model, "Runtime did not use the selected model"
            if any(m["role"] == "tool" for m in kwargs["messages"]):
                message = {"content": "Smoke: sample inbox inspected."}
            else:
                message = {"content": "", "tool_calls": [{"id": "search", "type": "function",
                    "function": {"name": "task_email_search", "arguments": '{"search_query":"inbox"}'}}]}
            return {"choices": [{"message": message}]}

        interaction.request_chat_completion = fake_interaction
        execution.request_chat_completion = fake_execution

    instrument_unmetered_runtime(interaction, record_llm_call, record_llm_usage, record_turn)

    async def invoke(event):
        runtime = interaction.InteractionAgentRuntime()
        result = await (runtime.execute(event["text"]) if event["kind"] == "user"
                        else runtime.handle_agent_message(event["text"]))
        return asdict(result)

    def capture_evidence():
        conversation = [
            {"type": tag, "timestamp": timestamp, "text": body}
            for tag, timestamp, body in get_conversation_log().iter_entries()
        ]
        state = deepcopy(mailbox.snapshot())
        return {"conversation": conversation, **state}

    if args.draft_check:
        return await check_draft_delivery(args, mailbox)

    output = {"mode": "smoke_fake_models" if args.smoke else "live_models_fake_mailbox",
              **target_metadata(args),
              "task_outcomes": "manual_review_required", "events": []}
    records = {}
    try:
        output["events"] = await replay(
            events, invoke, mailbox, fixture, args.timeout, records, capture_evidence
        )
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
        outcome_report = check_outcomes(output)
        output["message_checks"] = outcome_report
        summary = outcome_report["summary"]
        if summary["FAIL"] or not summary["global_checks_passed"]:
            output["task_outcomes"] = "failed"
        elif summary["MANUAL_REVIEW"]:
            output["task_outcomes"] = "manual_review_required"
        else:
            output["task_outcomes"] = "passed"
        output["send_and_reminder_checks"] = outcome_report["legacy_checks"]
        outcomes_path = args.report.with_name("outcomes.json")
        outcomes_path.write_text(json.dumps(outcome_report, indent=2) + "\n")
        args.report.write_text(json.dumps(output, indent=2) + "\n")
    return 0 if output["status"] == "finished" else 1


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--smoke", action="store_true", help="fake models; not a baseline")
    parser.add_argument("--draft-check", action="store_true", help="compare delivery of one identical finished draft")
    parser.add_argument(
        "--draft-ready", action=argparse.BooleanOptionalAction, default=True,
        help="same repo: display drafts directly, or use --no-draft-ready to display through the interaction LLM",
    )
    parser.add_argument("--limit", type=int, help="run only the first N fixture events")
    parser.add_argument("--timeout", type=float, default=1800)
    parser.add_argument("--report", type=Path)
    parser.add_argument("--workspace", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--model", choices=(BENCHMARK_MODEL,), default=BENCHMARK_MODEL,
                        help="fixed model for every role in both draft display modes")
    args = parser.parse_args()
    args.server_root = select_server_root(args.draft_ready)
    if args.limit is not None and args.limit < 1:
        parser.error("--limit must be positive")
    if args.smoke and args.limit is None:
        args.limit = 3
    if not args.smoke and not args.workspace:
        load_model_environment(ROOT / ".env")
        load_model_environment(ROOT.parent / "openpoke" / ".env")
    os.environ["OPENROUTER_MODEL"] = args.model
    os.environ["OPENPOKE_DRAFT_READY"] = "1" if args.draft_ready else "0"
    if not args.model.strip():
        parser.error("--model must not be empty")
    if not (args.server_root / "server").is_dir():
        parser.error(f"Selected repository has no server/ directory: {args.server_root}")
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
        shutil.copyfile(ROOT / "server/agents/interaction_agent/metrics.py", metrics_file)
        command = [sys.executable, str(Path(__file__).resolve()), "--workspace", directory,
                   "--report", str(args.report), "--timeout", str(args.timeout), "--model", args.model,
                   "--draft-ready" if args.draft_ready else "--no-draft-ready"]
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
