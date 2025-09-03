# Bu betik, "sürekli dinlemek" yerine "tarihsel kontrol" yaparak çalışır.
# GitHub Actions üzerinde günde bir kez çalışmak için tasarlanmıştır.

import os
import json
from datetime import datetime, timezone, timedelta
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# --- YAPILANDIRMA ---
# Bilgiler GitHub Sırları'ndan ortam değişkeni olarak alınacak.
API_ID = int(os.environ.get('TELEGRAM_API_ID'))
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# Kaynak ve hedef kanallar
# Kaynak kanalları JSON formatında bir string olarak al ve parse et
SOURCE_CHATS_JSON = os.environ.get('TELEGRAM_SOURCE_CHATS', '["magiskalpha"]')
SOURCE_CHATS = json.loads(SOURCE_CHATS_JSON)
DESTINATION_CHANNEL = os.environ.get('TELEGRAM_ARCHIVE_CHANNEL')
STATE_FILE = 'forwarder_state.json'

# --- BETİK KODU ---
client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)

def load_state():
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try: return json.load(f)
            except json.JSONDecodeError: return {}
    return {}

def save_state(state):
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

async def main():
    await client.connect()
    if not await client.is_user_authorized():
        raise Exception("TELEGRAM_SESSION_STRING geçersiz veya süresi dolmuş.")

    print("Yönlendirici başlatıldı (geçmiş kontrol modu).")
    state = load_state()
    offset_date = datetime.now(timezone.utc) - timedelta(days=2)
    
    for chat_username in SOURCE_CHATS:
        try:
            print(f"\n[{chat_username}] kanalı kontrol ediliyor...")
            chat_entity = await client.get_entity(chat_username)
            chat_id_str = str(chat_entity.id)
            
            last_forwarded_id = state.get(chat_id_str, 0)
            print(f"-> Bu kanal için en son iletilen mesaj ID: {last_forwarded_id}")

            messages_to_forward = []
            new_max_id = last_forwarded_id

            async for message in client.iter_messages(chat_entity, min_id=last_forwarded_id, limit=100, reverse=True):
                if message.document:
                    messages_to_forward.append(message)
                if message.id > new_max_id:
                    new_max_id = message.id
            
            if not messages_to_forward:
                print("-> Yeni dosya bulunamadı.")
                state[chat_id_str] = new_max_id # İD'yi yine de güncelle ki bir dahaki sefere boşuna taramasın
                save_state(state)
                continue

            print(f"-> {len(messages_to_forward)} adet yeni dosya bulundu. Yönlendiriliyor...")
            await client.forward_messages(DESTINATION_CHANNEL, messages_to_forward)
            
            state[chat_id_str] = new_max_id
            save_state(state)
            print(f"-> Yönlendirme tamamlandı. Yeni son mesaj ID: {new_max_id}")

        except Exception as e:
            print(f"HATA: [{chat_username}] işlenirken bir sorun oluştu: {e}")

    print("\nTüm kanallar kontrol edildi. İşlem tamamlandı.")

with client:
    client.loop.run_until_complete(main())
