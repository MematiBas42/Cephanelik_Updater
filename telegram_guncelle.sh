#!/bin/bash

# --- GÜVENLİK ---
# Betiğin herhangi bir komutta hata verirse durmasını sağlar.
set -e

# --- AYARLAR ---
# Ayarlar GitHub Actions ortam değişkenlerinden (env) okunur.
BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHANNEL_ID="${TELEGRAM_CHANNEL_ID}"
GITHUB_TOKEN="${GITHUB_API_TOKEN}" # mis betiği bu değişkeni kullanacak

# --- DURUM TAKİP DOSYALARI ---
# Bu betiğin durum takip dosyası
TELEGRAM_DURUM_DOSYASI="telegram_durum.txt"
# Günlük çalışma kontrolü için
LAST_RUN_FILE="last_run.txt"

# --- KONTROLLER ---
if [ -z "$BOT_TOKEN" ] || [ -z "$CHANNEL_ID" ]; then
  echo "[HATA] TELEGRAM_BOT_TOKEN veya TELEGRAM_CHANNEL_ID ayarlanmamış." >&2
  echo "Lütfen GitHub repository ayarlarından 'Secrets' kısmını kontrol edin." >&2
  exit 1
fi

if ! command -v jq &> /dev/null; then echo "[HATA] 'jq' komutu bulunamadı." >&2; exit 1; fi
touch "$TELEGRAM_DURUM_DOSYASI" # Dosya yoksa oluştur

# --- GÜNLÜK ÇALIŞMA KONTROLÜ ---
TODAY=$(date -u +%Y-%m-%d)
LAST_RUN_DATE=$(cat "$LAST_RUN_FILE" 2>/dev/null)

if [ "$TODAY" == "$LAST_RUN_DATE" ]; then
  echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Çıkılıyor."
  exit 0
fi

echo "-------------------------------------"
echo "Otomasyon Başlatıldı: $(date)"

# 'mis' betiğinin GitHub API limitlerine takılmaması için token'ı dışa aktar
export GITHUB_TOKEN

# 1. Adım: 'mis sync' ile tüm aktif modülleri kontrol et ve güncellemeleri indir
echo "[BİLGİ] 'mis sync' çalıştırılıyor... Bu işlem tüm güncellemeleri önbelleğe indirecektir."
chmod +x ./mis
./mis sync

echo "[BİLGİ] 'mis sync' tamamlandı. Telegram durumu kontrol ediliyor."

# --- mis AYARLARI (mis betiğinizdeki yollarla aynı olmalı) ---
MIS_CONFIG_DIR="$HOME/.config/ksu-manager"
MIS_MODULES_FILE="$MIS_CONFIG_DIR/modules.json"
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"

if [ ! -f "$MIS_MODULES_FILE" ]; then echo "[HATA] mis modül dosyası bulunamadı: $MIS_MODULES_FILE"; exit 1; fi

# 2. Adım: Aktif modülleri al ve her birini Telegram durumuyla karşılaştır
jq -r '.modules[] | select(.enabled == true) | .name' "$MIS_MODULES_FILE" | while read -r modul_adi; do
    echo "---"
    echo "[İŞLEM] Modül kontrol ediliyor: $modul_adi"

    guncel_dosya_adi=$(jq -r --arg mod "$modul_adi" '.[$mod] // ""' "$MIS_CACHE_MANIFEST")
    if [ -z "$guncel_dosya_adi" ]; then
        echo "[UYARI] '$modul_adi' için manifest dosyasında bir kayıt bulunamadı. Atlanıyor."
        continue
    fi
    
    eski_kayit=$(grep "^$modul_adi;" "$TELEGRAM_DURUM_DOSYASI" || true)
    eski_mesaj_id=$(echo "$eski_kayit" | cut -d';' -f2)
    eski_dosya_adi=$(echo "$eski_kayit" | cut -d';' -f3)

    if [ "$guncel_dosya_adi" == "$eski_dosya_adi" ]; then
        echo "[BİLGİ] '$modul_adi' Telegram'da zaten güncel. İşlem yapılmadı."
        continue
    fi

    echo "[GÜNCELLEME] '$modul_adi' için yeni sürüm bulundu: $guncel_dosya_adi"
    guncel_dosya_yolu="$MIS_CACHE_DIR/$guncel_dosya_adi"
    if [ ! -f "$guncel_dosya_yolu" ]; then
        echo "[HATA] Dosya önbellekte ($MIS_CACHE_DIR) bulunamadı. Atlanıyor."
        continue
    fi

    if [ ! -z "$eski_mesaj_id" ]; then
        echo "[TELEGRAM] Eski mesaj siliniyor (ID: $eski_mesaj_id)..."
        curl -s "https://api.telegram.org/bot$BOT_TOKEN/deleteMessage?chat_id=$CHANNEL_ID&message_id=$eski_mesaj_id" > /dev/null
    fi

    echo "[TELEGRAM] Yeni dosya '$guncel_dosya_adi' kanala sessizce yükleniyor..."
    API_YANITI=$(curl -s -F document=@"$guncel_dosya_yolu" \
                     -F caption="$guncel_dosya_adi" \
                     "https://api.telegram.org/bot$BOT_TOKEN/sendDocument?chat_id=$CHANNEL_ID&disable_notification=true")

    yeni_mesaj_id=$(echo "$API_YANITI" | jq -r '.result.message_id')

    if [ ! -z "$yeni_mesaj_id" ] && [ "$yeni_mesaj_id" != "null" ]; then
        sed -i "/^$modul_adi;/d" "$TELEGRAM_DURUM_DOSYASI"
        echo "$modul_adi;$yeni_mesaj_id;$guncel_dosya_adi" >> "$TELEGRAM_DURUM_DOSYASI"
        echo "[BAŞARILI] '$modul_adi' güncellendi. Yeni Mesaj ID: $yeni_mesaj_id"
    else
        echo "[HATA] Telegram'a yüklenemedi. API Yanıtı: $API_YANITI"
    fi
done

# Başarılı bir çalışmanın sonunda bugünün tarihini kaydet
echo "$TODAY" > "$LAST_RUN_FILE"

echo "-------------------------------------"
echo "Otomasyon Tamamlandı: $(date)"
