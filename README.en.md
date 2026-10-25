# ECY Agent Harbor

[English](README.en.md) · [Türkçe](README.tr.md) · [Overview](README.md)

**Author:** Emre Çağlar YILDIZ

**License:** [ECY Attribution License 1.0](LICENSE), a custom license requiring visible attribution in README or About/Credits. It is **not** the standard MIT License.

A personal, self-hosted workspace for running your own AI agents through API endpoints. Assign a task to one agent, gather independent opinions from a council, optionally run a peer-review round, and choose a lead expert to synthesize the results. Normal startup uses real providers. Credentials and existing inference machines are configured by you; no keys are bundled with the project.

## Start locally

Python 3.9+ is required. On macOS, open a terminal in the extracted project folder:

```sh
chmod +x Baslat.command
./Baslat.command
```

The launcher creates a project-local `.venv` and installs `requirements.txt` if necessary. Once dependencies are installed, you can also use:

```sh
python3 run.py
```

Open **http://localhost:8080**. On the first visit, set an administrator password of at least 12 characters. Use the same password on later visits. Stop the server with Ctrl+C.

The module command is `python3 -m app.server`, not `python3 -m app/server.py`. `run.py` also works with an absolute path from another working directory. Direct Python startup does not automatically load `.env`; export environment variables explicitly when needed.

Docker:

```sh
docker compose up --build
```

The port is published on loopback only. Persistent data lives in the `harbor-data` volume; `docker compose down` preserves it. Docker must be running. The image configuration has been checked, but container execution requires independent verification on your host.

## English and Turkish interface

The **TR / EN** control sits next to the theme button. On the first visit, a Turkish browser language selects Turkish; every other browser language selects English. An explicit selection is saved locally and takes priority on later visits. A language control is also available on the sign-in screen.

Navigation, forms, dialogs, statuses, system messages, dates, and report metadata support both languages. Prompts, project and agent names, evidence, and model outputs remain unchanged. The upper-left brand area displays **ECY Agent Harbor** and its harbor icon in both languages.

## Connect agents

1. Open **Management → Agents & API → Add agent**.
2. Enter the name, expertise, provider type, exact model ID, and API base URL.
3. For an OpenAI-compatible service, include its version path such as `/v1`. The application appends `/chat/completions`.
4. Add a key if the endpoint requires one. Leave it blank for an unauthenticated local inference server.
5. Set your USD rate per 1,000 combined input/output tokens. Use zero for an unbilled local service.
6. Save the agent and run **Test connection**. The test is a real, potentially billable call requesting at most 32 output tokens.
7. Select the agent in the task studio and start a task. Single-agent mode is selected by default.

When editing an agent, an empty key field preserves its saved key. Enter a replacement or select **Remove saved API key**. Disable or delete agents from settings. Deletion removes saved credentials and preserves names in historical reports. Agents participating in active tasks, including selected fallbacks, cannot be changed until those tasks end.

OpenAI-compatible services must implement the chat-completions contract. Native APIs using another contract require an adapter. The Hermes bridge is an explicit opt-in `/v1/tasks` integration that disables tools on its own side; this application does not assume Hermes provides that endpoint or install a bridge on an existing machine. If Hermes already exposes a compatible chat-completions API, use that provider type.

Hardware labels are editable profiles, not automatic connections. Existing Hermes, GPU inference nodes, Raspberry Pi tunnels, and Telegram automations are not modified.

## Projects, files, and evidence

Create a project with its own instructions, select it in the top bar, and keep related tasks and evidence together. Projects are shared across roles in this personal workspace; they are not isolated tenants.

Upload UTF-8 text, log, JSON, CSV, Markdown, PDF, PNG, JPEG, or WebP files. Check the files you want included in a task. Only selected files are sent to the assigned providers. The default upload limit is 32 MB per file, with up to 20 files per task. `MAX_FILE_BYTES` changes the server-side upload limit. These are operational upload limits, not spending or response-length limits.

Large text files are analyzed in chunks, including oversized individual lines. Intermediate runs are retained. Findings are combined while preserving requested source IDs and line ranges. This can require multiple billable model calls. If a model fails to reduce an oversized intermediate context, the task stops explicitly rather than silently truncating evidence. Model summaries can still lose semantic detail; source citations are review aids, not proof that every claim is correct.

Click a file citation to inspect the original extracted lines. The evidence viewer is paginated. Referenced files cannot be removed until dependent task history, templates, and schedules have been cleared.

PDF support extracts existing text; scanned PDFs do not receive OCR. Upload scanned pages as images instead. Images require a vision-capable compatible model. The Hermes bridge does not implement the image contract.

## Council and task templates

Choose up to four participants and an optional lead expert. Participants first work independently. Enable **Peer review** with at least two participants to have each critique the others' findings. The selected lead expert then synthesizes evidence, agreement, disagreements, and remaining uncertainty.

Save the prompt, selected agents, lead expert, fallback, project, files, and internet preference as a template. Built-in templates cover suspicious logs, security digests, and vulnerability-report review. User-written prompts and custom templates are not translated automatically.

Use **Continue this analysis** to create a follow-up task with the previous outputs and evidence. It creates new provider usage; the original task is preserved.

## Live output and recovery

When a provider supports SSE, visible response text is saved during generation. The task screen refreshes approximately every two seconds. Private reasoning is kept out of visible output. If streaming is rejected with HTTP 400, the adapter retries using the normal JSON response contract.

A transient connection error, rate limit, or selected 5xx error may be retried once before any visible output or saved tool round has arrived. Partial work is not automatically replayed. An explicitly selected fallback is recorded as a separate run; prior failures remain visible and may still be billable.

A response ending without a normal finish reason stays incomplete. An empty final answer is not marked completed. When research evidence is available, one finalization attempt reuses saved sources without repeating searches. A manual **Retry this step** starts a new task with the selected step's saved input. **Continue response** carries the prior response forward. There is no normal application-imposed `max_tokens` or output-character cap, but provider context/output limits remain.

Cancellation prevents subsequent work; a request already in flight may continue at the provider. Interrupted tasks are marked failed after a server restart and are not automatically replayed. Queued tasks and pending approvals remain.

## Internet access and Playwright MCP

Install the pinned Playwright MCP runtime from provider settings. Node.js 18+ and npm are required outside Docker. A separate, temporary, signed-out browser profile is used. On macOS, an existing Chrome installation may be reused as the browser binary, never as your personal profile.

The internet toggle applies to each task and is off by default. Compatible tool-calling models receive `web_search` and `web_open`. Public HTTP/HTTPS reading on ports 80/443 is supported. Private addresses and metadata endpoints are blocked; forms, uploads, code execution, and WebSockets are unavailable to the model. Search queries and visited URLs are sent to public web services, so do not include sensitive log content or keys in queries.

`WEB_MAX_TOOL_STEPS` defaults to 12. This controls browser work, not report length. After research, the model is asked for a final report. Authentication walls, CAPTCHAs, and anti-automation measures may prevent access. Internet-enabled Hermes bridge tasks are explicitly rejected because the bridge does not implement this tool loop.

The provider summary counts working, failing, and untested endpoints from recent tests and tasks. It does not make background paid health calls.

## Schedules and Telegram

Use **Schedule** in the studio to save a recurring task. The interval is expressed in minutes; all displayed times and the first-run input use Istanbul (Europe/Istanbul, GMT+03:00), independently of the browser. The database stores an epoch timestamp. The application must remain running.

Missed intervals do not create a burst of old tasks. A schedule will not overlap its previous queued, running, or approval-pending task. Invalid configurations pause the schedule with an explanation. Recurring calls may incur provider charges.

Telegram is opt-in. Administrators configure the bot token, chat ID, and optional externally reachable application URL under **Automations**. The bot token is encrypted and never returned to the browser. Saving settings does not send a test message.

By default, notifications contain only task ID, status, and cost. A separate option sends the title and the first 1,000 characters of the report to the configured chat. Report links require application sign-in. New tasks capture their chosen language for notification labels.

The durable outbox records pending, sending, delivered, failed, or uncertain delivery. Uncertain sends are not automatically replayed. Manually resending may produce a duplicate message. See the [Telegram API](https://core.telegram.org/bots/api#sendmessage).

## Reports, history, and cost

Task details offer full-task or per-agent Markdown/PDF downloads. Report headings and metadata follow the selected UI language; original model text is preserved. Web Markdown rendering includes headings, lists, tables, code, links, and images, sanitized with locally bundled Marked and DOMPurify. PDFs use basic text formatting and a font supporting Turkish; they are not pixel-identical copies of the web renderer and do not embed every complex Markdown feature.

Finished, failed, cancelled, and incomplete history may be deleted with confirmation. Operators can delete their own tasks; administrators can delete all finished history; viewers cannot delete. Active tasks, in-flight calls, and pending approvals are protected. Clearing history keeps anonymous date-and-amount records so usage totals remain accurate. Uploaded evidence is managed separately.

There is no financial spending quota. The application records today's and total USD usage using your configured rates. These amounts are not the provider's invoice. Missing provider usage is marked estimated. The queue size and worker count are operational controls, not financial limits.

## Access and storage

| Role | Permissions |
|---|---|
| Administrator | Provider credentials, passwords, all tasks, approvals, workspace settings |
| Operator | Create tasks, read the shared workspace, manage owned records |
| Viewer | Read tasks, outputs, logs, and evidence |

Each role corresponds to one account in this personal-workspace version. This is not per-person SSO or multi-tenant access control.

Passwords use salted PBKDF2-SHA256. API keys and the Telegram token are encrypted with Fernet; the master key is stored as `data/master.key` with restrictive permissions. Task text, evidence, and saved provider conversation state are not encrypted at rest. Sessions use HttpOnly/SameSite cookies; writes require CSRF tokens and an accepted origin. Protect the machine and its backups.

Back up the entire `data/` directory with the application stopped, including the database, master key, uploads, and browser runtime. Source ZIPs and GitHub exclude this personal data. Updates apply additive database migrations. Restore a pre-update backup before reverting to an older schema-dependent version.

The built-in Python HTTP server is intended for local personal use. Remote deployment additionally requires HTTPS, an appropriate reverse proxy, origin/cookie settings, and network controls. `localhost` in a container means the container itself; use a suitable host/network address for inference services.

## Verification

```sh
python3 -m unittest discover -s tests -v
node tests/test_i18n.cjs
node --check static/i18n.js
node --check static/app.js
docker compose config --quiet
```

Tests use temporary storage, local fake providers, and a fake Telegram endpoint. They do not call your real model keys or send Telegram messages. See [verification notes](docs/VERIFICATION.md) and [phase plan](docs/PHASES.md).

## Screenshots and attribution

Current application screenshots are linked from the [bilingual overview](README.md#screenshots--ekran-görüntüleri). They use an isolated sample workspace, not real provider credentials.

Under [ECY Attribution License 1.0](LICENSE), retain this visible credit in README documentation or a user-accessible About/Credits section:

> ECY Agent Harbor — Emre Çağlar YILDIZ

A notice only in source comments or a license file does not satisfy the added visible-attribution condition. Third-party components keep their own licenses. Earlier commits contain the standard MIT text; the current custom-license change is recorded in this version's license file.

### Search uploaded evidence

Open **Projects & files → Search file contents**. Search the selected project's text/log files and extracted PDF text with a literal, case-insensitive query (1–200 characters). Results show file names, source line numbers and highlighted snippets. Use **Open line** to inspect the original, or **Add to task** to select the whole file for analysis. This does not launch a task. Viewers may search/read but cannot attach files to tasks. Results are paginated in groups of 50 matching lines; no model requests are made. Images and OCR are not included. This first version scans stored text directly; large libraries may take longer, and uploading/deleting files between pages can shift page boundaries.

## Investigation platform

All eight platform phases are available: task conversations, report comparison, findings/actions, live activity, a project knowledge bank, declared agent capabilities, encrypted backup/restore and scoped incoming webhooks. Open the new sidebar sections or **Management → Backup / Webhook connections**. See [the platform guide](docs/PLATFORM.md) for workflows, costs, restore instructions and operational limitations.
