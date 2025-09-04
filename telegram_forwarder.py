# Bu betik, bir Telegram "kullanıcı hesabı" gibi davranarak belirtilen kanalları
# tarar ve son 24 saat içinde gönderilmiş yeni dosyaları, kaynak kanal
# bilgisini ekleyerek özel bir arşiv kanalına iletir.
# v2 - Sağlamlaştırılmış Sürüm: Session string hatası daha net loglanır ve 'chats' dosyası daha basit okunur.

import os
import json
from datetime import datetime, timezone, timedelta
from telethon.sync import TelegramClient
from telethon.sessions import StringSession

# --- YAPILANDIRMA (HASSAS BİLGİLER) ---
API_ID_STR = os.environ.get('TELEGRAM_API_ID')
if not API_ID_STR:
    raise ValueError("[HATA] TELEGRAM_API_ID ortam değişkeni ayarlanmamış!")
API_ID = int(API_ID_STR)
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# --- YAPILANDIRMA (HASSAS OLMAYAN BİLGİLER) ---
DESTINATION_CHANNEL = -1002542617400
CHATS_FILE = 'chats'
STATE_FILE = 'forwarder_state.json'
client = None

def read_source_chats():
    """Kaynak kanalları 'chats' dosyasından (her satır bir kanal) okur."""
    if not os.path.exists(CHATS_FILE):
        print(f"[HATA] Kaynak kanalları içeren '{CHATS_FILE}' dosyası bulunamadı.")
        return []
    with open(CHATS_FILE, 'r') as f:
        # Sadece satır satır oku, boş satırları ve yorumları atla.
        return [line.strip() for line in f if line.strip() and not line.startswith('#')]

def load_state():
    """Daha önce iletilen son mesaj ID'lerini dosyadan yükler."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {}
    return {}

def save_state(state):
    """En son iletilen mesaj ID'lerini dosyaya kaydeder."""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

async def main():
    print("Otomatik yönlendirici başlatıldı (GitHub Actions Modu).")

    SOURCE_CHATS = read_source_chats()
    if not SOURCE_CHATS:
        print(f"[UYARI] İzlenecek kaynak kanal bulunamadı ('{CHATS_FILE}' dosyası boş veya hatalı). Betik sonlandırılıyor.")
        return

    print(f"Kaynak kanallar: {', '.join(SOURCE_CHATS)}")
    print(f"Hedef kanal: {DESTINATION_CHANNEL}")

    state = load_state()
    time_limit = datetime.now(timezone.utc) - timedelta(days=1)

    for chat_username in SOURCE_CHATS:
        try:
            print(f"\n[{chat_username}] kanalı kontrol ediliyor...")
            chat_entity = await client.get_entity(chat_username)
            chat_id_str = str(chat_entity.id)
            last_forwarded_id = state.get(chat_id_str, 0)
            new_messages_to_forward = []
            highest_message_id_in_batch = last_forwarded_id

            async for message in client.iter_messages(chat_entity, min_id=last_forwarded_id, limit=200):
                if message.date < time_limit:
                    break
                if message.id > highest_message_id_in_batch:
                    highest_message_id_in_batch = message.id
                if message.document:
                    new_messages_to_forward.append(message)

            if not new_messages_to_forward:
                print("-> Yeni dosya bulunamadı.")
                state[chat_id_str] = highest_message_id_in_batch # İlerlemeyi kaydet
                continue

            print(f"-> {len(new_messages_to_forward)} adet yeni dosya bulundu. Yönlendiriliyor...")
            for message in reversed(new_messages_to_forward):
                try:
                    caption = f"Kaynak: @{chat_username}"
                    await client.send_file(DESTINATION_CHANNEL, message.document, caption=caption)
                    file_name = "Bilinmeyen Dosya"
                    if hasattr(message.document, 'attributes'):
                        for attr in message.document.attributes:
                            if hasattr(attr, 'file_name'):
                                file_name = attr.file_name
                                break
                    print(f"  - Dosya '{file_name}' yönlendirildi.")
                except Exception as e:
                    print(f"  - HATA: Mesaj yönlendirilemedi: {e}")

            state[chat_id_str] = highest_message_id_in_batch
        except Exception as e:
            print(f"[{chat_username}] kanalı işlenirken bir hata oluştu: {e}")

    save_state(state)
    print("\nTüm işlemler tamamlandı. Yönlendirici durduruluyor.")

async def run_with_client():
    async with client:
        is_authorized = await client.is_user_authorized()
        if not is_authorized:
            print("\n" + "="*60)
            print("[KRİTİK HATA] Oturum anahtarı (TELEGRAM_SESSION_STRING) geçersiz veya süresi dolmuş!")
            print("[ÇÖZÜM] Lütfen yeni bir oturum anahtarı oluşturup projenizin GitHub Secrets bölümünü güncelleyin.")
            print("="*60 + "\n")
            return
        await main()

if __name__ == "__main__":
    if not SESSION_STRING:
        print("[HATA] TELEGRAM_SESSION_STRING ortam değişkeni bulunamadı!")
    else:
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        client.loop.run_until_complete(run_with_client())
