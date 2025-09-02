#!/bin/bash

# --- GÜVENLİK ---
# Betiğin herhangi bir komutta hata verirse durmasını sağlar.
set -e

# --- AYARLAR ---
# Ayarlar GitHub Actions ortam değişkenlerinden (env) okunur.
BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHANNEL_ID="${TELEGRAM_CHANNEL_ID}"
# GitHub secret'ı GIT_API_TOKEN olarak ayarlandı, mis betiği ise GITHUB_TOKEN bekliyor.
# Bu yüzden gelen değişkeni doğru isme atıyoruz.
GITHUB_TOKEN="${GIT_API_TOKEN}"

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
LAST_RUN_DATE=$(cat "$LAST_RUN_FILE" 2>/dev/null || true)

if [ "$TODAY" == "$LAST_RUN_DATE" ]; then
  echo "[BİLGİ] Otomasyon bugün ($TODAY) zaten çalıştırılmış. Çıkılıyor."
  exit 0
fi

echo "-------------------------------------"
echo "Otomasyon Başlatıldı: $(date)"
# telegram_guncelle.sh'in başına ekleyin
mkdir -p $HOME/.config/ksu-manager
cp ./modules.json $HOME/.config/ksu-manager/modules.json

# 'mis' betiğinin GitHub API limitlerine takılmaması için token'ı dışa aktar
export GITHUB_TOKEN

# --- mis AYARLARI ---
MIS_CONFIG_DIR="$HOME/.config/ksu-manager"
MIS_MODULES_FILE="$MIS_CONFIG_DIR/modules.json"
MIS_CACHE_DIR="$HOME/.cache/ksu-manager"
MIS_CACHE_MANIFEST="$MIS_CACHE_DIR/manifest.json"

# mis betiği için yapılandırma dizinini ve modules.json dosyasını hazırla
if [ -f "./modules.json" ]; then
    echo "[BİLGİ] Depodaki 'modules.json' dosyası kopyalanıyor..."
    mkdir -p "$MIS_CONFIG_DIR"
    cp ./modules.json "$MIS_MODULES_FILE"
else
    echo "[UYARI] Depoda 'modules.json' dosyası bulunamadı. 'mis sync' komutu çalışmayabilir."
fi


# 1. Adım: 'mis sync' ile tüm aktif modülleri kontrol et ve güncellemeleri indir
echo "[BİLGİ] 'mis sync' çalıştırılıyor... Bu işlem tüm güncellemeleri önbelleğe indirecektir."
chmod +x ./mis
./mis sync

echo "[BİLGİ] 'mis sync' tamamlandı. Telegram durumu kontrol ediliyor."

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
    
    # --- YENİ: Zengin Açıklama için Bilgi Toplama ---
    module_json=$(jq --arg name "$modul_adi" '.modules[] | select(.name == $name)' "$MIS_MODULES_FILE")
    module_type=$(echo "$module_json" | jq -r '.type')
    module_source=$(echo "$module_json" | jq -r '.source')

    repo_link=""
    changelog_link=""

    if [ "$module_type" == "github_release" ]; then
        repo_link="https://github.com/$module_source"
        api_response=$(curl -sL -H "Authorization: Bearer $GITHUB_TOKEN" "https://api.github.com/repos/$module_source/releases/latest")
        changelog_link=$(echo "$api_response" | jq -r '.html_url // ""')
    
    elif [ "$module_type" == "gitlab_release" ]; then
        repo_link="https://gitlab.com/$module_source"
        encoded_source=$(echo "$module_source" | sed 's/\//%2F/g')
        api_response=$(curl -sL "https://gitlab.com/api/v4/projects/$encoded_source/releases")
        tag_name=$(echo "$api_response" | jq -r '.[0].tag_name // ""')
        if [ -n "$tag_name" ]; then
          changelog_link="https://gitlab.com/$module_source/-/releases/$tag_name"
        fi

    elif [ "$module_type" == "github_ci" ]; then
        owner_repo=$(echo "$module_source" | cut -d'/' -f4-5)
        branch=$(echo "$module_source" | rev | cut -d'/' -f1 | rev)
        repo_link="https://github.com/$owner_repo"
        changelog_link="https://github.com/$owner_repo/commits/$branch"
    fi

    # Telegram için HTML formatında açıklama oluştur
    if [ -z "$repo_link" ]; then repo_link="https://github.com/404"; fi
    if [ -z "$changelog_link" ]; then changelog_link=$repo_link; fi

    caption=$(cat <<EOF
📦 <code>$guncel_dosya_adi</code>

🔗 <b>Kaynak:</b> <a href="$repo_link">Depo Linki</a>
📋 <b>Değişiklikler:</b> <a href="$changelog_link">Sürüm Notları</a>
EOF
)
    # --- YENİ BÖLÜM SONU ---
    
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
                     -F caption="$caption" \
                     -F parse_mode="HTML" \
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

