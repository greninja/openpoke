# Changes in this fork

## 1. What does “overload” mean?

I treated overload as unnecessary work passing through the interaction LLM. My goal was to remove predictable, structured work from it while preserving task correctness. I expected this to reduce token usage as well.

## 2. How did I reduce it?

Initially, every completed email draft was sent back to the interaction agent. Its LLM then called `send_draft` to display the same draft to the user, adding an unnecessary LLM call and round trip.

I targeted this narrow but common scenario. The execution agent now returns a `draft_ready` result, and a backend **Draft Delivery Service** displays the draft directly instead of routing it through the interaction agent and its LLM. The service still saves the draft and its ID in the interaction agent’s conversation history, so follow-up questions, revisions, and approvals continue to work. Displaying a draft does **not** send the email. Removing this round trip saves time and model cost, which can add up quickly for a frequently used workflow like email.

## 3. How did I measure improvement?

I measured the interaction agent’s workload using:

1. Interaction-agent triggers/turns and LLM calls.
2. Input/output tokens.
3. Task outcomes.

Lower values indicate less interaction-agent work, as long as task completion and quality remain the same.

I created a [reproducible benchmark of 55 user tasks](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md) to measure this. It switches direct draft delivery on or off.

Using Gemini 2.5 Flash across 55 user tasks, direct delivery reduced interaction LLM calls from **123 to 102 (17.1%)** and triggers from **99 to 88 (11.1%)**. Both runs recorded **36 passes, 18 failures, and 1 unclear task**. See the [comparison and reproduction steps](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md).

## Before

Completed drafts return to the interaction LLM before being shown to the user.

![Original OpenPoke architecture](docs/images/architecture-before.jpg)

## With direct draft delivery

The teal path shows the new backend service displaying drafts and saving their context. Other results still go through the interaction agent.

![OpenPoke architecture with direct draft delivery](docs/images/architecture-draft-ready.png)

## Other questions and notes

### When is a draft displayed directly to the user?

The **Execution Batch Manager** makes this decision after all execution agents in the current batch finish. It displays a draft directly only when `draft_ready` is enabled and Gmail has successfully created a stored draft with a draft ID, recipient, subject, and body. The backend validates those fields. Failed or incomplete results, unknown result types, and other work that needs judgment go to the interaction agent.

I also tightened the agent loops: they stop after successful delegation or waiting, avoid identical repeated tool actions within a turn, recognize tool errors, and retry when the model describes a tool call without actually making one.

I also tried structuring other information passed between agents, but email drafts produced the clearest and most consistent improvement.

### Other approaches I explored

#### Execution-agent roster shortlisting

I also tested [execution-agent roster shortlisting](https://github.com/greninja/openpoke/tree/feature/roster-shortlisting). Instead of giving the interaction LLM the full agent roster on every turn, this approach provides a shortlist of recently used agents, agents matching the current message, and agents returning results during that turn. The interaction agent can still request the full roster. The [original OpenPoke write-up](https://www.shloked.com/openpoke) also proposed this idea.

In one paired Gemini 2.5 Flash Lite run, shortlisting reduced recorded tokens from **1,305,202 to 978,450**, about **25%**. Some successful calls had no usage data, so this is an observed reduction rather than a complete cost measurement. The shortlist run also created more agents and had worse automated correctness results. This showed that smaller prompts can reduce workload, but routing quality must be protected too.

That experiment also fixed a missing implementation for requesting the full roster, stopped unrelated agent reuse from changing agent categories, and corrected counters that measured the final roster instead of successful agent-creation events.

#### Other options considered

I also considered splitting the interaction agent into specialized sub-agents and adding a shared queue or concurrency limit. I did not pursue these options because they add coordination or restrict throughput without directly removing the reasoning work.
