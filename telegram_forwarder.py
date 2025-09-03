# Bu betik, bir Telegram "kullanıcı hesabı" gibi davranarak belirtilen kanalları
# tarar ve son 24 saat içinde gönderilmiş yeni dosyaları, kaynak kanal
# bilgisini ekleyerek özel bir arşiv kanalına iletir.
# Bu betik, GitHub Actions üzerinde günde bir kez çalışmak üzere tasarlanmıştır.

import os
import json
from datetime import datetime, timezone, timedelta
from telethon.sync import TelegramClient

# --- YAPILANDIRMA ---
# Tüm yapılandırma, GitHub Actions ortam değişkenlerinden (Secrets) alınır.
API_ID = int(os.environ.get('TELEGRAM_API_ID'))
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# Kaynak kanallar JSON formatında okunur. Örnek: '["kanal1", "kanal2"]'
SOURCE_CHATS_JSON = os.environ.get('TELEGRAM_SOURCE_CHATS', '[]')
SOURCE_CHATS = json.loads(SOURCE_CHATS_JSON)

DESTINATION_CHANNEL = os.environ.get('TELEGRAM_ARCHIVE_CHANNEL')
STATE_FILE = 'forwarder_state.json'

# --- BETİK KODU ---
client = TelegramClient(None, API_ID, API_HASH)

def load_state():
    """Daha önce iletilen son mesaj ID'lerini dosyadan yükler."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            return json.load(f)
    return {}

def save_state(state):
    """En son iletilen mesaj ID'lerini dosyaya kaydeder."""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

async def main():
    print("Otomatik yönlendirici başlatıldı (GitHub Actions Modu).")
    print(f"Kaynak kanallar: {', '.join(SOURCE_CHATS)}")
    print(f"Hedef kanal: {DESTINATION_CHANNEL}")

    state = load_state()
    
    # Son 24 saatlik zaman dilimini hesapla
    time_limit = datetime.now(timezone.utc) - timedelta(days=1)

    for chat_username in SOURCE_CHATS:
        try:
            print(f"\n[{chat_username}] kanalı kontrol ediliyor...")
            chat_entity = await client.get_entity(chat_username)
            chat_id_str = str(chat_entity.id)

            # Bu kanal için en son hangi mesajı ilettiğimizi state dosyasından oku
            last_forwarded_id = state.get(chat_id_str, 0)
            
            new_messages_to_forward = []
            highest_message_id_in_batch = last_forwarded_id

            # Sadece son 24 saatte ve en son ilettiğimiz mesajdan sonra gelenleri kontrol et
            async for message in client.iter_messages(chat_entity, min_id=last_forwarded_id, limit=200):
                if message.date < time_limit:
                    break # 24 saatten eskiyse döngüyü kır
                
                if message.id > highest_message_id_in_batch:
                    highest_message_id_in_batch = message.id
                
                if message.document:
                    new_messages_to_forward.append(message)
            
            if not new_messages_to_forward:
                print("-> Yeni dosya bulunamadı.")
                continue

            print(f"-> {len(new_messages_to_forward)} adet yeni dosya bulundu. Yönlendiriliyor...")
            
            # Mesajları eskiden yeniye doğru yönlendir
            for message in reversed(new_messages_to_forward):
                try:
                    # Kaynak kanal bilgisini mesaja ekle
                    caption = f"Kaynak: @{chat_username}"
                    await client.send_file(DESTINATION_CHANNEL, message.document, caption=caption)
                    print(f"  - Dosya '{message.document.attributes[0].file_name}' yönlendirildi.")
                except Exception as e:
                    print(f"  - HATA: Mesaj yönlendirilemedi: {e}")
            
            # Bu kanal için en son işlenen mesaj ID'sini güncelle
            state[chat_id_str] = highest_message_id_in_batch

        except Exception as e:
            print(f"[{chat_username}] kanalı işlenirken bir hata oluştu: {e}")

    save_state(state)
    print("\nTüm işlemler tamamlandı. Yönlendirici durduruluyor.")

# --- Çalıştırma Bloğu ---
async def run():
    await client.connect()
    if not await client.is_user_authorized():
        raise Exception("Oturum anahtarı (session string) geçersiz veya süresi dolmuş!")
    await main()
    await client.disconnect()

if __name__ == "__main__":
    if SESSION_STRING:
        client.session.set_dc(2, '149.154.167.51', 80) # Oturumun hızlı başlaması için yardımcı
        client.session.set_auth_key(None)
        client.session.auth_key = client.session.load_for_string(SESSION_STRING)

    client.loop.run_until_complete(run())

