#!/bin/bash
# ==============================================================================
# CEPHANELİK UPDATER - v2 GELİŞMİŞ YETKİ VE YAPILANDIRMA TEST BETİĞİ
# ==============================================================================
# YENİLİK: Yayıncı botun sadece metin değil, aynı zamanda dosya gönderme
# (`sendDocument`) yetkisini de test eder. Bu, en sık karşılaşılan hata
# kaynaklarından birini doğrulamak için kritik öneme sahiptir.

# --- Renkler ve Yardımcı Fonksiyonlar ---
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

bilgi() { echo -e "\n${YELLOW}===== $1 =====${NC}"; }
basarili() { echo -e "${GREEN}✓ BAŞARILI:${NC} $1"; }
hata() { echo -e "${RED}✗ HATA:${NC} $1"; exit 1; }
cleanup() { rm -f test_dosyasi.txt; }
trap cleanup EXIT

# --- Başlangıç Kontrolleri ---
if ! command -v curl &> /dev/null || ! command -v jq &> /dev/null; then
    hata "'curl' ve 'jq' komutları bulunamadı. Lütfen önce bunları kurun."
fi

if [[ -z "$BOT_TOKEN_FOR_ARCHIVE" || -z "$BOT_TOKEN_FOR_PUBLISH" ]]; then
    hata "BOT_TOKEN_FOR_ARCHIVE ve BOT_TOKEN_FOR_PUBLISH ortam değişkenleri ayarlanmamış."
fi

echo "======================================================"
echo "         NİHAİ YETKİ VE YAPILANDIRMA TESTİ"
echo "======================================================"

# --- SABİT KANAL ID'LERİ ---
TELEGRAM_ARCHIVE_CHANNEL="-1002542617400"
PUBLISH_CHANNEL_ID="-1002477121598"

# ==================== BÖLÜM 1: ARŞİV BOTU TESTLERİ ====================
bilgi "Bölüm 1: Arşiv Botu Testleri Başlatılıyor..."

bilgi "Adım 1a: Arşiv Botu'nun Token'ı geçerli mi?"
API_YANITI_1A=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_ARCHIVE}/getMe")
if [[ $(echo "$API_YANITI_1A" | jq -r '.ok') != "true" ]]; then
    hata "BOT_TOKEN_FOR_ARCHIVE geçersiz görünüyor. API Yanıtı: $(echo $API_YANITI_1A | jq .)"
fi
BOT_ADI_1=$(echo "$API_YANITI_1A" | jq -r '.result.first_name')
basarili "Arşiv Botu Token'ı geçerli. Bot Adı: $BOT_ADI_1"

bilgi "Adım 1b: Arşiv Botu, Arşiv Kanalı'nı görebiliyor mu?"
API_YANITI_1B=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_ARCHIVE}/getChat?chat_id=${TELEGRAM_ARCHIVE_CHANNEL}")
if [[ $(echo "$API_YANITI_1B" | jq -r '.ok') != "true" ]]; then
    hata "Arşiv Botu, Arşiv Kanalı'na erişemedi. API Yanıtı: $(echo $API_YANITI_1B | jq .). Botun kanala üye olduğundan ve kanal ID'sinin doğru olduğundan emin olun."
fi
KANAL_ADI_1=$(echo "$API_YANITI_1B" | jq -r '.result.title')
basarili "Arşiv Botu, Arşiv Kanalı'nı başarıyla gördü. Kanal Adı: $KANAL_ADI_1"

basarili "Bölüm 1 tamamlandı. Arşiv Botu yapılandırması DOĞRU."

# ==================== BÖLÜM 2: YAYINCI BOTU TESTLERİ ====================
bilgi "Bölüm 2: Yayıncı Botu Testleri Başlatılıyor..."

bilgi "Adım 2a: Yayıncı Botu'nun Token'ı geçerli mi?"
API_YANITI_2A=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/getMe")
if [[ $(echo "$API_YANITI_2A" | jq -r '.ok') != "true" ]]; then
    hata "BOT_TOKEN_FOR_PUBLISH geçersiz görünüyor. API Yanıtı: $(echo $API_YANITI_2A | jq .)"
fi
BOT_ADI_2=$(echo "$API_YANITI_2A" | jq -r '.result.first_name')
basarili "Yayıncı Botu Token'ı geçerli. Bot Adı: $BOT_ADI_2"

bilgi "Adım 2b: Yayıncı Botu, Yayın Kanalı'nı görebiliyor mu?"
API_YANITI_2B=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/getChat?chat_id=${PUBLISH_CHANNEL_ID}")
if [[ $(echo "$API_YANITI_2B" | jq -r '.ok') != "true" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'na erişemedi. API Yanıtı: $(echo $API_YANITI_2B | jq .). Botun kanalda yönetici olduğundan ve kanal ID'sinin doğru olduğundan emin olun."
fi
KANAL_ADI_2=$(echo "$API_YANITI_2B" | jq -r '.result.title')
basarili "Yayıncı Botu, Yayın Kanalı'nı başarıyla gördü. Kanal Adı: $KANAL_ADI_2"

bilgi "Adım 2c: Yayıncı Botu'nun mesaj gönderme yetkisi test ediliyor..."
API_YANITI_2C=$(curl --silent -X POST "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/sendMessage" -d chat_id="${PUBLISH_CHANNEL_ID}" -d text="Bu bir test mesajıdır. Birazdan silinecektir.")
MESAJ_ID_TEXT=$(echo "$API_YANITI_2C" | jq -r '.result.message_id')
if [[ $(echo "$API_YANITI_2C" | jq -r '.ok') != "true" || "$MESAJ_ID_TEXT" == "null" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'na metin mesajı gönderemedi. API Yanıtı: $(echo $API_YANITI_2C | jq .). Botun 'Mesaj Gönderme' iznini kontrol edin."
fi
basarili "Yayıncı Botu, Yayın Kanalı'na metin mesajı gönderebiliyor."
# Metin mesajını hemen silelim
curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/deleteMessage?chat_id=${PUBLISH_CHANNEL_ID}&message_id=${MESAJ_ID_TEXT}" > /dev/null


bilgi "Adım 2d: Yayıncı Botu'nun dosya gönderme yetkisi test ediliyor..."
echo "Bu bir test dosyasıdır." > test_dosyasi.txt
API_YANITI_2D=$(curl --silent -F document=@"test_dosyasi.txt" "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/sendDocument?chat_id=${PUBLISH_CHANNEL_ID}")
MESAJ_ID_DOC=$(echo "$API_YANITI_2D" | jq -r '.result.message_id')
if [[ $(echo "$API_YANITI_2D" | jq -r '.ok') != "true" || "$MESAJ_ID_DOC" == "null" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'na dosya gönderemedi. API Yanıtı: $(echo $API_YANITI_2D | jq .). Botun 'Medya Gönderme' veya benzeri bir izne sahip olduğundan emin olun."
fi
basarili "Yayıncı Botu, Yayın Kanalı'na dosya gönderebiliyor."

bilgi "Adım 2e: Yayıncı Botu'nun mesaj silme yetkisi test ediliyor..."
API_YANITI_2E=$(curl --silent "https://api.telegram.org/bot${BOT_TOKEN_FOR_PUBLISH}/deleteMessage?chat_id=${PUBLISH_CHANNEL_ID}&message_id=${MESAJ_ID_DOC}")
if [[ $(echo "$API_YANITI_2E" | jq -r '.ok') != "true" ]]; then
    hata "Yayıncı Botu, Yayın Kanalı'ndan mesaj silemedi. API Yanıtı: $(echo $API_YANITI_2E | jq .). Botun 'Mesajları Silme' iznini kontrol edin."
fi
basarili "Yayıncı Botu, Yayın Kanalı'ndan mesaj silebiliyor."


basarili "Bölüm 2 tamamlandı. Yayıncı Botu yapılandırması DOĞRU."

echo -e "\n======================================================"
basarili "TÜM TESTLER BAŞARIYLA TAMAMLANDI! Sırlarınız ve bot izinleriniz doğru yapılandırılmış."
echo "======================================================"
