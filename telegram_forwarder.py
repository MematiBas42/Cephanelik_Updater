# Bu betik, bir Telegram "kullanıcı hesabı" gibi davranarak belirtilen kanalları
# tarar ve son 24 saat içinde gönderilmiş yeni dosyaları, kaynak kanal
# bilgisini ekleyerek özel bir arşiv kanalına iletir.
# Bu betik, GitHub Actions üzerinde günde bir kez çalışmak üzere tasarlanmıştır.

import os
import json
from datetime import datetime, timezone, timedelta
from telethon.sync import TelegramClient
from telethon.sessions import StringSession # <-- EKLENDİ: Bu satır hatayı düzeltmek için kritik.

# --- YAPILANDIRMA ---
# Tüm yapılandırma, GitHub Actions ortam değişkenlerinden (Secrets) alınır.
API_ID = int(os.environ.get('TELEGRAM_API_ID'))
API_HASH = os.environ.get('TELEGRAM_API_HASH')
SESSION_STRING = os.environ.get('TELEGRAM_SESSION_STRING')

# Kaynak kanallar JSON formatında okunur. Örnek: '["kanal1", "kanal2"]'
# GÜNCELLEME: Değişken boş veya tanımsızsa, çökmemesi için varsayılan olarak boş bir liste ('[]') atanır.
SOURCE_CHATS_JSON = os.environ.get('TELEGRAM_SOURCE_CHATS', '[]')
try:
    SOURCE_CHATS = json.loads(SOURCE_CHATS_JSON)
    if not isinstance(SOURCE_CHATS, list):
        print("[UYARI] TELEGRAM_SOURCE_CHATS geçerli bir liste formatında değil. Boş olarak kabul ediliyor.")
        SOURCE_CHATS = []
except json.JSONDecodeError:
    print(f"[HATA] TELEGRAM_SOURCE_CHATS ortam değişkeni geçerli bir JSON formatında değil: {SOURCE_CHATS_JSON}")
    SOURCE_CHATS = []


DESTINATION_CHANNEL = os.environ.get('TELEGRAM_ARCHIVE_CHANNEL')
STATE_FILE = 'forwarder_state.json'

# --- BETİK KODU ---
# Bu client nesnesi sadece ana betik bloğunda session string ile yeniden oluşturulacak.
client = None

def load_state():
    """Daha önce iletilen son mesaj ID'lerini dosyadan yükler."""
    if os.path.exists(STATE_FILE):
        with open(STATE_FILE, 'r') as f:
            try:
                return json.load(f)
            except json.JSONDecodeError:
                return {} # Bozuk state dosyasını yoksay
    return {}

def save_state(state):
    """En son iletilen mesaj ID'lerini dosyaya kaydeder."""
    with open(STATE_FILE, 'w') as f:
        json.dump(state, f, indent=2)

async def main():
    print("Otomatik yönlendirici başlatıldı (GitHub Actions Modu).")
    
    if not SOURCE_CHATS:
        print("[UYARI] İzlenecek kaynak kanal bulunamadı (TELEGRAM_SOURCE_CHATS boş). Betik sonlandırılıyor.")
        return

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
                    
                    # Dosya adını güvenli bir şekilde almayı dene
                    file_name = "Bilinmeyen Dosya"
                    if hasattr(message.document, 'attributes'):
                        for attr in message.document.attributes:
                            if hasattr(attr, 'file_name'):
                                file_name = attr.file_name
                                break
                    print(f"  - Dosya '{file_name}' yönlendirildi.")

                except Exception as e:
                    print(f"  - HATA: Mesaj yönlendirilemedi: {e}")
            
            # Bu kanal için en son işlenen mesaj ID'sini güncelle
            state[chat_id_str] = highest_message_id_in_batch

        except Exception as e:
            print(f"[{chat_username}] kanalı işlenirken bir hata oluştu: {e}")

    save_state(state)
    print("\nTüm işlemler tamamlandı. Yönlendirici durduruluyor.")

# --- Çalıştırma Bloğu ---
async def run_with_client():
    async with client:
        if not await client.is_user_authorized():
            raise Exception("Oturum anahtarı (session string) geçersiz veya süresi dolmuş!")
        await main()

if __name__ == "__main__":
    # Oturum anahtarının varlığını kontrol et
    if not SESSION_STRING:
        print("[HATA] TELEGRAM_SESSION_STRING ortam değişkeni bulunamadı!")
    else:
        # DÜZELTİLDİ: client nesnesi, doğru yöntemle burada başlatılıyor.
        client = TelegramClient(StringSession(SESSION_STRING), API_ID, API_HASH)
        client.loop.run_until_complete(run_with_client())

