# Security policy / Güvenlik politikası

ECY Agent Harbor is a personal shared workspace, not a multi-tenant service. Administrator, operator and viewer are shared role accounts. Project selection alone is not an isolation control. Members-only projects now enforce account-level read/write membership; the general area and legacy workspace-shared projects remain shared. Tenant isolation is not implemented. Do not offer unrelated customers access to the same installation.

ECY Agent Harbor kişisel ve ortak bir çalışma alanıdır. Roller ortak hesaplardır. Üyelere özel projelerde hesap bazlı okuma/yazma üyeliği uygulanır; genel alan ve eski ortak projeler paylaşımlıdır. Tenant izolasyonu yoktur. Birbirine güvenmeyen müşterileri aynı kuruluma alma.

## Supported release / Desteklenen sürüm

Use the latest repository version and current compatible dependency patches. Review the [deployment guide](docs/SECURITY-DEPLOYMENT.md) before external exposure. No release provides a guarantee against compromise. / Güncel sürümü kullan; dış erişim öncesi yayın kılavuzunu uygula. Sıfır risk garantisi yoktur.

## Reporting / Bildirim

Use the repository's private vulnerability reporting feature if enabled. Otherwise contact the repository owner privately before sharing sensitive exploit details; open public issues only for non-sensitive coordination. Never post keys, passwords, live logs or private evidence. / Varsa GitHub özel güvenlik bildirimi kullan; yoksa hassas ayrıntıları yayımlamadan depo sahibiyle özel iletişim kur. Anahtar, parola ve özel log paylaşma.

## Boundaries / Sınırlar

Authentication, CSRF, role checks, encrypted provider/MCP secrets, local bootstrap, MFA, bounded request handling, isolated PDF parsing and per-tool MCP grants are security boundaries. Model/tool output, uploaded files and remote web pages are untrusted. Administrators and locally configured stdio programs are trusted operators. Tool descriptions and read-only annotations are not security controls. / Model, araç ve dosya içeriği güvenilmez girdidir; yerel stdio programları güvenilir işletmeci kodudur.

See [MCP permissions and limitations](docs/MCP.md) and [phased follow-up work](docs/NEXT-PHASES.md).

## Project authorization invariants / Proje yetkisi ilkeleri

Members-only project reads, searches, exports, retained snapshots and mutations must enforce current membership. A global viewer must never gain write permission through membership. Permission revocation must block future dispatch and cancel pending work; it cannot recall data already sent. Global administrators remain trusted. A demonstrated cross-project disclosure or unauthorized mutation is reportable; no new finding exclusions or accepted risks are introduced here.

Üyelere özel projenin okuma, arama, indirme, korunmuş çıktı ve değiştirme yolları güncel üyeliği doğrulamalıdır. Global gözlemci proje üyeliğiyle yazma izni kazanamaz. İptal edilen yetki yeni gönderimleri ve bekleyen işleri durdurmalıdır; gönderilmiş veriyi geri çağıramaz. Global yönetici güvenilir kalır. Kanıtlanan projeler arası veri sızıntısı veya yetkisiz değişiklik raporlanabilir; yeni kapsam dışı bulgu veya kabul edilmiş risk eklenmez.

Implementation and limitations: [Project access](docs/PROJECT-ACCESS.md).
