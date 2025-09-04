#!/bin/bash
# v5 - Gelişmiş Hata Raporlama ve Sağlamlaştırılmış Sürüm
# YENİLİK: Dosya yükleme işlemi başarısız olduğunda, Telegram API'sinden
# gelen hatayı ve HTTP durum kodunu yakalayıp net bir şekilde loglar.
# Bu, "API Yanıtı: (boş)" gibi belirsiz hataların kök nedenini bulmayı sağlar.

# --- AYARLAR ---
PUBLISH_CHANNEL_ID="-1002477121598"
FILE_SIZE_LIMIT_BYTES=$((48 * 1024 * 1024)) # 48 MB

# --- Dosya Yolları ---
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"
MIS_MODULES_FILE="$HOME/.config/ksu-manager/modules.json"
TELEGRAM_DURUM_DOSYASI="./telegram_durum.txt"
LAST_RUN_FILE="./last_run.txt"

# --- Başlangıç Kontrolleri ---
for cmd in jq curl git grep sed stat; do if ! command -v $cmd &> /dev/null; then echo "HATA: '$cmd' komutu bulunamadı."; exit 1; fi; done
if [ ! -f "$MIS_MODULES_FILE" ]; then echo "HATA: mis modül dosyası bulunamadı: $MIS_MODULES_FILE"; exit 1; fi
if [ ! -f "$MIS_CACHE_MANIFEST" ]; then echo "HATA: mis manifest dosyası bulunamadı: $MIS_CACHE_MANIFEST. 'mis' adımı çalışmamış olabilir."; exit 1; fi
touch "$TELEGRAM_DURUM_DOSYASI"

# --- Planlı Çalışma Kontrolü ---
if [ "$MANUAL_RUN" != "true" ]; then
    TODAY=$(date +%Y-%m-%d)
    if [ -f "$LAST_RUN_FILE" ]; then
        if [[ "$(cat "$LAST_RUN_FILE")" == "$TODAY" ]]; then
            echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Çıkılıyor."
            exit 0
        fi
    fi
fi

echo "-------------------------------------"
echo "Yayıncı Otomasyonu Başlatıldı: $(date)"

jq -r '.modules[] | select(.enabled == true) | .name' "$MIS_MODULES_FILE" | while read -r modul_adi; do
    echo "---"
    echo "[İŞLEM] Modül kontrol ediliyor: $modul_adi"

    guncel_dosya_adi=$(jq -r --arg mod "$modul_adi" '.[$mod] // ""' "$MIS_CACHE_MANIFEST")
    if [ -z "$guncel_dosya_adi" ]; then
        echo "[UYARI] '$modul_adi' için manifest'te kayıt bulunamadı. Atlanıyor."
        continue
    fi
    
    eski_kayit=$(grep "^$modul_adi;" "$TELEGRAM_DURUM_DOSYASI")
    eski_mesaj_id=$(echo "$eski_kayit" | cut -d';' -f2)
    eski_dosya_adi=$(echo "$eski_kayit" | cut -d';' -f3)

    if [ "$guncel_dosya_adi" == "$eski_dosya_adi" ]; then
        echo "[BİLGİ] '$modul_adi' zaten güncel ($guncel_dosya_adi)."
        continue
    fi

    echo "[GÜNCELLEME] '$modul_adi' için yeni sürüm bulundu: $guncel_dosya_adi"
    guncel_dosya_yolu="$MIS_CACHE_DIR/$guncel_dosya_adi"

    if [ ! -f "$guncel_dosya_yolu" ]; then
        echo "[HATA] Dosya manifest'te var ama diskte yok: $guncel_dosya_yolu. Atlanıyor."
        continue
    fi

    if [ -n "$eski_mesaj_id" ]; then
        echo "[TELEGRAM] Eski mesaj siliniyor (ID: $eski_mesaj_id)..."
        curl -s "https://api.telegram.org/bot$BOT_TOKEN_FOR_PUBLISH/deleteMessage?chat_id=$PUBLISH_CHANNEL_ID&message_id=$eski_mesaj_id" > /dev/null
    fi

    dosya_boyutu=$(stat -c%s "$guncel_dosya_yolu")
    API_YANITI=""

    if (( dosya_boyutu > FILE_SIZE_LIMIT_BYTES )); then
        echo "[UYARI] Dosya boyutu (${dosya_boyutu} bayt) Telegram limitini aşıyor. Direkt indirme linki gönderilecek."
        
        module_info=$(jq -r --arg name "$modul_adi" '.modules[] | select(.name == $name) | "\(.type);\(.source)"' "$MIS_MODULES_FILE")
        type=$(echo "$module_info" | cut -d';' -f1)
        source=$(echo "$module_info" | cut -d';' -f2)
        download_url="Bulunamadı"

        if [[ "$type" == "github_release" ]]; then download_url="https://github.com/$source/releases/latest";
        elif [[ "$type" == "github_ci" ]]; then download_url="$source"; fi

        caption="<b>$guncel_dosya_adi</b>\n\n⚠️ Bu dosya Telegram limitlerini aştığı için direkt yüklenemedi.\n\n<a href=\"$download_url\">Buradan İndirin</a>"
        API_YANITI=$(curl -s -X POST "https://api.telegram.org/bot$BOT_TOKEN_FOR_PUBLISH/sendMessage" -d chat_id="$PUBLISH_CHANNEL_ID" -d text="$caption" -d parse_mode="HTML" -d disable_notification="true")
    else
        echo "[TELEGRAM] Yeni dosya '$guncel_dosya_adi' kanala sessizce yükleniyor..."
        
        module_info=$(jq -r --arg name "$modul_adi" '.modules[] | select(.name == $name) | "\(.type);\(.source)"' "$MIS_MODULES_FILE")
        type=$(echo "$module_info" | cut -d';' -f1); source=$(echo "$module_info" | cut -d';' -f2)
        repo_url=""; changelog_url=""
        if [[ "$type" == "github_release" ]]; then
            repo_url="https://github.com/$source"; changelog_url="https://github.com/$source/releases/latest"
        fi

        caption="<b>$guncel_dosya_adi</b>"
        if [ -n "$repo_url" ]; then caption+="\n\n<a href=\"$repo_url\">Ana Depo</a> | <a href=\"$changelog_url\">Değişiklik Kaydı</a>"; fi
        
        # YENİ: Gelişmiş curl komutu ile hata yakalama
        RESPONSE_FILE=$(mktemp)
        HTTP_STATUS=$(curl --silent --write-out "%{http_code}" --output "$RESPONSE_FILE" \
                         -F document=@"$guncel_dosya_yolu" \
                         -F caption="$caption" \
                         -F parse_mode="HTML" \
                         "https://api.telegram.org/bot$BOT_TOKEN_FOR_PUBLISH/sendDocument?chat_id=$PUBLISH_CHANNEL_ID&disable_notification=true")
        API_YANITI=$(cat "$RESPONSE_FILE")
        rm "$RESPONSE_FILE"

        if [ "$HTTP_STATUS" -ne 200 ]; then
            echo "[HATA] Telegram API'sine dosya yüklenirken HTTP $HTTP_STATUS hatası alındı."
        fi
    fi

    yeni_mesaj_id=$(echo "$API_YANITI" | jq -r '.result.message_id')
    if [[ -n "$yeni_mesaj_id" && "$yeni_mesaj_id" != "null" ]]; then
        if [ -n "$eski_kayit" ]; then
            sed -i "/^$modul_adi;/d" "$TELEGRAM_DURUM_DOSYASI"
        fi
        echo "$modul_adi;$yeni_mesaj_id;$guncel_dosya_adi" >> "$TELEGRAM_DURUM_DOSYASI"
        echo "[BAŞARILI] '$modul_adi' güncellendi. Yeni Mesaj ID: $yeni_mesaj_id"
    else
        echo "[HATA] Telegram'a gönderilemedi. API Yanıtı: $(echo $API_YANITI | jq .)"
    fi
done

echo "$(date +%Y-%m-%d)" > "$LAST_RUN_FILE"
echo "-------------------------------------"
echo "Yayıncı Otomasyonu Tamamlandı: $(date)"
echo

