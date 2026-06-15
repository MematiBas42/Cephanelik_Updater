# Cephanelik Updater Guidelines

Bu dosya projenin karar defteridir. Yeni modül eklerken veya akışı değiştirirken önce buradaki kuralları kontrol et. Amaç, aynı hataları tekrar etmemek ve Telegram kanalındaki kullanıcı deneyimini tutarlı tutmaktır.

## Ana amaç

Bu proje GitHub/GitLab/Telegram kaynaklarındaki güncellemeleri takip eder ve ana Telegram kanalında yayınlar. Cron GitHub Actions üzerinde polling yapar. Yeni bir sürüm bulunursa önce kaynak doğrulanır, gerekiyorsa dosya indirilir, sonra Telegram mesajı yayınlanır veya mevcut mesaj düzenlenir.

Temel hedefler:

- Her güncellemede aynı modülü yeniden paylaşmak yerine mevcut kanal mesajını mümkünse düzenlemek.
- APK dosyalarını Telegram'a yüklememek; APK için sadece kaynak/link yayınlamak.
- Bağlı tartışma grubuna kullanıcı hesabı yerine kanal kimliğiyle güncelleme bildirimi göndermek.
- State push edilmeden başarılı yayın yapılmış gibi davranmamak.

## Yayın modeli

Her modül `modules.json` içinde tanımlanır. `state/state.json` ise son yayınlanan sürümü ve Telegram mesaj ID'lerini tutar.

Yayın mantığı:

- Aynı `version_id` görülürse modül güncel kabul edilir.
- Yeni sürüm varsa dosya/link hazırlanır.
- Başarılı Telegram yayını sonrası state güncellenir.
- Eski mesaj varsa mümkünse edit edilir.
- Edit sonrası tartışma grubuna yeni güncelleme bildirimi gönderilir ve sessizce pinlenir.
- Tartışma grubu bildirimi veya pin başarısız olursa pending state tutulur ve sonraki cron tekrar dener.

## APK politikası

APK Telegram'a dosya olarak yüklenmemeli.

Bir modül APK yayınlıyorsa:

```json
"is_apk": true
```

eklenmelidir. Ayrıca asset filtresi APK'yi seçmelidir:

```json
"asset_filter": "(?i)\\.apk$"
```

Kod tarafında `.apk` uzantısı zaten link-only kabul edilir, ama `is_apk: true` niyeti açık eder. APK link-only mesajlarında link preview kapalıdır; kanal daha kompakt kalır.

## Bağlı modüller tek yayın olmalı

Kullanıcı açısından tek ürün gibi görünen şeyler ayrı Telegram gönderilerine bölünmemeli.

Örnekler:

- ViPERFX_RE içinde AIDL ve standart ZIP aynı release'in iki seçeneğidir. Bunlar tek kanal mesajında gösterilir: bir ana dosya + caption içinde diğer ZIP linkleri.
- AccA app ve ACC module birlikte düşünülür. Bunlar ayrı ayrı bağımsız ürün gibi eklenmemelidir. İdeal model tek kanal gönderisi olmalı: ACC ZIP ana modül dosyası, AccA APK ise aynı caption içinde link-only kaynak olarak yer almalı.

Mevcut generic destek aynı GitHub release içindeki çoklu asset'ler içindir:

```json
"asset_group_filter": "(?i)^SomeAsset.*\\.zip$"
```

Farklı repolardan gelen bağlı ürünleri tek gönderide birleştirmek gerekiyorsa önce runtime'a açık bir `linked_release`/`related_release` modeli eklenmelidir. Sadece iki ayrı `enabled: true` modül eklemek, kullanıcı deneyimini böler ve ileride state/mesaj yönetimini karıştırır.

## Stable ve prerelease seçimi

Varsayılan `github_release` akışı GitHub'ın `releases/latest` endpoint'ini kullanır. Bu endpoint normalde draft/prerelease olmayan latest stable release'i döndürür.

Kural:

- Kullanıcı stable istiyorsa `github_release` kullan.
- Kullanıcı özellikle prerelease istiyorsa mevcut akışa özel prerelease desteği eklemeden tahminle ekleme yapma.
- Asset adını GitHub API'den doğrulamadan regex yazma.

## CI ve nightly kaynaklar

`github_ci` modüllerinde önce GitHub Actions API denenir. Artifact bulunamazsa `nightly.link` fallback denenir.

Kural:

- Bir repo stable GitHub Release üretiyorsa `github_release` tercih edilir.
- `nightly.link` kırılgandır; kalıcı modüllerde mümkünse release asset kullanılmalıdır.
- CI artifact ID migrasyonu bilinçli bir tradeoff'tur. Sabit dosya adlarında ilk geçişte sessiz atlama olabilir; state gerçek artifact ID bilmediği için bu tamamen çözülemez.

## Telegram kullanıcı deneyimi

Ana kanal:

- Dosya modülleri ZIP olarak gönderilir.
- APK/link-only modülleri sadece kaynak linkiyle gönderilir.
- Eski mesaj varsa edit edilir; gereksiz yeniden paylaşım yapılmaz.
- Tek normal Telegram mesajı bir dosya taşır. Birden fazla dosya aynı gönderide gerekiyorsa ana dosya + caption linkleri modelini kullan. Albüm/çoklu dosya mesajları edit ve state yönetimini zorlaştırır.

Tartışma grubu:

- Edit sonrası yeni bildirim gönderilir.
- Bildirim kanal kimliğiyle `send_as` gönderilmeye çalışılır.
- Kullanıcı hesabına fallback yapılmaz; izin yoksa pending state tutulur.
- Bildirim sessizce pinlenir.
- Pin başarısızsa `pending_discussion_pin` ile retry yapılır.

## State disiplini

State sadece başarılı işlerden sonra güncellenmelidir.

Kural:

- Telegram yayını başarısızsa manifest güncellenmemeli.
- Edit başarılı ama tartışma bildirimi başarısızsa pending state tutulmalı.
- State dosyası atomik yazılmalı.
- Cron sonrası workflow önce `git pull --rebase --autostash`, sonra state commit/push yapmalıdır.
- Manuel değişikliklerde push öncesi mutlaka remote state çekilmelidir.

## Modül ekleme checklist'i

1. `git status` ve `git diff` ile çalışma ağacını kontrol et.
2. `git pull --ff-only origin main` veya değişiklik varsa `git pull --rebase --autostash origin main` çalıştır.
3. Kaynak release/asset bilgisini GitHub API'den doğrula.
4. Modül tek başına mı, bağlı ürünün parçası mı karar ver.
5. Bağlı ürünse ayrı yayın yapma; önce tek gönderi modeli tasarla.
6. APK ise `is_apk: true` ekle.
7. Regex'i asset adına göre dar yaz.
8. Stable isteniyorsa prerelease kullanma.
9. Şu kontrolleri çalıştır:

```bash
python -c "import json; json.load(open('modules.json')); print('json_ok')"
python -c "import json, main_automation as m; m.validate_modules(json.load(open('modules.json'))['modules']); print('modules_ok')"
python -m py_compile main_automation.py generate_pyrogram.py test_nightly.py
git diff --check
```

10. `__pycache__` oluşursa commit'e alma.
11. Commit öncesi tekrar pull/rebase yap.
12. Açıklayıcı commit mesajı yaz ve pushla.

## Ne zaman kod modeli genişletilmeli?

Sadece `modules.json` eklemek yetmiyorsa runtime modeli genişletilmelidir.

Buna örnek durumlar:

- İki farklı GitHub repo tek Telegram gönderisinde yayınlanmalı.
- Bir APK linki ve bir ZIP dosyası aynı ürünün parçaları olarak gösterilmeli.
- Bir release içindeki birden fazla asset aynı mesajda görünmeli.
- Tartışma grubu UX'i veya pin davranışı state ile takip edilmeli.

Bu durumlarda hızlı JSON eklemek yerine küçük ama açık bir özellik ekle: state alanlarını, retry davranışını ve eski mesaj düzenleme yolunu baştan düşün.
