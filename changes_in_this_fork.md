# Changes in this fork

## 1. What does “overload” mean?

We treated overload as unnecessary work passing through the interaction LLM. Overload can also mean large prompts, too many routing decisions, model calls, or idle time, but this work focuses on removing predictable, structured work from the interaction LLM while checking that user tasks still complete correctly.

## 2. How do we reduce it?

Completed drafts can now reach the user without an extra interaction LLM call. The execution agent returns a `draft_ready` result, and a backend **Draft Delivery Service** displays the draft for review. It also saves the draft and its ID in the conversation history, so follow-up questions, revisions, and approvals still work through the interaction agent. Displaying a draft does **not** send the email.

### When a draft is displayed directly

The **Execution Batch Manager** makes this decision after all execution agents in the current batch finish. It displays a draft directly only when `draft_ready` is enabled and Gmail has successfully created a stored draft with a draft ID, recipient, subject, and body. The backend validates those fields. Failed or incomplete results, unknown result types, and other work that needs judgment go to the interaction agent.

We also tightened the agent loops: they stop after successful delegation or waiting, avoid identical repeated tool actions within a turn, recognize tool errors, and retry when the model describes a tool call without actually making one.

### Another approach we explored

We also tested [execution-agent roster shortlisting](https://github.com/greninja/openpoke/tree/feature/roster-shortlisting). Instead of giving the interaction LLM the full agent roster on every turn, it receives recently used agents, agents matching the current message, and agents returning results during that turn. It can still request the full roster.

In one paired Gemini 2.5 Flash Lite run, shortlisting reduced recorded tokens from **1,305,202 to 978,450**, about **25%**. Some successful calls had no usage data, so this is an observed reduction rather than a complete cost measurement. The shortlist run also created more agents and had worse automated correctness results. This showed that smaller prompts can reduce workload, but routing quality must be protected too.

That experiment also fixed a missing implementation for requesting the full roster, stopped unrelated agent reuse from changing agent categories, and corrected counters that measured the final roster instead of successful agent-creation events.

We considered splitting the interaction agent into specialized sub-agents and adding a shared queue or concurrency limit. We did not pursue them because they add coordination or restrict throughput without directly removing the reasoning work.

## 3. How do we measure improvement?

We created a [reproducible benchmark of 55 user tasks](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md). It can switch direct draft delivery on or off and tracks:

1. Interaction-agent triggers/turns and LLM calls.
2. Input/output tokens.
3. Task outcomes.

A trigger starts one interaction-agent turn. Each turn makes at least one LLM call and may make additional calls after tool results or retries.

With Gemini 2.5 Flash on 55 user tasks, direct delivery reduced interaction LLM calls from **123 to 102 (17.1%)** and triggers from **99 to 88 (11.1%)**. Both runs recorded **36 passes, 18 failures, and 1 unclear task**. See the [comparison and reproduction steps](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md).

## Before

Completed drafts return to the interaction LLM before being shown to the user.

![Original OpenPoke architecture](docs/images/architecture-before.jpg)

## With direct draft delivery

The teal path shows the new backend service displaying drafts and saving their context. Other results still go through the interaction agent.

![OpenPoke architecture with direct draft delivery](docs/images/architecture-draft-ready.png)
