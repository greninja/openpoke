# Benchmark comparison

- **Original flow:** the interaction LLM receives completed drafts and displays them through `send_draft`.
- **Our change:** `draft_ready` lets the backend display completed drafts directly, without that extra LLM step.

Both runs used the same code in this repo, with only draft delivery switched. All model settings used `google/gemini-2.5-flash`.

We treated overload as work passing through the interaction LLM unnecessarily. For more context, read [Changes in this fork](../../../changes_in_this_fork.md).

The [workload](../../sample_workload.json) contains **55 user tasks and 7 background events**. Both runs attempted all 62 events using a fake mailbox. No real emails were sent.

## Results (on our benchmark)

| Metric | Original flow | Our change | Reduction |
|---|---:|---:|---:|
| Interaction-agent triggers | 99 | 88 | 11.1% |
| Triggers from user messages | 55 | 55 | 0.0% |
| Triggers from agent/background messages | 44 | 33 | 25.0% |
| Interaction LLM calls | 123 | 102 | 17.1% |
| LLM calls within user turns | 62 | 61 | 1.6% |
| LLM calls within agent/background turns | 61 | 41 | 32.8% |
| Recorded input tokens | 1,533,808 | 1,122,607 | 26.8% |
| Recorded output tokens | 8,342 | 4,993 | 40.1% |

**In this run, our change used 17.1% fewer interaction LLM calls and 11.1% fewer triggers, with the same overall user-task score.**

Directly displayed drafts are still saved in the conversation history, including their draft ID. Follow-up questions, revisions, and approvals still go through the interaction LLM, which can send work back to an execution agent. The saving comes from skipping the LLM step just to display the completed draft.

## Task quality

Each user task has an expected outcome in the workload file. Automatic checks compare saved replies and mailbox actions against that outcome—for example, draft contents, sends after approval, and reminder timing. Tasks are marked **PASS**, **FAIL**, or **MANUAL_REVIEW**.

Codex then reviewed the unclear tasks using the saved replies and actions. Each decision and its reason is recorded in `manual_review.json`.

| Result out of 55 user tasks | Original flow | Our change |
|---|---:|---:|
| PASS | 36 | 36 |
| FAIL | 18 | 18 |
| Still unclear | 1 | 1 |

In each run, automatic checks gave 15 passes, 12 failures, and 28 tasks for review. Manual review added 21 passes and 6 failures, leaving 1 unclear.

## Run the benchmark

From the cloned repo, create a Python 3.10+ environment and install the backend dependencies:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r server/requirements.txt
cp .env.example .env
```

Set `OPENROUTER_API_KEY` in `.env` to your key. No Gmail, Composio, or frontend setup is needed. Both runs use Gemini 2.5 Flash and a [fake mailbox initialized from this sample inbox](../../sample_inbox.json).

Original flow:

```bash
.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  .venv/bin/python benchmarks/interaction_llm_calls/workload.py --no-draft-ready
```

Our change:

```bash
.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  .venv/bin/python benchmarks/interaction_llm_calls/workload.py --draft-ready
```

Results are saved under `benchmarks/results/interaction_llm_calls/`. New runs produce automatic grades; the manual grades shown above were reviewed separately. Exact results may vary between runs.
