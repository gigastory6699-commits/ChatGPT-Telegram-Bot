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

# ═══════════════════════════════════════════════════════════════════
# الإعدادات
# ═══════════════════════════════════════════════════════════════════
CONFIG = {
    "proxy_host": "0.0.0.0",
    "proxy_port": int(os.environ.get("PROXY_PORT", 8090)),
    "real_api_url": os.environ.get("REAL_API_URL", "https://api.agentrouter.to/api/agentic-api"),
    "real_api_key": os.environ.get("REAL_API_KEY", "sk-G8IDqowYc202qQlrBsrvpxL8JOtMlIdp0WdTO1w6zLHyAVqY"),
    "mode": os.environ.get("PROXY_MODE", "FAKE")
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
        # تحويل المسارات لتتوافق مع هيكلية AgentRouter Capability-based API
        mapped_path = path
        is_image = False
        is_video = False
        is_stream = False
        
        if "/chat/completions" in path:
            mapped_path = "/domains/models/capabilities/chat-complete/execute"
        elif "/images/generations" in path:
            mapped_path = "/domains/media/capabilities/image-generate/execute"
            is_image = True
        elif "/videos/generations" in path:
            mapped_path = "/domains/media/capabilities/video-generate/execute"
            is_video = True

        dest_url = CONFIG["real_api_url"].rstrip("/") + mapped_path
        
        headers = {}
        for key in self.headers:
            if key.lower() not in ["host", "content-length", "accept-encoding", "authorization"]:
                headers[key] = self.headers[key]
        
        # استخدام التوكن القادم من البوت، أو التوكن الافتراضي من الإعدادات
        incoming_auth = self.headers.get("Authorization")
        if incoming_auth:
            headers["Authorization"] = incoming_auth
        elif CONFIG["real_api_key"]:
            headers["Authorization"] = f"Bearer {CONFIG['real_api_key']}"
            
        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length) if content_length > 0 else None
        
        # تهيئة مدخلات رسم الصور أو توليد الفيديو لتناسب AgentRouter
        if (is_image or is_video) and body:
            try:
                payload = json.loads(body.decode('utf-8'))
                default_model = "flux-1.1-pro" if is_image else "luma-ray"
                ar_payload = {
                    "prompt": payload.get("prompt", ""),
                    "model": payload.get("model") or default_model
                }
                body = json.dumps(ar_payload).encode('utf-8')
            except Exception as e:
                print(f"Error mapping media payload: {e}")
        
        # للدردشة، تحقق من رغبة العميل بالبث المباشر (stream) وقم بإيقافه عند الاتصال بـ AgentRouter
        if "/chat/completions" in path and body:
            try:
                payload = json.loads(body.decode('utf-8'))
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
                    {"id": "luma-ray", "object": "model", "created": 1686935002, "owned_by": "openai"}
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
                self.send_header("Connection", "keep-alive")
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
