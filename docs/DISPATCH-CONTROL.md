# İş kuyruğunu bakım için duraklatma / Maintenance dispatch pause

**Operasyon → Görev işlemeyi duraklat** global yönetici içindir. Duraklatma iki noktayı aynı veritabanı durumu üzerinden kapatır: yeni görev oluşturma ve işleyicinin kuyruktan görev alması. Bekleyen görevler silinmez veya başarısız sayılmaz. Durum yeniden başlatmada korunur; yönetici açıkça **Görev işlemeyi devam ettir** seçmelidir.

Global administrators can use **Operations → Pause task processing**. A persisted database flag gates both new task admission and worker queue claims. Queued jobs are preserved, and restart does not silently resume processing. Use **Resume task processing** explicitly.

## Davranış / Behavior

- Yeni görev, takip sorusu, yeniden deneme, model denemesi ve reçete adımı yeni bir görev oluşturacaksa `503` ve `code: "dispatch_paused"` alır. Reddedilen istek görev oluşturmaz veya sağlayıcı çağrısı yapmaz. / Operations that create new jobs receive HTTP 503 with `dispatch_paused`, without creating a job or provider call.
- Daha önce başlamış bir görev kalan model/araç turlarını tamamlayabilir ve maliyet oluşturabilir. Duraklatma iptal veya harcama sınırı değildir. / Already running tasks may finish their model/tool rounds and incur usage; pause is not cancellation or a spending limit.
- Zamanlayıcı duraklatmada etkin kalır ve vade alanlarını ilerletmez. Devamda her zamanlama en fazla bir iş başlatır; kaçırılan her aralık yeniden oynatılmaz. / Schedules stay enabled and retain due times. On resume, each schedule launches at most one job per check without a missed-interval replay burst.
- Webhook'un daha önce kabul edilmiş aynı idempotency anahtarı eski görev sonucunu döndürebilir; yeni görev gerekiyorsa 503 alır. / A previously accepted idempotent webhook may replay its recorded task ID; new dispatch is blocked.
- Arayüzün **çalışan görev kalmadı** bilgisi, görev ve ajan çalıştırma tablolarındaki o anki kayıtları ifade eder. İşleyici dışındaki manuel MCP yönetim çağrılarını, bildirim gönderimini, HTTP isteklerini veya başka süreçleri dondurmaz. Tam sunucu bakım kilidi veya dağıtık işleyici lease'i değildir. / Drain status is a point-in-time observation of task/run records, not a global freeze of manual MCP administration, notifications, HTTP or other processes, and not a distributed worker lease.
- Genel HTTP liveness/readiness uçları web uygulamasının okunabilir veritabanıyla hizmet vermesini ölçer; duraklatmada açık kalır. Kuyruk kontrolü için `/api/operations` içindeki `dispatch` alanını kullan. / HTTP liveness/readiness remain healthy during pause because the web service still works. Inspect `dispatch` in `/api/operations` for queue state.

## API ve güvenlik / API & concurrency

`POST /api/operations/dispatch` requires an administrator session, CSRF and strict JSON booleans:

```json
{"paused": true, "expected_paused": false}
```

Devam: `{"paused": false, "expected_paused": true}`. Son okunan durum değiştiyse istek reddedilir; yenilemek gerekir. Aynı durumu tekrar kaydetmek fazladan bir duraklatma olayı üretmez. Pause/resume geçişleri denetim geçmişine yazılır. / A stale expected state is rejected; an idempotent update creates no duplicate transition event. Transitions enter the audit trail.

İş alımı ve duraklatma değişikliği SQLite yazma işlemiyle sıralanır. Duraklatma kararı commit edilmeden hemen önce alınan bir iş başlamış iş sayılır; sonraki alımlar engellenir. / Queue claims and pause updates serialize through SQLite write transactions; a claim committed before pause is already in flight.

Bu, faz 12'nin ilk parçasıdır. PostgreSQL, ayrı worker servisleri, lease/heartbeat tabanlı sahiplik ve çok sunuculu yükseltme henüz uygulanmadı. / This is the first phase-12 increment; PostgreSQL, separate workers, lease-based ownership and multi-server rollout are not shipped.
