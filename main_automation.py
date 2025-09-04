# -*- coding: utf-8 -*-
# ==============================================================================
# CEPHANELİK UPDATER - vFINAL - TEK DOSYALI NİHAİ OTOMASYON BETİĞİ
# ==============================================================================
# AÇIKLAMA: Bu betik, projenin tüm işlevselliğini (modül indirme ve yayınlama)
# tek bir dosyada birleştirir. Sade, performanslı ve son derece sağlam bir yapı
# sunmak üzere tasarlanmıştır.

import os
import json
import asyncio
import requests
import re
import shutil
from urllib.parse import quote_plus
from datetime import datetime
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# --- SABİTLER VE YAPILANDIRMA ---
# Hassas bilgiler GitHub Actions sırlarından (Secrets) alınır.
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

# Proje ayarları
PUBLISH_CHANNEL_ID = -1002542617400
STATE_DIR = "./state"
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
MANIFEST_FILE = os.path.join(STATE_DIR, "manifest.json")
TELEGRAM_DURUM_FILE = os.path.join(STATE_DIR, "telegram_durum.txt")
LAST_RUN_FILE = os.path.join(STATE_DIR, "last_run.txt")


class StateManager:
    """Projenin durumunu (manifest, telegram durumu vb.) yöneten sınıf."""
    def __init__(self, state_dir):
        self.state_dir = state_dir
        print(f"[BİLGİ] Durum dizini '{self.state_dir}' olarak ayarlandı.")
        os.makedirs(self.state_dir, exist_ok=True)

    def _load_json(self, path):
        if not os.path.exists(path):
            return {}
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

    def _save_json(self, path, data):
        with open(path, 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, sort_keys=True)

    def load_manifest(self):
        print(f"[BİLGİ] Manifest dosyası okunuyor: {MANIFEST_FILE}")
        return self._load_json(MANIFEST_FILE)

    def save_manifest(self, data):
        print("[BİLGİ] Manifest dosyası kaydediliyor...")
        self._save_json(MANIFEST_FILE, data)

    def load_telegram_durum(self):
        print(f"[BİLGİ] Telegram durum dosyası okunuyor: {TELEGRAM_DURUM_FILE}")
        durum = {}
        if not os.path.exists(TELEGRAM_DURUM_FILE):
            return durum
        with open(TELEGRAM_DURUM_FILE, 'r', encoding='utf-8') as f:
            for line in f:
                parts = line.strip().split(';', 2)
                if len(parts) == 3:
                    durum[parts[0]] = {'message_id': int(parts[1]), 'file_name': parts[2]}
        return durum

    def save_telegram_durum(self, durum):
        print("[BİLGİ] Telegram durum dosyası kaydediliyor...")
        with open(TELEGRAM_DURUM_FILE, 'w', encoding='utf-8') as f:
            for modul, data in sorted(durum.items()):
                f.write(f"{modul};{data['message_id']};{data['file_name']}\n")


class ModuleHandler:
    """Modülleri farklı kaynaklardan bulan ve indiren sınıf."""
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager
        self.manifest = self.state_manager.load_manifest()
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _get_api_call(self, url):
        headers = {"Authorization": f"Bearer {GIT_API_TOKEN}"} if "api.github.com" in url else {}
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json() if 'application/json' in response.headers.get('Content-Type', '') else response.text
        except requests.exceptions.RequestException as e:
            print(f"[HATA] API çağrısı başarısız: {url} - {e}")
            return None

    async def _get_telegram_remote_file(self, module):
        channel = module['source_channel']
        keyword = module['source']
        try:
            async for message in self.client.iter_messages(channel, limit=100):
                if message.document and hasattr(message.document.attributes[0], 'file_name') and keyword.lower() in message.document.attributes[0].file_name.lower():
                    return {
                        'file_name': message.document.attributes[0].file_name,
                        'source_type': 'telegram',
                        'source_url': f"https://t.me/{message.chat.username}/{message.id}",
                        'date': message.date.strftime("%d.%m.%Y"),
                        'downloader': lambda path: self.client.download_media(message, path)
                    }
            return None
        except Exception as e:
            print(f"[HATA] Telegram kanalı @{channel} işlenirken hata: {e}")
            return None

    def _get_github_release_remote_file(self, module):
        url = f"https://api.github.com/repos/{module['source']}/releases/latest"
        data = self._get_api_call(url)
        if not isinstance(data, dict) or 'assets' not in data: return None
        asset = next((a for a in data['assets'] if re.search(module['asset_filter'], a['name'])), None)
        if asset:
            return {
                'file_name': asset['name'], 'source_type': 'github_release',
                'source_url': data.get('html_url', '#'),
                'date': datetime.strptime(asset['updated_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y"),
                'downloader': lambda path: self._download_file(asset['browser_download_url'], path)
            }
        return None

    def _get_github_ci_remote_file(self, module):
        content = self._get_api_call(module['source'])
        if not content or not isinstance(content, str): return None
        match = re.search(r'https://nightly\.link/[^"]*\.zip', content)
        if match:
            url = match.group(0)
            return {
                'file_name': os.path.basename(url), 'source_type': 'github_ci',
                'source_url': module['source'], 'date': datetime.now().strftime("%d.%m.%Y"),
                'downloader': lambda path: self._download_file(url, path)
            }
        return None
        
    def _get_gitlab_release_remote_file(self, module):
        url = f"https://gitlab.com/api/v4/projects/{quote_plus(module['source'])}/releases"
        data = self._get_api_call(url)
        if not isinstance(data, list) or not data: return None
        release = data[0]
        link = next((l for l in release.get('assets', {}).get('links', []) if re.search(module['asset_filter'], l['name'])), None)
        if link:
            return {
                'file_name': link['name'], 'source_type': 'gitlab_release',
                'source_url': release.get('_links', {}).get('self', '#'),
                'date': datetime.strptime(release['released_at'], "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d.%m.%Y"),
                'downloader': lambda path: self._download_file(link['url'], path)
            }
        return None

    def _download_file(self, url, path):
        print(f"   -> İndiriliyor: {url}")
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(path, 'wb') as f:
                    for chunk in r.iter_content(chunk_size=8192): f.write(chunk)
            return True
        except requests.exceptions.RequestException as e:
            print(f"[HATA] Dosya indirilemedi: {url} - {e}")
            return False

    async def process_modules(self):
        print("\n--- Modül Kontrol ve İndirme Aşaması Başlatıldı ---")
        try:
            with open(MODULES_FILE_SRC, 'r', encoding='utf-8') as f:
                modules = json.load(f).get('modules', [])
        except (FileNotFoundError, json.JSONDecodeError):
            print(f"[KRİTİK HATA] '{MODULES_FILE_SRC}' dosyası bulunamadı veya bozuk. Çıkılıyor.")
            return

        for module in sorted([m for m in modules if m.get('enabled')], key=lambda x: x['name']):
            name, type = module['name'], module['type']
            print(f"\n[İŞLEM] Modül kontrol ediliyor: {name} (Tip: {type})")

            getters = {
                'telegram_forwarder': self._get_telegram_remote_file,
                'github_release': self._get_github_release_remote_file,
                'github_ci': self._get_github_ci_remote_file,
                'gitlab_release': self._get_gitlab_release_remote_file,
            }

            if type not in getters:
                print(f"[UYARI] Desteklenmeyen modül tipi: {type}. Atlanıyor.")
                continue
            
            # Asenkron fonksiyonlar için özel işlem
            if asyncio.iscoroutinefunction(getters[type]):
                info = await getters[type](module)
            else:
                info = getters[type](module)

            if not info:
                print(f"[BİLGİ] '{name}' için yeni sürüm bulunamadı.")
                continue

            cached_info = self.manifest.get(name, {})
            if info['file_name'] == cached_info.get('file_name') and os.path.exists(os.path.join(CACHE_DIR, info['file_name'])):
                print(f"[BİLGİ] '{name}' zaten güncel.")
                continue

            print(f"[İNDİRME] '{name}' için yeni sürüm ({info['file_name']}) indiriliyor...")
            path = os.path.join(CACHE_DIR, info['file_name'])
            downloader = info.pop('downloader')
            success = await downloader(path) if asyncio.iscoroutinefunction(downloader) else downloader(path)

            if success:
                old_file = cached_info.get('file_name')
                if old_file and old_file != info['file_name'] and os.path.exists(os.path.join(CACHE_DIR, old_file)):
                    os.remove(os.path.join(CACHE_DIR, old_file))
                self.manifest[name] = info
                print(f"[BAŞARILI] '{name}' indirildi ve manifest güncellendi.")

        self.state_manager.save_manifest(self.manifest)
        print("--- Modül Kontrol ve İndirme Aşaması Tamamlandı ---")


class TelethonPublisher:
    """İndirilen modülleri Telegram'a yayınlayan sınıf."""
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager
        self.manifest = state_manager.load_manifest()
        self.telegram_durum = state_manager.load_telegram_durum()

    async def publish_updates(self):
        print("\n--- Telegram Yayınlama Aşaması Başlatıldı ---")
        if not self.manifest:
            print("[BİLGİ] Manifest boş. Yayınlanacak bir şey yok.")
            return

        for name, info in sorted(self.manifest.items()):
            print(f"\n[İŞLEM] Yayın durumu kontrol ediliyor: {name}")
            
            current_filename = info['file_name']
            posted_info = self.telegram_durum.get(name)
            posted_filename = posted_info['file_name'] if posted_info else None

            if current_filename == posted_filename:
                print(f"[BİLGİ] '{name}' Telegram'da zaten güncel.")
                continue
            
            print(f"[GÜNCELLEME] '{name}' için yeni sürüm yayınlanacak: {current_filename}")
            filepath = os.path.join(CACHE_DIR, current_filename)
            if not os.path.exists(filepath):
                print(f"[HATA] Dosya diskte bulunamadı: {filepath}. Atlanıyor.")
                continue

            if posted_info:
                print(f"[TELEGRAM] Eski mesaj siliniyor (ID: {posted_info['message_id']})...")
                try:
                    await self.client.delete_messages(PUBLISH_CHANNEL_ID, posted_info['message_id'])
                except Exception as e:
                    print(f"[UYARI] Eski mesaj silinemedi: {e}")
            
            caption = (
                f"📦 <b>{info['file_name']}</b>\n\n"
                f"📅 <b>Güncelleme Tarihi:</b> {info['date']}\n\n"
                f"🔗 <b><a href='{info['source_url']}'>Kaynak</a></b>\n"
                f"⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯⎯\n"
                f"<i>Otomatik olarak güncellendi.</i>"
            )

            print(f"[TELEGRAM] Yeni dosya '{current_filename}' yükleniyor...")
            try:
                message = await self.client.send_file(
                    PUBLISH_CHANNEL_ID, filepath, caption=caption, parse_mode='html', silent=True)
                self.telegram_durum[name] = {'message_id': message.id, 'file_name': current_filename}
                print(f"[BAŞARILI] '{name}' güncellendi. Yeni Mesaj ID: {message.id}")
            except Exception as e:
                print(f"[KRİTİK HATA] Dosya yüklenemedi: {name} - {e}")
        
        self.state_manager.save_telegram_durum(self.telegram_durum)
        print("--- Telegram Yayınlama Aşaması Tamamlandı ---")


async def main():
    """Ana otomasyon fonksiyonu."""
    print("==============================================")
    print(f"   Cephanelik Updater vFINAL Başlatıldı")
    print(f"   {datetime.now()}")
    print("==============================================")
    
    # Gerekli tüm sırlar (secrets) var mı diye kontrol et
    if not all([API_ID, API_HASH, SESSION_STRING, GIT_API_TOKEN]):
        raise ValueError("TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING ve GIT_API_TOKEN ortam değişkenleri ayarlanmalıdır.")

    state_manager = StateManager(STATE_DIR)
    
    async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as client:
        # 1. Adım: Modülleri indir/güncelle
        handler = ModuleHandler(client, state_manager)
        await handler.process_modules()
        
        # 2. Adım: Güncellenen modülleri yayınla
        publisher = TelethonPublisher(client, state_manager)
        await publisher.publish_updates()

    # 3. Adım: Son çalışma tarihini kaydet
    with open(LAST_RUN_FILE, "w") as f:
        f.write(datetime.now().strftime("%Y-%m-%d"))

    print("\n[BİLGİ] Tüm işlemler başarıyla tamamlandı.")


if __name__ == "__main__":
    asyncio.run(main())
