import httpx
import sys
import asyncio

async def inspect_url(url: str):
    """
    Verilen URL'ye bir istek yapar ve ayrıntılı yanıt bilgilerini yazdırır.
    """
    # Ana betiğimizdeki ve tarayıcılardaki yaygın başlıkları taklit eden başlıklar
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/107.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8,application/signed-exchange;v=b3;q=0.9",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    }
    print(f"--- URL İnceleniyor: {url} ---")
    try:
        # `follow_redirects=True` yönlendirmeleri otomatik olarak takip eder
        async with httpx.AsyncClient(headers=headers, follow_redirects=True, timeout=30) as client:
            response = await client.get(url)
            
            print("\n--- YANIT KODU ---")
            print(response.status_code)
            
            print("\n--- YANIT BAŞLIKLARI (HEADERS) ---")
            for key, value in response.headers.items():
                print(f"{key}: {value}")
            
            # Yanıt bir yönlendirme geçmişine sahipse, bunu yazdır
            if response.history:
                print("\n--- YÖNLENDİRME GEÇMİŞİ ---")
                for i, r in enumerate(response.history):
                    print(f"Adım #{i+1}: {r.status_code} -> {r.headers.get('location')}")
                print(f"Son Adım: {response.status_code} -> {response.url}")

            print("\n--- YANIT İÇERİĞİ (ilk 1000 karakter) ---")
            print(response.text[:1000])

    except httpx.RequestError as e:
        print(f"\n--- İSTEK BAŞARISIZ OLDU ---")
        print(f"Bir hata oluştu: {e}")

if __name__ == "__main__":
    if len(sys.argv) != 2:
        print("Kullanım: python test_nightly.py <incelenecek_url>")
        sys.exit(1)
    
    target_url = sys.argv[1]
    asyncio.run(inspect_url(target_url))
