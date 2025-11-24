import os
import json
import asyncio
import httpx
import re
import shutil
import traceback
from urllib.parse import quote_plus
from datetime import datetime
from telethon import TelegramClient
from telethon.sessions import StringSession
from telethon.tl.types import KeyboardButtonUrl

# --- Configuration ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

PUBLISH_CHANNEL_ID = -1002477121598
STATE_DIR = "./state"
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

class StateManager:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)

    def load_state(self):
        return self.load_json(STATE_FILE, {"manifest": {}, "telegram_state": {}})

    def save_state(self, state):
        self.save_json(STATE_FILE, state)

    def load_json(self, path, default={}):
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] Failed to read JSON from {path}: {e}")
            return default

    def save_json(self, path, data):
        try:
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
        except Exception as e:
            print(f"[ERROR] Failed to write JSON to {path}: {e}")

class ModuleHandler:
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager

    async def _get_api_call(self, client, url, is_json=True):
        headers = {"Authorization": f"Bearer {GIT_API_TOKEN}"} if "api.github.com" in url else {}
        try:
            response = await client.get(url, headers=headers, timeout=30, follow_redirects=True)
            response.raise_for_status()
            return await response.json() if is_json else response.text
        except httpx.RequestError as e:
            print(f"[ERROR] API call failed for {url}: {e}")
            return None
        except json.JSONDecodeError as e:
            print(f"[ERROR] Failed to decode JSON from {url}: {e}")
            return None

    async def _get_telegram_remote_info(self, client, module):
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
            return None
        except Exception as e:
            print(f"[ERROR] Could not process Telegram channel @{channel}: {e}")
            return None

    async def _get_github_release_remote_info(self, client, module):
        url = f"https://api.github.com/repos/{module['source']}/releases/latest"
        data = await self._get_api_call(client, url)
        if not data:
            print(f"[ERROR] Failed to fetch release data for {module['source']}")
            return None
        if not isinstance(data, dict):
            print(f"[INFO] No valid release data found for {module['source']}")
            return None
            
        assets = data.get('assets')
        if not assets:
            print(f"[INFO] No assets found for {module['source']}")
            return None

        asset = None
        asset_filter = module.get('asset_filter')
        if asset_filter:
            asset = next((a for a in assets if 'name' in a and re.search(asset_filter, a['name'])), None)
        elif assets:
            asset = assets[0]
        
        if asset:
            try:
                return {
                    'file_name': asset['name'],
                    'version_id': asset['updated_at'],
                    'source_url': data.get('html_url', '#'),
                    'date': datetime.strptime(asset['updated_at'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y %H:%M"),
                    'download_url': asset['browser_download_url']
                }
            except KeyError as e:
                print(f"[ERROR] Asset for {module['name']} has a missing key: {e}")
                return None
        
        print(f"[INFO] No asset matched the filter '{asset_filter}' for {module['name']}")
        return None

    async def _get_github_ci_remote_info(self, client, module):
        content = await self._get_api_call(client, module['source'], is_json=False)
        if not content:
            print(f"[ERROR] Failed to fetch CI content for {module['source']}")
            return None
        if not content:
            print(f"[INFO] No CI content found for {module['source']}")
            return None
        
        all_zip_urls = re.findall(r'https://nightly\.link/[^"]+\.zip', content)
        if not all_zip_urls:
            print(f"[INFO] No .zip link found on {module['source']}")
            return None
        
        url = None
        asset_filter = module.get('asset_filter')
        if asset_filter:
            url = next((u for u in all_zip_urls if re.search(asset_filter, os.path.basename(u))), None)
        else:
            url = all_zip_urls[0]

        if not url:
            print(f"[INFO] No asset matched the filter '{asset_filter}' for {module['name']}")
            return None
        
        filename = os.path.basename(url)
        return {
            'file_name': filename,
            'version_id': filename,
            'source_url': module['source'],
            'date': datetime.now().strftime("%d.%m.%Y %H:%M"),
            'download_url': url
        }

    async def _get_gitlab_release_remote_info(self, client, module):
        url = f"https://gitlab.com/api/v4/projects/{quote_plus(module['source'])}/releases"
        data = await self._get_api_call(client, url)
        if not data:
            print(f"[ERROR] Failed to fetch release data for {module['source']}")
            return None
        if not isinstance(data, list) or not data:
            print(f"[INFO] No valid release data found for {module['source']}")
            return None

        release = data[0]
        links = release.get('assets', {}).get('links')
        if not links:
            print(f"[INFO] No assets found for {module['source']}")
            return None

        link = None
        asset_filter = module.get('asset_filter')
        if asset_filter:
            link = next((l for l in links if 'name' in l and re.search(asset_filter, l['name'])), None)
        elif links:
            link = links[0]

        if link:
            try:
                return {
                    'file_name': link['name'],
                    'version_id': release['released_at'],
                    'source_url': release.get('_links', {}).get('self', '#'),
                    'date': datetime.strptime(release['released_at'], "%Y-%m-%dT%H:%M:%S.%f%z").strftime("%d.%m.%Y %H:%M"),
                    'download_url': link['url']
                }
            except KeyError as e:
                print(f"[ERROR] Asset link for {module['name']} has a missing key: {e}")
                return None

        print(f"[INFO] No asset matched the filter '{asset_filter}' for {module['name']}")
        return None

    async def _download_file_async(self, client, url, path):
        print(f"   -> Downloading: {url}")
        try:
            async with client.stream('GET', url, timeout=180, follow_redirects=True) as r:
                r.raise_for_status()
                with open(path, 'wb') as f:
                    async for chunk in r.aiter_bytes():
                        f.write(chunk)
            return True
        except httpx.RequestError as e:
            print(f"[ERROR] Failed to download file: {url} - {e}")
            return False

    async def _process_single_module(self, client, module, state):
        name = module['name']
        type_ = module['type']

        getter_func = {
            'telegram_forwarder': self._get_telegram_remote_info,
            'github_release': self._get_github_release_remote_info,
            'github_ci': self._get_github_ci_remote_info,
            'gitlab_release': self._get_gitlab_release_remote_info,
        }.get(type_)

        if not getter_func:
            print(f"[WARNING] Unsupported module type: {type_}. Skipping.")
            return None

        print(f"\n[PROCESS] Checking remote version for: {name} (Type: {type_})")
        try:
            remote_info = await getter_func(client, module)
        except TypeError as e:
            print(f"[CRITICAL] TypeError in _process_single_module for {name}: {e}")
            print(f"getter_func was: {getter_func}")
            return None
        if not remote_info:
            return None

        remote_version_id = remote_info.get('version_id')
        if not remote_version_id:
            print(f"[ERROR] Could not determine remote version for '{name}'.")
            return None
            
        posted_version_id = state.get("manifest", {}).get(name, {}).get('version_id')

        if remote_version_id == posted_version_id:
            print(f"[INFO] '{name}' is already up-to-date (Version ID: {posted_version_id}).")
            return None

        print(f"[UPDATE] New version found for '{name}'. Preparing to download.")
        path = os.path.join(CACHE_DIR, remote_info['file_name'])
        success = False
        if 'telegram_message' in remote_info:
            message_to_download = remote_info.pop('telegram_message')
            downloaded_path = await self.client.download_media(message_to_download, path)
            success = downloaded_path is not None
        elif 'download_url' in remote_info:
            success = await self._download_file_async(client, remote_info.get('download_url'), path)

        if success:
            return name, remote_info
        
        print(f"[ERROR] Download failed for '{name}'.")
        return None

    async def process_modules(self):
        print("\n--- Starting Module Check and Download Phase ---")
        try:
            with open(MODULES_FILE_SRC, 'r', encoding='utf-8') as f:
                modules = json.load(f).get('modules', [])
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[CRITICAL] '{MODULES_FILE_SRC}' not found or corrupted: {e}")
            return

        state = self.state_manager.load_state()
        manifest_was_updated = False
        
        enabled_modules = sorted([m for m in modules if m.get('enabled')], key=lambda x: x['name'])
        
        async with httpx.AsyncClient() as client:
            tasks = [self._process_single_module(client, module, state) for module in enabled_modules]
            results = await asyncio.gather(*tasks, return_exceptions=True)

        new_manifest = state.get("manifest", {})
        for i, result in enumerate(results):
            module_name = enabled_modules[i]['name']
            if isinstance(result, Exception):
                print(f"[CRITICAL] An exception occurred while processing '{module_name}': {result}")
            elif result:
                name, remote_info = result
                manifest_was_updated = True
                old_file_in_manifest = new_manifest.get(name, {}).get('file_name')
                if old_file_in_manifest and old_file_in_manifest != remote_info.get('file_name') and os.path.exists(os.path.join(CACHE_DIR, old_file_in_manifest)):
                    os.remove(os.path.join(CACHE_DIR, old_file_in_manifest))
                new_manifest[name] = remote_info
                print(f"[SUCCESS] '{name}' was downloaded and manifest updated.")
        
        if manifest_was_updated:
            state["manifest"] = new_manifest
            self.state_manager.save_state(state)
            print("\n[INFO] Manifest was updated.")
        else:
            print("\n[INFO] No modules were downloaded, manifest remains unchanged.")
        print("--- Module Check and Download Phase Finished ---")


class TelethonPublisher:
    def __init__(self, client, state_manager):
        self.client = client
        self.state_manager = state_manager

    async def publish_updates(self):
        print("\n--- Starting Telegram Publishing Phase ---")
        state = self.state_manager.load_state()
        manifest = state.get("manifest", {})
        telegram_state = state.get("telegram_state", {})

        if not manifest:
            print("[INFO] Manifest is empty. Nothing to publish.")
            return

        try:
            with open(MODULES_FILE_SRC, 'r', encoding='utf-8') as f:
                modules_list = json.load(f).get('modules', [])
            modules_map = {m['name']: m for m in modules_list}
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] Failed to read '{MODULES_FILE_SRC}': {e}")
            modules_map = {}

        publish_tasks = []
        for name, info in sorted(manifest.items()):
            current_version_id = info.get('version_id')
            if not current_version_id or current_version_id == telegram_state.get(name, {}).get('version_id'):
                if current_version_id: print(f"[INFO] '{name}' is already up-to-date on Telegram.")
                continue
            
            publish_tasks.append(self._publish_single_update(name, info, state, modules_map))

        if publish_tasks:
            results = await asyncio.gather(*publish_tasks)
            for result in results:
                if result:
                    name, data = result
                    if data:
                        state["telegram_state"][name] = data
        
        self.state_manager.save_state(state)
        print("--- Telegram Publishing Phase Finished ---")

    async def _publish_single_update(self, name, info, state, modules_map):
        try:
            current_filename = info.get('file_name')
            if not current_filename:
                print(f"[ERROR] No filename found for '{name}'. Skipping publish.")
                return None

            print(f"[PUBLISH] Publishing new version for '{name}': {current_filename}")
            filepath = os.path.join(CACHE_DIR, current_filename)
            if not os.path.exists(filepath):
                print(f"[ERROR] File not found on disk: {filepath}. Skipping.")
                return None

            posted_info = state.get("telegram_state", {}).get(name)
            if posted_info and 'message_id' in posted_info:
                print(f"[TELEGRAM] Deleting old message (ID: {posted_info['message_id']})...")
                try:
                    await self.client.delete_messages(PUBLISH_CHANNEL_ID, posted_info['message_id'])
                except Exception as e:
                    print(f"[WARNING] Could not delete old message: {e}")

            module_def = modules_map.get(name, {})
            display_name = module_def.get('description') or info.get('file_name')
            caption = (
                f"📦 <b>{display_name}</b>\n\n"
                f"📄 <b>File Name:</b> <code>{info.get('file_name')}</code>\n"
                f"📅 <b>Update Date:</b> {info.get('date')}\n\n"
                f"🔗 <b><a href='{info.get('source_url')}'>Source</a></b>\n"
            )
            
            buttons = []
            repo_url = None
            module_type = module_def.get('type')
            module_source = module_def.get('source')

            if module_source:
                if module_type in ['github_release', 'github_ci']:
                    repo_owner_and_name = None
                    if module_type == 'github_release':
                        repo_owner_and_name = module_source
                    elif module_type == 'github_ci':
                        match = re.search(r"nightly\.link/([^/]+/[^/]+)", module_source)
                        if match:
                            repo_owner_and_name = match.group(1)
                    if repo_owner_and_name:
                        repo_url = f"https://github.com/{repo_owner_and_name}"
                elif module_type == 'gitlab_release':
                    repo_url = f"https://gitlab.com/{module_source}"
            
            if repo_url:
                buttons.append([KeyboardButtonUrl('⭐ Star Repo', url=repo_url)])

            print(f"[TELEGRAM] Uploading new file '{current_filename}'...")
            message = await self.client.send_file(
                PUBLISH_CHANNEL_ID, filepath, caption=caption, parse_mode='html', silent=True, buttons=buttons or None)
            
            print(f"[SUCCESS] '{name}' updated. New Message ID: {message.id}")
            return name, {
                'message_id': message.id,
                'file_name': current_filename,
                'version_id': info.get('version_id')
            }
        except Exception:
            print(f"[CRITICAL] An exception occurred while publishing '{name}':")
            print(traceback.format_exc())
            return name, None


async def main():
    print("==============================================")
    print(f"   Cephanelik Updater v8.5 (Defensive) Started")
    print(f"   {datetime.now()}")
    print("==============================================")

    if not all([API_ID, API_HASH, SESSION_STRING, GIT_API_TOKEN]):
        raise ValueError("[ERROR] All required environment variables (secrets) must be set.")

    state_manager = StateManager(STATE_DIR)
    
    async with TelegramClient(StringSession(SESSION_STRING), int(API_ID), API_HASH) as client:
        os.makedirs(CACHE_DIR, exist_ok=True)
        handler = ModuleHandler(client, state_manager)
        await handler.process_modules()

        publisher = TelethonPublisher(client, state_manager)
        await publisher.publish_updates()

    print("\n[INFO] All operations completed successfully.")

if __name__ == "__main__":
    asyncio.run(main())
