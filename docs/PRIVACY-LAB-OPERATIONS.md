# Bağlam, model laboratuvarı ve operasyon / Context, model lab and operations

## 5 · Bağlam ve gizlilik / Context and privacy

### Türkçe

Stüdyoda **Gönderirken hassas metni maskele** yeni görevler için varsayılan açıktır. Görevi başlatmak önce yerel kaynak önizlemesini açar; **İnceledim, görevi başlat** ayrı işlemdir. Önizleme model çağırmaz. Prompt, proje talimatı, ajan talimatları, açıkça seçilen bilgi notları ve dosyalar listelenir. Kaynak metni 16.000 karakterlik sayfalarla incelenir; bu yalnızca önizleme sayfa boyutudur. Tüm kaynak metni görev motorunda işlenir; yeni bir yanıt uzunluğu veya harcama kotası konmaz.

Kaynakları değiştirmek için stüdyoya dön; dosya kutularını, proje alanını veya bilgi bankası seçimlerini düzenle. Önizleme 30 dakika geçerlidir, sahibiyle ve seçilen ayar/kaynakların özetiyle bağlıdır, bir görev için kullanılır. Değişen kaynaklar yeniden inceleme gerektirir. Önizlemede ilk kaynaklar görünür; gelecekte üretilecek yanıt/araç sonuçları veya her sağlayıcının kesin JSON isteği önceden bilinemez.

Maskeleme tanınan anahtar önekleri, JWT, PEM özel anahtar blokları, e-posta adresleri ve parola/anahtar atamaları için örüntü tabanlıdır. Her görevde rastgele bir gizli tohumla kararlı takma değer üretilir; aynı görevde aynı değer aynı etiketi alır. Tohum API'de gösterilmez. IP adresleri ve sıradan olay kimlikleri korunur. **Tüm kişisel/gizli verileri bulma garantisi yoktur**; ad, telefon, serbest anlatımdaki kişisel bilgiler veya bilinmeyen anahtar biçimleri kaçabilir. Maskeyi kapatmak orijinal metni gönderir; kaynak dosyası değiştirilmez. Görsellerde OCR maskelemesi yoktur: görseli çıkar veya maskelenmeden gönderilmesini ayrıca onayla. Dosya adları, sağlayıcı adresleri ve ağ metadata'sı için anonimlik garantisi verilmez.

Kontrol yalnızca UI değildir: dosya parçası analizi, proje talimatı, sentez, eleştiri, fallback, tekrar/devam istekleri; OpenAI uyumlu ve Hermes payload'ları; modelden MCP'ye giden argümanlar ve sonraki model turlarında gönderilen araç sonuçları metin katmanından geçer. API kimlik doğrulama başlıkları, protokol kimlikleri ve sağlayıcının imzalı/kapalı `reasoning_details` alanları uyumluluk için değiştirilmez; kapalı protokol alanları maskeleme garantisi dışında kalır. Orijinaller ve yerel görev geçmişi hassas kalabilir; disk/yedek erişimini kısıtla. Modelin döndürdüğü rapor metni değiştirilmez.

Eski görevler, zamanlamalar ve API istemcileri davranışını korur: `mask_sensitive` verilmezse kapalıdır. Yeni stüdyo seçimleri şablon/reçete/zamanlamaya kaydedilir. API'de `mask_sensitive: true`, görsel izni için yalnızca gerçek JSON boolean `allow_unmasked_images: true` kullanılır. Devam/tekrar önceki tercihi korur. `POST /api/context/preview` görev ayarlarını alır; `POST /api/context/page` (`id`, `config`, `source_id`, `offset`) sayfalı yerel metni verir; `POST /api/tasks` isteğine `context_preview_id` eklemek incelemeyi bağlar. API üzerinden otomasyonlar önizleme olmadan açık maskeleme tercihiyle çalışabilir.

### English

New studio tasks default to **Mask sensitive text before sending**. Starting opens a local source review; the separate confirmation starts the actual task. Review lists the prompt, project/agent instructions, selected notes and files. Text is paged at 16,000 characters for display only; source processing and model output are not truncated. Previews expire after 30 minutes and bind the owner, settings and source hashes to a single task. Source changes require review again. Future model/tool content and exact per-round provider JSON cannot be previewed in advance.

Önizleme envanteri dosya kimliği, özeti ve karakter sayısını kullanır; her sayfa isteğinde tüm seçili logları yeniden belleğe kopyalamaz. Yalnızca açılan dosyanın metni okunur; maskeleme, sayfa sınırını aşan gizli değerleri de korumak için o dosyanın tamamına uygulanır. / The preview inventory uses file IDs, hashes and character counts rather than copying every selected log into memory on each page request. Only the opened file's text is read; masking processes that whole file to protect secrets crossing display-page boundaries.

Pattern masking covers recognized key formats, JWTs, PEM private keys, emails and labelled secret/password assignments. Per-task keyed pseudonyms retain correlations without returning the seed through APIs. Ordinary IPs/IDs remain. Detection is incomplete: names, phones, free-form personal data and unknown secret formats may be missed. Originals remain local; disabling masking sends original text. Images have no OCR redaction and need explicit unmasked-image permission or removal. No anonymity guarantee covers file names, endpoints or network metadata.

Masking is applied at provider and model-to-tool text boundaries, including chunks, project instructions, reviews/synthesis, fallback/retry/continuation and subsequent tool-result rounds. Provider authentication headers, protocol identifiers and signed/opaque `reasoning_details` are preserved for compatibility; opaque protocol data is outside the masking guarantee. Returned report content is not rewritten. Local history/backups may remain sensitive. Legacy tasks/schedules and API clients retain their previous behavior unless `mask_sensitive: true` is supplied. New studio configuration persists into saved workflows. The API routes and fields are listed above.

## 6 · Model laboratuvarı / Model lab

### Türkçe

**Model laboratuvarı → Yeni model denemesi**: aynı prompt ve seçili stüdyo dosya/bilgi notlarını 2–4 ajana ayrı görevler halinde gönder. İnternet, ek MCP, yedek ajan ve karşılıklı eleştiri kapalıdır; maskeleme tercihi korunur. Başlatma gerçek sağlayıcı çağrıları ve ücret doğurabilir; kendiliğinden benchmark koşulmaz.

A/B/C/D etiketli ham çıktılar, model kimliği, kaydedilen maliyet, token sayısı ve **kuyruk dahil geçen süre** gösterilir. Bu süre GPU hesaplama hızı veya sağlayıcı iç gecikmesi değildir. Model yanıtı kimliğini belli edebileceğinden etiketler tam körleme sağlamaz. Doğruluk, kanıt ve kullanışlılık 0–5 insan puanlarıdır; tamamlanmamış görev puanlanmaz, puanların nesnel doğruluk garantisi yoktur.

**Baş uzmanı çalıştır** tüm çıktılar başarıyla bitince ayrıca seçilen modele tek bir ek görev gönderir. Gerekçe, belirsizlik ve kontrol adımları istenir; otomatik olarak en iyi ajan ilan edilmez. Ham çıktılar korunur; normal görev ekranından rapor indirilebilir. Sonuçlara göre stüdyoda ajanı kendin seç. Görev geçmişi silinince karşılaştırma kopyası/ölçümleri korunur; deneme kopyasını ayrıca sil. Kopyayı silmek bağlı görevleri silmez.

API: `GET /api/lab?project_id=__general`, `GET /api/lab?id=<id>`, `POST /api/lab` (`name`, `agents`, `config`), `/api/lab/rate` (`id`: çıktı kimliği, `rating`: `accuracy/evidence/usefulness`, `notes`), `/api/lab/judge` (`id`, `agent`), `/api/lab/delete` (`id`). Sahip/yönetici yazar, gözlemci okuyabilir. Hatalı toplu oluşturma tüm görevleri geri alır.

### English

Launch an opt-in trial of 2–4 agents using the same task and selected studio evidence. Internet, MCP, fallback and peer review are disabled; masking preference persists. Each task may incur a provider charge. Compare labelled raw outputs, model identities, recorded cost/tokens and elapsed time including queueing—not GPU throughput or provider-internal latency. Labels are not guaranteed blinding. Accuracy/evidence/usefulness scores are human ratings, not ground truth.

A separate judge action creates one extra task only after successful trials, asking for reasons and uncertainty. It never automatically changes routing or declares a winner. Choose your agent manually in the studio. Task-history deletion preserves trial copies/metrics; delete the trial copy separately. Existing role/ownership rules apply; API routes are listed above.

## 7 · Düğüm ve operasyon / Nodes and operations

### Türkçe

**Operasyon** sayfası kuyrukta/çalışan/başarısız görev sayılarını, en eski kuyruk zamanını, düğüm sinyallerini ve sağlayıcı devre kesicilerini gösterir. Bu sayfayı açmak sağlayıcı çağrısı veya port taraması yapmaz.

Yönetici **Düğüm kaydet** ile isim/donanım notu ve bir kez gösterilen anahtar oluşturur. Anahtar yalnızca kendi `/api/node-heartbeats/<id>` uç noktasına sağlık verisi yazabilir; görev/ayar okuyamaz ve komut çalıştıramaz. Anahtarın yalnızca hash'i saklanır; pasife alma/silme hemen sonraki sinyalleri reddeder. Yedekten geri yüklenen düğümler devre dışıdır. Makineye kendin reporter kurmadan veri gelmez; mevcut M4/GPU/Raspberry Pi makinelerine otomatik müdahale yoktur.

[Örnek reporter](node-heartbeat.py) yalnızca standart Python kullanır. Sahibi dışında kimsenin okuyamadığı token dosyası oluştur (`chmod 600`). Aşağıdaki ortam değişkenlerini kendi makinenin güvenli başlatma ortamına tanımla; anahtar değerini URL'ye, komut satırına, README'ye veya GitHub'a yazma:

```text
HARBOR_NODE_URL=https://your-harbor.example/api/node-heartbeats/<node-id>
HARBOR_NODE_TOKEN_FILE=/private/path/node-token
```

Sonra `python3 docs/node-heartbeat.py` çalıştır. HTTPS veya yalnızca loopback HTTP kabul eder; yönlendirme takip etmez. Her 30 saniyede durum, varsayılan 0 iş sayısı ve varsa 1 dakikalık OS yükünü bildirir. Bu örnek çalışan inference işlerini/GPU'yu otomatik keşfetmez. Kendi ajan entegrasyonun `status`, `running_jobs`, isteğe bağlı `load_1m`, `memory_used_mb`, `memory_total_mb` alanlarını JSON ile gönderebilir. Rakamlar sonlu ve negatif olmayan olmalıdır; bilinmeyen alan/komut reddedilir. Bunlar düğümün beyanıdır, bağımsız donanım doğrulaması değildir. 120 saniye sinyal yoksa **Sinyal gecikti**, hiç sinyal yoksa **Henüz veri yok** görünür; kaynak saati yerine sunucu alım zamanı kullanılır.

Sağlayıcı başına endpoint+model kimliğiyle devre kesici tutulur. 5 dakika içinde 3 **sonlanmış çağrıdaki** geçici bağlantı hatası (timeout/ağ, HTTP 429/5xx) 60 saniye dinlenme başlatır. Sonra tek çağrıya kontrollü deneme verilir; diğerleri beklemek yerine açık hata alır ve varsa normal fallback uygulanır. Başarılı yanıt devreyi kapatır. Kontrollü çağrı başarısızsa yeniden dinlenir. Parola, prompt/400 hatası veya içerik filtresi ağ arızası gibi sayılmaz. Yeniden başlatmada belirsiz deneme otomatik tekrarlanmaz; dinlenmeye döner. Yönetici elle sıfırlayabilir. Görev kaybolmaz; normal hata geçmişi ve açık tekrar seçenekleri korunur. Bu harcama veya yanıt kotası değildir.

Tek uygulama süreci/kalıcı veritabanı varsayılır; aynı SQLite dosyasına birden çok aktif Harbor sunucusu bağlama. Dış ortamda TLS/ağ sınırlarını [yayın kılavuzuna](SECURITY-DEPLOYMENT.md) göre ayrıca kur.

### English

Operations displays queue counts/age, self-reported node health and provider circuit state without automatic API probes. Admin registration returns a one-use-visible key scoped exclusively to writing telemetry for that node. Keys cannot read tasks/settings or execute commands, are hashed at rest and become invalid when disabled/deleted. Restored nodes start disabled. No existing machine is modified automatically.

The optional stdlib Python reporter above reads an owner-only token file via environment settings, requires HTTPS except loopback, refuses redirects and sends a heartbeat every 30 seconds. It reports OS load when available and a default zero job count, not automatically discovered inference/GPU utilization. Custom agents may send the documented finite numeric metrics. Unknown fields/commands are rejected. Heartbeats are self-reported, stamped by server receipt time, unknown until first receipt and stale after 120 seconds.

Per endpoint/model, three completed transient call failures within five minutes open a 60-second cooldown. After that, one controlled trial is admitted; other jobs fail visibly and may use configured fallback. Success closes the circuit; failure reopens it. Network/timeouts and HTTP 429/5xx count, while invalid requests/content outcomes do not. Restart never replays an ambiguous probe. Admins may reset it explicitly. This is resilience control, not a spending/output quota. Use one active Harbor process per SQLite database and validate real TLS/network isolation separately.
