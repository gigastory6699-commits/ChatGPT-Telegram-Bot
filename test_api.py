import httpx

urls = [
    "https://image.pollinations.ai/prompt/cute%20cat%20running%20in%20garden",
    "https://image.pollinations.ai/prompt/cute%20cat%20running%20in%20garden?width=512&height=512",
    "https://image.pollinations.ai/prompt/cute%20cat%20running%20in%20garden?nologo=true"
]

for url in urls:
    try:
        r = httpx.get(url, timeout=20)
        print(f"URL: {url}")
        print("Status code:", r.status_code)
        print("Content length:", len(r.content))
        print("---")
    except Exception as e:
        print(f"Error for {url}: {e}")
