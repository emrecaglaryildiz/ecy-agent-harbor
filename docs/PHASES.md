# ECY Agent Harbor — geliştirme fazları

1. Canlı sağlayıcı akışı, iptal, kaydedilmiş adımı yeniden deneme, açık seçimle yedek ajan.
2. Projeler, dosya yükleme, satır numaralı kaynak görünümü, PDF/metin/görsel girdileri ve tüm parçaları kapsayan log analizi.
3. Görev şablonları, bağımsız uzman değerlendirmesi → karşılıklı eleştiri → baş uzman sentezi, kaynak envanteri.
4. Kalıcı zamanlanmış görevler, kaçırılmış çalışmaları üst üste yığmadan tekrar planlama, şifrelenmiş Telegram ayarı ve teslimat geçmişi.

Her faz testlerle doğrulanır. Eski veriler için eklemeli SQLite geçişleri kullanılır. Gerçek kullanıcı anahtarlarıyla test çağrısı veya Telegram gönderimi yapılmaz. Harcama takibi sürer, finansal kota uygulanmaz. Modelin kendi bağlam/çıktı sınırı kaldırılmış sayılmaz; büyük girdiler bölünür ve ara çıktılar korunur.

Durum: dört faz uygulandı. 76 otomatik test geçti. Ayrı mock çalışma alanında proje/dosya seçimi, şablon kaydetme, zamanlama oluşturma/duraklatma, kaynak atfından log satırına dönüş ve responsive görünüm kontrol edildi. Gerçek kullanıcı API anahtarlarıyla sağlayıcı çağrısı veya Telegram mesajı gönderilmedi. Docker yapılandırması kontrol edilir; bu makinede daemon yoksa container çalışması ayrıca doğrulanmış sayılmaz.

Bu belge ilk dört fazın tarihsel teslim kaydıdır; güncel kabul durumu için [NEXT-PHASES.md](NEXT-PHASES.md), kurumsal devam için [ENTERPRISE-ROADMAP.md](ENTERPRISE-ROADMAP.md) dosyasına bak. / This records the original four phases; use those linked roadmaps for current status and enterprise continuation.
