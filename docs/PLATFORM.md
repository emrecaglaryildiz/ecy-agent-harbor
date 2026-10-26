# Investigation platform / İnceleme platformu

## 1. Task conversation / Görev sohbeti

Open a finished task and use **Task conversation / Görev sohbeti** to send a follow-up. Previous prompts, full stored outputs and synthesis from the selected ancestry are preserved in the next request. Messages are durable tasks linked by `parent_task` and `root_task`; they retain separate cost and status. Forking from an earlier task creates another branch. Add evidence with the conversation upload control; previously attached files retain provenance, while only newly added text files need another chunk analysis. Each message may incur provider usage. Provider context capacities still apply; no success or unlimited model context is promised.

Deleting one task does not erase copies already included in descendants or explicitly saved knowledge snapshots. Deleting all task history removes tasks, not notes deliberately saved in the knowledge bank.

## 2. Comparison / Karşılaştırma

Select 2–4 tasks from the selected project's recent history. Compare reports, input/output tokens, cost and wall time, plus unified text differences against the first report. No extra model call is made. Wall time includes queue time. New runs store model/provider/name snapshots; older runs show that the model was not recorded. Textual differences are not a truth, safety or quality score.

## 3. Findings & actions / Bulgular & aksiyonlar

Findings belong to a project or general workspace and contain severity, status, description/actions, optional source task and file line range. Statuses: new, investigating, confirmed, resolved, dismissed. Every edit records an immutable snapshot with actor and Istanbul-rendered time. The owner/operator or administrator can edit; viewers only read. Markdown export includes evidence references. A valid line range verifies the source exists, not that the claim is true. Findings can be dismissed instead of deleting their audit history.

## 4. Live activity / Canlı akış

The workspace-wide queue, active stages, agents, elapsed seconds and last 100 events refresh roughly every four seconds. Open a task for live output, cancellation and original event history. This is polling rather than a separate event broker. Completion times are recorded on terminal run transitions. Raw tool checkpoints and credentials are excluded from this endpoint.

## 5. Knowledge bank / Bilgi bankası

Store notes (up to 64,000 characters) or snapshots of finished reports. Search notes, previous reports and text/PDF evidence within the selected project. Choose up to 20 notes explicitly for the next task; project changes clear the selection. Selected text is sent to the assigned provider with `[knowledge:ID]` provenance and an untrusted-data label. Saving/selecting/searching does not itself call a model. Deleting a note removes the snapshot, not its original report. This is literal search and explicit retrieval, not embeddings or automatic semantic RAG. Large libraries use direct scans and may take longer.

## 6. Agent capabilities / Ajan yetenekleri

In **Management → Agents & API**, edit vision support, tool calling, context capacity and specialty tags. Unknown is allowed for older configurations. These are declarations, not remotely verified capabilities. Explicitly unsupported image/tool configurations are rejected before tasks are created. Studio recommendations use tag matches and declared capabilities; capacity is a rough heuristic, not a tokenizer benchmark. Recommendations never switch agents without the user's selection, truncate output or set a spending limit.

## 7. Encrypted backup / Şifreli yedek

**Administrator only:** Management → Backup downloads `.ecybak`, protected by a separate 12–256 character backup password. PBKDF2-HMAC-SHA256 (600,000 iterations, random salt) derives a Fernet authenticated-encryption key. It includes a consistent SQLite snapshot, uploaded evidence, saved report artifacts and `master.key`. Database-stored credentials are therefore portable inside the encrypted archive. `.env` and environment-provided API keys are excluded. Keep the backup password separately; there is no recovery backdoor. Do not upload encrypted backups to the source repository.

Wait for in-flight work to finish. Queued jobs may be included when dispatch is explicitly paused; the exporter rechecks readiness under a database write lock. See [restore rehearsals](RECOVERY-DRILLS.md) for file/key/audit validation. The in-memory exporter supports up to 1 GiB compressed snapshots. Larger installations should stop the service and use an offline filesystem backup. An upload still being committed can require retrying the backup.

Restore on a trusted machine, using project dependencies:

```sh
python3 -m app.backup restore /path/to/ecy-harbor.ecybak --destination ./restored-data
```

The command prompts for the password; it must not be put in shell arguments. It refuses an existing destination, checks authenticated contents, archive paths, SQLite integrity and uploaded-file presence. Existing data is never overwritten. Stop the app, set `DATABASE_PATH` to the new directory's `harbor.db`, then start it again. Schedules and Telegram are disabled; webhook tokens are disabled and require creating fresh connections. Pending jobs become incomplete, pending tool approvals are rejected and dispatch starts paused. Use `python3 -m app.backup verify archive.ecybak` for a disposable offline rehearsal before switching data directories. Review and deliberately re-enable scheduled work. Restore environment-only keys separately. For Docker, use a new data volume/directory and point the database path there; container restoration was not runtime-tested on this host.

## 8. Incoming webhooks / Gelen webhook bağlantıları

Administrators can create a webhook from current studio agent/project/file/internet settings in **Management → Webhook connections**. A random token is displayed once and stored only as SHA-256. Revoke a connection to disable it immediately. No connector, remote service or public network exposure is enabled automatically.

```sh
curl -X POST http://localhost:8080/api/hooks/HOOK_ID \
  -H 'Authorization: Bearer TOKEN' \
  -H 'Content-Type: application/json' \
  -H 'Idempotency-Key: a-new-unique-event-id' \
  -d '{"prompt":"Investigate the configured evidence"}'
```

The bearer token can create tasks only with that hook's stored configuration. Callers cannot select other models/projects/files or grant tools. `tool_requested` is always false; existing read-only internet behavior follows the explicitly configured setting. Use a fresh idempotency key for every new event and retain the same key for retries. Creation and deduplication commit atomically; retries return the original task ID even if its history was later deleted. Reusing a key with different input still returns that original task. Queue capacity remains an operational guard, not a financial quota. Token holders can incur provider charges: use HTTPS outside localhost, keep tokens private, and revoke unused connections. No token is included in routine logs, GET listings or task outputs.

## Türkçe hızlı kullanım

- **Görev ayrıntısı → Görev sohbeti:** Devam sorusu ve yeni kanıt ekle. Her mesaj ayrı ücretlenebilen bir görevdir.
- **Karşılaştırma:** 2–4 raporu seç; metin, token, süre ve maliyeti yan yana gör.
- **Bulgular & aksiyonlar:** Önem, durum, kaynak satırı ve inceleme adımlarını kaydet. Değişiklik geçmişi korunur.
- **Canlı akış:** Tüm çalışma alanlarının kuyruğunu ve ajan aşamalarını izle.
- **Bilgi bankası:** Not ve rapor kopyalarını sakla; göreve göndereceğin bilgileri açıkça seç.
- **Yönetim → Ajanlar & API:** Görsel/araç desteğini ve uzmanlık etiketlerini tanımla. Bunlar otomatik doğrulama değildir.
- **Yönetim → Yedekleme:** Ayrı parola ile şifreli yedek indir. Yukarıdaki geri yükleme komutu parolayı sorar; mevcut klasör üzerine yazmaz. Geri yüklemeden sonra zamanlamalar, Telegram ve webhook'lar kapalıdır. Parolayı kaybedersen yedek açılamaz.
- **Yönetim → Webhook bağlantıları:** Stüdyo ayarlarıyla bağlantı oluştur. Anahtar bir kez gösterilir. Aynı istek kimliği ikinci görev oluşturmaz. Kullanılmayan bağlantıyı iptal et.
