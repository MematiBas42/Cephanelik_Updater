import os
import json
import asyncio
import httpx
import re
import traceback
from html import escape
from urllib.parse import quote, quote_plus
from datetime import datetime, timedelta
from telethon import TelegramClient, Button
from telethon.sessions import StringSession

# --- Hassas Bilgiler ve Proje Ayarları ---
API_ID = os.environ.get('TELEGRAM_API_ID')
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')
GIT_API_TOKEN = os.environ.get('GIT_API_TOKEN')

PUBLISH_CHANNEL_ID = -1002477121598
DISCUSSION_GROUP_ID = -1002322194303
CI_MIGRATION_TOLERANCE = timedelta(minutes=10)
STATE_DIR = "./state"
CACHE_DIR = os.path.expanduser("~/.cache/ksu-manager")
MODULES_FILE_SRC = "./modules.json"
STATE_FILE = os.path.join(STATE_DIR, "state.json")

def should_publish_link_only(module, file_name):
    return bool(module.get('is_apk')) or file_name.lower().endswith('.apk')

def state_info_from_remote_info(remote_info):
    return {
        key: value
        for key, value in remote_info.items()
        if key not in ('download_url', 'telegram_message')
    }

def telegram_message_url(chat_id, message_id):
    chat_id_text = str(chat_id)
    if chat_id_text.startswith("-100"):
        return f"https://t.me/c/{chat_id_text[4:]}/{message_id}"
    return None

def is_telegram_button_url(url):
    return isinstance(url, str) and url.startswith(("http://", "https://", "tg://"))

def telegram_url_button(label, url):
    if not is_telegram_button_url(url):
        return None
    return Button.url(label, url=url)

def source_caption_line(source_url):
    if is_telegram_button_url(source_url):
        return f"🔗 <b><a href='{escape(source_url, quote=True)}'>Kaynak</a></b>"
    return "🔗 <b>Kaynak:</b> bağlantı bulunamadı"

def document_file_name(message):
    document = getattr(message, 'document', None)
    if not document:
        return None

    for attribute in getattr(document, 'attributes', []) or []:
        file_name = getattr(attribute, 'file_name', None)
        if file_name:
            return file_name

    return None

def parse_stored_date(value):
    if not isinstance(value, str):
        return None

    for date_format in ("%d.%m.%Y %H:%M", "%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(value, date_format)
        except ValueError:
            continue

    return None

def should_migrate_ci_without_publish(posted_info, remote_info):
    if posted_info.get('file_name') != remote_info['file_name']:
        return False
    if posted_info.get('version_id') != remote_info['file_name']:
        return False

    posted_date = parse_stored_date(posted_info.get('date'))
    remote_date = parse_stored_date(remote_info.get('date'))
    if posted_date and remote_date:
        return remote_date <= posted_date + CI_MIGRATION_TOLERANCE

    return True

def validate_modules(modules):
    if not isinstance(modules, list):
        raise ValueError("'modules' alanı liste olmalıdır.")

    supported_types = {'telegram_forwarder', 'github_release', 'github_ci', 'gitlab_release'}
    seen_names = set()

    for index, module in enumerate(modules):
        if not isinstance(module, dict):
            raise ValueError(f"modules[{index}] nesne olmalıdır.")

        name = module.get('name')
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"modules[{index}] geçerli bir name içermelidir.")
        if name in seen_names:
            raise ValueError(f"Duplicate module name: {name}")
        seen_names.add(name)

        type_ = module.get('type')
        if type_ not in supported_types:
            raise ValueError(f"'{name}' desteklenmeyen type içeriyor: {type_}")

        source = module.get('source')
        if not isinstance(source, str) or not source.strip():
            raise ValueError(f"'{name}' geçerli bir source içermelidir.")

        if type_ == 'telegram_forwarder':
            source_channel = module.get('source_channel')
            if not isinstance(source_channel, str) or not source_channel.strip():
                raise ValueError(f"'{name}' telegram_forwarder için source_channel içermelidir.")

        asset_filter = module.get('asset_filter')
        if asset_filter:
            re.compile(asset_filter)

# --- Yardımcı Sınıflar ---
class StateManager:
    def __init__(self, state_dir):
        self.state_dir = state_dir
        os.makedirs(self.state_dir, exist_ok=True)

    def load_state(self):
        state = self._load_json(STATE_FILE, {"manifest": {}, "telegram_state": {}}, strict=True)
        state.setdefault("manifest", {})
        state.setdefault("telegram_state", {})
        return state

    def save_state(self, state):
        self._save_json(STATE_FILE, state)

    def _load_json(self, path, default=None, strict=False):
        if default is None:
            default = {}
        if not os.path.exists(path):
            return default
        try:
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
        except (FileNotFoundError, json.JSONDecodeError) as e:
            print(f"[ERROR] JSON okuma hatası: {path} - {e}")
            if strict:
                raise
            return default

    def _save_json(self, path, data):
        temp_path = f"{path}.tmp"
        try:
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(temp_path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, sort_keys=True)
                f.write("\n")
            os.replace(temp_path, path)
        except Exception as e:
            print(f"[ERROR] JSON kaydetme hatası: {path} - {e}")
            if os.path.exists(temp_path):
                try:
                    os.remove(temp_path)
                except OSError:
                    pass
            raise

class ModuleHandler:
    def __init__(self, http_client, tg_client, state_manager):
        self.http_client = http_client
        self.tg_client = tg_client
        self.state_manager = state_manager

    def _auth_headers_for_url(self, url):
        headers = {}
        if "api.github.com" in url:
            headers["Accept"] = "application/vnd.github+json"
            if GIT_API_TOKEN:
                headers["Authorization"] = f"Bearer {GIT_API_TOKEN}"
        return headers

    def _format_github_date(self, value):
        return datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m.%Y %H:%M")

    async def _api_call(self, url, is_json=True):
        headers = self._auth_headers_for_url(url)
        
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
                file_name = document_file_name(message)
                if file_name and module['source'].lower() in file_name.lower():
                    chat_username = getattr(message.chat, 'username', None)
                    source_url = f"https://t.me/{chat_username}/{message.id}" if chat_username else "#"
                    return {
                        'file_name': file_name,
                        'version_id': str(message.id),
                        'source_url': source_url,
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

    async def _get_nightly_link_remote_info(self, module):
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

    async def _get_github_ci_remote_info(self, module):
        match = re.search(r"https://nightly\.link/([^/]+)/([^/]+)/workflows/([^/]+)/(.+)$", module['source'])
        if not match:
            return await self._get_nightly_link_remote_info(module)

        owner, repo, workflow_file, branch = match.groups()
        runs_url = (
            f"https://api.github.com/repos/{owner}/{repo}/actions/workflows/"
            f"{quote(workflow_file, safe='')}/runs?branch={quote(branch, safe='')}"
            "&status=success&per_page=10"
        )
        runs_data = await self._api_call(runs_url)
        runs = runs_data.get('workflow_runs', []) if isinstance(runs_data, dict) else []
        asset_filter = module.get('asset_filter')

        for run in runs:
            artifacts_data = await self._api_call(run.get('artifacts_url', ''))
            artifacts = artifacts_data.get('artifacts', []) if isinstance(artifacts_data, dict) else []

            for artifact in artifacts:
                if artifact.get('expired'):
                    continue

                artifact_name = artifact.get('name', '')
                file_name = artifact_name if artifact_name.lower().endswith('.zip') else f"{artifact_name}.zip"
                if asset_filter and not (
                    re.search(asset_filter, artifact_name) or re.search(asset_filter, file_name)
                ):
                    continue

                updated_at = artifact.get('updated_at') or artifact.get('created_at') or run.get('updated_at')
                download_url = artifact.get('archive_download_url')
                if not updated_at or not download_url:
                    continue

                version_id = f"{run.get('id')}:{artifact.get('id')}:{updated_at}"
                return {
                    'file_name': file_name,
                    'version_id': version_id,
                    'source_url': run.get('html_url') or module['source'],
                    'date': self._format_github_date(updated_at),
                    'download_url': download_url
                }

        print(f"[WARNING] GitHub Actions API ile artifact bulunamadı, nightly.link yedeği deneniyor: {module['source']}")
        return await self._get_nightly_link_remote_info(module)

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
        headers = self._auth_headers_for_url(url)
        
        try:
            for attempt in range(3):
                try:
                    async with self.http_client.stream('GET', url, headers=headers, timeout=180, follow_redirects=True) as r:
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
        posted_info = state.get("manifest", {}).get(name, {})
        posted_version_id = posted_info.get('version_id')

        if remote_version_id == posted_version_id:
            print(f"[INFO] '{name}' zaten güncel (ID: {posted_version_id}).")
            return None

        if type_ == 'github_ci' and should_migrate_ci_without_publish(posted_info, remote_info):
            print(f"[MIGRATION] '{name}' CI sürüm kimliği dosya adından artifact ID formatına taşındı; Telegram'a yeniden yayınlanmayacak.")
            return 'migrate', name, state_info_from_remote_info(remote_info)

        if should_publish_link_only(module, remote_info['file_name']):
            remote_info.pop('telegram_message', None)
            remote_info.pop('download_url', None)
            remote_info['link_only'] = True
            print(f"[LINK-ONLY] '{name}' APK/link-only olarak işaretli, dosya indirilmeden kaynak linki yayınlanacak.")
            return 'publish', name, remote_info

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
            return 'publish', name, remote_info
        
        print(f"[ERROR] '{name}' indirilemedi.")
        return None

    async def process_modules(self):
        print("\n--- Modül Kontrol ve İndirme Aşaması ---")
        try:
            modules = self.state_manager._load_json(MODULES_FILE_SRC, strict=True).get('modules', [])
            validate_modules(modules)
        except Exception as e:
            print(f"[CRITICAL] '{MODULES_FILE_SRC}' okunamadı: {e}")
            raise

        state = self.state_manager.load_state()
        state.setdefault("manifest", {})
        state.setdefault("telegram_state", {})
        tasks = [self._process_single_module(m, state) for m in modules if m.get('enabled')]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        pending_updates = {}
        state_migrations = {}

        for res, mod in zip(results, (m for m in modules if m.get('enabled'))):
            if isinstance(res, Exception):
                print(f"[CRITICAL] '{mod['name']}' işlenirken istisna oluştu: {res}")
                traceback.print_exc()
            elif res:
                action, name, remote_info = res
                if action == 'migrate':
                    state_migrations[name] = remote_info
                else:
                    pending_updates[name] = remote_info

        if state_migrations:
            state["manifest"].update(state_migrations)
            self.state_manager.save_state(state)
            print(f"\n[INFO] {len(state_migrations)} eski CI state kaydı yeniden yayın yapılmadan migrate edildi.")

        if pending_updates:
            print(f"\n[INFO] {len(pending_updates)} modül yayın için hazırlandı. State, Telegram yayını başarılı olunca güncellenecek.")
        else:
            print("\n[INFO] Manifest değişmedi, yeni modül indirilmedi.")
        
        print("--- Modül Kontrol Aşaması Tamamlandı ---")
        return pending_updates

class TelethonPublisher:
    def __init__(self, tg_client, state_manager):
        self.tg_client = tg_client
        self.state_manager = state_manager

    def _build_pending_discussion(self, info, channel_message_id):
        pending = state_info_from_remote_info(info)
        pending['channel_message_id'] = channel_message_id
        return pending

    async def _send_discussion_notification(self, name, info, modules_map, channel_message_id):
        module_def = modules_map.get(name, {})
        display_name = module_def.get('description', name)
        filename = info['file_name']
        channel_url = telegram_message_url(PUBLISH_CHANNEL_ID, channel_message_id)
        source_url = info['source_url']
        link_only = should_publish_link_only(module_def, filename) or info.get('link_only')
        update_type = "Kaynak linki güncellendi" if link_only else "Dosya güncellendi"

        text = (
            f"🔔 <b>{escape(display_name)}</b>\n"
            f"{escape(update_type)}\n\n"
            f"📄 <code>{escape(filename)}</code>\n"
            f"📅 {escape(info['date'])}"
        )

        buttons = []
        channel_button = telegram_url_button('Kanal Gönderisi', channel_url)
        if channel_button:
            buttons.append(channel_button)
        source_button = telegram_url_button('Kaynak', source_url)
        if source_button:
            buttons.append(source_button)
        if not buttons:
            buttons = None

        try:
            message = await self.tg_client.send_message(
                DISCUSSION_GROUP_ID, text, parse_mode='html', buttons=buttons
            )
            print(f"[DISCUSSION] '{name}' için bağlı gruba bildirim gönderildi. Mesaj ID: {message.id}")
            return message.id
        except Exception as e:
            print(f"[WARNING] '{name}' bağlı grup bildirimi gönderilemedi: {e}")
            return None

    async def retry_pending_discussions(self, state, modules_map, skip_names=None):
        if skip_names is None:
            skip_names = set()

        changed = False
        tg_state = state.setdefault("telegram_state", {})

        for name, entry in sorted(tg_state.items()):
            if name in skip_names:
                continue

            pending = entry.get('pending_discussion')
            if not pending:
                continue

            channel_message_id = pending.get('channel_message_id') or entry.get('message_id')
            if not channel_message_id:
                print(f"[WARNING] '{name}' pending grup bildirimi için kanal mesaj ID bulunamadı.")
                continue

            print(f"[DISCUSSION] '{name}' için bekleyen grup bildirimi yeniden deneniyor...")
            discussion_message_id = await self._send_discussion_notification(
                name, pending, modules_map, channel_message_id
            )

            if discussion_message_id:
                entry['discussion_message_id'] = discussion_message_id
                entry['discussion_version_id'] = pending.get('version_id')
                entry.pop('pending_discussion', None)
                changed = True

        return changed

    async def _publish_single_update(self, name, info, modules_map, old_tg_entry):
        try:
            filename = info['file_name']
            module_def = modules_map.get(name, {})
            display_name = module_def.get('description', name)
            source_url = info['source_url']
            link_only = should_publish_link_only(module_def, filename) or info.get('link_only')
            old_message_id = old_tg_entry.get('message_id')
            old_was_link_only = old_tg_entry.get('link_only')
            caption = (
                f"📦 <b>{escape(display_name)}</b>\n\n"
                f"📄 <b>Dosya Adı:</b> <code>{escape(filename)}</code>\n"
                f"📅 <b>Güncelleme:</b> {escape(info['date'])}\n\n"
                f"{source_caption_line(source_url)}"
            )

            buttons = None
            module_type = module_def.get('type')

            if link_only:
                source_button = telegram_url_button('Kaynağa Git', source_url)
                buttons = [source_button] if source_button else None
            elif module_type == 'telegram_forwarder':
                # DÜZELTME 1: Button.url kullanımı
                source_button = telegram_url_button('Kaynak Mesaja Git', source_url)
                buttons = [source_button] if source_button else None
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

            if link_only:
                if old_message_id and old_was_link_only:
                    print(f"[TELEGRAM] '{filename}' için mevcut link mesajı düzenleniyor...")
                    message = await self.tg_client.edit_message(
                        PUBLISH_CHANNEL_ID, old_message_id, caption, parse_mode='html', buttons=buttons
                    )
                    edited = True
                else:
                    print(f"[TELEGRAM] '{filename}' için sadece kaynak linki yayınlanıyor...")
                    message = await self.tg_client.send_message(
                        PUBLISH_CHANNEL_ID, caption, parse_mode='html', silent=True, buttons=buttons
                    )
                    edited = False
            else:
                filepath = os.path.join(CACHE_DIR, filename)
                if not os.path.exists(filepath):
                    print(f"[ERROR] Dosya bulunamadı: {filepath}")
                    return None

                edited = False
                if old_message_id and not old_was_link_only:
                    try:
                        print(f"[TELEGRAM] '{filename}' için mevcut dosya mesajı düzenleniyor...")
                        message = await self.tg_client.edit_message(
                            PUBLISH_CHANNEL_ID, old_message_id, caption,
                            file=filepath, parse_mode='html', buttons=buttons
                        )
                        edited = True
                    except Exception as e:
                        print(f"[WARNING] '{name}' mevcut mesajı düzenlenemedi, yeni mesaj atılacak: {e}")
                        message = None

                if not edited:
                    print(f"[TELEGRAM] '{filename}' yükleniyor...")
                    # Telethon Button.url kullandığında otomatik olarak doğru formatı ayarlar
                    message = await self.tg_client.send_file(
                        PUBLISH_CHANNEL_ID, filepath, caption=caption, parse_mode='html', silent=True, buttons=buttons
                    )

            print(f"[SUCCESS] '{name}' güncellendi. Mesaj ID: {message.id}")
            discussion_message_id = None
            if edited:
                discussion_message_id = await self._send_discussion_notification(
                    name, info, modules_map, message.id
                )

            return name, {
                'message_id': message.id,
                'file_name': filename,
                'version_id': info['version_id'],
                'link_only': link_only,
                'edited': edited,
                'discussion_message_id': discussion_message_id
            }
        except Exception as e:
            print(f"[CRITICAL] '{name}' yayınlanırken istisna: {e}")
            traceback.print_exc()
            return name, None

    async def publish_updates(self, pending_updates):
        print("\n--- Telegram Yayınlama Aşaması ---")
        state = self.state_manager.load_state()
        state.setdefault("manifest", {})
        state.setdefault("telegram_state", {})
        tg_state = state["telegram_state"]
        modules = self.state_manager._load_json(MODULES_FILE_SRC, strict=True).get('modules', [])
        modules_map = {m['name']: m for m in modules}
        retried_discussions = await self.retry_pending_discussions(
            state, modules_map, skip_names=set(pending_updates)
        )

        if not pending_updates:
            if retried_discussions:
                self.state_manager.save_state(state)
            print("[INFO] Yayınlanacak bir şey yok.")
            return

        for name, info in sorted(pending_updates.items()):
            res = await self._publish_single_update(name, info, modules_map, tg_state.get(name, {}))
            if not res:
                continue

            name, data = res
            if not data:
                continue

            old_state = state["manifest"].get(name, {})
            old_tg_entry = tg_state.get(name, {})
            old_message_id = old_tg_entry.get('message_id')
            old_was_link_only = old_tg_entry.get('link_only')
            old_file = old_state.get('file_name')
            link_only = data.get('link_only')
            edited = data.pop('edited', False)
            discussion_message_id = data.get('discussion_message_id')

            if edited:
                if discussion_message_id:
                    data['discussion_version_id'] = info['version_id']
                    data.pop('pending_discussion', None)
                else:
                    data['pending_discussion'] = self._build_pending_discussion(info, data['message_id'])

            if old_message_id and not edited and (not link_only or old_was_link_only):
                try:
                    await self.tg_client.delete_messages(PUBLISH_CHANNEL_ID, [old_message_id])
                except Exception as e:
                    print(f"[WARNING] '{name}' eski mesajı silinemedi: {e}")

            if old_file and old_file != info['file_name'] and not link_only:
                try:
                    os.remove(os.path.join(CACHE_DIR, old_file))
                except OSError:
                    pass # Dosya yoksa sorun değil

            state["manifest"][name] = info
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
            pending_updates = await handler.process_modules()
            
            publisher = TelethonPublisher(tg_client, state_manager)
            await publisher.publish_updates(pending_updates)

    print("\n[INFO] Tüm işlemler başarıyla tamamlandı.")

if __name__ == "__main__":
    asyncio.run(main())
