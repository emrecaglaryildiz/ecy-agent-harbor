# Depolama ve saklama planlama / Storage & retention planning

## Kullanım / Usage

**Yönetim → Depolama & saklama**, yalnızca yöneticiye açık iki kontrol sunar:

- Yerel yüklemeler ve onayla kaydedilen raporların dosya sayısı/boyutu; veritabanı ve WAL/SHM toplam boyutu; eksik yüklemeler, boyut uyuşmazlıkları ve referanssız/güvensiz/geçici kayıt sayıları.
- Üst menüde seçili projenin kapanmış görevleri için 1–3650 günlük saklama önizlemesi. Yaş, oluşturulma değil **son güncellenme anından** hesaplanır. İstanbul'da gösterilen eşik, UTC anı olarak karşılaştırılır; farklı saat ofsetli eski kayıtlar desteklenir.

**Management → Storage & retention** provides admin-only metadata inventory and retention planning for the project selected in the top bar. Age is based on the last update instant, not creation time. Terminal tasks only (`completed`, `failed`, `cancelled`, `incomplete`); reserved tasks and tasks with a running provider run are excluded. Queued, running and approval-pending tasks never qualify. Results paginate in groups of 100 and contain IDs, status and last update only, without prompt/report content.

Bu sürüm **önizleme yapar; silmez, taşımaz ve otomatik temizlik açmaz**. Gün seçimi oturum içi bir sorgudur; kalıcı veya etkin bir silme politikası değildir. Silme etkisini incelemek için uygun görev sayısı ve silme sonrasında da korunacak kullanım tutarı gösterilir. Mevcut Görev geçmişi silme akışı ayrıdır. Yüklenen kaynaklar, denetim geçmişi, kullanım tutarları, bulgular ve bilgi bankası/reçete/laboratuvar rapor kopyaları geçmiş silinmesiyle otomatik temizlenmez. Bu nedenle önizleme tam veri imhası veya mevzuata uygun silme kanıtı değildir.

This release is **preview-only**, with no deletion/move endpoint, retention scheduler or activated policy. The day selection is a query, not a saved cleanup policy. Existing task-history deletion remains a separate action. Evidence uploads, audit history, usage totals, findings and knowledge/playbook/lab copies remain independently retained; this preview is not a full erasure workflow.

## Yerel depo / Local store

`app.storage.LocalStore`, mevcut veritabanı yanındaki `uploads/<opaque-id>` ve `artifacts/<opaque-id>.txt` düzenini korur. Veriler taşınmaz; mevcut yedek ve geri yükleme yolları değişmez. Yükleme, görsel okuma, dosya silme ve onaylı rapor yazımı bu katmanı kullanır. Yeni klasörler 0700 ve dosyalar 0600 izinleriyle oluşturulur (platform desteğine bağlı); eski klasörlerin izinleri kendiliğinden değiştirilmez.

Uploads, image reads, evidence removal and approved report writes use the local-store adapter. It validates opaque IDs, rejects symbolic links and publishes completed temporary bytes atomically. Evidence publication is exclusive; an existing evidence ID is never overwritten. Approved report replacement keeps the old report if the final replacement fails. Temporary files are cleaned on ordinary errors; a process crash can leave `.pending-*` entries, which inventory reports without deleting. Parent-directory fsync/power-loss durability and hostile local filesystem writers are outside this adapter's guarantees.

Veritabanı kaydı dosyadan önce görünür olabilir; yedekleme eksik veya uyuşmayan kaynağı reddeder. Depo envanteri eşzamanlı yükleme/silme sırasında anlık bir gözlemdir, tutarlı yedek değildir. Dosya içerikleri okunmaz veya SHA256 kontrol edilmez; bütünlük için [geri yükleme provasını](RECOVERY-DRILLS.md) kullan. Klasör başına en çok 10.000 kayıt ve 10.000 veritabanı referansı taranır. Sınıra ulaşılırsa `truncated` açıktır ve sayılar kısmidir; referanslar tam değilse referanssız sınıflaması yapılmaz. Rapor dosyası isteğe bağlı olduğundan raporu bulunmayan görev eksik sayılmaz.

Inventory is read-only metadata, not a consistent snapshot or integrity proof. Concurrent operations may change counts. It never reads evidence bodies, hashes sources, follows file symlinks or deletes unknown entries. Per-store limits are explicit and partial results are flagged. S3/object storage, automatic retention, restore-linked deletion approval and full erasure remain later work; this release introduces no financial quota.

## API

- `GET /api/platform/storage`: admin-only, read-only local inventory; no paths, filenames or source bodies returned.
- `POST /api/platform/retention/preview`: admin-only, normal authenticated CSRF protection. Body: `{"project_id": null, "days": 90, "offset": 0}`. `null` selects the general workspace; explicit project IDs are checked. Returns `preview_only`, `automatic_deletion:false`, cutoff, candidate count, retained cost, metadata page and next offset. No selection/execution token is issued. Results may change between pages as tasks update.

No storage migration, external service, paid call or automatic cleanup is performed by these endpoints.
