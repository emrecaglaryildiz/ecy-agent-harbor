# HTTP API

JSON istekleri `Content-Type: application/json` ister. Oturum açıldıktan sonra yazma istekleri `/api/state` yanıtındaki `csrf` değerini `X-CSRF-Token` başlığında taşımalıdır. Oturum HttpOnly çerezde tutulur; CORS açık değildir.

| Metot | Yol | Yetki | İşlev |
|---|---|---|---|
| GET | /api/health | anonim | Sağlık ve çalışma modu |
| GET | /api/auth/status | anonim | mode, setup_required |
| POST | /api/setup | yalnızca ilk kurulum, localhost | `{password}`; admin parolası en az 12 karakter |
| POST | /api/login | anonim | `{user: admin/operator/viewer, token: parola}` |
| POST | /api/logout | oturum | Oturumu kapat |
| GET | /api/state | tüm roller | Kullanıcı, csrf, redakte ajanlar, son görev/onaylar, bütçe |
| GET | /api/agents | admin | Düzenlenebilir ayarlar, has_api_key, last_test; anahtar dönmez |
| POST | /api/agents | admin | Ajan oluştur veya id ile güncelle |
| POST | /api/agents/{id}/toggle | admin | `{enabled: true/false}`; pasif/aktif durumunu değiştir |
| POST | /api/agents/{id}/delete | admin | Ajanı kaldır, erişim bilgilerini temizle; geçmiş adını koru |
| POST | /api/history/delete | admin / sahibi operator | `{ids:[...]}` veya `{all:true}`; sadece bitmiş görevler |
| POST | /api/agents/{id}/test | admin | Gerçek model isteği ile bağlantı kontrolü; bütçelenir |
| POST | /api/access/password | admin | `{user,password}`; ilgili rolün oturumlarını kapatır |
| GET | /api/browser/status | oturum | MCP/Node/tarayıcı ve kurulum durumu |
| POST | /api/browser/install | admin | Arka planda sabit sürümlü MCP ve gerekirse Chromium kurulumu |
| POST | /api/tasks | admin/operator | Görev oluştur |
| GET | /api/tasks/{id} | tüm roller | task, runs, logs |
| POST | /api/tasks/{id}/continue | admin/operator | `{run_id}`; eksik yanıtın tam bağlamıyla yeni görev |
| POST | /api/tasks/{id}/cancel | admin veya sahibi operator | Görev iptali |
| GET | /api/tasks/{id}/export?format=md | tüm roller | Markdown attachment |
| GET | /api/tasks/{id}/export?format=pdf | tüm roller | PDF attachment |
| GET | /api/tasks/{id}/export?format=pdf&run={run_id} | tüm roller | Tek ajan raporu; md de desteklenir |
| POST | /api/approvals/{id} | admin | `{decision: approve/reject}` |

Ajan kaydı:

```json
{
  "name": "Yerel uzmanım",
  "role": "Teknik analist",
  "provider": "openai-compatible",
  "model": "sunucundaki-model-kimliği",
  "endpoint": "http://localhost:11434/v1",
  "api_key": "",
  "clear_key": false,
  "instruction": "Kanıta dayalı, kısa bir teknik analiz üret.",
  "hardware": "Yerel GPU düğümü",
  "price": 0,
  "color": "blue",
  "enabled": true,
  "safe_mode": false
}
```

Güncellemek için aynı gövdeye `id` ekle. Boş api_key mevcut anahtarı korur; clear_key true ise kaldırır. API türleri `openai-compatible` ve `hermes-bridge`. Yerel özel ağ adresleri desteklenir; file URL, URL içinde kullanıcı/parola, query/fragment ve bilinen cloud metadata adresi kabul edilmez. Ajanın queued/running görevi varsa güncelleme reddedilir.

Görev oluşturma:

```json
{
  "prompt": "Yerel altyapım için test planı hazırla",
  "mode": "council",
  "agents": ["kayıtlı-ajan-kimliği"],
  "judge": null,
  "budget": 1,
  "tool_requested": false
}
```

single modunda tam bir ajan seçilir ve judge yok sayılır. council modunda 1–4 katılımcı ve isteğe bağlı judge seçilir. Devre dışı veya endpoint'i eksik ajanla görev açılamaz. Yeni ajan kimlikleri sunucu tarafından UUID olarak üretilir; başlangıç kimlikleri hermes/local/research/critic olarak korunur.

Durumlar: queued → running → completed / awaiting_approval / failed. Onay kararı sonrası completed. queued/running/awaiting_approval iptal edilebilir. Run türü participant veya judge olur. Bağlantı testleri görev geçmişine ayrı tek ajan işi olarak kaydedilir.

Hatalar `{error: string}` biçimindedir. 400 doğrulama veya durum çakışması, 401 oturumsuz okuma, 403 yazma yetkisi/CSRF/origin/giriş reddi, 404 bulunamayan kayıt, 500 iç hata. Provider ham hata gövdeleri istemciye gönderilmez. Export endpoint'i JSON yerine binary PDF veya UTF-8 Markdown ve Content-Disposition attachment döndürür.

Bağlam/prompt sınırı 16.000 karakter; JSON gövde sınırı 262.144 bayttır. Silinen görevlerin harcama tutarı içeriksiz deleted_costs tablosunda korunur ve bütçeye katılır. Silinmiş ajan listelenmez ve tekrar göreve atanamaz.

Yeni terminal durum `incomplete` (Yanıt eksik) sağlayıcı üretim sınırı, filtre veya desteklenmeyen tool-call bitişini bildirir. finish_reason run kaydında döner. Yeni normal görevlerde output_limit NULL; kullanıcıya veya sağlayıcıya bir uygulama output cap değeri verilmez.

Görev oluşturma gövdesi `web_enabled: true/false` destekler; varsayılan false. Gerçek MCP hazır değilse internet açık görev reddedilir. /api/state providers alanında endpoint bazında çalışan/çalışmayan/bilinmeyen özetini, browser alanında MCP durumunu döndürür. Tool-call döngüsü modelin kullanım toplamına dahildir.

Harcama takibi kotasızdır: daily_limit=null, usage.cost bugünkü tutar, usage.total_cost tüm zamanların tutarıdır. budget alanı eski istemciler için kabul edilir ancak uygulanmaz. Yeni görevlerde rezerv ve bütçe alanları sıfırdır. Boş görünür metin completed olamaz; empty_response bitişi incomplete durumuna karşılık gelir. GET task run çıktısında can_resume_from_evidence özelliği bulunur, provider_state bulunmaz. continue endpoint'i metinsiz sonuçta varsa kayıtlı model/araç durumunu kullanır ve web_enabled=false olan bir finalleştirme işi açar; web çağrılarını tekrarlamaz.
