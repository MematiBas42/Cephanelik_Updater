#!/bin/bash

# --- AYARLAR ---
# Betiklerin ve yapılandırma dosyalarının yolları
MIS_SCRIPT_PATH="./mis"
GITHUB_REPO_ROOT="." # Betiklerin ve modules.json'ın bulunduğu ana dizin
MODULES_JSON_FILE="$GITHUB_REPO_ROOT/modules.json"

# mis betiğinin kullanacağı yapılandırma ve önbellek yolları
MIS_CONFIG_DIR="$HOME/.config/ksu-manager"
MIS_MODULES_FILE_TARGET="$MIS_CONFIG_DIR/modules.json"
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"

# Telegram ve durum takibi için ayarlar
TELEGRAM_BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHANNEL_ID="${TELEGRAM_CHANNEL_ID}"
TELEGRAM_DURUM_DOSYASI="$GITHUB_REPO_ROOT/telegram_durum.txt"
LAST_RUN_FILE="$GITHUB_REPO_ROOT/last_run.txt"

# --- GÜVENLİK KONTROLLERİ ---
if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$CHANNEL_ID" ]; then
    echo "[HATA] TELEGRAM_BOT_TOKEN veya CHANNEL_ID ortam değişkenleri ayarlanmamış." >&2
    exit 1
fi
if [ -n "$GIT_API_TOKEN" ]; then
    export GITHUB_TOKEN="$GIT_API_TOKEN"
fi

# --- İYİLEŞTİRME: MANUEL ÇALIŞTIRMA KONTROLÜ ---
# 1. Adım: Günlük çalıştırma kontrolü
# Bu kontrol artık sadece zamanlanmış görevlerde (schedule) çalışacak.
# Manuel tetiklemeler (workflow_dispatch) bu kontrolden etkilenmeyecek.
TODAY=$(date +%Y-%m-%d)
TRIGGER_TYPE=$1 # Workflow'dan gelen argüman (schedule, workflow_dispatch, vb.)

if [ "$TRIGGER_TYPE" == "schedule" ]; then
    if [ -f "$LAST_RUN_FILE" ] && [ "$(cat "$LAST_RUN_FILE")" == "$TODAY" ]; then
        echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Zamanlanmış görev atlanıyor."
        exit 0
    fi
fi
# --- İYİLEŞTİRME SONU ---

# Gerekli dosya ve programların kontrolü
if ! command -v jq &> /dev/null; then echo "[HATA] 'jq' komutu bulunamadı." >&2; exit 1; fi
if [ ! -f "$MODULES_JSON_FILE" ]; then echo "[HATA] Depoda 'modules.json' dosyası bulunamadı: $MODULES_JSON_FILE" >&2; exit 1; fi
touch "$TELEGRAM_DURUM_DOSYASI" # Dosya yoksa oluştur

echo "-------------------------------------"
echo "Otomasyon Başlatıldı: $(date)"
echo "[BİLGİ] Tetiklenme türü: ${TRIGGER_TYPE:-Manuel/Bilinmiyor}"

# mis betiğinin çalışması için ortamı hazırla
echo "[BİLGİ] Depodaki 'modules.json' dosyası kopyalanıyor..."
mkdir -p "$MIS_CONFIG_DIR"
cp "$MODULES_JSON_FILE" "$MIS_MODULES_FILE_TARGET"

# 2. Adım: mis sync ile tüm aktif modülleri kontrol et ve güncellemeleri indir
echo "[BİLGİ] 'mis sync' çalıştırılıyor... Bu işlem tüm güncellemeleri önbelleğe indirecektir."
bash "$MIS_SCRIPT_PATH" sync

echo "[BİLGİ] 'mis sync' tamamlandı. Telegram durumu kontrol ediliyor."

# 3. Adım: Aktif modülleri al ve her birini Telegram durumuyla karşılaştır
jq -r '.modules[] | select(.enabled == true) | .name' "$MODULES_JSON_FILE" | while read -r modul_adi; do
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
        echo "[BİLGİ] '$modul_adi' Telegram'da zaten güncel. İşlem yapılmadı."
        continue
    fi

    echo "[GÜNCELLEME] '$modul_adi' için yeni sürüm bulundu: $guncel_dosya_adi"
    
    guncel_dosya_yolu="$MIS_CACHE_DIR/$guncel_dosya_adi"
    if [ ! -f "$guncel_dosya_yolu" ]; then
        echo "[HATA] Dosya önbellekte bulunamadı: $guncel_dosya_yolu. Atlanıyor."
        continue
    fi

    if [ ! -z "$eski_mesaj_id" ]; then
        echo "[TELEGRAM] Eski mesaj siliniyor (ID: $eski_mesaj_id)..."
        curl -s "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/deleteMessage?chat_id=$CHANNEL_ID&message_id=$eski_mesaj_id" > /dev/null
    fi

    # Telegram açıklaması için linkleri hazırla
    module_info=$(jq -r --arg name "$modul_adi" '.modules[] | select(.name == $name) | .source + ";" + .type' "$MODULES_JSON_FILE")
    source_repo=$(echo "$module_info" | cut -d';' -f1)
    source_type=$(echo "$module_info" | cut -d';' -f2)
    
    repo_url=""
    changelog_url=""

    if [[ "$source_type" == "github_release" || "$source_type" == "github_ci" ]]; then
        repo_url="https://github.com/$source_repo"
        changelog_url="https://github.com/$source_repo/releases/latest"
    elif [[ "$source_type" == "gitlab_release" ]]; then
        repo_url="https://gitlab.com/$source_repo"
        changelog_url="https://gitlab.com/$source_repo/-/releases"
    fi

    caption="<b>Modül:</b> <code>$guncel_dosya_adi</code>"
    if [ ! -z "$repo_url" ]; then
        caption+="\n\n<a href=\"$repo_url\">📦 Kaynak Kod</a>"
    fi
    if [ ! -z "$changelog_url" ]; then
        caption+="\n<a href=\"$changelog_url\">📜 Değişiklikler</a>"
    fi

    echo "[TELEGRAM] Yeni dosya '$guncel_dosya_adi' kanala sessizce yükleniyor..."
    API_YANITI=$(curl -s -F document=@"$guncel_dosya_yolu" \
                     -F caption="$caption" \
                     -F parse_mode="HTML" \
                     "https://api.telegram.org/bot$TELEGRAM_BOT_TOKEN/sendDocument?chat_id=$CHANNEL_ID&disable_notification=true")

    yeni_mesaj_id=$(echo "$API_YANITI" | jq -r '.result.message_id')

    if [ ! -z "$yeni_mesaj_id" ] && [ "$yeni_mesaj_id" != "null" ]; then
        if [ ! -z "$eski_kayit" ]; then
            sed -i "/^$modul_adi;/d" "$TELEGRAM_DURUM_DOSYASI"
        fi
        echo "$modul_adi;$yeni_mesaj_id;$guncel_dosya_adi" >> "$TELEGRAM_DURUM_DOSYASI"
        echo "[BAŞARILI] '$modul_adi' güncellendi. Yeni Mesaj ID: $yeni_mesaj_id"
    else
        echo "[HATA] Telegram'a yüklenemedi. API Yanıtı: $API_YANITI"
    fi
done

# Son çalıştırma tarihini kaydet
echo "$TODAY" > "$LAST_RUN_FILE"

echo "-------------------------------------"
echo "Otomasyon Tamamlandı: $(date)"
echo

