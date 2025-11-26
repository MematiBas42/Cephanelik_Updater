import os
import json
import asyncio
import httpx
import re
import shutil
import traceback
from urllib.parse import quote_plus
from datetime import datetime
from telethon import TelegramClient, Button
from telethon.sessions import StringSession

# --- Hassas Bilgiler ve Proje Ayarları ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

PUBLISH_CHANNEL_ID = -1002477121598
STATE_DIR = "./state"
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

# --- Yardımcı Sınıflar ---
class StateManager:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)

    def load_state(self):
        return self._load_json(STATE_FILE, {"manifest": {}, "telegram_state": {}})

    def save_state(self, state):
        self._save_json(STATE_FILE, state)

    def _load_json(self, path, default=None):
        if default is None:
            default = {}
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] JSON okuma hatası: {path} - {e}")
            return default

    def _save_json(self, path, data):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[ERROR] JSON kaydetme hatası: {path} - {e}")

class ModuleHandler:
    def __init__(self, http_client, tg_client, state_manager):
        self.http_client = http_client
        self.tg_client = tg_client
        self.state_manager = state_manager

    async def _api_call(self, url, is_json=True):
        headers = {}
        if "api.github.com" in url:
            headers["Authorization"] = f"Bearer {GIT_API_TOKEN}"
        
        for attempt in range(3):
            try:
                response = await self.http_client.get(url, headers=headers, timeout=45, follow_redirects=True)
                response.raise_for_status()
                return response.json() if is_json else response.text
            except httpx.HTTPStatusError as e:
                if 500 <= e.response.status_code < 600 and attempt < 2:
                    print(f"[WARNING] Sunucu hatası (5xx): {e.response.status_code}. {5 * (attempt + 1)} saniye içinde yeniden deneniyor... ({url})")
                    await asyncio.sleep(5 * (attempt + 1))
                    continue
                else:
                    print(f"[ERROR] HTTP Hatası: {e.response.status_code} - {url}")
                    return None
            except httpx.RequestError as e:
                print(f"[ERROR] İstek Hatası: {url} - {e}")
                break 
            except json.JSONDecodeError as e:
                print(f"[ERROR] JSON Çözümleme Hatası: {url} - {e}")
                break
        return None

    async def _get_telegram_remote_info(self, module):
        try:
            async for message in self.tg_client.iter_messages(module['source_channel'], limit=100, search=module['source']):
                if message.document and module['source'].lower() in message.document.attributes[0].file_name.lower():
                    return {
                        'file_name': message.document.attributes[0].file_name,
                        'version_id': str(message.id),
                        'source_url': f"https://t.me/{message.chat.username}/{message.id}",
                        'date': message.date.strftime("%d.%m.%Y %H:%M"),
                        'telegram_message': message
                    }
            return None
        except Exception as e:
            print(f"[ERROR] Telegram kanalı işlenemedi @{module['source_channel']}: {e}")
            return None

    async def _get_github_release_remote_info(self, module):
        url = f"https://api.github.com/repos/{module['source']}/releases/latest"
        data = await self._api_call(url)
        if not isinstance(data, dict) or 'assets' not in data or not data['assets']:
            return None
        asset = next((a for a in data['assets'] if re.search(module['asset_filter'], a['name'])), data['assets'][0])
        if asset:
            return {
                'file_name': asset['name'],
                'version_id': asset['updated_at'],
                'source_url': data.get('html_url', '#'),
                'date': datetime.strptime(asset['updated_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y %H:%M"),
                'download_url': asset['browser_download_url']
            }
        return None

    async def _get_github_ci_remote_info(self, module):
        content = await self._api_call(module['source'], is_json=False)
        if not content: return None

        all_zip_urls = re.findall(r'https://nightly\.link/[^"]*\.zip', content)
        if not all_zip_urls:
            return None

        url_to_download = None
        asset_filter = module.get('asset_filter')

        if asset_filter:
            for url in all_zip_urls:
                if re.search(asset_filter, os.path.basename(url)):
                    url_to_download = url
                    break
        else:
            url_to_download = all_zip_urls[0]

        if not url_to_download:
            return None

        filename = os.path.basename(url_to_download)
        return {
            'file_name': filename, 'version_id': filename,
            'source_url': module['source'],
            'date': datetime.now().strftime("%d.%m.%Y %H:%M"),
            'download_url': url_to_download
        }

    async def _get_gitlab_release_remote_info(self, module):
        url = f"https://gitlab.com/api/v4/projects/{quote_plus(module['source'])}/releases"
        data = await self._api_call(url)
        if not isinstance(data, list) or not data: return None
        release = data[0]
        link = next((l for l in release.get('assets', {}).get('links', []) if re.search(module['asset_filter'], l['name'])), None)
        if link:
            return {
                'file_name': link['name'], 'version_id': release['released_at'],
                'source_url': release.get('_links', {}).get('self', '#'),
                'date': datetime.strptime(release['released_at'], "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d.%m.%Y %H:%M"),
                'download_url': link['url']
            }
        return None

    async def _download_file(self, url, path):
        print(f"   -> İndiriliyor: {url}")
        temp_path = path + ".tmp"
        os.makedirs(os.path.dirname(path), exist_ok=True)
        
        try:
            for attempt in range(3):
                try:
                    async with self.http_client.stream('GET', url, timeout=180, follow_redirects=True) as r:
                        r.raise_for_status()
                        with open(temp_path, 'wb') as f:
                            async for chunk in r.aiter_bytes():
                                f.write(chunk)
                    
                    os.rename(temp_path, path)
                    return True
                except httpx.HTTPStatusError as e:
                    if 500 <= e.response.status_code < 600 and attempt < 2:
                        print(f"[WARNING] İndirme sırasında sunucu hatası (5xx): {e.response.status_code}. {5 * (attempt + 1)} saniye içinde yeniden deneniyor... ({url})")
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    else:
                        print(f"[ERROR] İndirme sırasında HTTP Hatası: {e.response.status_code} - {url}")
                        return False
                except httpx.RequestError as e:
                    if attempt < 2:
                        print(f"[WARNING] İndirme sırasında istek hatası: {e}. {5 * (attempt + 1)} saniye içinde yeniden deneniyor... ({url})")
                        await asyncio.sleep(5 * (attempt + 1))
                        continue
                    else:
                        print(f"[ERROR] İndirme sırasında İstek Hatası: {url} - {e}")
                        return False
            return False
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    async def _process_single_module(self, module, state):
        name, type_ = module['name'], module['type']
        print(f"\n[PROCESS] Uzak sürüm kontrol ediliyor: {name} (Tip: {type_})")

        getters = {
            'telegram_forwarder': self._get_telegram_remote_info,
            'github_release': self._get_github_release_remote_info,
            'github_ci': self._get_github_ci_remote_info,
            'gitlab_release': self._get_gitlab_release_remote_info,
        }
        getter_func = getters.get(type_)
        if not getter_func:
            print(f"[WARNING] Desteklenmeyen tip: {type_}")
            return None

        remote_info = await getter_func(module)
        if not remote_info:
            print(f"[INFO] '{name}' için kaynakta dosya bulunamadı.")
            return None

        remote_version_id = remote_info['version_id']
        posted_version_id = state["manifest"].get(name, {}).get('version_id')

        if remote_version_id == posted_version_id:
            print(f"[INFO] '{name}' zaten güncel (ID: {posted_version_id}).")
            return None

        print(f"[DOWNLOAD] '{name}' için yeni sürüm indirilecek.")
        path = os.path.join(CACHE_DIR, remote_info['file_name'])
        
        if 'telegram_message' in remote_info:
            msg = remote_info.pop('telegram_message')
            success = await self.tg_client.download_media(msg, path) is not None
        elif 'download_url' in remote_info:
            success = await self._download_file(remote_info.pop('download_url'), path)
        else:
            success = False

        if success:
            print(f"[SUCCESS] '{name}' indirildi.")
            return name, remote_info
        
        print(f"[ERROR] '{name}' indirilemedi.")
        return None

    async def process_modules(self):
        print("\n--- Modül Kontrol ve İndirme Aşaması ---")
        try:
            modules = self.state_manager._load_json(MODULES_FILE_SRC).get('modules', [])
        except Exception as e:
            print(f"[CRITICAL] '{MODULES_FILE_SRC}' okunamadı: {e}")
            return

        state = self.state_manager.load_state()
        tasks = [self._process_single_module(m, state) for m in modules if m.get('enabled')]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        manifest_was_updated = False
        new_manifest = state["manifest"].copy()

        for res, mod in zip(results, (m for m in modules if m.get('enabled'))):
            if isinstance(res, Exception):
                print(f"[CRITICAL] '{mod['name']}' işlenirken istisna oluştu: {res}")
                traceback.print_exc()
            elif res:
                name, remote_info = res
                manifest_was_updated = True
                
                old_file = new_manifest.get(name, {}).get('file_name')
                if old_file and old_file != remote_info['file_name']:
                    try:
                        os.remove(os.path.join(CACHE_DIR, old_file))
                    except OSError: pass # Dosya yoksa sorun değil
                
                new_manifest[name] = remote_info

        if manifest_was_updated:
            state["manifest"] = new_manifest
            self.state_manager.save_state(state)
            print("\n[INFO] Manifest güncellendi.")
        else:
            print("\n[INFO] Manifest değişmedi, yeni modül indirilmedi.")
        
        print("--- Modül Kontrol Aşaması Tamamlandı ---")

class TelethonPublisher:
    def __init__(self, tg_client, state_manager):
        self.tg_client = tg_client
        self.state_manager = state_manager

    async def _publish_single_update(self, name, info, modules_map):
        try:
            filename = info['file_name']
            filepath = os.path.join(CACHE_DIR, filename)
            if not os.path.exists(filepath):
                print(f"[ERROR] Dosya bulunamadı: {filepath}")
                return None

            module_def = modules_map.get(name, {})
            display_name = module_def.get('description', name)
            caption = (
                f"📦 <b>{display_name}</b>\n\n"
                f"📄 <b>Dosya Adı:</b> <code>{filename}</code>\n"
                f"📅 <b>Güncelleme:</b> {info['date']}\n\n"
                f"🔗 <b><a href='{info['source_url']}'>Kaynak</a></b>"
            )

            buttons = None
            module_type = module_def.get('type')

            if module_type == 'telegram_forwarder':
                # DÜZELTME 1: Button.url kullanımı
                buttons = [Button.url('Kaynak Mesaja Git', url=info['source_url'])]
            else:
                repo_url = None
                module_source = module_def.get('source')
                
                # URL oluşturma mantığı doğru, buraya dokunmuyoruz
                if module_source:
                    if module_type == 'github_release':
                        # DİKKAT: JSON'da source sadece "User/Repo" olmalı, başında https olmamalı.
                        repo_url = f"https://github.com/{module_source}"
                    elif module_type == 'github_ci':
                        match = re.search(r"nightly\.link/([^/]+/[^/]+)", module_source)
                        if match:
                            repo_url = f"https://github.com/{match.group(1)}"
                    elif module_type == 'gitlab_release':
                        repo_url = f"https://gitlab.com/{module_source}"
                
                if repo_url:
                    # DÜZELTME 2: Button.url kullanımı
                    buttons = [Button.url('⭐ Star Repo', url=repo_url)]

            print(f"[TELEGRAM] '{filename}' yükleniyor...")
            # Telethon Button.url kullandığında otomatik olarak doğru formatı ayarlar
            message = await self.tg_client.send_file(
                PUBLISH_CHANNEL_ID, filepath, caption=caption, parse_mode='html', silent=True, buttons=buttons
            )
            print(f"[SUCCESS] '{name}' güncellendi. Mesaj ID: {message.id}")
            return name, {'message_id': message.id, 'file_name': filename, 'version_id': info['version_id']}
        except Exception as e:
            print(f"[CRITICAL] '{name}' yayınlanırken istisna: {e}")
            traceback.print_exc()
            return name, None

    async def publish_updates(self):
        print("\n--- Telegram Yayınlama Aşaması ---")
        state = self.state_manager.load_state()
        manifest = state.get("manifest", {})
        tg_state = state.get("telegram_state", {})

        if not manifest:
            print("[INFO] Yayınlanacak bir şey yok.")
            return

        modules_map = {m['name']: m for m in self.state_manager._load_json(MODULES_FILE_SRC).get('modules', [])}
        
        # Önce silinecek mesajları topla
        messages_to_delete = [
            tg_state[name]['message_id']
            for name, info in manifest.items()
            if name in tg_state and info.get('version_id') != tg_state[name].get('version_id') and 'message_id' in tg_state[name]
        ]
        if messages_to_delete:
            print(f"[TELEGRAM] {len(messages_to_delete)} eski mesaj siliniyor...")
            try:
                await self.tg_client.delete_messages(PUBLISH_CHANNEL_ID, messages_to_delete)
            except Exception as e:
                print(f"[WARNING] Eski mesajlar silinemedi: {e}")

        # Sonra yayınlanacakları topla
        publish_tasks = []
        for name, info in sorted(manifest.items()):
            if info.get('version_id') != tg_state.get(name, {}).get('version_id'):
                publish_tasks.append(self._publish_single_update(name, info, modules_map))
            else:
                print(f"[INFO] '{name}' Telegram'da zaten güncel.")
        
        if publish_tasks:
            results = await asyncio.gather(*publish_tasks)
            for res in results:
                if res:
                    name, data = res
                    if data:
                        state["telegram_state"][name] = data
        
        self.state_manager.save_state(state)
        print("--- Telegram Yayınlama Aşaması Tamamlandı ---")


async def main():
    print("=" * 40)
    print(f"   Cephanelik Updater v9.0 (Concurrent) Başlatıldı")
    print(f"   {datetime.now()}")
    print("=" * 40)

    if not all([API_ID, API_HASH, SESSION_STRING, GIT_API_TOKEN]):
        raise ValueError("[ERROR] Gerekli tüm ortam değişkenleri (Secrets) ayarlanmalıdır.")

    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
    }
    
    state_manager = StateManager(STATE_DIR)
    
    async with httpx.AsyncClient(headers=headers) as http_client:
        async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as tg_client:
            handler = ModuleHandler(http_client, tg_client, state_manager)
            await handler.process_modules()
            
            publisher = TelethonPublisher(tg_client, state_manager)
            await publisher.publish_updates()

    print("\n[INFO] Tüm işlemler başarıyla tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())
