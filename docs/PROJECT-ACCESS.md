# Proje üyeliği ve erişim / Project membership & access

## Kullanım / Usage

Yeni projeler **Üyelere özel** oluşturulur. Sahip ve global yönetici daima erişebilir. Proje kartındaki **Üyeler ve erişim** ekranından mevcut hesaplara **Salt okunur** veya **Düzenleme erişimi** verilir; **Üyeliği kaldır** erişimi geri alır. Üyelik yönetimi yalnızca sahip ve yönetici içindir; proje editörü başkalarına izin veremez.

New projects default to **Members only**. Owners and global administrators always have access. Use **Members & access** on the project card to grant existing accounts **Read only** or **Edit access**, or remove membership. Editors cannot grant access. Global viewers remain read only regardless of project grants.

Eski projeler ve **Genel alan** önceki ortak alan davranışını korur. Proje düzenleme ekranından eski bir projeyi üyelere özel yapabilirsin. **Çalışma alanıyla paylaş** seçimi, projeyi bütün oturum açan rollere görünür yapar; operatörlerin kayıt oluşturmasına izin verir. Kayıt sahibine bağlı değiştirme/silme kuralları ayrıca korunur. Genel alana gizli proje kanıtı koyma.

Existing projects and the **General workspace** retain their shared behavior. You may restrict an existing project through its visibility setting. **Share with workspace** makes it readable by every authenticated role and writable by operators, subject to existing record-owner controls. Membership removal cannot hide a workspace-shared project. Keep private evidence inside a members-only project.

## Yetki kapsamı / Enforcement

Sunucu; proje listeleri, dosya içeriği/görseller/arama, görev ayrıntısı ve logları, PDF/Markdown, konuşma geçmişi ve karşılaştırmalar, bulgular/bilgi bankası/olay masası, reçeteler ve korunmuş model denemesi çıktılarında üyeliği doğrular. Şablon ve zamanlama listeleri de filtrelenir. URL veya kayıt kimliğini bilmek izin vermez. Kimliği doğrulanmış ancak izinsiz istekler `403`, oturum gerektiren istekler `401` alır.

The server checks membership for project lists, files/images/search, tasks/logs/exports, conversations/comparisons, findings/knowledge/incidents, playbooks and retained model-trial outputs. Templates and schedules are filtered too. Knowing an ID or URL does not grant access. Authenticated access denials return 403; missing sessions return 401.

Operasyon sayıları ve yönetici dışındaki kullanım toplamları yalnızca erişilebilen mevcut görevlerden hesaplanır. Geçmişte silinmiş görevlerin anonim maliyetleri projeye bağlanamadığından yönetici dışındaki toplamlara eklenmez. Yönetici bütün kullanım muhasebesini görür. Finansal kota uygulanmaz.

Non-admin usage and operation counts cover currently accessible tasks. Anonymous costs from deleted history cannot be attributed to a project and are excluded from non-admin totals; administrators retain complete accounting. No spending quota is added.

## İptal, otomasyon ve dış gönderim / Revocation & outbound work

Üyelik kaldırılınca/düzenleme izni düşünce veya proje özelleştirilince erişimi kalmayan hesapların kuyruktaki/çalışan/onay bekleyen görevleri iptal edilir. Bekleyen dosya ve MCP araç onayları reddedilir. Yeni model çağrısı ve MCP gönderimi öncesinde görev sahibinin güncel izni yeniden kontrol edilir. Zamanlama bir sonraki çalıştırmada izin yoksa devre dışı kalır. Genel Telegram bildirim hattı özel projeleri otomatik göndermez; daha önce kuyruğa girmiş ortak bildirimler de gönderim öncesi kontrol edilir.

Revocation, downgrade or a visibility change cancels affected queued/running/approval-pending tasks and rejects pending artifact/MCP approvals. Provider and MCP boundaries recheck the owner's access. Schedules stop on their next authorization failure. Shared Telegram notifications skip private projects, including notifications queued before a visibility change.

İptal, daha önce dış sağlayıcıya gönderilmiş veriyi veya başlamış çağrının ücretini geri alamaz. Yetki kontrolünü geçmiş bir okuma/çağrı aynı anda sürüyor olabilir. Elde tutulmuş indirilen dosyalar uzaktan silinmez. Yedekleme, global sağlayıcı/MCP yönetimi ve işletim sistemi erişimi global yöneticiye güvenmeye devam eder.

Revocation cannot recall data already sent, stop billing for an in-flight request, or erase downloaded copies. A request that already passed authorization may still finish. Backups, global provider/MCP configuration and host access continue to trust global administrators.

## API

- `POST /api/projects`: `name`, `instruction`, optional `visibility: "private" | "workspace"`; omitted visibility defaults to private for new projects and preserves the current setting on edits.
- `GET /api/projects/members?project_id=...`: owner/admin-only account options and current membership.
- `POST /api/projects/members`: `project_id`, `user_id`, `access: "read" | "write" | "none"`; owner/admin only, normal session and CSRF required.
- `/api/workspace` project rows include `visibility`, effective `access`, and `can_manage`.

## Sınır / Boundary

Bu sürüm mevcut admin/operator/viewer hesaplarına proje üyeliği ekler. Dinamik kullanıcı daveti, kuruluş izolasyonu, SSO/OIDC ve servis hesapları henüz teslim edilmedi. Bu nedenle çok müşterili bir SaaS olarak tanımlanmamalıdır. Dosya deposu veya global altyapı ayarları ayrı tenant'lara bölünmez. / This release adds project membership to the existing admin/operator/viewer accounts. Dynamic invitations, organization isolation, SSO/OIDC and service accounts remain planned. It is not a multi-tenant SaaS; storage and global infrastructure are not tenant-separated.

API regresyonları ve izole mock tarayıcı akışı doğrulanır. Gerçek sağlayıcı anahtarları veya gerçek ekip izinleri test için kullanılmaz. / Acceptance uses API regressions and a disposable mock browser flow, without real provider keys or live team permission changes.
