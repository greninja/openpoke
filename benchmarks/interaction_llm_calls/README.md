# Interaction LLM call counts

Counts each attempted `request_chat_completion` in the interaction runtime,
including failures. Counts model calls, not activations or HTTP retries inside
the model client. Execution-agent and summarizer model calls are excluded.

- `user_turn_calls`: calls originating in `execute()`.
- `agent_turn_calls`: calls originating in `handle_agent_message()`, including
  execution results and watcher notifications.
- `total_calls`: their sum.

## Run

From the repository root:

```sh
.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  .venv/bin/python benchmarks/interaction_llm_calls/workload.py
```

The command must run the backend in the same process or start it as a child
process, so it inherits the metrics environment variables. Wrapping an HTTP
client talking to an already-running server will **not** instrument that server.
Use an isolated backend with no unrelated traffic. The command must wait for
all background execution and interaction tasks before exiting; returning from
`execute()` alone does not mean delegated work has finished.

Each run gets a separate directory under `benchmarks/results/interaction_llm_calls/`
containing `calls.jsonl` and `report.json`. No message content is recorded.
Without `OPENPOKE_INTERACTION_METRICS_PATH`, instrumentation is disabled.
The runner does not read `.env`; any workload command's configuration is separate.

## Sample driver

Set `OPENROUTER_API_KEY` in the process environment or the project root `.env`.
The live driver loads only `OPENROUTER_API_KEY` and `OPENROUTER_MODEL` from that
file without printing them; existing environment values take priority.
Smoke mode and tests do not load it. Model calls use OpenRouter;
all execution tools use a local fixture adapter. No real email is sent.

`workload.py` reads the existing workload and inbox JSON files, imports the
server from a temporary copy without existing data or credentials, and runs
the normal interaction/execution loops and batch manager. Agent state is
discarded afterward. `--limit 10` runs the first ten fixture events.

It writes `workload.json` beside the counts report, with event timing, expected
outcomes, tool actions, final mailbox state and conversation text. Unlike
`calls.jsonl`, this file **does contain the benchmark messages and replies**.

Scope and limits (keep these identical for before/after comparisons):

- Completion means all background work has drained, not just an initial
  acknowledgement. Shared batches are conservatively treated as one wave:
  dependent events wait for global idle rather than per-request completion.
- `while_pending` events start at their configured offsets. If the parent has
  already finished, `overlap_met` is false; the report makes this visible.
- Search returns all currently visible fixture emails for the execution LLM
  to select from. This measures orchestration, not Gmail search quality or its
  separate search LLM. Incoming fixture mail becomes visible at its event.
- U34 email search calls return the specified injected failure. If the model
  answers from history and never searches, no failure is injected; actions
  make this visible.
- Reminder tools store local records. B05/B07 advance the fixture clock to the
  corresponding stored reminder and inject the sample notification. Missing
  reminders produce an event error; no reminder is silently invented.
  The two fixture reminders are matched by their payload (desk break or charger),
  since a shared batch callback can inherit a different event ID. Multiple
  matching reminders fail the check rather than choosing one silently.
- Contact lookup uses only addresses present in the initial mailbox or user
  messages already delivered. It does not consult real contacts or future events.
- Summarization is disabled. Model prompts and conversation timestamps use the
  fixture clock, which advances with elapsed time and reminder delivery.
- Outcome checks are preserved for **manual review**, not automatically graded.
  A zero exit code only means the driver finished without input-turn errors;
  inspect the transcript and actions for task correctness or execution errors.

Run a quick wiring check with no network or API key:

```sh
.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  .venv/bin/python benchmarks/interaction_llm_calls/workload.py --smoke
```

Smoke mode runs the first three sample events with fake model responses. It
exercises delegation, the local mailbox, batch callbacks, and the counter;
its expected count is **9 interaction calls (6 user, 3 agent)**. These are
test counts, not a real-model baseline.

The count above describes the original runtime. The corrected draft-ready
runtime ends a turn after successful delegation and can use fewer smoke calls.

## Compare the same completed draft

Append `--draft-check` to the workload command (and `--server-root` for the other
version). Both receive the same completed draft. The check requires the exact
draft to be displayed once, one stored draft, and no email sent. This isolates
draft delivery from differing searches, model plans, and later user turns.

Full runs also check actual sends after scripted approvals and whether the two
reminders were created. These checks do not grade message wording, reminder
timing, or all natural-language outcomes. To check an older saved run without
changing it:

```sh
python3 benchmarks/interaction_llm_calls/check_outcomes.py /path/to/workload.json
```

To compare another worktree, append `--server-root /absolute/path/to/worktree`
to the workload command. The driver uses that worktree's server code (including
uncommitted changes) in a temporary copy, while keeping this runner's fixtures
and model settings. If the target lacks counters, it wraps the same interaction
model-call boundary in memory. It does not modify the target worktree.

## Verify counting without model calls

```sh
python3 -m unittest discover -s benchmarks/interaction_llm_calls -p 'test_*.py' -v
```

These tests use fake model responses, do not read `.env`, and do not send email.
They verify instrumentation only; their counts are not a real-model baseline.
