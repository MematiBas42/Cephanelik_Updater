#!/bin/bash

# Bu betik, mis'i çalıştırır ve güncellenen dosyaları Telegram'a yükler.

# --- AYARLAR ---
# Bu ayarlar artık GitHub Actions ortam değişkenlerinden alınır.
BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHANNEL_ID="${TELEGRAM_CHANNEL_ID}"
RUN_TYPE="${RUN_TYPE:-manual}" # 'auto' veya 'manual' olabilir.

# --- DOSYA YOLLARI ---
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_MODULES_FILE="$HOME/.config/ksu-manager/modules.json"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"
TELEGRAM_DURUM_DOSYASI="telegram_durum.txt" # Yayın kanalının durumunu tutar
LAST_RUN_FILE="last_run.txt"

# --- KONTROLLER ---
if ! command -v jq &> /dev/null; then echo "HATA: 'jq' komutu bulunamadı."; exit 1; fi
touch "$TELEGRAM_DURUM_DOSYASI"

# --- GÜNDE BİR KEZ ÇALIŞMA KONTROLÜ ---
# Sadece otomatik çalıştırmalarda aktif
if [ "$RUN_TYPE" == "auto" ]; then
    TODAY=$(date +%Y-%m-%d)
    if [ -f "$LAST_RUN_FILE" ] && [ "$(cat $LAST_RUN_FILE)" == "$TODAY" ]; then
        echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Çıkılıyor."
        exit 0
    fi
fi

echo "-------------------------------------"
echo "Otomasyon Başlatıldı: $(date)"

# --- 'mis sync' İLE ÖNBELLEĞİ GÜNCELLE ---
info () { echo -e "\033[0;32m[BİLGİ]\033[0m $1"; }
# modules.json dosyasını doğru yere kopyala
mkdir -p "$HOME/.config/ksu-manager"
cp ./modules.json "$MIS_MODULES_FILE"
info "Depodaki 'modules.json' dosyası kopyalandı."

info "'mis sync' çalıştırılıyor..."
# mis betiğine arşiv kanalı bilgisini ortam değişkeniyle aktar
BOT_TOKEN="$BOT_TOKEN" TELEGRAM_ARCHIVE_CHANNEL="$TELEGRAM_ARCHIVE_CHANNEL" ./mis sync

info "'mis sync' tamamlandı. Yayın kanalı kontrol ediliyor."

# --- GÜNCELLENEN DOSYALARI YAYIN KANALINA YÜKLE ---
jq -r '.modules[] | select(.enabled == true) | .name' "$MIS_MODULES_FILE" | while read -r modul_adi; do
    echo "---"
    echo "[İŞLEM] Modül kontrol ediliyor: $modul_adi"

    guncel_dosya_adi=$(jq -r --arg mod "$modul_adi" '.[$mod] // ""' "$MIS_CACHE_MANIFEST")
    if [ -z "$guncel_dosya_adi" ]; then continue; fi
    
    eski_kayit=$(grep "^$modul_adi;" "$TELEGRAM_DURUM_DOSYASI")
    eski_mesaj_id=$(echo "$eski_kayit" | cut -d';' -f2)
    eski_dosya_adi=$(echo "$eski_kayit" | cut -d';' -f3)

    if [ "$guncel_dosya_adi" == "$eski_dosya_adi" ]; then
        echo "[BİLGİ] '$modul_adi' yayın kanalında zaten güncel."
        continue
    fi

    echo "[GÜNCELLEME] '$modul_adi' için yeni sürüm bulundu: $guncel_dosya_adi"
    guncel_dosya_yolu="$MIS_CACHE_DIR/$guncel_dosya_adi"
    if [ ! -f "$guncel_dosya_yolu" ]; then echo "[HATA] Dosya önbellekte bulunamadı."; continue; fi

    # Açıklama metnini oluştur
    module_info=$(jq -r --arg n "$modul_adi" '.modules[] | select(.name==$n)' "$MIS_MODULES_FILE")
    source_url=$(echo "$module_info" | jq -r '.source')
    type=$(echo "$module_info" | jq -r '.type')
    if [[ $type == "github_release" ]]; then
      source_url="https://github.com/$source_url"
    fi
    caption="<b>Modül:</b> ${modul_adi}\n<b>Dosya:</b> <code>${guncel_dosya_adi}</code>\n\n<a href=\"${source_url}\">📄 Kaynak</a>"

    if [ ! -z "$eski_mesaj_id" ]; then
        echo "[TELEGRAM] Eski mesaj siliniyor (ID: $eski_mesaj_id)..."
        curl -s "https://api.telegram.org/bot$BOT_TOKEN/deleteMessage?chat_id=$CHANNEL_ID&message_id=$eski_mesaj_id" > /dev/null
    fi

    echo "[TELEGRAM] Yeni dosya yayın kanalına yükleniyor..."
    API_YANITI=$(curl -s -F document=@"$guncel_dosya_yolu" -F caption="$caption" -F parse_mode="HTML" \
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

# --- ÇALIŞMA TARİHİNİ KAYDET ---
if [ "$RUN_TYPE" == "auto" ]; then
    echo "$TODAY" > "$LAST_RUN_FILE"
fi

echo "-------------------------------------"
echo "Otomasyon Tamamlandı: $(date)"
