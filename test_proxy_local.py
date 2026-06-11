import requests
import json
import sys

# Configure stdout to use UTF-8
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

BASE_URL = "http://localhost:8090/v1"
headers = {
    "Authorization": "Bearer sk-189RC16HMbO4fhp0Fgt0bZmpEGAB4yNZIqbaBMaFHC8BOc2a",
    "Content-Type": "application/json"
}

def test_models():
    print("\n[1] Testing GET /models...")
    r = requests.get(f"{BASE_URL}/models", headers=headers)
    print("Status:", r.status_code)
    try:
        print("Response:", json.dumps(r.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print("Failed to parse JSON:", e, r.text)

def test_chat_stream():
    print("\n[2] Testing POST /chat/completions (Stream)...")
    payload = {
        "model": "gpt-4o-mini",
        "messages": [{"role": "user", "content": "hi"}],
        "stream": True
    }
    r = requests.post(f"{BASE_URL}/chat/completions", headers=headers, json=payload, stream=True)
    print("Status:", r.status_code)
    for line in r.iter_lines():
        if line:
            decoded = line.decode('utf-8', errors='replace')
            print("Line:", decoded)

def test_image():
    print("\n[3] Testing POST /images/generations...")
    payload = {
        "prompt": "a green leaf with water drops",
        "model": "flux-1.1-pro"
    }
    r = requests.post(f"{BASE_URL}/images/generations", headers=headers, json=payload)
    print("Status:", r.status_code)
    try:
        print("Response:", json.dumps(r.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print("Failed to parse JSON:", e, r.text)

def test_video():
    print("\n[4] Testing POST /videos/generations...")
    payload = {
        "prompt": "a floating astronaut in space slow motion",
        "model": "kling-video"
    }
    r = requests.post(f"{BASE_URL}/videos/generations", headers=headers, json=payload)
    print("Status:", r.status_code)
    try:
        print("Response:", json.dumps(r.json(), indent=2, ensure_ascii=False))
    except Exception as e:
        print("Failed to parse JSON:", e, r.text)

if __name__ == "__main__":
    test_models()
    test_chat_stream()
    test_image()
    test_video()
