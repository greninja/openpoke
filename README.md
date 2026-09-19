# OpenPoke 🌴

OpenPoke is a simplified, open-source take on [Interaction Company’s](https://interaction.co/about) [Poke](https://poke.com/) assistant—built to show how a multi-agent orchestration stack can feel genuinely useful. It keeps the handful of things Poke is great at (email triage, reminders, and persistent agents) while staying easy to spin up locally.

- Multi-agent FastAPI backend that mirrors Poke's interaction/execution split, powered by [OpenRouter](https://openrouter.ai/).
- Gmail tooling via [Composio](https://composio.dev/) for drafting/replying/forwarding without leaving chat.
- Trigger scheduler and background watchers for reminders and "important email" alerts.
- Next.js web UI that proxies everything through the shared `.env`, so plugging in API keys is the only setup.

## Changes in this fork

We treated overload as unnecessary work passing through the interaction LLM. Overload can also mean large prompts, too many routing decisions, model calls, or idle time, but this work focuses on removing predictable, structured work from the interaction LLM while checking that user tasks still complete correctly.

Completed drafts can now reach the user without an extra interaction LLM call. The execution agent returns a `draft_ready` result, and a backend **Draft Delivery Service** displays the draft for review. It also saves the draft and its ID in the conversation history, so follow-up questions, revisions, and approvals still work through the interaction agent. Displaying a draft does **not** send the email.

### When a draft is displayed directly

The **Execution Batch Manager** makes this decision after all execution agents in the current batch finish. It displays a draft directly only when `draft_ready` is enabled and Gmail has successfully created a stored draft with a draft ID, recipient, subject, and body. The backend validates those fields. Failed or incomplete results, unknown result types, and other work that needs judgment go to the interaction agent.

We also tightened the agent loops: they stop after successful delegation or waiting, avoid identical repeated tool actions within a turn, recognize tool errors, and retry when the model describes a tool call without actually making one.

We created a [reproducible benchmark of 55 user tasks](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md). It can switch direct draft delivery on or off and tracks:

1. Interaction-agent triggers/turns and LLM calls.
2. Input/output tokens.
3. Task outcomes.

A trigger starts one interaction-agent turn. Each turn makes at least one LLM call and may make additional calls after tool results or retries.

With Gemini 2.5 Flash on 55 user tasks, direct delivery reduced interaction LLM calls from **123 to 102 (17.1%)** and triggers from **99 to 88 (11.1%)**. Both runs recorded **36 passes, 18 failures, and 1 unclear task**. See the [comparison and reproduction steps](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md).

### Another approach we explored

We also tested [execution-agent roster shortlisting](https://github.com/greninja/openpoke/tree/feature/roster-shortlisting). Instead of giving the interaction LLM the full agent roster on every turn, it receives recently used agents, agents matching the current message, and agents returning results during that turn. It can still request the full roster.

In one paired Gemini 2.5 Flash Lite run, shortlisting reduced recorded tokens from **1,305,202 to 978,450**, about **25%**. Some successful calls had no usage data, so this is an observed reduction rather than a complete cost measurement. The shortlist run also created more agents and had worse automated correctness results. This showed that smaller prompts can reduce workload, but routing quality must be protected too.

That experiment also fixed a missing implementation for requesting the full roster, stopped unrelated agent reuse from changing agent categories, and corrected counters that measured the final roster instead of successful agent-creation events.

We considered splitting the interaction agent into specialized sub-agents and adding a shared queue or concurrency limit. We did not pursue them because they add coordination or restrict throughput without directly removing the reasoning work.

### Before

Completed drafts return to the interaction LLM before being shown to the user.

![Original OpenPoke architecture](docs/images/architecture-before.jpg)

### With direct draft delivery

The teal path shows the new backend service displaying drafts and saving their context. Other results still go through the interaction agent.

![OpenPoke architecture with direct draft delivery](docs/images/architecture-draft-ready.png)

## Requirements
- Python 3.10+
- Node.js 18+
- npm 9+

For the benchmark only, follow [these steps](benchmarks/results/interaction_llm_calls/comparison-same-repo-2026-09-19.md#run-the-benchmark). You need Python and an OpenRouter key; Node.js and Gmail setup are not required.

## Quickstart
1. **Clone and enter the repo.**
   ```bash
   git clone https://github.com/greninja/openpoke.git
   cd openpoke
   ```
2. **Create a shared env file.** Copy the template and open it in your editor:
   ```bash
   cp .env.example .env
   ```
3. **Get your API keys and add them to `.env`:**
   
   **OpenRouter (Required)**
   - Create an account at [openrouter.ai](https://openrouter.ai/)
   - Generate an API key
   - Replace `your_openrouter_api_key_here` with your actual key in `.env`
   
   **Composio (Required for Gmail)**
   - Sign in at [composio.dev](https://composio.dev/)
   - Create an API key
   - Set up Gmail integration and get your auth config ID
   - Replace `your_composio_api_key_here` and `your_gmail_auth_config_id_here` in `.env`
4. **(Required) Create and activate a Python 3.10+ virtualenv:**
   ```bash
   # Ensure you're using Python 3.10+
   python3.10 -m venv .venv
   source .venv/bin/activate
   
   # Verify Python version (should show 3.10+)
   python --version
   ```
   On Windows (PowerShell):
   ```powershell
   # Use Python 3.10+ (adjust path as needed)
   python3.10 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   
   # Verify Python version
   python --version
   ```

5. **Install backend dependencies:**
   ```bash
   pip install -r server/requirements.txt
   ```
6. **Install frontend dependencies:**
   ```bash
   npm install --prefix web
   ```
7. **Start the FastAPI server:**
   ```bash
   python -m server.server --reload
   ```
8. **Start the Next.js app (new terminal):**
   ```bash
   npm run dev --prefix web
   ```
9. **Connect Gmail for email workflows.** With both services running, open [http://localhost:3000](http://localhost:3000), head to *Settings → Gmail*, and complete the Composio OAuth flow. This step is required for email drafting, replies, and the important-email monitor.

The web app proxies API calls to the Python server using the values in `.env`, so keeping both processes running is required for end-to-end flows.

## Project Layout
- `server/` – FastAPI application and agents
- `web/` – Next.js app
- `server/data/` – runtime data (ignored by git)

## License
MIT — see [LICENSE](LICENSE).
