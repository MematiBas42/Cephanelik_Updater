import asyncio
from pyrogram import Client

async def generate_session():
    print("--- Pyrogram Session String Generator ---")
    print("NOT: Bu yöntemle session alırsak ana scripti Pyrogram'a çevirmemiz gerekecek.")
    
    api_id = input("Enter your API ID: ").strip()
    api_hash = input("Enter your API HASH: ").strip()

    async with Client(":memory:", api_id=int(api_id), api_hash=api_hash) as app:
        session_string = await app.export_session_string()
        print("\n" + "="*50)
        print("BAŞARILI! PYROGRAM SESSION STRING:")
        print("="*50)
        print(session_string)
        print("="*50)
        print("\nNOT: Bu string Telethon ile çalışmaz. Eğer bunu alabilirseniz söyleyin, ana scripti güncelleyelim.")

if __name__ == "__main__":
    asyncio.run(generate_session())

