import os
import json
import asyncio
import requests
import re
import shutil
import subprocess
from urllib.parse import quote_plus
from datetime import datetime
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# Hassas bilgiler GitHub Actions sırlarından (Secrets) alınır.
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

# Proje Ayarları
PUBLISH_CHANNEL_ID = -1002477121598
STATE_DIR = "./state"
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

def run_command(command):
    result = subprocess.run(command, shell=True, capture_output=True, text=True)
    if result.returncode != 0:
        print(f"[ERROR] Komut başarısız: {command}\n{result.stderr}")
    return result

def setup_git():
    run_command('git config --global user.name "github-actions[bot]"')
    run_command('git config --global user.email "github-actions[bot]@users.noreply.github.com"')
    run_command('git fetch origin')

    # state branch'ı kontrol et ve geçiş yap
    if run_command('git show-ref --quiet refs/remotes/origin/state').returncode == 0:
        print("[INFO] 'state' branch bulundu, geçiş yapılıyor.")
        run_command('git checkout state')
    else:
        print("[INFO] 'state' branch bulunamadı, yeni oluşturuluyor.")
        run_command('git checkout --orphan state')
    run_command('git reset --hard')
    run_command('git clean -fdx')

def pull_state_file():
    if os.path.exists(STATE_DIR):
        shutil.rmtree(STATE_DIR)
    os.makedirs(STATE_DIR, exist_ok=True)
    if run_command(f'git checkout origin/state -- {STATE_FILE}').returncode != 0:
        print(f"[WARNING] {STATE_FILE} dosyası state branch'ında bulunamadı. Yeni oluşturulacak.")

def push_state_file():
    run_command(f'git add {STATE_FILE}')
    run_command('git commit -m "[CI]: Durum dosyasını yenile" || git commit --allow-empty -m "[CI]: Durum dosyalarını yenile (değişiklik yok)"')
    run_command('git push -f origin state')

# Projenin durumunu (manifest, telegram durumu vb.) JSON olarak yöneten sınıf.
class StateManager:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        print(f"[INFO] Durum dizini '{self.state_dir}' olarak ayarlandı.")
        os.makedirs(self.state_dir, exist_ok=True)

    def load_json(self, path, default={}):
        print(f"[INFO] JSON okunuyor: {path}")
        if not os.path.exists(path):
            print(f"[WARNING] Dosya bulunamadı: {path}. Varsayılan değer döndürülüyor.")
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] JSON okuma hatası: {e}")
            return default

    def save_json(self, path, data):
        print(f"[INFO] JSON kaydediliyor: {path}")
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[ERROR] JSON kaydetme hatası: {e}")

# Modülleri farklı kaynaklardan bulan ve sürüm kimliğine göre indiren sınıf.
class ModuleHandler:
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager
        self.state = self.state_manager.load_json(STATE_FILE)
        os.makedirs(CACHE_DIR, exist_ok=True)

    def _get_api_call(self, url, is_json=True):
        headers = {"Authorization": f"Bearer {GIT_API_TOKEN}"} if "api.github.com" in url else {}
        try:
            response = requests.get(url, headers=headers, timeout=30)
            response.raise_for_status()
            return response.json() if is_json else response.text
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] API çağrısı başarısız: {url} - {e}")
            return None

    async def _get_telegram_remote_info(self, module):
        channel = module['source_channel']
        keyword = module['source']
        try:
            async for message in self.client.iter_messages(channel, limit=100):
                if message.document and hasattr(message.document.attributes[0], 'file_name') and keyword.lower() in message.document.attributes[0].file_name.lower():
                    return {
                        'file_name': message.document.attributes[0].file_name,
                        'version_id': str(message.id),
                        'source_url': f"https://t.me/{message.chat.username}/{message.id}",
                        'date': message.date.strftime("%d.%m.%Y %H:%M"),
                        'telegram_message': message
                    }
            print(f"[INFO] '{keyword}' için Telegram'da dosya bulunamadı.")
            return None
        except Exception as e:
            print(f"[ERROR] Telegram kanalı @{channel} işlenirken hata: {e}")
            return None

    def _get_github_release_remote_info(self, module):
        url = f"https://api.github.com/repos/{module['source']}/releases/latest"
        data = self._get_api_call(url)
        if not isinstance(data, dict) or 'assets' not in data:
            print(f"[INFO] '{module['source']}' için GitHub'da dosya bulunamadı.")
            return None
        asset = next((a for a in data['assets'] if re.search(module['asset_filter'], a['name'])), None)
        if asset:
            return {
                'file_name': asset['name'],
                'version_id': asset['updated_at'],
                'source_url': data.get('html_url', '#'),
                'date': datetime.strptime(asset['updated_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y %H:%M"),
                'download_url': asset['browser_download_url']
            }
        return None

    def _get_github_ci_remote_info(self, module):
        content = self._get_api_call(module['source'], is_json=False)
        if not content or not isinstance(content, str):
            print(f"[INFO] '{module['source']}' için GitHub CI'da dosya bulunamadı.")
            return None
        match = re.search(r'https://nightly\.link/[^"]*\.zip', content)
        if match:
            url = match.group(0)
            filename = os.path.basename(url)
            return {
                'file_name': filename,
                'version_id': filename,
                'source_url': module['source'],
                'date': datetime.now().strftime("%d.%m.%Y %H:%M"),
                'download_url': url
            }
        return None

    def _get_gitlab_release_remote_info(self, module):
        url = f"https://gitlab.com/api/v4/projects/{quote_plus(module['source'])}/releases"
        data = self._get_api_call(url)
        if not isinstance(data, list) or not data:
            print(f"[INFO] '{module['source']}' için GitLab'da dosya bulunamadı.")
            return None
        release = data[0]
        link = next((l for l in release.get('assets', {}).get('links', []) if re.search(module['asset_filter'], l['name'])), None)
        if link:
            return {
                'file_name': link['name'],
                'version_id': release['released_at'],
                'source_url': release.get('_links', {}).get('self', '#'),
                'date': datetime.strptime(release['released_at'], "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d.%m.%Y %H:%M"),
                'download_url': link['url']
            }
        return None

    def _download_file_sync(self, url, path):
        print(f"   -> İndiriliyor: {url}")
        try:
            with requests.get(url, stream=True, timeout=180) as r:
                r.raise_for_status()
                with open(path, 'wb') as f:
                    shutil.copyfileobj(r.raw, f)
            return True
        except requests.exceptions.RequestException as e:
            print(f"[ERROR] Dosya indirilemedi: {url} - {e}")
            return False

    async def process_modules(self, force_all=False):
        print("\n--- Modül Kontrol ve İndirme Aşaması Başlatıldı ---")
        try:
            with open(MODULES_FILE_SRC, 'r', encoding='utf-8') as f:
                modules = json.load(f).get('modules', [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[CRITICAL MISTAKE] '{MODULES_FILE_SRC}' dosyası bulunamadı veya bozuk. Çıkılıyor: {e}")
            return

        state_was_updated = False

        for module in sorted([m for m in modules if m.get('enabled')], key=lambda x: x['name']):
            name, type_ = module['name'], module['type']
            print(f"\n[PROCESS] Uzak sürüm kontrol ediliyor: {name} (Tip: {type_})")
            getter_func = {
                'telegram_forwarder': self._get_telegram_remote_info,
                'github_release': self._get_github_release_remote_info,
                'github_ci': self._get_github_ci_remote_info,
                'gitlab_release': self._get_gitlab_release_remote_info,
            }.get(type_)

            if not getter_func:
                print(f"[WARNING] Desteklenmeyen modül tipi: {type_}. Atlanıyor.")
                continue

            remote_info = await getter_func(module) if asyncio.iscoroutinefunction(getter_func) else getter_func(module)
            if not remote_info:
                print(f"[INFO] '{name}' için kaynakta dosya bulunamadı.")
                continue

            remote_version_id = remote_info['version_id']
            current_state = self.state.get(name, {})
            posted_version_id = current_state.get('version_id')

            if not force_all and remote_version_id == posted_version_id:
                print(f"[INFO] '{name}' Telegram'da zaten güncel (Sürüm ID: {posted_version_id}). İndirme atlanıyor.")
                continue

            print(f"[DOWNLOAD] '{name}' için yeni sürüm indirilecek (Bulut ID: {remote_version_id}, Kanal ID: {posted_version_id or 'YOK'})")
            path = os.path.join(CACHE_DIR, remote_info['file_name'])
            success = False
            if 'telegram_message' in remote_info:
                message_to_download = remote_info.pop('telegram_message')
                downloaded_path = await self.client.download_media(message_to_download, path)
                success = downloaded_path is not None
            elif 'download_url' in remote_info:
                success = self._download_file_sync(remote_info.pop('download_url'), path)

            if success:
                state_was_updated = True
                old_file_in_state = current_state.get('file_name')
                if old_file_in_state and old_file_in_state != remote_info['file_name'] and os.path.exists(os.path.join(CACHE_DIR, old_file_in_state)):
                    os.remove(os.path.join(CACHE_DIR, old_file_in_state))

                self.state[name] = remote_info
                print(f"[SUCCESSFUL] '{name}' indirildi ve state güncellendi.")
            else:
                print(f"[ERROR] '{name}' indirilemediği için bu döngüde atlanacak.")

        if state_was_updated:
            self.state_manager.save_json(STATE_FILE, self.state)
        else:
            print("\n[INFO] Hiçbir modül indirilmedi, state dosyası değişmedi.")

        print("--- Modül Kontrol ve İndirme Aşaması Tamamlandı ---")

# İndirilen modülleri Telegram'a yayınlayan sınıf.
class TelethonPublisher:
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager
        self.state = state_manager.load_json(STATE_FILE)
        try:
            with open(MODULES_FILE_SRC, 'r', encoding='utf-8') as f:
                modules_list = json.load(f).get('modules', [])
            self.modules_map = {m['name']: m for m in modules_list}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] '{MODULES_FILE_SRC}' dosyası okunurken hata: {e}")
            self.modules_map = {}

    async def publish_updates(self, force_all=False):
        print("\n--- Telegram Yayınlama Aşaması Başlatıldı ---")
        if not self.state:
            print("[INFO] State boş. Yayınlanacak bir şey yok.")
            return

        for name, info in sorted(self.state.items()):
            print(f"\n[PROCESS] Yayın durumu kontrol ediliyor: {name}")
            current_version_id = info.get('version_id')
            if not current_version_id:
                print(f"[WARNING] State'te '{name}' için version_id bulunamadı. Atlanıyor.")
                continue

            if not force_all and 'message_id' in info and info.get('version_id') == current_version_id:
                print(f"[INFO] '{name}' Telegram'da zaten güncel (Sürüm ID: {current_version_id}).")
                continue

            current_filename = info['file_name']
            print(f"[UPDATE] '{name}' için yeni sürüm yayınlanacak: {current_filename}")
            filepath = os.path.join(CACHE_DIR, current_filename)
            if not os.path.exists(filepath):
                print(f"[ERROR] Dosya diskte bulunamadı: {filepath}. Atlanıyor.")
                continue

            if 'message_id' in info:
                print(f"[TELEGRAM] Eski mesaj siliniyor (ID: {info['message_id']})...")
                try:
                    await self.client.delete_messages(PUBLISH_CHANNEL_ID, info['message_id'])
                except Exception as e:
                    print(f"[WARNING] Eski mesaj silinemedi: {e}")

            module_def = self.modules_map.get(name, {})
            display_name = module_def.get('description') or info['file_name']
            caption = (
                f"📦 <b>{display_name}</b>\n\n"
                f"📄 <b>File Name:</b> <code>{info['file_name']}</code>\n"
                f"📅 <b>Update Date:</b> {info['date']}\n\n"
                f"🔗 <b><a href='{info['source_url']}'>Source</a></b>\n"
            )
            print(f"[TELEGRAM] Yeni dosya '{current_filename}' yükleniyor...")
            try:
                message = await self.client.send_file(
                    PUBLISH_CHANNEL_ID, filepath, caption=caption, parse_mode='html', silent=True)
                self.state[name]['message_id'] = message.id
                print(f"[SUCCESSFUL] '{name}' güncellendi. Yeni Mesaj ID: {message.id}")
            except Exception as e:
                print(f"[CRITICAL MISTAKE] Dosya yüklenemedi: {name} - {e}")

        self.state_manager.save_json(STATE_FILE, self.state)
        print("--- Telegram Yayınlama Aşaması Tamamlandı ---")

# Ana otomasyon fonksiyonu.
async def main():
    print("==============================================")
    print(f"   Cephanelik Updater v7.1 Başlatıldı")
    print(f"   {datetime.now()}")
    print("==============================================")
    if not all([API_ID, API_HASH, SESSION_STRING, GIT_API_TOKEN]):
        raise ValueError("[ERROR] Gerekli tüm ortam değişkenleri (Secrets) ayarlanmalıdır.")

    setup_git()
    pull_state_file()

    state_manager = StateManager(STATE_DIR)
    state_exists = os.path.exists(STATE_FILE)
    force_all = not state_exists

    async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as client:
        handler = ModuleHandler(client, state_manager)
        await handler.process_modules(force_all=force_all)
        publisher = TelethonPublisher(client, state_manager)
        await publisher.publish_updates(force_all=force_all)

    push_state_file()

    print("\n[INFO] Tüm işlemler başarıyla tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())
