# -*- coding: utf-8 -*-
# v2.0 - Tam Kapsamlı, Birleşik ve Nihai Modül İşleyici
# AÇIKLAMA: Bu betik, `mis` ve `telegram_forwarder.py` betiklerinin görevlerini
# birleştirir. GitHub Release, GitHub CI, GitLab Release ve Telegram kaynaklarını
# kontrol eder ve en güncel dosyaları indirir. Bu versiyon, tüm modül tiplerini
# destekleyerek sistemi eksiksiz ve son derece sağlam hale getirir.

import os
import json
import asyncio
import requests
import re
import shutil
from urllib.parse import quote_plus
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# --- YAPILANDIRMA (HASSAS BİLGİLER) ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

# --- AYARLAR ---
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
CONFIG_DIR = os.path.expanduser("~/.config/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
MODULES_FILE_DST = os.path.join(CONFIG_DIR, "modules.json")
CACHE_MANIFEST = os.path.join(CACHE_DIR, "manifest.json")

# --- Yardımcı Fonksiyonlar ---
def setup_directories():
    """Gerekli yapılandırma ve önbellek dizinlerini oluşturur."""
    print("[BİLGİ] Gerekli dizinler oluşturuluyor...")
    os.makedirs(CACHE_DIR, exist_ok=True)
    os.makedirs(CONFIG_DIR, exist_ok=True)

def load_manifest():
    """Önbellek manifest dosyasını yükler."""
    if not os.path.exists(CACHE_MANIFEST):
        print(f"[UYARI] '{CACHE_MANIFEST}' bulunamadı, yeniden oluşturuluyor.")
        return {}
    try:
        with open(CACHE_MANIFEST, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_manifest(manifest_data):
    """Önbellek manifest dosyasını kaydeder."""
    print("[BİLGİ] Manifest dosyası güncelleniyor...")
    with open(CACHE_MANIFEST, 'w', encoding='utf-8') as f:
        json.dump(manifest_data, f, indent=2, sort_keys=True)

def get_api_call(url):
    """API'lere (GitHub, GitLab, vb.) güvenli bir şekilde GET isteği gönderir."""
    headers = {}
    if "api.github.com" in url and GIT_API_TOKEN:
        headers["Authorization"] = f"Bearer {GIT_API_TOKEN}"
    
    try:
        response = requests.get(url, headers=headers, timeout=30)
        response.raise_for_status()
        if response.headers.get('Content-Type', '').startswith('application/json'):
            return response.json()
        return response.text
    except requests.exceptions.RequestException as e:
        print(f"[HATA] API çağrısı başarısız: {url} - {e}")
        return None

# --- Modül İşleyicileri ---
async def get_telegram_remote_file(client, module):
    """Telegram kanalını tarayarak en güncel modül dosyasını bulur."""
    print(f"-> Telegram kaynağı taranıyor: @{module['source_channel']}")
    try:
        channel_entity = await client.get_entity(module['source_channel'])
        keyword = module['source']
        
        async for message in client.iter_messages(channel_entity, limit=100):
            if message.document and hasattr(message.document.attributes[0], 'file_name') and keyword.lower() in message.document.attributes[0].file_name.lower():
                file_name = message.document.attributes[0].file_name
                print(f"-> Eşleşen dosya bulundu: {file_name}")
                return {
                    'name': file_name,
                    'downloader': lambda path: client.download_media(message, path)
                }
        print("-> Kaynak kanalda eşleşen yeni dosya bulunamadı.")
        return None
    except Exception as e:
        print(f"[HATA] Telegram kanalı işlenirken hata: {e}")
        return None

def get_github_release_remote_file(module):
    """GitHub'daki en son sürümden modül dosyasını bulur."""
    url = f"https://api.github.com/repos/{module['source']}/releases/latest"
    data = get_api_call(url)
    if not isinstance(data, dict) or 'assets' not in data:
        return None
    
    asset_filter = module.get('asset_filter', '')
    asset = next((asset for asset in data['assets'] if re.search(asset_filter, asset['name'])), None)
    
    if asset:
        return {
            'name': asset['name'],
            'downloader': lambda path: download_file(asset['browser_download_url'], path)
        }
    return None

def get_github_ci_remote_file(module):
    """GitHub Actions CI (nightly.link) üzerinden modül dosyasını bulur."""
    url = module['source']
    content = get_api_call(url)
    if not content or not isinstance(content, str):
        return None
    
    match = re.search(r'https://nightly\.link/[^"]*\.zip', content)
    if match:
        download_url = match.group(0)
        file_name = os.path.basename(download_url)
        return {
            'name': file_name,
            'downloader': lambda path: download_file(download_url, path)
        }
    return None

def get_gitlab_release_remote_file(module):
    """GitLab'daki en son sürümden modül dosyasını bulur."""
    encoded_source = quote_plus(module['source'])
    url = f"https://gitlab.com/api/v4/projects/{encoded_source}/releases"
    data = get_api_call(url)
    if not isinstance(data, list) or not data:
        return None

    latest_release = data[0]
    asset_filter = module.get('asset_filter', '')
    link = next((link for link in latest_release.get('assets', {}).get('links', []) if re.search(asset_filter, link['name'])), None)
    
    if link:
        return {
            'name': link['name'],
            'downloader': lambda path: download_file(link['url'], path)
        }
    return None

def download_file(url, path):
    """Verilen URL'den dosyayı indirir."""
    print(f"   -> İndiriliyor: {url}")
    try:
        with requests.get(url, stream=True, timeout=180) as r:
            r.raise_for_status()
            with open(path, 'wb') as f:
                for chunk in r.iter_content(chunk_size=8192):
                    f.write(chunk)
        return True
    except requests.exceptions.RequestException as e:
        print(f"[HATA] Dosya indirilemedi: {url} - {e}")
        return False

# --- Ana İşleyici ---
async def main():
    print("--- Birleşik Modül İşleyici Başlatıldı ---")
    setup_directories()
    
    try:
        print(f"[BİLGİ] Modül listesi kopyalanıyor: {MODULES_FILE_SRC} -> {MODULES_FILE_DST}")
        shutil.copyfile(MODULES_FILE_SRC, MODULES_FILE_DST)
        with open(MODULES_FILE_DST, 'r', encoding='utf-8') as f:
            all_modules = json.load(f).get('modules', [])
    except (FileNotFoundError, json.JSONDecodeError):
        print(f"[HATA] '{MODULES_FILE_SRC}' dosyası bulunamadı veya bozuk. Çıkılıyor.")
        return

    manifest = load_manifest()
    enabled_modules = [m for m in all_modules if m.get('enabled')]

    async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as client:
        for module in enabled_modules:
            modul_adi = module['name']
            modul_tipi = module['type']
            print(f"---\n[İŞLEM] Modül kontrol ediliyor: {modul_adi} (Tip: {modul_tipi})")
            
            remote_file_info = None
            if modul_tipi == 'telegram_forwarder':
                remote_file_info = await get_telegram_remote_file(client, module)
            elif modul_tipi == 'github_release':
                remote_file_info = get_github_release_remote_file(module)
            elif modul_tipi == 'github_ci':
                remote_file_info = get_github_ci_remote_file(module)
            elif modul_tipi == 'gitlab_release':
                remote_file_info = get_gitlab_release_remote_file(module)
            else:
                print(f"[UYARI] Desteklenmeyen modül tipi: {modul_tipi}. Atlanıyor.")
                continue

            if not remote_file_info:
                print(f"[BİLGİ] '{modul_adi}' için yeni sürüm bulunamadı veya alınamadı.")
                continue

            remote_filename = remote_file_info['name']
            cached_filename = manifest.get(modul_adi)

            if remote_filename == cached_filename and os.path.exists(os.path.join(CACHE_DIR, cached_filename)):
                print(f"[BİLGİ] '{modul_adi}' zaten güncel.")
                continue

            print(f"[İNDİRME] '{modul_adi}' için yeni sürüm ({remote_filename}) indiriliyor...")
            download_path = os.path.join(CACHE_DIR, remote_filename)
            
            downloader = remote_file_info['downloader']
            if asyncio.iscoroutine(downloader(download_path)):
                 success = await downloader(download_path)
            else:
                 success = downloader(download_path)

            if success:
                old_file = manifest.get(modul_adi)
                if old_file and old_file != remote_filename and os.path.exists(os.path.join(CACHE_DIR, old_file)):
                    try:
                        os.remove(os.path.join(CACHE_DIR, old_file))
                    except OSError as e:
                        print(f"[UYARI] Eski dosya silinemedi: {e}")
                
                manifest[modul_adi] = remote_filename
                print(f"[BAŞARILI] '{modul_adi}' indirildi ve manifest güncellendi.")
            else:
                print(f"[HATA] '{modul_adi}' indirilemedi.")
                if os.path.exists(download_path):
                    os.remove(download_path)

    save_manifest(manifest)
    print("\n--- Tüm modül işlemleri tamamlandı. ---")

if __name__ == "__main__":
    if not all([API_ID, API_HASH, SESSION_STRING, GIT_API_TOKEN]):
        raise ValueError("TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_SESSION_STRING ve GIT_API_TOKEN ortam değişkenleri ayarlanmalıdır.")
    
    asyncio.run(main())

