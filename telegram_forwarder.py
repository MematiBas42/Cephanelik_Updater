# -*- coding: utf-8 -*-
# v1.0 - Kullanıcı Hesabıyla Çalışan Nihai Yayıncı
# AÇIKLAMA: Bu betik, Telegram Bot API limitlerinden ve sorunlarından kaçınmak için
# bot yerine doğrudan bir kullanıcı hesabı (Telethon aracılığıyla) kullanarak
# indirilen modül dosyalarını yayın kanalına gönderir. Bu yöntem, 50MB dosya
# limiti sorununu ortadan kaldırır ve ağ hatalarına karşı çok daha dayanıklıdır.
# telegram_guncelle.sh betiğinin yerini almıştır.

import os
import json
import asyncio
from telethon.sync import TelegramClient
from telethon.sessions import StringSession
from telethon.errors.rpcerrorlist import MessageDeleteForbiddenError, MessageIdInvalidError

# --- YAPILANDIRMA (HASSAS BİLGİLER) ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# --- AYARLAR ---
PUBLISH_CHANNEL_ID = -1002477121598
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")
MODULES_FILE = os.path.expanduser("~/.config/ksu-manager/modules.json")
TELEGRAM_DURUM_DOSYASI = "./telegram_durum.txt"
LAST_RUN_FILE = "./last_run.txt"
MANUAL_RUN = os.environ.get('MANUAL_RUN') == 'true'

# --- Yardımcı Fonksiyonlar ---
def load_modules():
    try:
        with open(MODULES_FILE, 'r', encoding='utf-8') as f:
            return json.load(f).get('modules', [])
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[HATA] '{MODULES_FILE}' dosyası okunamadı.")
        return []

def load_manifest():
    try:
        with open(CACHE_MANIFEST, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[HATA] '{CACHE_MANIFEST}' dosyası okunamadı.")
        return {}

def load_telegram_durum():
    durum = {}
    if not os.path.exists(TELEGRAM_DURUM_DOSYASI):
        return durum
    try:
        with open(TELEGRAM_DURUM_DOSYASI, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(';', 2)
                if len(parts) == 3:
                    modul_adi, mesaj_id, dosya_adi = parts
                    durum[modul_adi] = {'mesaj_id': int(mesaj_id), 'dosya_adi': dosya_adi}
    except Exception as e:
        print(f"[UYARI] '{TELEGRAM_DURUM_DOSYASI}' okunurken hata: {e}")
    return durum

def save_telegram_durum(durum):
    try:
        with open(TELEGRAM_DURUM_DOSYASI, 'w', encoding='utf-8') as f:
            for modul_adi, data in sorted(durum.items()):
                f.write(f"{modul_adi};{data['mesaj_id']};{data['dosya_adi']}\n")
    except Exception as e:
        print(f"[HATA] '{TELEGRAM_DURUM_DOSYASI}' yazılırken hata: {e}")

# --- Ana İşleyici ---
async def main(client):
    print("-------------------------------------")
    print(f"Telethon Yayıncı Başlatıldı: {asyncio.get_event_loop().time()}")

    modules = load_modules()
    manifest = load_manifest()
    telegram_durum = load_telegram_durum()
    
    if not modules or not manifest:
        print("[HATA] Gerekli modül veya manifest dosyaları olmadan işlem yapılamaz. Çıkılıyor.")
        return

    for module in modules:
        if not module.get('enabled', False):
            continue

        modul_adi = module.get('name')
        print(f"---\n[İŞLEM] Modül kontrol ediliyor: {modul_adi}")

        guncel_dosya_adi = manifest.get(modul_adi)
        if not guncel_dosya_adi:
            print(f"[UYARI] '{modul_adi}' için manifest'te kayıt bulunamadı. Atlanıyor.")
            continue

        eski_kayit = telegram_durum.get(modul_adi, {})
        eski_dosya_adi = eski_kayit.get('dosya_adi')

        if guncel_dosya_adi == eski_dosya_adi:
            print(f"[BİLGİ] '{modul_adi}' zaten güncel ({guncel_dosya_adi}).")
            continue

        print(f"[GÜNCELLEME] '{modul_adi}' için yeni sürüm bulundu: {guncel_dosya_adi}")
        guncel_dosya_yolu = os.path.join(CACHE_DIR, guncel_dosya_adi)

        if not os.path.exists(guncel_dosya_yolu):
            print(f"[HATA] Dosya manifest'te var ama diskte yok: {guncel_dosya_yolu}. Atlanıyor.")
            continue

        # Eski mesajı silme
        eski_mesaj_id = eski_kayit.get('mesaj_id')
        if eski_mesaj_id:
            print(f"[TELEGRAM] Eski mesaj siliniyor (ID: {eski_mesaj_id})...")
            try:
                await client.delete_messages(PUBLISH_CHANNEL_ID, eski_mesaj_id)
            except (MessageDeleteForbiddenError, MessageIdInvalidError):
                print("[UYARI] Eski mesaj silinemedi (muhtemelen zaten silinmiş veya yetki yok). Devam ediliyor.")
            except Exception as e:
                print(f"[HATA] Eski mesaj silinirken beklenmedik hata: {e}")
        
        # Yeni dosyayı gönderme
        repo_url, changelog_url = "", ""
        if module.get('type') == "github_release":
            source = module.get('source', '')
            repo_url = f"https://github.com/{source}"
            changelog_url = f"https://github.com/{source}/releases/latest"

        caption = f"<b>{guncel_dosya_adi}</b>"
        if repo_url:
            caption += f"\n\n<a href='{repo_url}'>Ana Depo</a> | <a href='{changelog_url}'>Değişiklik Kaydı</a>"

        print(f"[TELEGRAM] Yeni dosya '{guncel_dosya_adi}' kanala yükleniyor...")
        try:
            sent_message = await client.send_file(
                PUBLISH_CHANNEL_ID,
                guncel_dosya_yolu,
                caption=caption,
                parse_mode='html',
                silent=True
            )
            telegram_durum[modul_adi] = {'mesaj_id': sent_message.id, 'dosya_adi': guncel_dosya_adi}
            print(f"[BAŞARILI] '{modul_adi}' güncellendi. Yeni Mesaj ID: {sent_message.id}")
        except Exception as e:
            print(f"[HATA] Dosya gönderilemedi: {e}")

    save_telegram_durum(telegram_durum)
    print("-------------------------------------")
    print(f"Telethon Yayıncı Tamamlandı: {asyncio.get_event_loop().time()}")
    print()


if __name__ == "__main__":
    if not all([API_ID, API_HASH, SESSION_STRING]):
        raise ValueError("TELEGRAM_API_ID, TELEGRAM_API_HASH, ve TELEGRAM_SESSION_STRING ortam değişkenleri ayarlanmalıdır.")
    
    # Telethon'un event loop ile ilgili uyarısını bastırmak için
    # asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy()) # Sadece Windows için
    
    client = TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH)
    with client:
        client.loop.run_until_complete(main(client))
