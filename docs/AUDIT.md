# Denetim kayıtları / Audit trail

**Yönetim → Denetim kayıtları** yalnızca global yöneticide görünür. İşlem türü, hesap ve sunucunun ürettiği istek kimliğiyle filtreleyebilir, daha eski sayfalara geçebilir, JSONL arşivi ve kontrol noktası indirebilirsin. Tarihler İstanbul saatinde gösterilir. `X-Request-ID` yanıt başlığı, ilgili HTTP isteğini kayıtlarla ilişkilendirir; istemcinin gönderdiği kimlik benimsenmez.

**Management → Audit trail** is restricted to global administrators. Filter by event, account or server-generated request ID, page through older records, and download a JSONL archive or checkpoint. The `X-Request-ID` response header correlates HTTP activity without accepting caller-supplied IDs. Dates use Istanbul time.

## Kaydedilen ve dışarıda bırakılan veriler / Data minimization

Bu sürüm hesap erişimi/MFA değişiklikleri, başarılı giriş/çıkış, ajan/çalışma alanı/proje üyeliği ayarları, görev ve reçete başlatmaları, görev geçmişi silme, MCP hazırlama/onay/sonuç ve yedekleme gibi tanımlı olayları kaydeder. Alanlar: sıra, UTC zamanı, hesap kimliği, görev sahibi hesabı, olay türü, istek kimliği, sabit işlem yolu, opak kayıt/görev kimliği ve zincir hash'leri. Üyelik değişiklikleri proje/hesap kimliği ve sabit `read/write/none` izin kodunu; parola/MFA olayları hedef rol hesabını gösterir. Arka plan işçisi `system`, görev sahibi ise ayrı alanda görünür. Mevcut hesaplar ortak rol hesaplarıdır; bir hesabın kaydı tek bir gerçek kişiyi kanıtlamaz.

Predefined events cover account/MFA changes, successful sign-in/out, agent/workspace/project membership settings, task/playbook dispatch, history deletion, MCP preparation/approval/results and backups. Records contain sequence, UTC time, account/initiator IDs, event, request ID, a canonical route, opaque target/task IDs and chain hashes. Membership targets include project/account IDs and fixed read/write/none grant codes; password/MFA targets identify the affected role account. Background work is attributed to `system` with the initiating task account separately. Shared role accounts do not identify individual humans.

**Parola, API anahtarı, token, prompt, rapor, dosya/log içeriği, araç argümanı/sonucu, serbest metin olay mesajı, URL sorgusu ve IP adresi denetim alanlarına kopyalanmaz.** Bu, mevcut görev çıktılarının ve yürütme loglarının ayrıca tutulmadığı anlamına gelmez. Denetim, sırların tüm olası biçimlerini tanıyan bir maskeleme sistemi değil; yalnızca belirlenmiş alanları yazan ayrı bir veri modelidir. İzin verilmeyen veya başarısız her isteğin tamamı bu ilk sürümde kaydedilmez.

Passwords, keys, tokens, prompts, reports, source content, tool arguments/results, free-text log messages, URL queries and IPs are excluded from audit fields. Existing task outputs and execution logs remain separate. Audit uses an allowlisted schema rather than promising universal secret detection. This first release does not record every denied or failed request.

Kayıtlar görev geçmişinden ayrı ve görev tablosuna silme bağıyla bağlı değildir. Görev silinince ilgili denetim kanıtı kalır. Değişiklik ile denetim olayı aynı veritabanı işlemi içindeyse birlikte commit/rollback edilir. Bazı dış MCP/bağlantı işlemlerinin yerel sonuç olayı sonraki işlemde oluşur; bu kayıtlar uzak bir sistemde ne olduğuna dair inkâr edilemez kanıt değildir. Eski geçmiş geriye dönük uydurulmaz; kayıtlar yükseltmeden itibaren başlar.

Audit events survive task history deletion. Events emitted in a mutation transaction commit or roll back with it; external calls may record their local outcome in a later transaction. They are not non-repudiation evidence of remote execution. Historical activity is not reconstructed; collection starts at upgrade.

## Bütünlük ve sınırları / Integrity and limits

Normal uygulama yollarında kayıt değiştirme/silme API'si yoktur; SQLite tetikleyicileri sıradan UPDATE/DELETE işlemlerini reddeder. Her kayıt önceki hash'i ve sabit JSON alanlarının SHA256 özetini taşır. Arayüz kontrolü sabit bir baş kayda kadar 500 kayıtlık parçalarda zincir tutarlılığına bakar. Arşiv tüm kayıtları aynı baş kayıt sınırına kadar, sunucuda 500 kayıtlık partilerle akıtır; filtrelenmiş ekranın yalnızca görünen sayfasını dışa aktarmaz.

There is no mutation/deletion API for audit records. SQLite triggers reject normal UPDATE/DELETE, and each record chains canonical JSON through SHA256. UI verification checks a fixed head in chunks of 500. Full archive export streams that fixed prefix in server batches of 500, independent of the screen's filters, without loading the entire history into server memory.

**Hash zinciri imza veya harici güvenilir saklama değildir.** Sunucu/veritabanını tamamen yöneten biri tetikleyicileri kaldırıp zinciri yeniden hesaplayabilir. Bu nedenle kontrol noktasını ayrı, güvenilir bir yerde tut. Dış sisteme otomatik sabitleme henüz eklenmedi. Yereldeki iki dosyanın aynı anda değiştirilmesine karşı koruma iddia edilmez. Dolu disk/erişilemeyen denetim deposu ilgili yazma işlemini başarısız kılabilir; otomatik temizlik/silme uygulanmaz, kapasite işletmeci tarafından izlenmelidir.

A hash chain is neither a signature nor independent storage. A fully compromised host/database can remove triggers and rewrite the whole chain. Keep checkpoints in a separate trusted location; automatic off-host anchoring is not shipped. Local files changed together provide no independent assurance. Storage failure can fail the associated mutation; there is no automatic audit purge, so operators must monitor capacity.

## Çevrimdışı kontrol / Offline verification

```bash
python3 -m app.audit_verify harbor-audit.jsonl
python3 -m app.audit_verify harbor-audit.jsonl --checkpoint harbor-audit-checkpoint.json
```

İlk komut yalnızca yerel tutarlılığı kontrol eder. İkinci komut bağımsız saklanmış kontrol noktasının akış kimliği, sıra ve hash'ini de doğrular. Daha eski bir kontrol noktası yeni arşivin bilinen ön ekine bağlanabilir. Arşiv kontrol noktasından eskiyse, eksikse veya farklı bir akışsa doğrulama başarısızdır. Yedekten geri dönüş geçmişin farklı bir dalını oluşturabilir; eski harici kontrol noktalarını koru ve geri yüklemeyi ayrıca belgele.

The first command checks consistency only. An independently held checkpoint anchors a known prefix of a newer archive; an export older than the anchor, incomplete, or from another stream fails. Backup restore may fork history; preserve external checkpoints and document restores separately.

API: `GET /api/audit` (`before`, `limit` 1–100, exact `event`, `actor`, `request_id`); `/api/audit/checkpoint`; `/api/audit/verify?after=0&until=...`; `/api/audit/export`. All require an administrator session. Export is read-only and does not create an audit event or send data to a third-party service.
