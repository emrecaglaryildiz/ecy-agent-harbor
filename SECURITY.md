# Security policy / Güvenlik politikası

ECY Agent Harbor is a personal shared workspace, not a multi-tenant service. Administrator, operator and viewer are shared role accounts. Project selection does not isolate users or tenants. Do not offer unrelated customers access to the same installation.

ECY Agent Harbor kişisel ve ortak bir çalışma alanıdır. Roller ortak hesaplardır; projeler kiracı/kullanıcı izolasyonu sağlamaz. Birbirine güvenmeyen müşterileri aynı kuruluma alma.

## Supported release / Desteklenen sürüm

Use the latest repository version and current compatible dependency patches. Review the [deployment guide](docs/SECURITY-DEPLOYMENT.md) before external exposure. No release provides a guarantee against compromise. / Güncel sürümü kullan; dış erişim öncesi yayın kılavuzunu uygula. Sıfır risk garantisi yoktur.

## Reporting / Bildirim

Use the repository's private vulnerability reporting feature if enabled. Otherwise contact the repository owner privately before sharing sensitive exploit details; open public issues only for non-sensitive coordination. Never post keys, passwords, live logs or private evidence. / Varsa GitHub özel güvenlik bildirimi kullan; yoksa hassas ayrıntıları yayımlamadan depo sahibiyle özel iletişim kur. Anahtar, parola ve özel log paylaşma.

## Boundaries / Sınırlar

Authentication, CSRF, role checks, encrypted provider/MCP secrets, local bootstrap, MFA, bounded request handling, isolated PDF parsing and per-tool MCP grants are security boundaries. Model/tool output, uploaded files and remote web pages are untrusted. Administrators and locally configured stdio programs are trusted operators. Tool descriptions and read-only annotations are not security controls. / Model, araç ve dosya içeriği güvenilmez girdidir; yerel stdio programları güvenilir işletmeci kodudur.

See [MCP permissions and limitations](docs/MCP.md) and [phased follow-up work](docs/NEXT-PHASES.md).
