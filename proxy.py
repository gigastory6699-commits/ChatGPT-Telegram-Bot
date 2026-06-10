import os
import sys
import json
import requests
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler

# Configure stdout to use UTF-8 if possible, or backslashreplace
if sys.platform.startswith('win'):
    sys.stdout.reconfigure(encoding='utf-8', errors='backslashreplace')
    sys.stderr.reconfigure(encoding='utf-8', errors='backslashreplace')

def load_env_file():
    """تحميل متغيرات البيئة من ملف .env يدوياً"""
    if os.path.exists(".env"):
        try:
            with open(".env", "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, v = line.strip().split("=", 1)
                        k = k.strip()
                        v = v.strip().strip('"').strip("'")
                        if k and k not in os.environ:
                            os.environ[k] = v
        except Exception as e:
            print(f"Error loading .env file: {e}")

load_env_file()

# ═══════════════════════════════════════════════════════════════════
# الإعدادات
# ═══════════════════════════════════════════════════════════════════
CONFIG = {
    "proxy_host": "0.0.0.0",
    "proxy_port": int(os.environ.get("PROXY_PORT", 8090)),
    "real_api_url": os.environ.get("REAL_API_URL", "https://api.vectorengine.ai"),
    "real_api_key": os.environ.get("REAL_API_KEY", "sk-189RC16HMbO4fhp0Fgt0bZmpEGAB4yNZIqbaBMaFHC8BOc2a"),
    "mode": os.environ.get("PROXY_MODE", "FAKE"),
    "free_image_generation": os.environ.get("FREE_IMAGE_GENERATION", "True") == "True"
}

def extract_first_url(data):
    """البحث عن أول رابط (URL) في كائن الرد JSON"""
    if isinstance(data, dict):
        if "url" in data and isinstance(data["url"], str):
            return data["url"]
        for val in data.values():
            url = extract_first_url(val)
            if url:
                return url
    elif isinstance(data, list):
        for item in data:
            url = extract_first_url(item)
            if url:
                return url
    return None

class FakeZeroProxy(BaseHTTPRequestHandler):
    def _forward_request(self, method, path):
        # التحقق مما إذا كان العنوان المستهدف هو AgentRouter أو منصة قياسية مثل Vector Engine
        is_agentrouter = "agentrouter.to" in CONFIG["real_api_url"]
        is_chat = "/chat/completions" in path
        
        mapped_path = path
        is_image = False
        is_video = False
        is_stream = False
        
        if is_chat:
            if is_agentrouter:
                mapped_path = "/domains/models/capabilities/chat-complete/execute"
            else:
                mapped_path = "/v1/chat/completions"
        elif "/images/generations" in path:
            is_image = True
            if is_agentrouter:
                mapped_path = "/domains/media/capabilities/image-generate/execute"
            else:
                mapped_path = "/v1/images/generations"
        elif "/videos/generations" in path:
            is_video = True
            if is_agentrouter:
                mapped_path = "/domains/media/capabilities/video-generate/execute"
            else:
                mapped_path = "/v1/images/generations"

        # تحديد العنوان الفعلي بناءً على نوع الطلب
        if is_chat:
            # توجيه طلبات المحادثة بالكامل لـ Groq لتفادي أي خصم للرصيد
            dest_url = "https://api.groq.com/openai/v1/chat/completions"
        else:
            base_url = CONFIG["real_api_url"].rstrip("/")
            if is_agentrouter:
                dest_url = base_url + mapped_path
            else:
                cleaned_path = mapped_path
                if base_url.endswith("/v1") and cleaned_path.startswith("/v1/"):
                    cleaned_path = cleaned_path[3:] # إزالة تكرار /v1
                dest_url = base_url + cleaned_path
        
        headers = {}
        for key in self.headers:
            if key.lower() not in ["host", "content-length", "accept-encoding", "authorization"]:
                headers[key] = self.headers[key]
        
        # تعيين ترويسة المصادقة المناسبة
        if is_chat:
            groq_key = os.environ.get("GROQ_API_KEY")
            headers["Authorization"] = f"Bearer {groq_key}"
        else:
            incoming_auth = self.headers.get("Authorization")
            if incoming_auth:
                headers["Authorization"] = incoming_auth
            elif CONFIG["real_api_key"]:
                headers["Authorization"] = f"Bearer {CONFIG['real_api_key']}"
            
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        
        # تهيئة مدخلات رسم الصور أو توليد الفيديو
        if (is_image or is_video) and body:
            try:
                payload = json.loads(body.decode('utf-8'))
                if is_agentrouter:
                    default_model = "flux-1.1-pro" if is_image else "luma-ray"
                    model = payload.get("model")
                    if not is_image and model == "kling-video":
                        model = "luma-ray"
                    ar_payload = {
                        "prompt": payload.get("prompt", ""),
                        "model": model or default_model
                    }
                    body = json.dumps(ar_payload).encode('utf-8')
                else:
                    default_model = "flux-1.1-pro" if is_image else "grok-video-3"
                    model = payload.get("model")
                    if not is_image and (model == "luma-ray" or model == "kling-video"):
                        model = "grok-video-3"
                    payload["model"] = model or default_model
                    body = json.dumps(payload).encode('utf-8')
            except Exception as e:
                print(f"Error mapping media payload: {e}")
        
        # للدردشة، تحقق من رغبة العميل بالبث المباشر (stream) وقم بإيقافه عند الاتصال بالسيرفر
        if is_chat and body:
            try:
                payload = json.loads(body.decode('utf-8'))
                
                # تعيين أقوى نموذج شات من Groq
                payload["model"] = "openai/gpt-oss-120b"
                
                if payload.get("stream") is True:
                    is_stream = True
                    payload["stream"] = False
                body = json.dumps(payload).encode('utf-8')
            except Exception as e:
                print(f"Error reading/modifying stream flag: {e}")

        try:
            print(f"Forwarding to: {dest_url}")
            if method == "GET":
                resp = requests.get(dest_url, headers=headers, timeout=30)
            elif method == "POST":
                resp = requests.post(dest_url, headers=headers, data=body, timeout=30)
            else:
                resp = requests.request(method, dest_url, headers=headers, data=body, timeout=30)
            return resp, is_image, is_video, is_stream
        except Exception as e:
            print(f"Error forwarding request: {e}")
            traceback.print_exc()
            return None, is_image, is_video, is_stream

    def _send_response(self, data, status_code, headers=None):
        self.send_response(status_code)
        
        if headers:
            for k, v in headers.items():
                if k.lower() not in ["content-length", "transfer-encoding", "content-encoding", "content-type"]:
                    self.send_header(k, v)
        
        self.send_header("Content-Type", "application/json; charset=utf-8")
        
        response_bytes = json.dumps(data, ensure_ascii=False).encode('utf-8')
        self.send_header("Content-Length", str(len(response_bytes)))
        self.end_headers()
        self.wfile.write(response_bytes)

    def _fake_usage(self, response_data):
        if isinstance(response_data, dict):
            for k, v in list(response_data.items()):
                if k in ["completion_tokens", "total_tokens", "prompt_tokens"]:
                    response_data[k] = 0
                else:
                    self._fake_usage(v)
        elif isinstance(response_data, list):
            for item in response_data:
                self._fake_usage(item)
        return response_data

    def do_GET(self):
        path = self.path
        print(f"GET {path}")
        
        if "/models" in path:
            mock_models = {
                "object": "list",
                "data": [
                    {"id": "gpt-4o-mini", "object": "model", "created": 1686935002, "owned_by": "openai"},
                    {"id": "gpt-4o", "object": "model", "created": 1686935002, "owned_by": "openai"},
                    {"id": "flux-1.1-pro", "object": "model", "created": 1686935002, "owned_by": "openai"},
                    {"id": "grok-video-3", "object": "model", "created": 1686935002, "owned_by": "openai"}
                ]
            }
            self._send_response(mock_models, 200)
            return

        resp_tuple = self._forward_request("GET", path)
        if resp_tuple and resp_tuple[0] is not None:
            resp, is_image, is_video, is_stream = resp_tuple
            try:
                data = resp.json()
            except:
                data = {"raw": resp.text}
            
            if CONFIG["mode"] == "FAKE":
                data = self._fake_usage(data)
                
            self._send_response(data, resp.status_code, dict(resp.headers))
        else:
            self._send_response({"error": "Failed to forward request"}, 500)

    def do_POST(self):
        path = self.path
        print(f"POST {path}")
        
        # 🎨 توليد الصور باستخدام Fal.ai مع الانتقال التلقائي لـ Pollinations.ai في حالة فشل الرصيد
        is_image = "/images/generations" in path
        if is_image:
            import urllib.parse
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length) if content_length > 0 else None
            try:
                payload = json.loads(body.decode('utf-8'))
                prompt = payload.get("prompt", "a beautiful scene")
            except Exception:
                prompt = "a beautiful scene"
            
            fal_key = os.environ.get("FAL_API_KEY")
            image_url = None
            
            if fal_key:
                try:
                    print(f"Attempting image generation via Fal.ai (prompt: {prompt})...")
                    fal_url = "https://queue.fal.run/fal-ai/flux/schnell"
                    headers = {
                        "Authorization": f"Key {fal_key}",
                        "Content-Type": "application/json"
                    }
                    fal_payload = {"prompt": prompt}
                    resp = requests.post(fal_url, headers=headers, json=fal_payload, timeout=20)
                    if resp.status_code == 200:
                        resp_data = resp.json()
                        if "images" in resp_data and len(resp_data["images"]) > 0:
                            image_url = resp_data["images"][0]["url"]
                            print("Image generated successfully via Fal.ai!")
                    else:
                        print(f"Fal.ai failed with status {resp.status_code}: {resp.text[:200]}")
                except Exception as e:
                    print(f"Error calling Fal.ai: {e}")
            
            # إذا لم يتم التوليد عبر Fal.ai (بسبب انتهاء الرصيد أو عدم التكوين) نقوم بالتحويل لـ Pollinations المجاني
            if not image_url:
                print("Falling back to Pollinations.ai for free image generation...")
                encoded_prompt = urllib.parse.quote(prompt)
                image_url = f"https://image.pollinations.ai/prompt/{encoded_prompt}?model=flux&nologo=true&private=true"
            
            data = {
                "created": 1600000000,
                "data": [{"url": image_url}]
            }
            self._send_response(data, 200)
            return
            
        resp_tuple = self._forward_request("POST", path)
        
        if resp_tuple and resp_tuple[0] is not None:
            resp, is_image, is_video, is_stream = resp_tuple
            try:
                data = resp.json()
            except:
                data = {"raw": resp.text}
            
            # 🎯 تزوير الاستهلاك
            if CONFIG["mode"] == "FAKE":
                data = self._fake_usage(data)
            
            # مواءمة رد الصور أو الفيديو ليطابق شكل رد OpenAI المتوقع من البوت
            if (is_image or is_video) and resp.status_code == 200:
                media_url = extract_first_url(data)
                if media_url:
                    data = {
                        "created": 1600000000,
                        "data": [{"url": media_url}]
                    }
            
            # إذا كان البث المباشر (stream) مطلوباً والطلب نجح
            if is_stream and resp.status_code == 200:
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream; charset=utf-8")
                self.send_header("Cache-Control", "no-cache")
                self.send_header("Connection", "close")
                self.end_headers()
                
                content = ""
                if isinstance(data, dict):
                    choices = data.get("choices")
                    if choices and len(choices) > 0:
                        msg = choices[0].get("message")
                        if msg:
                            content = msg.get("content", "")
                
                model_name = data.get("model", "gpt-4o-mini") if isinstance(data, dict) else "gpt-4o-mini"
                chat_id = data.get("id", "chatcmpl-fake") if isinstance(data, dict) else "chatcmpl-fake"
                
                # Chunk 1: Role delta
                chunk1 = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": 1600000000,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"role": "assistant"},
                            "finish_reason": None
                        }
                    ]
                }
                self.wfile.write(f"data: {json.dumps(chunk1, ensure_ascii=False)}\n\n".encode('utf-8'))
                self.wfile.flush()
                
                # Chunk 2: Content delta
                if content:
                    chunk2 = {
                        "id": chat_id,
                        "object": "chat.completion.chunk",
                        "created": 1600000000,
                        "model": model_name,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": content},
                                "finish_reason": None
                            }
                        ]
                    }
                    self.wfile.write(f"data: {json.dumps(chunk2, ensure_ascii=False)}\n\n".encode('utf-8'))
                    self.wfile.flush()
                
                # Chunk 3: Finish reason & usage
                chunk3 = {
                    "id": chat_id,
                    "object": "chat.completion.chunk",
                    "created": 1600000000,
                    "model": model_name,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {},
                            "finish_reason": "stop"
                        }
                    ],
                    "usage": {
                        "prompt_tokens": 0,
                        "completion_tokens": 0,
                        "total_tokens": 0
                    }
                }
                self.wfile.write(f"data: {json.dumps(chunk3, ensure_ascii=False)}\n\n".encode('utf-8'))
                self.wfile.flush()
                
                # Done flag
                self.wfile.write(b"data: [DONE]\n\n")
                self.wfile.flush()
                print(f"Response streamed (status=200, mode={CONFIG['mode']})")
                return
            
            self._send_response(data, resp.status_code, dict(resp.headers))
            print(f"Response sent (status={resp.status_code}, mode={CONFIG['mode']})")
        else:
            self._send_response({"error": "Failed to forward request"}, 500)

def main():
    print("=" * 60)
    print("🤖 AI SYSTEM PROXY - FAKE ZERO CONSUMPTION")
    print("=" * 60)
    print(f"Proxy is running on: http://localhost:{CONFIG['proxy_port']}")
    print(f"Real API URL: {CONFIG['real_api_url']}")
    print(f"API Key: {CONFIG['real_api_key'][:15]}...")
    print(f"Mode: {CONFIG['mode']}")
    print("Press Ctrl+C to stop")
    print("=" * 60)
    
    server = HTTPServer((CONFIG["proxy_host"], CONFIG["proxy_port"]), FakeZeroProxy)
    
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping Proxy...")
        server.shutdown()

if __name__ == "__main__":
    main()
