import requests
import json
import sys

# Configure stdout to use UTF-8 if possible, or backslashreplace
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

BASE_URL = "http://localhost:8090"

API_KEY = "any-key-since-proxy-overrides-it"

headers = {
    "Authorization": f"Bearer {API_KEY}",
    "Content-Type": "application/json"
}

def test_chat():
    print("\n[1] Testing Chat Completion...")
    url = f"{BASE_URL}/domains/models/capabilities/chat-complete/execute"
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "Say hello in Arabic shortly"}],
        "max_tokens": 50
    }
    try:
        r = requests.post(url, headers=headers, json=payload)
        print("Status Code:", r.status_code)
        try:
            print("Response JSON:")
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except:
            print("Response text:", r.text)
    except Exception as e:
        print("Error:", e)

def test_image():
    print("\n[2] Testing Image Generation...")
    url = f"{BASE_URL}/domains/media/capabilities/image-generate/execute"
    payload = {
        "prompt": "a cute orange cat running in a sunny green garden, highly detailed",
        "model": "flux-1.1-pro"
    }
    try:
        r = requests.post(url, headers=headers, json=payload)
        print("Status Code:", r.status_code)
        try:
            print("Response JSON:")
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except:
            print("Response text:", r.text)
    except Exception as e:
        print("Error:", e)

def test_video():
    print("\n[3] Testing Video Generation...")
    url = f"{BASE_URL}/domains/media/capabilities/video-generate/execute"
    payload = {
        "prompt": "a cute orange cat running in a sunny green garden, slow motion",
        "model": "luma-ray"
    }
    try:
        r = requests.post(url, headers=headers, json=payload)
        print("Status Code:", r.status_code)
        try:
            print("Response JSON:")
            print(json.dumps(r.json(), indent=2, ensure_ascii=False))
        except:
            print("Response text:", r.text)
    except Exception as e:
        print("Error:", e)

if __name__ == "__main__":
    print("Starting AgentRouter proxy test...")
    test_chat()
    test_image()
    test_video()
