# Görev reçeteleri / Task playbooks

## Türkçe

**Görev reçeteleri** menüsü tekrarlanan işleri 1–12 sıralı adıma dönüştürür. Örneğin: kanıtları incele → iddiaları doğrula → kaynaklı rapor hazırla.

1. Stüdyoda proje alanını, dosyaları, internet/MCP ve bilgi bankası seçimlerini hazırla.
2. Görev reçeteleri → **Yeni görev reçetesi**. Her adımın başlığını, ajanını ve talimatını düzenle. Stüdyodaki dosya/internet/MCP/bilgi seçimleri adımlara kopyalanır. Arayüz her adım için tek ajan seçer; API mevcut tek ajan/topluluk görev ayarlarını kabul eder.
3. **Çalışma oluştur** ile en fazla 16.000 karakter başlangıç girdisi ekle. Bu işlem sağlayıcı çağrısı yapmaz.
4. **Sonraki adımı incele** ekranında ayarları ve önceki adımların bağlamını kontrol et. **Onayla ve adımı başlat** yalnızca o adımı kuyruğa alır.
5. Sonucu incele. Bir sonraki adım, önceki adım tamamen bitince ayrıca onaylanır. Önceki sonuçlar kesilmeden aktarılır; sağlayıcının bağlam sınırı aşılırsa normal görev hatası görünür. Model yanıtına ek bir çıktı sınırı konmaz.

Her çalışmanın reçete tanımı ve adım ayarları bir kopyadır. Ana reçeteyi pasife almak/silmek mevcut çalışmaları değiştirmez. Yeni çalışmalara güncel reçete gerekir. Dosya, bilgi ve sağlayıcı kayıtları çalıştırma anında normal görev kurallarıyla doğrulanır; API anahtarları reçeteye kopyalanmaz. MCP izinleri her görevde tekrar denetlenir; reçete onayı, kapalı bir aracı açmaz.

### Duraklatma, iptal ve hata

- **Duraklat:** yeni adım başlatılmasını engeller. Çalışan çağrı devam edebilir. **Devam etmeye hazırla** yeni çağrı başlatmaz; adımı yine sen onaylarsın.
- **İptal:** kuyruktaki/onay bekleyen/çalışan bağlı görevi iptal işaretler ve sonraki adımları durdurur. Uzağa gönderilmiş bir işlem geri alınamaz ve ücret doğurabilir.
- **İnceleme gerekiyor:** başarısız, eksik, iptal edilmiş veya yeniden başlatmada kesilmiş adım. Sonraki adım başlamaz. Tam argüman ve araç geçmişini kontrol ettikten sonra yeni bir görev denemesi oluşturabilirsin; eski deneme ve çıktısı saklanır. Otomatik adım tekrarı yoktur.
- Aynı adımı iki kez başlatan eşzamanlı istekler tek işlemle korunur. Görev motorunun mevcut sağlayıcı bağlantı tekrar politikası korunur; manuel yan etkili MCP çağrıları reçetelerden otomatik çağrılmaz. “Görevlerde izin ver” yetkisini yalnızca uygun araçlara ver.

### Geçmiş ve çıktılar

Çalışma ekranı her adımın talimatını, görev ayarlarını, gönderilen önceki adım bağlamını, çıktısını, görev bağlantısını ve deneme geçmişini gösterir. Tüm çalışma **Markdown** indirilebilir. Her adımın görev raporu mevcut Markdown/PDF indirme ekranında açılır.

Görev geçmişi silinmeden önce sonuç reçete çalışma kopyasında saklanır. **Görev geçmişini temizlemek reçete çalışma kopyasını silmez.** Çalışma kopyasını ayrıca silebilirsin; bu, bağlı görevleri silmez. Çalışan adım bitmeden çalışma kopyası silinemez. Bu ayrım iki ekranda da bağımsız geçmiş incelemesini korur.

Reçeteler kişisel ortak çalışma alanının parçasıdır. Her rol okuyabilir; gözlemci değiştiremez. Çalışmayı sahibi veya yönetici değiştirebilir. Başkasının paylaşılan reçetesinden başlattığın çalışmanın sahibi sensin. Şifreli yedekler reçete tablolarını da kapsar; geri yüklenen çalışmalar duraklatılır ve kendiliğinden devam etmez.

### API

- `GET /api/playbooks?project_id=__general` · `POST /api/playbooks` (`name`, `project_id`, `steps: [{title, config}]`)
- `POST /api/playbooks/toggle` (`id`, `enabled`) · `POST /api/playbooks/delete` (`id`)
- `POST /api/playbooks/start` (`id`, `input`) → çalışma kimliği; çağrı başlamaz.
- `GET /api/playbooks/runs?project_id=__general` · `GET /api/playbooks/runs?id=<run>`
- `POST /api/playbooks/runs/advance` (`id`, `expected_step`: sıfır tabanlı güncel adım; tekrar için `retry_task`: önceki görev kimliği). Kimlik/sıra uyuşmazlığında işlem reddedilir.
- `POST /api/playbooks/runs/pause|resume|cancel|delete` (`id`)

Tüm API'ler mevcut oturum/CSRF/rol kontrollerinden geçer. Liste istekleri rapor gövdelerini yüklemez ve sağlayıcı çağrısı yapmaz. Kontrol işlemleri yürütme kayıtlarına yazılır; promptlar bu özet kayıtlara kopyalanmaz.

## English

Use **Task playbooks** to define 1–12 ordered steps, such as inspect evidence → verify claims → report. Prepare your project, files, knowledge and Internet/MCP selections in the studio, then save a playbook with an agent and instruction for each step. The UI uses one agent per step; the API also accepts compatible existing council configurations.

**Create run** snapshots the definition with an initial input of up to 16,000 characters. It makes no provider call. **Review next step** shows settings and previous-step context; **Approve and start step** queues only that step. Every next step requires a new explicit action after successful completion. Previous outputs are preserved verbatim; provider context errors remain visible, with no new application output cap.

Pausing blocks new steps while an active call may finish. Resuming prepares the run without dispatching. Cancellation marks active/queued/pending-approval work cancelled but cannot undo remote work already dispatched. Failed/incomplete/interrupted steps need attention and an explicit retry bound to the previous task ID; old attempts remain visible. No automatic step replay occurs on restart or restore. Existing task-level connection retry behavior remains; manually approved side-effecting MCP tools are not automatically run by playbooks. MCP task grants still require care and are revalidated by the normal task engine.

Definitions and runs are independent snapshots. Disabling/deleting a definition does not change existing runs. Referenced files, knowledge, agents and MCP grants are checked through the normal task pipeline at dispatch; API keys are never copied into playbooks. Full pre-send context privacy controls are a later phase.

Each run shows step inputs/settings, previous-step context, results, linked tasks and attempts. Export the combined run as Markdown or open an individual task for its Markdown/PDF report. Deleting task history retains the playbook copy; delete the run copy separately to remove its stored input/output. Deleting a run does not delete task history. Active steps must finish before deleting a run copy.

This remains a shared personal workspace: all roles may read; only the owner/admin may mutate a run, and viewers cannot write. Encrypted backups include the new tables; restored runs are paused. API routes and request fields are listed above. All routes retain session, CSRF and role controls; GET/list operations never dispatch tasks.

## Validation / Doğrulama

Regression tests cover no-dispatch reads/creation, fresh per-step approval, complete large-output transfer, exact retry binding, attempt retention, concurrent duplicate requests, pause/resume/cancel, owner/viewer boundaries, scoped lists, invalid definitions, task deletion snapshots and restart behavior. Tests use disposable databases and local fake services. Final browser visual verification must be recorded separately; automated API tests alone do not prove layout quality.
