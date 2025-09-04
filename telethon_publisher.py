# -*- coding: utf-8 -*-
# v2.0 - Sağlamlaştırılmış ve Birleştirilmiş Telethon Yayıncısı
# AÇIKLAMA: Bu betik, `module_handler.py` tarafından indirilen ve
# manifest'te listelenen yeni dosyaları Telegram kanalına gönderir.
# Artık bot yerine doğrudan kullanıcı hesabıyla (Telethon) çalışır,
# bu sayede 50MB dosya limiti ortadan kalkar ve ağ hatalarına karşı
# daha dirençli hale gelir.

import os
import json
import asyncio
from datetime import datetime
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# --- YAPILANDIRMA (HASSAS BİLGİLER) ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# --- AYARLAR ---
PUBLISH_CHANNEL_ID = -1002477121598  # Yayın yapılacak kanalın ID'si
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
CONFIG_DIR = os.path.expanduser("~/.config/ksu-manager")
MODULES_FILE = os.path.join(CONFIG_DIR, "modules.json")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")
TELEGRAM_DURUM_DOSYASI = "./telegram_durum.txt"
LAST_RUN_FILE = "./last_run.txt"

def load_json_file(path, description):
    """Bir JSON dosyasını güvenli bir şekilde yükler."""
    if not os.path.exists(path):
        print(f"[HATA] Gerekli '{description}' dosyası bulunamadı: {path}")
        return None
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (json.JSONDecodeError, FileNotFoundError):
        print(f"[HATA] '{description}' dosyası okunamadı veya bozuk: {path}")
        return None

def load_telegram_durum():
    """Telegram durum dosyasını (modul;mesaj_id;dosya_adi) bir sözlüğe yükler."""
    durum = {}
    if not os.path.exists(TELEGRAM_DURUM_DOSYASI):
        # Dosya yoksa oluştur
        open(TELEGRAM_DURUM_DOSYASI, 'a').close()
        return durum
    
    with open(TELEGRAM_DURUM_DOSYASI, 'r', encoding='utf-8') as f:
        for line in f:
            if line.strip():
                parts = line.strip().split(';', 2)
                if len(parts) == 3:
                    durum[parts[0]] = {'message_id': int(parts[1]), 'file_name': parts[2]}
    return durum

def save_telegram_durum(durum):
    """Telegram durum sözlüğünü dosyaya yazar."""
    with open(TELEGRAM_DURUM_DOSYASI, 'w', encoding='utf-8') as f:
        for modul, data in durum.items():
            f.write(f"{modul};{data['message_id']};{data['file_name']}\n")

async def main():
    print("-------------------------------------")
    print(f"Telethon Yayıncı Başlatıldı: {datetime.now()}")

    # Gerekli dosyaları yükle
    modules_data = load_json_file(MODULES_FILE, "Modül Listesi")
    manifest_data = load_json_file(CACHE_MANIFEST, "İndirme Manifestosu")
    
    if not modules_data or not manifest_data:
        print("[HATA] Gerekli modül veya manifest dosyaları olmadan işlem yapılamaz. Çıkılıyor.")
        return

    telegram_durum = load_telegram_durum()
    all_modules = {m['name']: m for m in modules_data.get('modules', [])}

    async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as client:
        for modul_adi, guncel_dosya_adi in manifest_data.items():
            print(f"---\n[İŞLEM] Modül kontrol ediliyor: {modul_adi}")
            
            eski_kayit = telegram_durum.get(modul_adi)
            eski_dosya_adi = eski_kayit['file_name'] if eski_kayit else None

            if guncel_dosya_adi == eski_dosya_adi:
                print(f"[BİLGİ] '{modul_adi}' Telegram'da zaten güncel.")
                continue

            print(f"[GÜNCELLEME] '{modul_adi}' için yeni sürüm bulundu: {guncel_dosya_adi}")
            guncel_dosya_yolu = os.path.join(CACHE_DIR, guncel_dosya_adi)

            if not os.path.exists(guncel_dosya_yolu):
                print(f"[HATA] Dosya manifest'te listeleniyor ancak diskte bulunamadı: {guncel_dosya_yolu}. Atlanıyor.")
                continue

            # Eski mesajı sil
            if eski_kayit:
                print(f"[TELEGRAM] Eski mesaj siliniyor (ID: {eski_kayit['message_id']})...")
                try:
                    await client.delete_messages(PUBLISH_CHANNEL_ID, eski_kayit['message_id'])
                except Exception as e:
                    print(f"[UYARI] Eski mesaj silinemedi (muhtemelen zaten yok): {e}")

            # Caption oluştur
            module_info = all_modules.get(modul_adi, {})
            repo_url, changelog_url = "", ""
            if module_info.get('type') == "github_release":
                source = module_info.get('source', '')
                repo_url = f"https://github.com/{source}"
                changelog_url = f"https://github.com/{source}/releases/latest"
            
            caption = f"<b>{guncel_dosya_adi}</b>"
            if repo_url and changelog_url:
                caption += f"\n\n<a href='{repo_url}'>Ana Depo</a> | <a href='{changelog_url}'>Değişiklik Kaydı</a>"

            # Yeni dosyayı gönder
            print(f"[TELEGRAM] Yeni dosya '{guncel_dosya_adi}' kanala yükleniyor...")
            try:
                yeni_mesaj = await client.send_file(
                    PUBLISH_CHANNEL_ID,
                    guncel_dosya_yolu,
                    caption=caption,
                    parse_mode='html',
                    silent=True
                )
                telegram_durum[modul_adi] = {'message_id': yeni_mesaj.id, 'file_name': guncel_dosya_adi}
                print(f"[BAŞARILI] '{modul_adi}' güncellendi. Yeni Mesaj ID: {yeni_mesaj.id}")
            except Exception as e:
                print(f"[HATA] Dosya yüklenemedi: {e}")

    save_telegram_durum(telegram_durum)
    
    # Son çalışma tarihini güncelle
    with open(LAST_RUN_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))

    print("-------------------------------------")
    print(f"Yayıncı Otomasyonu Tamamlandı: {datetime.now()}")

if __name__ == "__main__":
    if not all([API_ID, API_HASH, SESSION_STRING]):
        raise ValueError("TELEGRAM_API_ID, TELEGRAM_API_HASH ve TELEGRAM_SESSION_STRING ortam değişkenleri ayarlanmalıdır.")
    asyncio.run(main())

