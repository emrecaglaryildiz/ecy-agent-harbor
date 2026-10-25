# Olay masası / Incident desk

## Türkçe

**Olay masası** seçili projenin yüklenmiş metin/log/PDF metnini yerelde inceler. Sağlayıcı, internet ve MCP çağrısı yapmaz. API: `GET /api/incidents/log?project_id=<id veya __general>&file_id=<id>&offset=0&limit=1000`.

Sayfa varsayılan olarak 1.000, en fazla 2.000 kaynak satırı içerir. Tekrar grupları **yalnızca bu sayfada** hesaplanır. Aynı metinden tanınan ilk ISO zaman damgası (ilk 512 karakterde) ve kenar boşlukları çıkarılır; IP, kullanıcı, sayısal kimlikler ve kalan metin değiştirilmez. Böylece farklı kullanıcılar veya adresler tek olay diye birleştirilmez. Bu, anlamsal kümeleme veya saldırı tespiti değildir. Hash gruplaması orijinal kaynak metnini değiştirmez.

Açık saat dilimi olan ISO zamanları UTC'ye çevrilir; uygulama İstanbul saatinde gösterir. Saat dilimi olmayan/bozuk/tanınmayan zamanlara İstanbul veya başka bir dilim **varsayılmaz**; zamanı belirsiz satır sayısı ayrıca gösterilir. Gruplar bilinen ilk zamana göre sıralanır, bilinmeyenler sonradır. Kaynak satır numaraları ham dosyayla aynıdır. Log seviyesi yalnızca ilk 512 karakterdeki yaygın seviye sözcüğüdür; önem veya tehdit puanı değildir. 1.000 karakteri aşan grup metni açıkça kısaltılmış önizleme olarak işaretlenir; ham kaynak korunur.

**Bulgu taslağı oluştur** ham kaynak ve satır bağlantılı bir düzenleme formu açar. Kaydetmeden bulgu oluşmaz. Taslak varsayılan olarak bilgi önemindedir; insan başlık, kaynak, önem ve durumunu kontrol etmelidir. Kaynak satırları arasında başka olaylar varsa verilen aralık bunları da içerebilir; tüm eşleşen satır numaraları ayrıca listelenir.

Bulgu güven düzeyini sahibi/yönetici `değerlendirilmedi/düşük/orta/yüksek` olarak kaydeder. Bu bir model doğruluk puanı değildir. Her değerlendirmede aktör ve zaman kaydedilir. `POST /api/incidents/confidence` → `finding_id`, `confidence`.

Aksiyonlar `önerildi → onaylandı → tamamlandı` akışını izler; önerilmiş/onaylanmış aksiyondan vazgeçilebilir. Atlanan veya eski duruma dayalı geçiş reddedilir. **Onaylamak dış sistemde işlem yapmaz**; bu bir insan iş listesidir. Aksiyonun sahibi/yönetici değiştirebilir, gözlemci yalnızca okuyabilir. Değişiklik geçmişi saklanır. `POST /api/incidents/actions` → `finding_id`, `title`; `POST /api/incidents/actions/transition` → `id`, `expected_status`, `status`.

Bulgu zaman çizelgesi son 200 kayıtlı bulgu değişikliğini gösterir. Log olayları ile uygulama bulgu değişiklikleri farklı zaman çizelgeleridir. Kanıtın gerçek oluşma zamanı bulunamadığında uygulama kaydetme zamanı yerine konmaz. Tablolar şifreli yedeğe dahildir; ortak kişisel çalışma alanının mevcut erişim kuralları geçerlidir.

## English

The **Incident desk** reviews uploaded project text/log/PDF text locally, without provider, Internet or MCP calls. The log API above returns a default 1,000-line page, capped at 2,000 lines. Repetition counts apply only to that page, not the whole file.

Grouping removes the first recognized ISO timestamp within the first 512 characters and edge whitespace, while retaining IPs, users, IDs and other text. It is exact textual grouping, not semantic clustering or attack detection. Explicit timezone offsets are normalized to UTC and displayed in Istanbul time. Naive, invalid and unrecognized timestamps remain unknown; no timezone is guessed. Raw source and matching line numbers remain available. Long previews are explicitly marked shortened. Detected log-level words are not threat/severity scores.

**Draft finding** opens an editable, source-linked form; saving is a separate human action. Drafts start at informational severity. The first-to-last source range may contain intervening unrelated records; all matching line numbers are also shown.

Owners/admins can assign a finding confidence of unassessed/low/medium/high with actor/time provenance. This is a human assessment, not a model accuracy score. Actions require proposed → approved → done transitions, with dismissal available before completion. Stale or skipped transitions are rejected and each transition is recorded. Approval updates tracking state only: no external remediation runs. Viewers cannot write.

The finding timeline shows the latest 200 finding changes, separately from log event times. Unknown evidence times are never replaced with application insertion times. Metadata/actions use the existing shared personal-workspace role model and are included in encrypted backups. API routes and request fields are listed in the Turkish section above.
