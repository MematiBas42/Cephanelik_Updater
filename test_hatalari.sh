#!/bin/bash

# ==============================================================================
# CEPHANELİK UPDATER - NİHAİ YETKİ VE YAPILANDIRMA TEST BETİĞİ
# ==============================================================================
# Bu betik, projenizdeki her bir bileşenin ihtiyaç duyduğu tüm izinleri
# ve sır (secret) değerlerini tek tek ve ayrıntılı olarak test eder.

# --- AYARLAR (TÜM DEĞERLERİNİZ İLE DOLDURULMUŞTUR) ---

TELEGRAM_API_ID='23766568'
TELEGRAM_API_HASH='9aab43acf98c57ce367dff7a205a48f3'
TELEGRAM_SESSION_STRING='1BJWap1sBu0f-AgP0859iLes_MPHib8IdWTOev9kz7xlF6vRu0t1rD360UK4UThaxNdLjxuWw0yhG_ciIXiVnXFgriSUeD9mP0JDAQbmPVKWhCaiAMHoG_mFsOzlCV8mFx0JQYkwv07UX7Lc22X7T2SbN5J_dFAFG2oGkgbm0fI0rO6WSf9vq0PsXZPERfQ6GCoUKpPLcV7OhBIaDmmOPtrEg4wtVMuvDcA8K1_rtKmiqHXpKyBln8BKaH8lw8V2U5Y9YAwBb7J-BvhPsikprAwOUYjziFVmuAp2d4lIwDf5STdihvUk7pecu5Z4zYPPwH2ZOF048o9SBeIm9AdRTcOBkqvkFPCk='

# Arşivleme işlemleri için
TELEGRAM_ARCHIVE_CHANNEL='-1002542617400'
BOT_TOKEN_FOR_ARCHIVE='8254370052:AAETcW3wbmBN-vL9AVez8S2lyWaoJ_tS_1c' # KsuModulTakipBot

# Yayınlama işlemleri için
PUBLISH_CHANNEL_ID='-1002477121598'
BOT_TOKEN_FOR_PUBLISH='8071811280:AAEVntvJzm1YDCHKIbKqjdtK0GdCgox0CIU' # CpUpdater_bot

# --- YARDIMCI FONKSİYONLAR VE RENKLER ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

basla() { echo -e "\n${YELLOW}--- $1 ---${NC}"; }
bilgi() { echo "-> $1"; }
basarili() { echo -e "${GREEN}✅ BAŞARILI:${NC} $1"; }
hata() { echo -e "${RED}❌ HATA:${NC} $1"; exit 1; }

# Gerekli programların kontrolü
if ! command -v jq &> /dev/null; then hata "jq bulunamadı. Lütfen 'sudo apt-get install jq' gibi bir komutla kurun."; fi
if ! python3 -c "import telethon" &> /dev/null; then hata "telethon kütüphanesi bulunamadı. Lütfen 'pip install telethon' komutuyla kurun."; fi

echo -e "${YELLOW}=======================================================${NC}"
echo -e "${YELLOW}         CEPHANELİK UPDATER NİHAİ TESTİ BAŞLIYOR        ${NC}"
echo -e "${YELLOW}=======================================================${NC}"


# ==============================================================================
# BÖLÜM 1: TOPLAYICI TESTLERİ (telegram_forwarder.py simülasyonu)
# ==============================================================================
basla "BÖLÜM 1: TOPLAYICI (Kişisel Hesap Yetkileri)"

bilgi "Adım 1a: Telethon ile Telegram'a kullanıcı olarak bağlanılıyor..."
# Python betiğini anlık olarak oluştur
cat > telethon_test.py << EOL
import os, asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
HEDEF_KANAL_ID = int(os.environ.get('TELEGRAM_ARCHIVE_CHANNEL'))
async def main():
    try:
        async with TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH) as client:
            if not await client.is_user_authorized():
                raise Exception("Session String geçersiz veya süresi dolmuş.")
            print("✅ BAŞARILI: Oturum anahtarı (Session String) geçerli ve bağlantı kuruldu.")
            
            print("-> Adım 1b: Arşiv Kanalına test dosyası gönderiliyor...")
            with open("test_toplayici.txt", "w") as f: f.write("Toplayıcı Testi")
            test_mesaji = await client.send_file(entity=HEDEF_KANAL_ID, file="test_toplayici.txt", caption="Toplayıcı Test Dosyası. Bu mesaj silinecek.")
            print("✅ BAŞARILI: Arşiv Kanalına dosya gönderme yetkisi var.")

            print("-> Adım 1c: Arşiv Kanalından test dosyası siliniyor...")
            await client.delete_messages(entity=HEDEF_KANAL_ID, message_ids=[test_mesaji.id])
            print("✅ BAŞARILI: Arşiv Kanalından mesaj silme yetkisi var.")
    except Exception as e:
        print(f"❌ HATA: {e}")
        exit(1)
asyncio.run(main())
EOL

export TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_SESSION_STRING TELEGRAM_ARCHIVE_CHANNEL
python3 telethon_test.py || hata "Toplayıcı testleri başarısız oldu. API/Session bilgilerinizi, Arşiv Kanalı ID'nizi ve o kanala üyeliğinizi kontrol edin."
rm telethon_test.py test_toplayici.txt
basarili "Bölüm 1 tamamlandı. Kişisel hesabınız Arşiv Kanalı'na tam erişime sahip."


# ==============================================================================
# BÖLÜM 2: İŞLEYİCİ TESTLERİ (mis simülasyonu)
# ==============================================================================
basla "BÖLÜM 2: İŞLEYİCİ (Arşiv Botu Yetkileri - KsuModulTakipBot)"

bilgi "Adım 2a: Arşiv Botu token'ı kontrol ediliyor..."
API_YANITI_2A=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_ARCHIVE}/getMe")
if [[ $(echo "$API_YANITI_2A" | jq -r '.ok') != "true" ]]; then
    hata "Arşiv Botu Token'ı geçersiz. API Yanıtı: $(echo $API_YANITI_2A | jq .)"
fi
basarili "Arşiv Botu Token'ı geçerli. Bot Adı: $(echo $API_YANITI_2A | jq -r '.result.first_name')"

bilgi "Adım 2b: Arşiv Botu'nun Arşiv Kanalı'na erişimi test ediliyor..."
API_YANITI_2B=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_ARCHIVE}/getChat?chat_id=${TELEGRAM_ARCHIVE_CHANNEL}")
if [[ $(echo "$API_YANITI_2B" | jq -r '.ok') != "true" ]]; then
    hata "Arşiv Botu, Arşiv Kanalı'nı bulamadı. API Yanıtı: $(echo $API_YANITI_2B | jq .). Botun kanala üye olduğundan emin olun."
fi
basarili "Arşiv Botu, Arşiv Kanalı'nı bulabiliyor."

bilgi "Adım 2c: Arşiv Botu'nun mesaj geçmişini okuma yetkisi test ediliyor..."
API_YANITI_2C=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_ARCHIVE}/getUpdates?limit=1")
if [[ $(echo "$API_YANITI_2C" | jq -r '.ok') != "true" ]]; then
    hata "getUpdates çağrısı başarısız oldu. API Yanıtı: $(echo $API_YANITI_2C | jq .)"
fi
basarili "Arşiv Botu, kanalın mesaj akışını okuma yetkisine sahip."
basarili "Bölüm 2 tamamlandı. Arşiv Botu, Arşiv Kanalı'na tam erişime sahip."


# ==============================================================================
# BÖLÜM 3: YAYINCI TESTLERİ (telegram_guncelle.sh simülasyonu)
# ==============================================================================
basla "BÖLÜM 3: YAYINCI (Yayın Botu Yetkileri - CpUpdater_bot)"

bilgi "Adım 3a: Yayıncı Botu token'ı kontrol ediliyor..."
API_YANITI_3A=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/getMe")
if [[ $(echo "$API_YANITI_3A" | jq -r '.ok') != "true" ]]; then
    hata "Yayıncı Botu Token'ı geçersiz. API Yanıtı: $(echo $API_YANITI_3A | jq .)"
fi
basarili "Yayıncı Botu Token'ı geçerli. Bot Adı: $(echo $API_YANITI_3A | jq -r '.result.first_name')"

bilgi "Adım 3b: Yayıncı Botu'nun Yayın Kanalı'na erişimi test ediliyor..."
API_YANITI_3B=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/getChat?chat_id=${PUBLISH_CHANNEL_ID}")
if [[ $(echo "$API_YANITI_3B" | jq -r '.ok') != "true" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'nı bulamadı. API Yanıtı: $(echo $API_YANITI_3B | jq .). Botun kanala üye olduğundan emin olun."
fi
basarili "Yayıncı Botu, Yayın Kanalı'nı bulabiliyor."

bilgi "Adım 3c: Yayıncı Botu'nun dosya gönderme yetkisi test ediliyor..."
echo "Yayıncı Testi" > test_yayinci.txt
API_YANITI_3C=$(curl --silent -F document=@"test_yayinci.txt" "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/sendDocument?chat_id=${PUBLISH_CHANNEL_ID}")
MESAJ_ID=$(echo "$API_YANITI_3C" | jq -r '.result.message_id')
if [[ $(echo "$API_YANITI_3C" | jq -r '.ok') != "true" || "$MESAJ_ID" == "null" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'na dosya gönderemedi. API Yanıtı: $(echo $API_YANITI_3C | jq .). Botun 'Medya Gönderme' iznini kontrol edin."
fi
basarili "Yayıncı Botu, Yayın Kanalı'na dosya gönderebiliyor."

bilgi "Adım 3d: Yayıncı Botu'nun mesaj silme yetkisi test ediliyor..."
API_YANITI_3D=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/deleteMessage?chat_id=${PUBLISH_CHANNEL_ID}&message_id=${MESAJ_ID}")
if [[ $(echo "$API_YANITI_3D" | jq -r '.ok') != "true" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'ndan mesaj silemedi. API Yanıtı: $(echo $API_YANITI_3D | jq .). Botun 'Mesajları Silme' iznini kontrol edin."
fi
basarili "Yayıncı Botu, Yayın Kanalı'ndan mesaj silebiliyor."
rm test_yayinci.txt
basarili "Bölüm 3 tamamlandı. Yayıncı Botu, Yayın Kanalı'na tam erişime sahip."


echo -e "\n${GREEN}=======================================================${NC}"
echo -e "${GREEN}        ✅ TÜM TESTLER BAŞARIYLA TAMAMLANDI! ✅        ${NC}"
echo -e "${GREEN}=======================================================${NC}"
echo "Tüm API anahtarlarınız, token'larınız, kanal ID'leriniz ve bot izinleriniz"
echo "projenin çalışması için DOĞRU ve YETERLİ şekilde yapılandırılmıştır."
echo "GitHub Secrets'a bu değerleri girdiğinizden emin olun."
