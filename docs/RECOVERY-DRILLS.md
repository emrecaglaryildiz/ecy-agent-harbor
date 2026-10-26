# Yedekleme ve geri yükleme provası / Backup & restore rehearsal

## Bakım kuyruğunu koruyarak yedekle / Back up a paused queue

**Yönetim → Yedekleme** ekranı yedek hazır olma durumunu gösterir. Çalışan görev veya ajan çağrısı, çalışan/gönderilmiş MCP çağrısı ya da gönderilmekte olan bildirim varsa yedek bekler. Kuyrukta görev varsa **Operasyon** ekranından görev işlemeyi önce duraklat; kuyruk boşsa duraklatma şart değildir. Uygulama kendiliğinden duraklatmaz veya devam ettirmez.

**Management → Backup** shows readiness. In-flight tasks/provider runs, MCP calls and notification sends block backup. If tasks are queued, explicitly pause dispatch in **Operations** first; an empty queue does not require pause. The application never silently pauses or resumes processing.

Yedek oluşturma, SQLite yazma işlemi içinde bu durumu tekrar kontrol eder; ekrandaki eski bir bilgiye güvenmez. Kuyruk kayıtları şifreli arşivde korunur. Dosyanın yalnızca varlığı yeterli değildir: yüklenen kanıtın boyutu ve SHA256 özeti veritabanıyla eşleşmelidir. Yarım yazılmış/uyuşmayan bir dosya için yedek indirilmez, tekrar deneme gerekir.

Creation rechecks readiness under a SQLite write transaction. Queued records are preserved in the encrypted archive. Each uploaded file must match the size and SHA256 stored in the database, so a partially committed source is rejected rather than silently archived.

## İçerik ve uyumluluk / Contents & compatibility

Parola korumalı `.ecybak`, veritabanı, yüklenen kanıtlar, yerel rapor dosyaları ve varsa `master.key` içerir. Ortam değişkeni anahtarları ve `.env` dahil değildir. Mevcut şifreli zarf ve manifest `format: 1` korunur; yeni yedeklerde ek `integrity` envanteri her üyenin boyut ve SHA256 özetini taşır. Eski envantersiz yedekler okunabilir, ancak raporda `inventory_verified: false` görünür. Bu eski yedeğin doğrulanmış bir envanteri varmış gibi gösterilmez.

The encrypted envelope and manifest format 1 remain compatible; new archives add an integrity inventory with size/SHA256 for every member. Older inventory-free backups remain readable, with `inventory_verified: false`. Environment-only keys and `.env` are excluded. The archive contains sensitive data and the encryption master key: keep its password separately and do not commit backups to GitHub.

Mevcut bellek içi dışa aktarma sınırı 1 GiB sıkıştırılmış arşivdir. Büyük kurulumlar için çevrimdışı dosya yedeği gerekir. Dosya deposu soyutlaması ve otomatik saklama/silme bu fazın sonraki parçalarıdır. Finansal kullanım kotası eklenmez.

The existing in-memory exporter supports up to 1 GiB compressed archives; larger deployments need offline filesystem backup. Storage abstraction and opt-in retention/deletion remain future work. No financial quota is introduced.

## Canlı verilere dokunmadan prova / Rehearse without changing live data

Bağımlılıkları kurulu proje klasöründe:

```sh
python3 -m app.backup verify /path/to/backup.ecybak
```

Komut parolayı gizli girişle sorar; komut argümanına veya URL'ye yazma. Parola doğrulandıktan sonra yalnızca geçici bir dizine geri yükleme yapar. Uygulama sunucusu/işleyici başlatmaz, model veya MCP çağrısı yapmaz ve mevcut veri dizinini değiştirmez. Geçici kopya işlem sonunda kaldırılır; bu bir güvenli disk silme iddiası değildir.

The command prompts privately for the password and restores only into a disposable directory. It starts no app server/worker, makes no provider/MCP calls and does not modify the live data directory. Temporary removal is ordinary cleanup, not a secure-erasure guarantee.

Kontroller:

- Şifreli zarf, arşiv yolları, varsa üye envanteri, SQLite bütünlüğü ve yabancı anahtar ilişkileri.
- Kaynak dosyalarının kayıtlı boyut/özetleri.
- Kayıtlı ajan/MCP/Telegram/MFA şifreli değerlerinin taşınan `master.key` ile açılabilmesi; açık değerler rapora yazılmaz.
- Varsa denetim zincirinin yerel tutarlılığı. Harici kontrol noktasının bağımsız doğrulaması ayrıca [AUDIT.md](AUDIT.md) kapsamındadır.
- Kopya dönüştürüldükten sonra bu kontroller tekrar yapılır; otomasyonların kapalı, görev işlemenin duraklatılmış ve bekleyen işlerin askıda olduğu doğrulanır.

Checks cover authenticated contents and paths, optional inventory, SQLite integrity/foreign keys, evidence hashes, decryption of stored agent/MCP/Telegram/MFA values, and local audit-chain consistency. Checks run again after suspension changes, including disabled automation and paused dispatch. Plaintext secrets never enter the report. External audit checkpoints remain a separate verification step.

Rapor yalnızca başarı bayrakları ve sayılar içerir: `files_verified`, `encrypted_values_verified`, `audit_events_verified`, `restored_audit_events_verified`, `pending_tasks_held`, `inventory_verified`. Eski bir yedekte denetim tablosu yoksa `audit_present` false olabilir; yeni kopyada geri yükleme olayı yeni bir denetim akışı başlatır. Prova sağlayıcı anahtarının hâlâ geçerli olduğunu veya gerçek afet kurtarmayı kanıtlamaz; yalnızca kopyanın bu yerel kontrollerini doğrular.

The report contains counts/flags only. An older backup may have no audit history; restoration then creates a new stream containing the restore event. A rehearsal does not certify provider credential validity, external service availability or a real disaster recovery environment.

## Gerçek geri yükleme / Restore into a new directory

```sh
python3 -m app.backup restore /path/to/backup.ecybak --destination ./restored-data
```

Hedef önceden varsa veya doğrulama sırasında başka biri oluşturursa üzerine yazılmaz. Önce ayrı hazırlama dizini doğrulanır; yalnızca sonra yeni hedef oluşturulur. Son taşıma sırasında disk/izin hatası olursa yeni hedefte kısmi içerik kalabilir; mevcut canlı veri değiştirilmez ve bu hedefi uygulamaya bağlamadan önce komutun başarıyla bittiğini doğrula.

An existing destination, including one created during validation, is never replaced. A staging directory is validated first, then an exclusive new target is created. A final filesystem failure can leave a partial new target, so require successful completion before switching the app to it; existing live data is untouched.

Zamanlamalar, webhook/MCP/düğüm bağlantıları ve Telegram kapalı; görev işleme duraklatılmış başlar. Kuyruktaki/çalışan/onay bekleyen görevler silinmez, `incomplete` olur; belirsiz yan etkileri otomatik tekrar etmek yerine elle incelenir. Araç onayları reddedilir ve teslimat bekleyen bildirimler belirsiz olarak tutulur. Yeni kopya bu askıya alma durumunu sağlayamıyorsa geri yükleme reddedilir.

Schedules, webhooks/MCP/nodes and Telegram start disabled, with dispatch paused. Pending tasks remain as incomplete evidence rather than being replayed; approvals are rejected and pending notification delivery becomes unknown. Restore fails if the new copy cannot maintain these suspension properties.

Uygulamayı kapatıp `DATABASE_PATH` değerini yeni dizindeki `harbor.db` dosyasına yönlendir; sonra başlat. Bağlantıları ve işleri inceleyip yalnızca açık kararla yeniden etkinleştir. Geri yükleme denetim geçmişinde yeni bir dal oluşturabilir; eski harici kontrol noktalarını sakla ve geri dönüşü ayrıca belgele.

Stop the app before switching `DATABASE_PATH`, then inspect and explicitly re-enable connections/work. Retain independent audit checkpoints and record restore lineage separately.

API: `GET /api/platform/backup/status` is admin-only, read-only and returns readiness/queue/in-flight counts. The existing admin-only `POST /api/platform/backup` download remains. There is deliberately no web endpoint that accepts a backup password/file and restores over live data.
