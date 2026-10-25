# HTTP API

JSON istekleri `Content-Type: application/json` ister. Oturum açıldıktan sonra yazma istekleri `/api/state` yanıtındaki `csrf` değerini `X-CSRF-Token` başlığında taşımalıdır. Oturum HttpOnly çerezde tutulur; CORS açık değildir.

| Metot | Yol | Yetki | İşlev |
|---|---|---|---|
| GET | /api/health | anonim | Sağlık ve çalışma modu |
| GET | /api/auth/status | anonim | mode, setup_required |
| POST | /api/setup | yalnızca ilk kurulum, localhost | `{password}`; admin parolası en az 12 karakter |
| POST | /api/login | anonim | `{user: admin/operator/viewer, token: parola}` |
| POST | /api/logout | oturum | Oturumu kapat |
| GET | /api/state | tüm roller | Kullanıcı, csrf, redakte ajanlar, son görev/onaylar, bütçe |
| GET | /api/agents | admin | Düzenlenebilir ayarlar, has_api_key, last_test; anahtar dönmez |
| POST | /api/agents | admin | Ajan oluştur veya id ile güncelle |
| POST | /api/agents/{id}/toggle | admin | `{enabled: true/false}`; pasif/aktif durumunu değiştir |
| POST | /api/agents/{id}/delete | admin | Ajanı kaldır, erişim bilgilerini temizle; geçmiş adını koru |
| POST | /api/history/delete | admin / sahibi operator | `{ids:[...]}` veya `{all:true}`; sadece bitmiş görevler |
| POST | /api/agents/{id}/test | admin | Gerçek model isteği ile bağlantı kontrolü; bütçelenir |
| POST | /api/access/password | admin | `{user,password}`; ilgili rolün oturumlarını kapatır |
| GET | /api/browser/status | oturum | MCP/Node/tarayıcı ve kurulum durumu |
| POST | /api/browser/install | admin | Arka planda sabit sürümlü MCP ve gerekirse Chromium kurulumu |
| POST | /api/tasks | admin/operator | Görev oluştur |
| GET | /api/tasks/{id} | tüm roller | task, runs, logs |
| POST | /api/tasks/{id}/continue | admin/operator | `{run_id}`; eksik yanıtın tam bağlamıyla yeni görev |
| POST | /api/tasks/{id}/cancel | admin veya sahibi operator | Görev iptali |
| GET | /api/tasks/{id}/export?format=md | tüm roller | Markdown attachment |
| GET | /api/tasks/{id}/export?format=pdf | tüm roller | PDF attachment |
| GET | /api/tasks/{id}/export?format=pdf&run={run_id} | tüm roller | Tek ajan raporu; md de desteklenir |
| POST | /api/approvals/{id} | admin | `{decision: approve/reject}` |

Ajan kaydı:

```json
{
  "name": "Yerel uzmanım",
  "role": "Teknik analist",
  "provider": "openai-compatible",
  "model": "sunucundaki-model-kimliği",
  "endpoint": "http://localhost:11434/v1",
  "api_key": "",
  "clear_key": false,
  "instruction": "Kanıta dayalı, kısa bir teknik analiz üret.",
  "hardware": "Yerel GPU düğümü",
  "price": 0,
  "color": "blue",
  "enabled": true,
  "safe_mode": false
}
```

Güncellemek için aynı gövdeye `id` ekle. Boş api_key mevcut anahtarı korur; clear_key true ise kaldırır. API türleri `openai-compatible` ve `hermes-bridge`. Yerel özel ağ adresleri desteklenir; file URL, URL içinde kullanıcı/parola, query/fragment ve bilinen cloud metadata adresi kabul edilmez. Ajanın queued/running görevi varsa güncelleme reddedilir.

Görev oluşturma:

```json
{
  "prompt": "Yerel altyapım için test planı hazırla",
  "mode": "council",
  "agents": ["kayıtlı-ajan-kimliği"],
  "judge": null,
  "budget": 1,
  "tool_requested": false
}
```

single modunda tam bir ajan seçilir ve judge yok sayılır. council modunda 1–4 katılımcı ve isteğe bağlı judge seçilir. Devre dışı veya endpoint'i eksik ajanla görev açılamaz. Yeni ajan kimlikleri sunucu tarafından UUID olarak üretilir; başlangıç kimlikleri hermes/local/research/critic olarak korunur.

Durumlar: queued → running → completed / awaiting_approval / failed. Onay kararı sonrası completed. queued/running/awaiting_approval iptal edilebilir. Run türü participant veya judge olur. Bağlantı testleri görev geçmişine ayrı tek ajan işi olarak kaydedilir.

Hatalar `{error: string}` biçimindedir. 400 doğrulama veya durum çakışması, 401 oturumsuz okuma, 403 yazma yetkisi/CSRF/origin/giriş reddi, 404 bulunamayan kayıt, 500 iç hata. Provider ham hata gövdeleri istemciye gönderilmez. Export endpoint'i JSON yerine binary PDF veya UTF-8 Markdown ve Content-Disposition attachment döndürür.

Bağlam/prompt sınırı 16.000 karakter; JSON gövde sınırı 262.144 bayttır. Silinen görevlerin harcama tutarı içeriksiz deleted_costs tablosunda korunur ve bütçeye katılır. Silinmiş ajan listelenmez ve tekrar göreve atanamaz.

Yeni terminal durum `incomplete` (Yanıt eksik) sağlayıcı üretim sınırı, filtre veya desteklenmeyen tool-call bitişini bildirir. finish_reason run kaydında döner. Yeni normal görevlerde output_limit NULL; kullanıcıya veya sağlayıcıya bir uygulama output cap değeri verilmez.

Görev oluşturma gövdesi `web_enabled: true/false` destekler; varsayılan false. Gerçek MCP hazır değilse internet açık görev reddedilir. /api/state providers alanında endpoint bazında çalışan/çalışmayan/bilinmeyen özetini, browser alanında MCP durumunu döndürür. Tool-call döngüsü modelin kullanım toplamına dahildir.

Harcama takibi kotasızdır: daily_limit=null, usage.cost bugünkü tutar, usage.total_cost tüm zamanların tutarıdır. budget alanı eski istemciler için kabul edilir ancak uygulanmaz. Yeni görevlerde rezerv ve bütçe alanları sıfırdır. Boş görünür metin completed olamaz; empty_response bitişi incomplete durumuna karşılık gelir. GET task run çıktısında can_resume_from_evidence özelliği bulunur, provider_state bulunmaz. continue endpoint'i metinsiz sonuçta varsa kayıtlı model/araç durumunu kullanır ve web_enabled=false olan bir finalleştirme işi açar; web çağrılarını tekrarlamaz.


## Çalışma alanı ve otomasyon API'si

Tüm uçlar mevcut oturum/RBAC kurallarını kullanır; POST için CSRF zorunludur. Projeler aynı kişisel çalışma alanındaki tüm rollere görünür; yazma/silme işlemleri kayıt sahibi veya yöneticidedir.

- `GET /api/workspace`: projeler, dosya metadata'sı, şablonlar ve zamanlamalar; yöneticiye şifresiz bot tokenı içermeyen Telegram ayarı ve teslimat geçmişi.
- `POST /api/projects`: `{id?, name, instruction}`. `/api/projects/delete`: `{id}`. Kullanılan projeler silinmez.
- `POST /api/files`: `{name, data: base64, project_id?}`. Dosya başına varsayılan 32 MB; UTF-8 metin/log/JSON/CSV, PDF, PNG/JPEG/WebP. Ham içerik ve çıkarılmış metin uygulama verisidir.
- `GET /api/files/:id?start=1&count=200`: en fazla 1.000 satırlık sayfa. `?image=1` yalnızca doğrulanan görsel türlerinde oturum korumalı görüntü.
- `POST /api/files/delete`: `{id}`. Görev kanıtı, şablon veya zamanlama tarafından kullanılan dosyalar korunur.
- `POST /api/tasks` ek alanları: `project_id`, `file_ids`, `review_enabled`, `fallback_agent`. Varsayılan mod `single`.
- `GET /api/state?project_id=:id`: proje görevleri. `__general` genel alanı seçer; parametre yoksa son 100 görev.
- `GET /api/tasks/:id`: ek `files`, `sources`, görünür canlı `runs[].output`, `attempt`, `cost_estimated`. `provider_state` ve `request_state` hiçbir zaman bu yanıta eklenmez.
- `POST /api/tasks/:id/retry`: `{run_id}` seçili başarısız/eksik aşamayı yeni görevde çalıştırır. Orijinal görev sona ermiş olmalıdır.
- `POST /api/tasks/:id/followup`: `{prompt}` önceki çıktılar, proje ve dosya referanslarıyla yeni görev.
- `POST /api/templates`: `{name, config: task_body}`. `/api/templates/delete`: `{id}`.
- `POST /api/schedules`: `{name, config: task_body, interval_minutes: 5..525600, next_run?: epoch_seconds}`.
- `POST /api/schedules/toggle`: `{id, enabled}`; `/api/schedules/delete`: `{id}`.
- `POST /api/telegram`: yönetici; `{token?, clear_token?, chat_id, origin?, enabled, include_summary}`. Boş token kayıtlı değeri korur. Token şifrelenir ve geri döndürülmez. Kaydetmek mesaj göndermez.
- `POST /api/notifications/retry`: yönetici; `{id}`. Belirsiz teslimatı tekrar göndermek kopya mesaj oluşturabilir.

Zamanlayıcı her beş saniyede kontrol eder; iş kaydı ve `next_run` aynı SQLite işlemi içinde ilerletilir. Telegram outbox durumu `pending → sending → sent/failed/unknown` olur. Başlangıçta `sending` kayıtları `unknown` yapılır; otomatik tekrar edilmez.

## Local evidence search

`GET /api/files/search?q=401&project_id=__general&offset=0&limit=50` requires an authenticated session (all roles). Use a project ID to search that shared project, or omit `project_id` / use `__general` for general workspace files. A missing project returns 400. Literal Unicode case-insensitive queries must be 1–200 characters on one line. `limit` is 1–100; offset is a nonnegative count of matching lines to skip.

Response: `{results:[{file:{...metadata},line:2,snippet:"...",match_start:10,match_end:13,prefix_omitted:false,suffix_omitted:false}],offset:0,next_offset:null}`. Match positions are Unicode code-point offsets for the snippet; JavaScript clients should slice `Array.from(snippet)` to preserve emoji and other non-BMP characters. Each matching source line appears once, even if it contains several matches. `next_offset` points to the next page or is null when exhausted. Images are excluded; text extracted from PDFs is included. Search uses a direct scan, has no model cost, and returns plain data. Render snippets as escaped text. Source lines use the same numbering as `/api/files/{id}`. There is no snapshot isolation between separate pages; file changes can shift results.

## Platform phases

All `/api/platform/*` endpoints require a session; POST also requires CSRF. Projects are shared, while record writes remain owner/admin scoped.

| Method | Route | Access / function |
|---|---|---|
| GET | `/api/platform/thread?task=ID` | all roles; conversation tasks and full visible reports |
| POST | `/api/tasks/ID/followup` | owner/operator or admin; `{prompt,language,file_ids:[additional evidence]}` |
| GET | `/api/platform/compare?ids=A,B` | all roles; 2–4 full reports, run snapshots, metrics and text diffs |
| GET | `/api/platform/activity` | all roles; global active queue/stages and recent 100 events |
| GET | `/api/platform/findings?project_id=ID` | all roles; findings with immutable edit snapshots |
| POST | `/api/platform/findings` | operator/admin; `{id?,project_id,title,details,severity,status,task_id?,file_id?,first_line?,last_line?}`; existing owner/admin only |
| GET | `/api/platform/knowledge?project_id=ID&q=literal` | all roles; notes, report matches and first page of file matches |
| POST | `/api/platform/knowledge` | operator/admin; `{project_id,title,content}` or `{project_id,title,source_task}` |
| POST | `/api/platform/knowledge/delete` | owner/admin; `{id}` |
| POST | `/api/platform/backup` | admin; `{password}` → encrypted binary attachment |
| GET | `/api/platform/webhooks` | admin; config/status without token hashes or plaintext tokens |
| POST | `/api/platform/webhooks` | admin; `{name,config}` → `{id,token}` once |
| POST | `/api/platform/webhooks/revoke` | admin; `{id}` |
| POST | `/api/hooks/ID` | scoped bearer token, JSON `{prompt}`, mandatory `Idempotency-Key`; 201 first call, 200 replay |

Tasks, templates and webhook configuration accept `knowledge_ids` explicitly; cross-project knowledge is rejected. Agent settings accept `capabilities:{vision:true|false|null,tools:true|false|null,context_tokens:number|null,specialties:[strings]}`. Run responses now include `finished_at`, `model_snapshot`, `provider_snapshot`, `agent_name_snapshot`; legacy snapshots may be null. Full backup/restore behavior and webhook contracts: [PLATFORM.md](PLATFORM.md).
