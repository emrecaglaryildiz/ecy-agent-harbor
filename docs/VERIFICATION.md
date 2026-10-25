# Doğrulama kaydı

Tarih: 24 Ekim 2026. Normal uygulama modu: **live**.

| Kontrol | Sonuç |
|---|---|
| Python entegrasyon testleri | 55 test geçti |
| JavaScript sözdizimi | app.js ve settings.js kontrol edildi |
| İlk kurulum ve parola | Setup, parola hashleme, ikinci setup reddi ve parola oturumu test edildi |
| API anahtarı | Veritabanında şifreli, API yanıtlarında geri dönmüyor; koruma/değiştirme/kaldırma test edildi |
| Kalıcı ajan ayarları | Yeniden başlatma sonrası ayar ve anahtarın korunduğu test edildi |
| Gerçek HTTP akışı | Yerel sahte sağlayıcıda kaydedilmiş endpoint/model/key üzerinden gerçek HTTP isteği doğrulandı |
| Tarayıcı | Ayrı live QA ortamında giriş, yeni ajan kaydı, bağlantı testi, yeni ajanla görev çalıştırma doğrulandı |
| İndirmeler | Görev ve ajan düzeyinde PDF/.md düğmeleri; endpoint içerik türü, attachment başlığı ve oturum kontrolü doğrulandı |
| PDF | Türkçe karakterler pypdf ile okundu; Poppler ile PNG'ye render edilip sayfa düzeni görsel olarak kontrol edildi |
| Docker Compose | Yapılandırma doğrulanır; Docker daemon kapalı olduğu için image build/container çalıştırma test edilmedi |
| Kullanıcının gerçek sağlayıcıları | Kullanıcı API adreslerini ve anahtarlarını kendisi ekleyecek; dış servislere test çağrısı yapılmadı |
| Hermes bridge | Sözleşme ve opt-in var; mevcut Hermes üzerinde ayrı bridge kurulumu bu teslimatta yapılmadı |

Tarayıcı QA'sı 8787 portunda ve sahte sağlayıcısı 8790 portunda, ayrı `work/qa-live.db` veritabanıyla yapıldı. Bu kayıtlar kullanıcının gerçek çalışma alanına eklenmedi. Normal uygulama 8080 portunu ve kendi `data/harbor.db` dosyasını kullanır. Kaynak ZIP içinde hiçbir veritabanı, master key, kullanıcı parolası veya API anahtarı bulunmaz.

Son eklemeler: 16.000 Türkçe karakter uçtan uca kabul edildi; 16.001 karakter reddedildi. Geçmiş silme, maliyetin korunması, aktif görev koruması, ajan pasifleştirme/silme ve geçmiş adı koruması test edildi. Tarayıcıda tek ajan varsayılanı, 16.000 maxlength ve açık/karanlık tema geçişi ile tercihin reload sonrası korunduğu doğrulandı.

Son düzeltme: 2 MB üzerindeki sağlayıcı cevabı eksiksiz alındı; 50.000 karakter üzerindeki çıktı korunuyor; judge tam çıktıyı alıyor. Normal isteklerde max_tokens/max_output_tokens bulunmadığı test edildi. Eski 1.500 token kayıtları, kesilme durumları ve tam bağlamla devam görevi doğrulandı. Tarayıcıda Markdown başlık/listeler/tablo/kod/checkbox/görsel işlendi; script/event/javascript-link temizliği DOM üzerinden kontrol edildi.

Playwright MCP 0.0.83 resmi npm paketinden proje veri klasörüne kuruldu. Ayrı Chromium indirmesi dosya kilidi hatasıyla tamamlanmadı; mevcut sistem Chrome, ayrı ve geçici profilde kullanılarak MCP üzerinden example.com sayfasının okunabilir içeriği başarıyla alındı. Tarayıcıda internet anahtarı, kaldırılmış sidebar kartı ve sağlayıcı özeti kontrol edildi. MCP hazır durumu ayarlar sayfasında doğrulandı. Yerel sahte modelde tool-call → MCP → web → model dönüşü uçtan uca tamamlandı; gerçek kullanıcı API anahtarlarıyla ücretli test yapılmadı.

Boş content + stop yanıtı yeniden üretildi; reasoning alanlarının tool turlarında korunması, bir finalleştirme tekrarı, tekrar boşsa incomplete, kotaların kaldırılması ve kaynaklardan yeniden raporda browser işinin tekrarlanmaması doğrulandı. Gerçek 4d332af2 görevinin boş rapor durumu başlangıç düzeltmesiyle incomplete olarak işaretlenir; önceki çağrı tutarı korunur.


Dört faz sonrası: **76 test geçti**. Ek testler; HTTP üzerinden gerçek SSE taşıması ve yanıt bitmeden görünür metnin kaydı, reasoning ayrımı, yarıda kesilmiş akış, kontrollü tekrar, seçili yedek ve aktif yedek ajan koruması, UTF-8 log parçalarının eksiksiz kapsanması, PDF metin çıkarımı, dosya/proje kapsamı ve viewer sınırları, iki eleştiri turu ve baş uzman, atomik zamanlama, çakışan aralıkların atlanması, Telegram token şifrelemesi ve belirsiz teslimatın otomatik yinelenmemesini kapsar.

Tarayıcı kontrolü ayrı 8787 mock veritabanında yapıldı. Şablon kaydedildi, zamanlama oluşturulup duraklatıldı, rapordan satır aralığı açıldı, güncel ekran görüntüleri alındı. 390 px genişlikte yatay sayfa taşması yok; masaüstü test görünümünde tablo taşması kapsayıcı içinde tutuluyor. Tarayıcı konsolunda hata kaydı yok. Gerçek modellerin görsel anlama kalitesi, gerçek Telegram teslimatı ve tüm sağlayıcıların SSE uyumluluğu kullanıcı bağlantılarıyla sınanmadı.


Çift dil ve logo güncellemesi: **84 Python testi** ve `node tests/test_i18n.cjs` geçti. Tarayıcı dili Türkçe değilse İngilizce seçimi, kayıtlı tercihin önceliği, hatalı/erişilemeyen depolama davranışı, EN/TR API hata metinleri, rapor başlıkları ve özgün model/prompt metninin korunması test edildi. Tarayıcıda giriş, menüler, proje diyaloğu, şablonlar, sağlayıcılar, Telegram ayarları, geçmiş ve rapor ekranları kontrol edildi. Dil değişimi yazılmış promptu korudu; yenilemede tercih saklandı. 390 px genişlikte yatay sayfa taşması ve konsol hatası görülmedi. Yeni SVG logo ve dil düğmesinin yerleşimi geniş ekranda doğrulandı. Kullanıcı anahtarlarıyla ücretli test yapılmadı.

Devam ettirme, tek adımı yeniden deneme ve önceki analizden takip görevi açma uçları da seçilen bildirim dilini korur; bu akışlar regresyon testine dahildir.

## Istanbul time and management

86 Python tests pass, plus `node tests/test_i18n.cjs` and `node tests/test_time.cjs`. Istanbul midnight boundaries and local daily cost totals are tested. Schedule parsing is checked with UTC, New York, Tokyo and Istanbul device time zones. The Management tabs were checked in the mock browser without contacting providers. Persisted instants stay UTC; new deleted-cost ledger dates use Istanbul. Legacy UTC date-only ledger rows retain their original dates and are identified as approximate in daily usage.

## Local evidence search

89 Python tests pass. Search regressions cover project isolation, literal regex-like input, Turkish case-insensitive matching, CR/CRLF source line consistency, 121 results across three pages, a 200 KB single log line, viewer access, invalid parameters, and anonymous rejection. Browser checks covered Turkish/English language switching, highlighted results, source line opening, attaching a file without starting a task, and mobile layout without horizontal overflow. Language and Istanbul scheduling Node checks pass. No paid provider calls were used.

## Eight platform phases

101 isolated Python tests pass, including conversation ancestry/new evidence, comparison, audited findings, explicit project knowledge, model snapshots/live activity, declared capability rejection, encrypted backup round-trip and tampering/path traversal rejection, restoration with automations disabled, scoped webhook authorization/idempotency/revocation, and admin-only controls. Both Node suites and syntax checks for all application scripts pass. Browser checks use mock providers and disposable sample records. See [ROADMAP-PLATFORM.md](ROADMAP-PLATFORM.md) and [PLATFORM.md](PLATFORM.md).
