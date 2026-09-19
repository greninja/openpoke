# Same-repo draft_ready comparison — September 19, 2026

Both runs used **this checkout**, `google/gemini-2.5-flash`, and the same 55 user tasks plus 7 background events. `--no-draft-ready` sent completed drafts to the interaction LLM with `send_draft` enabled; `--draft-ready` displayed them directly. All other runtime fixes were shared. Both runs attempted all 62 events.

The code and fixture hashes were captured during execution and checked again afterward: unchanged. Both runs include the same source manifest and uncommitted source diff. The benchmark used isolated temporary server copies and a fake mailbox.

## Interaction workload

| Metric | draft_ready off | draft_ready on | Reduction |
|---|---:|---:|---:|
| Interaction-agent triggers | 99 | 88 | 11.1% |
| Triggers from user messages | 55 | 55 | 0.0% |
| Triggers from agent/background messages | 44 | 33 | 25.0% |
| Interaction LLM calls | 123 | 102 | 17.1% |
| LLM calls within user turns | 62 | 61 | 1.6% |
| LLM calls within agent/background turns | 61 | 41 | 32.8% |
| Recorded input tokens | 1,533,808 | 1,122,607 | 26.8% |
| Recorded output tokens | 8,342 | 4,993 | 40.1% |

A trigger is one invocation of the interaction agent, either from a user message or an agent/background update. One trigger can make several LLM calls. Directly displayed drafts do not trigger the interaction agent. Counts here are only for the interaction agent, not all agents.

**Token coverage:** off has usage for 120 of 123 calls; on has usage for 99 of 102 calls. Three returned responses in each run did not supply usable token counts. The token totals above sum known usage only; the percentages are reductions in recorded usage, not exact full-run token reductions. No token estimation or cost comparison was performed.

## Task quality, out of 55 user tasks

| Score | draft_ready off | draft_ready on |
|---|---:|---:|
| PASS | 36 | 36 |
| FAIL | 18 | 18 |
| Still unclear | 1 | 1 |

Both raw automatic reports scored 15 PASS, 12 FAIL, and 28 MANUAL_REVIEW. The assistant reviewed those 28 unclear rows in each run and assigned 21 PASS, 6 FAIL, and 1 still unclear. The combined scores above retain the automatic grades and add these manual decisions. Reasons and evidence locations are saved in each run's `manual_review.json`.

The same total does not mean the same tasks succeeded. For example, off missed the later-flight time in U41, while on answered it correctly; on failed to identify today's actionable emails in U05, while off answered that question. Both failed the strict U20 requirement against guaranteeing time for check-in from calendar times alone. Both runs still have duplicate-draft issues.

The remaining unclear task is U34: its lookup fails as deliberately injected, while overlapping background tasks retrieve the invoice and the visible reply gives its due date. We did not count that as a proven successful U34 retrieval.

Background events are separate from the 55-task score. Off completed without an event runtime error. On failed B05 because duplicate desk-break reminders made notification selection ambiguous. Therefore the equal user-task score does **not** establish equal quality across every background event. Off also sent Frank/Grace emails before the fixture's explicit follow-up confirmation; on passed the global send check. The off global check additionally flags Carol because of missing draft details, so not every flagged item represents premature sending.

## Interpretation

In this single pair of runs, enabling draft_ready reduced interaction calls by **17.1%** and triggers by **11.1%**, with the **same recorded user-task score**. Recorded input/output tokens were also lower. This supports lower interaction workload in this sample; it does not establish unchanged quality generally, exact token savings, or a total-system cost reduction. More paired runs would show how much these results vary.

## Artifacts

- **Off:** [report](4a452ef5e7f5474cb577b42ae9118154/report.json), [automatic outcomes](4a452ef5e7f5474cb577b42ae9118154/outcomes.json), [manual grading](4a452ef5e7f5474cb577b42ae9118154/manual_review.json), [full evidence](4a452ef5e7f5474cb577b42ae9118154/workload.json), [metrics events](4a452ef5e7f5474cb577b42ae9118154/calls.jsonl), [source manifest](4a452ef5e7f5474cb577b42ae9118154/source_manifest.json), [source diff](4a452ef5e7f5474cb577b42ae9118154/source_changes.patch).
- **On:** [report](d2062821f70444509d982b81ae3e7070/report.json), [automatic outcomes](d2062821f70444509d982b81ae3e7070/outcomes.json), [manual grading](d2062821f70444509d982b81ae3e7070/manual_review.json), [full evidence](d2062821f70444509d982b81ae3e7070/workload.json), [metrics events](d2062821f70444509d982b81ae3e7070/calls.jsonl), [source manifest](d2062821f70444509d982b81ae3e7070/source_manifest.json), [source diff](d2062821f70444509d982b81ae3e7070/source_changes.patch).

## Commands

From this checkout:

```bash
../openpoke/.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  ../openpoke/.venv/bin/python benchmarks/interaction_llm_calls/workload.py --no-draft-ready

../openpoke/.venv/bin/python benchmarks/interaction_llm_calls/runner.py -- \
  ../openpoke/.venv/bin/python benchmarks/interaction_llm_calls/workload.py --draft-ready
```

The Python executable supplies installed dependencies; both flags now run this repo. The earlier `comparison-2026-09-19.md` is a historical two-repository comparison and is not the source of these numbers.
