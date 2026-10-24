# Gerçek sağlayıcı bağlantısı

Bu proje senin makinelerine bağlanmadı ve mevcut Hermes/GPU/Tunnel kurulumlarını değiştirmedi. Bu belgedeki bağlantılar gelecekte operatörün bilinçli yapılandırması içindir. `.env.example` hiçbir gerçek adres veya anahtar içermez.

## OpenAI uyumlu modeller

`OpenAICompatibleAdapter`, operatör tarafından verilen base URL'ye `/chat/completions` ekler. Base URL API sürüm önekini içermelidir (ör. kendi sunucunun `/v1` yolu). Bu, OpenAI'nin her ürünü veya her model sağlayıcısının native API'si için bir adaptör değildir. Uyumlu olmayan sağlayıcılar yeni adapter gerektirir.

```text
POST <BASE_URL>/chat/completions
Authorization: Bearer <server environment key, if configured>
Content-Type: application/json
```

İstek: `model`, `messages`, çıkış token sınırı olmadan. İnternet kapalıyken araç ilan edilmez. İnternet açık görevlerde izinli web_search/web_open araçları için model → MCP → model döngüsü çalışır. Beklenen yanıt `choices[0].message.content`, isteğe bağlı `usage.prompt_tokens` ve `usage.completion_tokens`. Sağlayıcı bu sözleşmeyi ve isteğe bağlı kullanım alanlarını desteklemelidir. Bağlantı okuma zaman aşımı 90 saniyedir; yanıt boyutu/token sınırı uygulama tarafından konmaz. HTTP yönlendirmeleri izlenmez.

- Yerel inference: `LOCAL_BASE_URL`, `LOCAL_MODEL`. Anahtar kullanmayan güvenilir özel ağ endpoint'i hedeflenir; erişim denetimini inference düğümü/ağ katmanı sağlamalı. Anahtar gerekiyorsa profilin `key_env` alanını operatör değiştirebilir.
- Araştırmacı: `CLOUD_BASE_URL`, `CLOUD_MODEL`, `CLOUD_API_KEY`.
- Judge: `JUDGE_BASE_URL`, `JUDGE_MODEL`, `JUDGE_API_KEY`.

Container içinden `localhost` container'ın kendisidir. Mac host inference servisi için uygun olduğunda `host.docker.internal` kullanılabilir. Başka bir makinedeki inference için operatörün belirlediği özel ağ adresini kullan. Bulut anahtarı taşıyan bağlantılarda HTTPS kullan; Endpoint yalnızca yönetici tarafından ayarlardan kaydedilir; yönetici hesabı bu bağlantının güven sınırıdır.

Normal açılış live moddur. Yönetici girişi sonrası ajan adı, API türü, endpoint, model ve anahtarı ayarlardan kaydet. Anahtarlar şifreli saklanır. Ortam değişkenleri sadece ilk veritabanı başlangıç profilleri ve eski kurulumlarla uyumluluk için korunur. Bağlantı testinde en çok 32 çıktı tokenı istenir; test maliyeti ayrı görev olarak kaydedilir.

## Hermes bridge sözleşmesi

**Bu bridge sözleşmesi projeye özeldir; Hermes Agent'ın varsayılan bir HTTP endpoint'ine sahip olduğu iddia edilmez. Bridge servisi bu başlangıç projesinde uygulanmamıştır.** Hermes kurulumunun sürümüne uygun bir bridge daha sonra ayrıca hazırlanmalı ve izole şekilde test edilmelidir. UI'daki MiniMax M3 adı mevcut kurulumun kullanıcı tarafından verilen etiketidir, API model kimliği garantisi değildir.

```text
POST <HERMES_BRIDGE_URL>/v1/tasks
Authorization: Bearer <HERMES_BRIDGE_TOKEN>
```

```json
{
  "prompt": "Kullanıcının görevi",
  "context": "Diğer ajanların güvenilmeyen çıktıları",
  "tools": [],
  "allow_side_effects": false,
    "model": "MiniMax M3"
}
```

90 saniye içinde senkron sonuç:

```json
{
  "output": "Ajanın metin yanıtı",
  "input_tokens": 250,
  "output_tokens": 400
}
```

Uzun süren asenkron Hermes görevleri için submit/poll/cancel destekleyen başka bir adapter gerekir. Bu sürüm yalnızca yukarıdaki senkron sözleşmeyi bilir. Bridge tarafında kimlik doğrulama, görev izolasyonu, iptal, gerçek araç kapatma ve kaynak sınırları uygulanmalıdır. Ayarlardaki güvenli bridge onayı (veya eski `HERMES_BRIDGE_SAFE_MODE=1` değeri) operatörün bu koşulları uyguladığını belirten açık opt-in'dir; otomatik doğrulama değildir.

Herhangi bir ajan çıktısı güvenilmeyen veri kabul edilir. Baş uzmanın sistem mesajı bu ayrımı belirtir, ancak prompt injection'a karşı mutlak koruma iddiası yoktur. Gerçek koruma araç yetkisinin modelden ayrılması ve onay sınırıdır.

## Yeni düğüm ekleme

`app/server.py` içindeki `AGENTS` listesine benzersiz kimlik, etiket, model, endpoint, key_env, instruction ve örnek price içeren bir kayıt ekle. Gerekirse `app/adapters.py` içinde `complete(agent, prompt, context) -> Result` sözleşmesini uygulayıp `adapter_for` içine yönlendir. Gerçek fiyatlar, usage şeması, token sınırları ve hata davranışını test et. Bu sürüm görev başına en fazla dört katılımcıya izin verir.

Normal görevler çıktı boyutu/token limiti göndermez; bağlantı testindeki özel 32-token sağlık çağrısı bunun dışındadır. JSON response read() tam yanıtı okur; saklama ve judge aktarımı uygulama tarafından kırpılmaz. finish_reason bitiş bilgisini göndermen önerilir.
