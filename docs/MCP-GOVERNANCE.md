# MCP sürüm yönetişimi / MCP revision governance

**Yönetim → Araçlar & MCP → Sürüm geçmişi**: Her bağlantının ayar kaydı, başarılı/başarısız araç keşfi, izin değişikliği ve pasife alma işlemi ayrı sürümdür. Yönetici, sürüm zamanını, işlemi, hesap kodunu, etkinlik durumunu ve araç izinlerini görebilir. Liste 20 sürümde sayfalanır. Mevcut kurulumlar yükseltilirken yalnızca o anki sürüm için `baseline` oluşturulur; geçmişte yapılmış işlemler uydurulmaz. Başlangıç kaydı tekrar oluşturulmaz.

**Management → Tools & MCP → Revision history** shows connection saves, discovery success/failure, policy changes and disabling. Admin-only pages contain 20 revisions. Existing connections receive an explicit migration baseline, with no fabricated earlier history.

## İçerik ve sınırlar / Contents & limits

Sürüm kayıtları yalnızca sunucu kimliği, sürüm, işlem nedeni, hesap kodu, zaman, etkinlik ve araç adı/izin/salt-okunur beyanı/şema ve açıklama SHA256 özetlerini taşır. Adres, erişim anahtarı, şema/açıklama gövdesi, prompt, çağrı argümanı ve araç sonucu bu geçmişe alınmaz. Araç adları uzak sunucunun güvenilmeyen verisidir; arayüzde metin olarak gösterilir. Araç şemasını görmek için mevcut araç tanımını kullan; sürüm geçmişi eski şemanın yeniden oynatılabilen kopyası değildir.

Revision metadata excludes endpoints, credentials, full schemas/descriptions, prompts, arguments and results. Tool names are escaped untrusted text. Fingerprints support comparisons but do not prove a remote tool's behavior. This SQLite journal is administrative history, not an independently anchored or signed integrity log. Host/database administrators remain trusted. The separate [audit trail](AUDIT.md) records administrative events under its own trust limits.

## Eski izin ve çağrılar / Stale grants & calls

- Bağlantı değişikliği araçları ve izinleri sıfırlar; keşif sonrası bağlantı yeniden etkinleştirilmelidir.
- Şema, açıklama veya salt-okunur beyanı değişen araç `blocked` durumuna döner. Beyan bir garanti değildir; görev izni yine yöneticinin açık seçimidir.
- Her keşif bağlantıyı pasif bırakır ve sürümü artırır. Tanımı değişmeyen araçların kayıtlı politikası korunabilir, ancak bağlantı kendiliğinden açılmaz.
- Sürüm değiştiğinde eski sürümdeki `pending` ve `approved` çağrılar reddedilir. Çalışan/gönderilmiş çağrılara tamamlanmış/iptal edilmiş gibi davranılmaz. Dış gönderimden hemen önce mevcut sürüm yeniden kontrol edilir; zaten gönderilmiş bir uzak işlem geri alınamaz.
- Çağrı çalıştırılırken uzaktaki şema, açıklama ve salt-okunur beyanı yeniden karşılaştırılır. Tanım kayması dış çağrıyı engeller; yeni keşif ve izin incelemesi gerekir. Modelin internet anahtarı ayrıca MCP yazma yetkisi vermez.

Connection changes clear tools. Discovery disables the connection and increments its revision. A changed schema, description or read-only hint resets that tool to blocked. A matching definition may retain its recorded policy, but discovery never automatically re-enables the connection. Remote read-only hints are declarations, not an OS or network sandbox.

Revision changes reject stale pending/approved calls; running/dispatched calls retain honest states. A final revision check gates dispatch, and the live tool definition is compared before execution. Already dispatched remote effects cannot be undone. There is no automatic revision rollback, grant replay or remote remediation.

## Eşzamanlı yönetim / Concurrent changes

Yeni arayüz, düzenleme/keşif/politika/pasife alma/elle çağrı için `expected_revision` gönderir; uyuşmazlık HTTP 400 ile reddedilir. Yavaş keşif sırasında başka yönetici bağlantıyı değiştirirse eski keşif sonucu veya hatası yeni ayarı ezmez. Sunucu, kayıt ve politika yazımında da kilit altında sürümü tekrar karşılaştırır. Eski API istemcileri bu isteğe bağlı alanı göndermeden çalışabilir; onlar için ekrandan beri eskime tespiti yoktur, ancak sunucu içindeki eşzamanlı değişiklik kontrolü korunur.

The current UI sends `expected_revision`. Stale edits fail rather than overwrite newer settings. Slow discovery cannot overwrite a concurrent revocation, including on its error path. Legacy clients may omit the optional field for compatibility; server-side mutation races are still guarded, but those clients do not get the UI's earlier-form staleness check.

`GET /api/mcp/history?server_id=<id>&before=<revision>` is admin-only, read-only and paginated. Existing authenticated/CSRF protections apply to mutation endpoints. History does not change or broaden any tool permission.

Bu teslim Faz 14'ün sürüm ve onay geçersizleştirme parçasıdır. Ortam bazlı sırlar, işletim sistemi/ağ izolasyonu, iki ayrı kişinin onayı ve sürümlü görev akışları sonraki parçalardır. / This is Phase 14's revision/history increment; environment secrets, process/network isolation, dual-person approval and versioned playbooks remain later work.
