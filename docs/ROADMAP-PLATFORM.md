# Platform development / Platform geliştirmeleri

The following eight phases are implemented in order, tested in isolation and then verified together. Existing provider credentials and external systems are not used for tests.

| Phase | Deliverable | Acceptance |
|---|---|---|
| 1 | Threaded task conversation | Persistent ancestry, complete previous prompts/reports as context, inline follow-up, additional evidence, role checks |
| 2 | Report comparison | 2–4 task outputs side by side, tokens/cost/duration, textual diff without another model call |
| 3 | Findings and actions | Project-scoped findings with severity, status, evidence links and durable change history |
| 4 | Live activity | Queue/running stage, run time and event timeline, periodic updates, cancellation through existing controls |
| 5 | Project knowledge bank | Search previous reports, pin notes/reports, explicitly select knowledge for a new task with provenance |
| 6 | Agent capabilities | User-declared vision/tools/context/specialties, transparent recommendations and capability checks |
| 7 | Portable encrypted backup | Admin download, authenticated encryption, database/files/key consistency, validation and restore into a new empty directory |
| 8 | Incoming webhooks | Explicit admin creation, one-time scoped token, revoke, idempotency, task creation audit; no remote shell or deployment |

All screens support Turkish and English, Istanbul time and both themes. A phase is not complete merely because its menu exists. Operational limitations and restore instructions belong in README guides.

Status: all eight phases implemented. 101 Python tests and both Node suites pass. Mock browser verification covers conversation submission, side-by-side comparison, evidence-linked finding creation, explicit knowledge selection, bilingual management and mobile navigation. No real provider, Telegram or external webhook call was made.
