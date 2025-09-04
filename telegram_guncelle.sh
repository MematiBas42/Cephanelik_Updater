#!/bin/bash
# Adli Tıp Loglama Modu

# --- AYARLAR ---
PUBLISH_CHANNEL_ID="-1002477121598"

# --- Dosya Yolları ---
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"
MIS_MODULES_FILE="$HOME/.config/ksu-manager/modules.json"
TELEGRAM_DURUM_DOSYASI="./telegram_durum.txt"
LAST_RUN_FILE="./last_run.txt"

if ! command -v jq &> /dev/null; then echo "HATA: 'jq' komutu bulunamadı."; exit 1; fi
if [ ! -f "$MIS_MODULES_FILE" ]; then echo "HATA: mis modül dosyası bulunamadı: $MIS_MODULES_FILE"; exit 1; fi
touch "$TELEGRAM_DURUM_DOSYASI"

if [ "$MANUAL_RUN" != "true" ]; then
    TODAY=$(date +%Y-%m-%d)
    if [ -f "$LAST_RUN_FILE" ]; then
        LAST_RUN_DATE=$(cat "$LAST_RUN_FILE")
        if [ "$LAST_RUN_DATE" == "$TODAY" ]; then
            echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Çıkılıyor."
            exit 0
        fi
    fi
fi

echo "-------------------------------------"
echo "Otomasyon Başlatıldı: $(date)"

jq -r '.modules[] | select(.enabled == true) | .name' "$MIS_MODULES_FILE" | while read -r modul_adi; do
    echo "---"
    echo "[İŞLEM] Modül kontrol ediliyor: $modul_adi"

    guncel_dosya_adi=$(jq -r --arg mod "$modul_adi" '.[$mod] // ""' "$MIS_CACHE_MANIFEST")
    if [ -z "$guncel_dosya_adi" ]; then
        echo "[UYARI] '$modul_adi' için manifest dosyasında bir kayıt bulunamadı. Atlanıyor."
        continue
    fi
    
    eski_kayit=$(grep "^$modul_adi;" "$TELEGRAM_DURUM_DOSYASI")
    eski_mesaj_id=$(echo "$eski_kayit" | cut -d';' -f2)
    eski_dosya_adi=$(echo "$eski_kayit" | cut -d';' -f3)

    if [ "$guncel_dosya_adi" == "$eski_dosya_adi" ]; then
        echo "[BİLGİ] '$modul_adi' Telegram'da zaten güncel ($guncel_dosya_adi). İşlem yapılmadı."
        continue
    fi

    echo "[GÜNCELLEME] '$modul_adi' için yeni sürüm bulundu: $guncel_dosya_adi"
    guncel_dosya_yolu="$MIS_CACHE_DIR/$guncel_dosya_adi"
    if [ ! -f "$guncel_dosya_yolu" ]; then
        echo "[HATA] Dosya önbellekte bulunamadı: $guncel_dosya_yolu. Atlanıyor."
        continue
    fi

    module_info=$(jq -r --arg name "$modul_adi" '.modules[] | select(.name == $name) | "\(.type);\(.source)"' "$MIS_MODULES_FILE")
    type=$(echo "$module_info" | cut -d';' -f1)
    source=$(echo "$module_info" | cut -d';' -f2)
    repo_url=""
    changelog_url=""
    if [[ "$type" == "github_release" ]]; then
        repo_url="https://github.com/$source"
        changelog_url="https://github.com/$source/releases/latest"
    fi

    caption="<b>$guncel_dosya_adi</b>"
    if [ -n "$repo_url" ]; then
        caption+="\n\n<a href=\"$repo_url\">Ana Depo</a> | <a href=\"$changelog_url\">Değişiklik Kaydı</a>"
    fi

    if [ ! -z "$eski_mesaj_id" ]; then
        echo "[TELEGRAM] Eski mesaj siliniyor (ID: $eski_mesaj_id)..."
        curl -s "https://api.telegram.org/bot$BOT_TOKEN_FOR_PUBLISH/deleteMessage?chat_id=$PUBLISH_CHANNEL_ID&message_id=$eski_mesaj_id" > /dev/null
    fi

    echo "[TELEGRAM] Yeni dosya '$guncel_dosya_adi' kanala sessizce yükleniyor..."
    # GÜNCELLEME: -v (verbose) parametresi ile daha detaylı curl çıktısı alınır.
    API_YANITI=$(curl --verbose -F document=@"$guncel_dosya_yolu" \
                     -F caption="$caption" \
                     -F parse_mode="HTML" \
                     "https://api.telegram.org/bot$BOT_TOKEN_FOR_PUBLISH/sendDocument?chat_id=$PUBLISH_CHANNEL_ID&disable_notification=true")

    echo "[DEBUG] Alınan Ham API Yanıtı:"
    echo "$API_YANITI"
    echo "--- Yanıt Sonu ---"

    yeni_mesaj_id=$(echo "$API_YANITI" | jq -r '.result.message_id')
    if [ ! -z "$yeni_mesaj_id" ] && [ "$yeni_mesaj_id" != "null" ]; then
        if [ ! -z "$eski_kayit" ]; then
            sed -i "/^$modul_adi;/d" "$TELEGRAM_DURUM_DOSYASI"
        fi
        echo "$modul_adi;$yeni_mesaj_id;$guncel_dosya_adi" >> "$TELEGRAM_DURUM_DOSYASI"
        echo "[BAŞARILI] '$modul_adi' güncellendi. Yeni Mesaj ID: $yeni_mesaj_id"
    else
        echo "[HATA] Telegram'a yüklenemedi."
    fi
done

echo "$(date +%Y-%m-%d)" > "$LAST_RUN_FILE"
echo "-------------------------------------"
echo "Otomasyon Tamamlandı: $(date)"
echo

