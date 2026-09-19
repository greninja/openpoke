"""Run a workload command and summarize interaction LLM request counts."""

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
from uuid import uuid4


def summarize(events_path: Path, run_id: str) -> dict:
    """Calls are request attempts; turns are entry-point invocations.

    Tokens sum available provider usage only. Check calls_without_token_usage
    before treating token totals as complete. Old logs have unknown turn counts.
    """
    counts = {"user": 0, "agent": 0}
    turns = {"user": 0, "agent": 0}
    usage = []
    legacy_log = False
    for line in events_path.read_text(encoding="utf-8").splitlines():
        event = json.loads(line)
        if event["run_id"] != run_id:
            continue
        legacy_log |= "event_type" not in event
        kind = event.get("event_type", "llm_call")  # Older logs only recorded calls.
        if kind == "llm_call":
            counts[event["turn_type"]] += 1
        elif kind == "turn":
            turns[event["turn_type"]] += 1
        elif kind == "llm_usage":
            usage.append(event)
    complete_usage = sum(e.get("input_tokens") is not None and
                         e.get("output_tokens") is not None for e in usage)

    def tokens(key):
        values = [e[key] for e in usage if e.get(key) is not None]
        return sum(values) if values else None

    return {
        "total_calls": sum(counts.values()),
        "user_turn_calls": counts["user"],
        "agent_turn_calls": counts["agent"],
        "total_turns": None if legacy_log else sum(turns.values()),
        "user_turns": None if legacy_log else turns["user"],
        "agent_turns": None if legacy_log else turns["agent"],
        "input_tokens": tokens("input_tokens"),
        "output_tokens": tokens("output_tokens"),
        "calls_with_token_usage": complete_usage,
        "calls_without_token_usage": sum(counts.values()) - complete_usage,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path(__file__).resolve().parents[1] / "results" / "interaction_llm_calls",
    )
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command
    if command[:1] == ["--"]:
        command = command[1:]
    if not command:
        parser.error("provide a workload command after --")

    run_id = uuid4().hex
    run_dir = args.output_dir.resolve() / run_id
    run_dir.mkdir(parents=True)
    events_path = run_dir / "calls.jsonl"
    events_path.touch()
    env = dict(os.environ)
    env["OPENPOKE_INTERACTION_METRICS_PATH"] = str(events_path)
    env["OPENPOKE_BENCHMARK_RUN_ID"] = run_id
    error = None
    try:
        returncode = subprocess.run(command, env=env, check=False).returncode
    except OSError as exc:
        returncode = 127
        error = str(exc)
    report = {
        "run_id": run_id,
        "command_exit_code": returncode,
        "error": error,
        "task_outcomes": "not_evaluated",
        **summarize(events_path, run_id),
    }
    workload_path = run_dir / "workload.json"
    if workload_path.exists():
        workload = json.loads(workload_path.read_text(encoding="utf-8"))
        report["task_outcomes"] = workload.get("task_outcomes", "not_evaluated")
        report["mode"] = workload.get("mode")
        report["message_checks"] = workload.get("message_checks", {}).get("summary")
        for field in ("server_root", "server_commit", "server_dirty", "model", "draft_delivery", "draft_ready", "comparison"):
            report[field] = workload.get(field)
    report_path = run_dir / "report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))
    print(f"Report: {report_path}")
    return returncode


if __name__ == "__main__":
    sys.exit(main())
