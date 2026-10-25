# MCP araç merkezi / MCP tool center

## Türkçe

Yönetim → Araçlar & MCP → **MCP bağlantısı**. HTTP adresini ve gerekiyorsa erişim anahtarını ekle. Anahtar veritabanında mevcut anahtar kasasıyla şifrelenir; listelerde geri gösterilmez. Bağlantı kapalı kaydedilir. **Bağlan ve araçları keşfet** ile listeyi al, her araç için izin seç, bağlantıyı etkinleştir ve izinleri kaydet.

- **Kapalı:** modele ve elle çalıştırmaya kapalıdır.
- **Elle onayla:** JSON argümanlarını hazırla; ayrı ekranda tam argümanları onaylayınca bir kez çağrılır. Yeni argümanlar yeni onay ister.
- **Görevlerde izin ver:** yalnızca görev girişinde bu bağlantıyı açıkça seçtiğinde model çağırabilir. Her çağrıda tekrar onay sorulmaz. Bunu yalnızca güvendiğin ve davranışını doğruladığın araçlara ver. `readOnlyHint` güvenlik garantisi değildir.

İnternet düğmesi mevcut kısıtlı Playwright tarayıcısını açar. Ek MCP kutuları ayrı bağlantılardır; internet düğmesinin kapalı olması seçilmiş bir MCP'nin ağ erişimini engellemez. Modelin OpenAI uyumlu function calling desteği gerekir. Prompt, gereken araç argümanları ve sonuçlar ilgili sağlayıcı/MCP sunucusuyla paylaşılabilir. MCP metni güvenilmeyen kanıttır; talimatlarına körlemesine uyulmaz. Prompt injection riski tamamen ortadan kalkmaz.

Araç şeması/değişen açıklama yeniden keşfedildiğinde izin kapanır; bağlantı sürümü değişen görevlere eski izin uygulanmaz. Çağrı gönderilmeden önce güncel sürüm ve izin tekrar kontrol edilir. **Gönderilmiş** bir işlem pasife alınsa da uzakta bitmiş olabilir. Kesintide sonuç belirsiz işaretlenir ve otomatik tekrarlanmaz; ağda “tam bir kez” garanti edilemez. Argümanlar ve sonuçlar çağrı geçmişinde saklanır; hassas veri göndermeden önce değerlendir.

### HTTP

Streamable HTTP JSON ve SSE yanıtları desteklenir. Müzakere edilen sürümler: `2025-11-25`, `2025-06-18`, `2025-03-26`, `2024-11-05`. OAuth onboarding, eski ayrı SSE endpoint'i, sampling, elicitation ve roots desteği yoktur. Uyumlu MCP araç sunucusu gerekir; her MCP özelliğinin desteklendiği iddia edilmez.

Genelde HTTPS zorunludur. Özel ağa erişim ayrıca seçilir; düz HTTP yalnızca açıkça izin verilen loopback adresinde kullanılabilir. Metadata/link-local/rezerv adresleri reddedilir; DNS sonucu bağlantıya sabitlenir, yönlendirme izlenmez. Özel ağ seçimi erişilen servisin güvenilir olduğunu kanıtlamaz. Frame boyutu 8 MiB, keşif 200 araç/20 sayfa, çağrı süresi en çok 120 saniyedir; aşım hata verir, sessizce kesmez. Bunlar model yanıt uzunluğu veya harcama kotası değildir.

### Güvenilir stdio profilleri

`MCP_STDIO_MANIFEST` ortam değişkenini bilgisayar sahibinin yönettiği JSON dosyasına ayarla. [Örnek](../deploy/mcp-profiles.example.json) içindeki komutu kurulu, güvendiğin sunucunun mutlak yoluyla değiştir; paketleri resmi kaynaktan kendin kur. UI komut, kabuk veya paket indirme talimatı kabul etmez. Dosya grup/diğer kullanıcılarca yazılabilir olmamalıdır (`chmod 600`). Profil değişiklikleri işletmecinin yetkisindedir; arayüzde yeniden keşfet.

Alt süreç temiz geçici HOME/TMPDIR ile çalışır; yalnızca temel PATH/LANG/SYSTEMROOT ve profildeki `env_keys` aktarılır. Gereksiz anahtarları ekleme. **Bu bir işletim sistemi sandbox'ı değildir:** sunucu aynı kullanıcı yetkisiyle dosya/ağ erişimi yapabilir. Ayrı, düşük yetkili container/kullanıcı kullan; Docker soketi veya uygulamanın veri/anahtar dizinlerini bağlama. Profildeki komutun container içinde de bulunması gerekir. Süre sınırı ve süreç grubu temizliği ayrıcalık sınırı sağlamaz.

## English

Open Management → Tools & MCP, add an HTTP endpoint and optional bearer key, then discover tools. Connections and tools start disabled. Keys are encrypted using the existing application key store and never returned in listings.

Choose **Blocked**, **Manual approval**, or **Allow in tasks** for each tool. Manual calls show exact JSON arguments for a separate one-use approval. Task grants permit automatic model calls only when that connection is explicitly selected for the task. A server's read-only hint is advisory, not enforcement. The Internet toggle controls the existing restricted Playwright browser; selected additional MCP servers have separate access. Use models with compatible function calling. Task content/arguments/results may reach their provider and MCP endpoint. Treat tool content as untrusted; prompt injection remains possible.

Discovery changes reset affected tool grants. Task selection freezes the connection revision; current permissions and revision are checked again before dispatch. Revocation cannot undo already dispatched remote work. Interrupted calls become unknown and are never automatically replayed; exactly-once network execution is not promised. Arguments/results persist in private call history.

The client supports Streamable HTTP JSON/SSE and trusted local stdio profiles, negotiating the four versions listed above. OAuth onboarding, legacy separate SSE endpoints, sampling, elicitation and roots are unsupported. HTTPS is required except explicitly allowed loopback HTTP. Private networking needs explicit opt-in; metadata/link-local/reserved addresses and redirects are denied, with DNS-pinned connections. Frames are capped at 8 MiB, discovery at 200 tools/20 pages, and calls at 120 seconds; violations fail explicitly. These are transport safeguards, not model-output or spending quotas.

For stdio, set `MCP_STDIO_MANIFEST` to an owner-managed JSON file using the example above; use an absolute executable path and secure file permissions. The UI cannot supply arbitrary commands. Only the base environment and explicitly listed `env_keys` reach the child process; HOME/TMPDIR are temporary. **Stdio is not an OS sandbox** and runs with the service user's authority. Use a separate low-privilege container/user and avoid mounting secrets, application data or Docker sockets. Trusted binaries must already exist inside that environment.
