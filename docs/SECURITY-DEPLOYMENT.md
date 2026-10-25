# Güvenli yayın / Secure deployment

## Bu sürümde / In this release

- İlk yönetici kurulumu yerel terminaldeki tek kullanımlık kodu gerektirir. `Host: localhost` veya Forwarded başlığı bu kodun yerine geçmez. Kurulum tamamlanınca uç nokta kapanır. / First enrollment requires a locally held bootstrap code; spoofed headers do not authorize setup.
- TOTP iki adımlı giriş, tekrar kullanım kontrolü ve tek kullanımlık kurtarma kodları. Yönetim → Güvenlik bölümünde kurulur. Gizli anahtarı/kurtarma kodlarını kendin güvenli sakla. / TOTP with replay checks and one-use recovery codes; enroll in Management → Security and store recovery material privately.
- Parola doğrulama eşzamanlılığı, giriş hızı, bağlantı sayısı ve mutlak istek süreleri sınırlandırılır. Bunlar hizmet kaynaklarını korur; harcama kotası değildir. / Bounded password work, login rate, connections and absolute request deadlines protect service resources.
- PDF ayrı süreçte; eşzamanlılık 2, CPU 20 sn, duvar saati 25 sn, metin 32 MiB, çıktı 64 MiB, sayfa 1000. Linux'ta sert bellek sınırı 512 MiB. macOS'ta RSS gözetimi örneklemelidir, sert sınır değildir. Public modda PDF için Linux gerekir veya `PDF_UPLOADS=disabled`. / PDF resource isolation uses hard Linux memory limits; macOS sampling is not a hard boundary.
- Markdown uzak görselleri otomatik istemez; kullanıcı tıklayınca dış bağlantı açılır. CSP yalnızca uygulama ve data görsellerine izin verir. / Remote Markdown images are inert links; CSP blocks automatic external image requests.
- MCP varsayılan kapalı, araç bazlı izin, görev bazlı açık seçim, elle işlemlerde tam argüman onayı ve sürüm kontrolü. / MCP default-deny, explicit selection, exact-argument manual approval and revision checks.

## Yerel kurulum / Local enrollment

1. `./Baslat.command` veya bağımlılıklar hazırsa `.venv/bin/python -m app.server`. / Start locally.
2. İlk kurulum kodunu terminalden al; yalnızca localhost sayfasındaki kurulum formuna gir. HTTP API kodu göstermez. Veri dizinindeki `bootstrap.token` dosyasını paylaşma. / Use the terminal code only in the local setup form; never distribute the bootstrap token.
3. Mevcut kurulumların parolaları ve sağlayıcıları korunur. Yönetim → Güvenlik'ten etkin tüm hesaplara MFA kur; mevcut parola ve yeni doğrulayıcı kodunu hesap sahibi girsin. / Existing credentials/providers remain; enroll every enabled role in MFA.
4. Veritabanı ve master.key birlikte gereklidir. Şifreli taşınabilir yedek al, başka boş dizinde geri yüklemeyi dene. ZIP kaynak paketinde veri ve anahtar bulunmaz. / Back up DB and key securely; test encrypted restore in a clean directory.

## Dış erişim / External exposure

Bu sürüm kendiliğinden port veya tünel açmaz. Şablonu gerçek ortamda doğrulamadan yayınlama. / This release opens no ports/tunnels automatically. Validate the template in the actual deployment environment.

Önerilen ortam: güncel Linux, düşük yetkili container, ayrı HTTPS reverse proxy, yalnızca gerekli ağ erişimi. İlk kurulumu ve MFA'yı aynı kalıcı veri alanında yerelde tamamla. Ardından:

```sh
HARBOR_DOMAIN=harbor.example.com docker compose -f compose.yaml -f compose.public.yaml config
# İnceledikten ve DNS/TLS/ağ kurallarını hazırladıktan sonra / after reviewing DNS/TLS/networking:
HARBOR_DOMAIN=harbor.example.com docker compose -f compose.yaml -f compose.public.yaml up --build -d
```

Alan adını kendine ait adla değiştir. Aynı compose proje adı/kalıcı volume korunmalı. `!reset` destekleyen güncel Docker Compose gerekir. Şablon backend'in host portunu kaldırır; yalnızca gateway 80/443 yayınlar, `/api/setup` kapalıdır. Caddy TLS sağlar. Harici proxy/tünel kullanıyorsan aynı izolasyonu ve setup engelini ayrıca uygula. / Preserve the same Compose project/data volume; use Compose supporting `!reset`. Only gateway ports are published, setup is blocked, and Caddy terminates TLS. Apply equivalent isolation for another proxy/tunnel.

`EXPOSURE_MODE=public` başlangıç kapısı: live mod, tamamlanmış kurulum, HTTPS `PUBLIC_ORIGIN`, `COOKIE_SECURE=1`, tüm etkin parola/token hesaplarında MFA ve PDF için Linux (veya devre dışı) gerektirir. Yeni MFA'sız girişler public modda reddedilir. MFA kapatmak için önce dış erişimi kapatıp yerel moda dön. / Public startup/login enforce enrollment, HTTPS cookies/origin and MFA; disabling MFA requires returning to local mode.

Public mod yalnızca bir uygulama kontrolüdür; güvenlik duvarı değildir. Backend'e doğrudan internet erişimi verme. Proxy'de bağlantı, hız ve gövde sınırlarını koru. Uygulama istemci IP'si için Forwarded başlıklarına güvenmez; ortak proxy IP'si hız limitini paylaşır. Kenarda uygun kullanıcı/IP hız kontrolü ve DDoS koruması gerekir. / Public mode is not a firewall. Block direct backend access and apply edge rate/DDoS controls; the app ignores forwarded IPs, so proxy users share application rate limits.

## Yayın kabul kontrolü / Deployment acceptance

Gerçek Linux container + TLS testi, sertifika yenileme, backend portuna erişilememe, setup reddi, tüm rollerin MFA/CSRF kontrolü, yük/yavaş istek deneyi ve temiz yedek geri yükleme provası tamamlanmalıdır. Bu geliştirme ortamında yerel Docker daemon yoktur; ayrı Linux CI sonucuna aşağıda bak. / Validate Linux containers/TLS, renewal, backend isolation, setup denial, role/MFA/CSRF, load/slow requests and a clean restore. Local Docker is unavailable; see the separate Linux CI acceptance below.

## Kalan riskler / Residual risks

Hiçbir uygulama “hacklenmez” diye garanti edilemez. Prompt injection, hatalı/zararlı MCP sunucusu, aynı yetkili stdio kodu, bağımlılık açıkları, işletim sistemi ve yayın yanlış yapılandırmaları kalır. Markdown kütüphanesi için tüm kötü durum karmaşıklık ve tarayıcı saldırı örnekleri kapsamlı biçimde doğrulanmış değildir. Dosyalar/raporlar ve MCP argüman geçmişi hassas olabilir; disk/yedek erişimini kısıtla. Düzenli güncelleme ve gerçek yayın ortamı testleri gerekir. / No compromise-free guarantee is possible. Prompt injection, trusted-process authority, dependency/OS defects and deployment mistakes remain. Exhaustive vendor Markdown complexity and browser adversarial-corpus assurance is incomplete. Restrict access to reports, call history, disks and backups; update regularly.

## CI container/TLS smoke

GitHub Actions `container-smoke` job runs `python tests/container_smoke.py` in disposable Linux containers. It builds the image, enrolls an ephemeral administrator with MFA, restarts in public mode, checks backend port isolation, trusts only the generated fixture CA for verified local HTTPS, rejects setup/CSRF/recovery replay and extracts a sample PDF. No real API keys or existing data are used. This validates local TLS, not real-domain DNS/ACME renewal, production firewall rules or external load/DDoS behavior. Check the latest [Actions result](https://github.com/emrecaglaryildiz/ecy-agent-harbor/actions) before relying on it.

### 25 Ekim 2026 doğrulama durumu / Validation status, 25 October 2026

macOS ve Linux'ta 175 test geçti. Linux CI; Python/JavaScript, TR/EN ve İstanbul saati kontrollerini ve bağımlılık güvenlik denetimini tamamladı. Ayrı container işi; doğrulanmış yerel HTTPS, MFA/kurtarma kodunun tekrar kullanım reddi, backend port izolasyonu, setup reddi, CSRF, güvenlik başlıkları, PDF metin çıkarımı ve canlılık/hazır olma uçlarını doğruladı. [Doğrulanan kaynak çalışması / Verified source run](https://github.com/emrecaglaryildiz/ecy-agent-harbor/actions/runs/40231506687), commit `2a717f9d1cd235bf878a76f7de490d3ad4991b9d`.

İlk Linux PDF hatası, doğrudan çalıştırılan işleyicinin uygulama klasörünü modül arama yoluna eklemesinden kaynaklandı: `app/platform.py` standart `platform` modülünü gölgeleyebiliyordu. İşleyici artık bu yerel yolu kaldırır; mevcut CPU/bellek/zaman sınırları korunur. Tanılama testi üretimdeki gerçek dosya yolunu çalıştırır. Bağımlılık denetiminin bildirdiği eski şifreleme sürümü yerine `cryptography>=50.0.2,<51` kullanılır; [üretici sürüm notları](https://cryptography.io/en/latest/changelog/#v50-0-2).

175 tests passed on both macOS and Linux. CI also passed JavaScript, language/time checks and the dependency audit. The disposable Linux container passed verified local HTTPS, MFA/recovery replay denial, backend port isolation, setup denial, CSRF, headers, PDF extraction and liveness/readiness checks. The initial PDF failure came from the application directory shadowing standard-library imports; the worker now removes that path without weakening resource limits. The probe exercises the canonical production worker path. The cryptography baseline is updated to 50.0.2 or newer within major version 50.

Gerçek alan adı DNS/ACME yenilemesi, üretim ağı, dış yük/DDoS, disk kapasitesi ve olay müdahalesi henüz bu testlerle doğrulanmış sayılmaz. / Real-domain DNS/ACME renewal, production networking, external load/DDoS, disk capacity and incident response are outside this fixture's acceptance.
