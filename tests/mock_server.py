"""本地假 DeepSeek 接口，用于离线自测（不联网、不消耗任何额度）。

覆盖的端点与官方一致：

* GET  /user/balance                    （API Key）
* GET  /auth-api/v0/users/current       （平台令牌校验）
* GET  /api/v0/users/get_user_summary   （平台余额汇总）
* POST /auth-api/v0/users/login         （邮箱密码登录）
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

# API Key -> (总额, 赠送, 充值)
API_KEYS = {
    "sk-good": (123.45, 10.00, 113.45),
    "sk-low": (12.34, 0.0, 12.34),
    "sk-zero": (0.0, 0.0, 0.0),
}

# 平台令牌 -> (邮箱, 总额, 赠送)
PLATFORM_TOKENS = {
    "tok-good": ("test@example.com", 123.45, 10.00),
    "tok-low": ("poor@example.com", 12.34, 0.0),
}

LOGIN_PASSWORDS = {"goodpass": "tok-good", "lowpass": "tok-low"}

# 按 API Key 的今日用量假数据：tracking_id -> (名称, 请求数, token 数, 费用)
KEY_USAGE_TODAY = {
    "key-alpha": ("Alpha", 3, 1250, 0.0123),
    "key-beta": ("Beta", 10, 2_000_000, 1.5000),
}
PLATFORM_KEYS = [
    {"tracking_id": "key-alpha", "name": "Alpha", "key_type": "NORMAL", "last_use": 1790960000,
     "sensitive_id": "sk-aaaa****1111", "valid": True},
    {"tracking_id": "key-beta", "name": "Beta", "key_type": "NORMAL", "last_use": 1790967000,
     "sensitive_id": "sk-bbbb****2222", "valid": True},
    {"tracking_id": "key-gamma", "name": "Gamma", "key_type": "NORMAL", "last_use": 0,
     "sensitive_id": "sk-cccc****3333", "valid": True},
]


class _Handler(BaseHTTPRequestHandler):
    server_version = "DeepSeekMock/1.0"

    def log_message(self, *args) -> None:  # 静音
        pass

    # -- 工具 ----------------------------------------------------------
    def _reply(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _token(self) -> str:
        header = self.headers.get("Authorization", "")
        if header.lower().startswith("bearer "):
            return header[7:].strip()
        return ""

    # -- GET -----------------------------------------------------------
    def do_GET(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        token = self._token()
        if path == "/user/balance":
            if token == "sk-broken":
                self._reply(200, {"is_available": True, "balance_infos": "oops"})
                return
            if token not in API_KEYS:
                self._reply(
                    401,
                    {"error": {"message": f"Authentication Fails, Your api key: ****{token[-4:]} is invalid"}},
                )
                return
            total, granted, topped = API_KEYS[token]
            self._reply(
                200,
                {
                    "is_available": total > 0,
                    "balance_infos": [
                        {
                            "currency": "CNY",
                            "total_balance": f"{total:.2f}",
                            "granted_balance": f"{granted:.2f}",
                            "topped_up_balance": f"{topped:.2f}",
                        }
                    ],
                },
            )
            return
        if path in ("/api/v0/users/get_api_keys", "/api/v0/usage/by_api_key/amount", "/api/v0/usage/by_api_key/cost"):
            if token not in PLATFORM_TOKENS:
                self._reply(200, {"code": 40003, "msg": "token expired", "data": None})
                return
            if path == "/api/v0/users/get_api_keys":
                self._reply(200, {"code": 0, "msg": "", "data": {
                    "biz_code": 0, "biz_msg": "", "biz_data": {"api_keys": PLATFORM_KEYS}}})
                return
            query = parse_qs(urlsplit(self.path).query)
            start = int((query.get("start") or ["0"])[0])
            hour = start + 3600
            if path.endswith("/amount"):
                series = []
                for key_id, (_name, req, tokens, _cost) in KEY_USAGE_TODAY.items():
                    series.append({
                        "api_key": next(k for k in PLATFORM_KEYS if k["tracking_id"] == key_id),
                        "model": "deepseek-chat",
                        "buckets": [{"time": hour, "usage": {
                            "REQUEST": req,
                            "PROMPT_CACHE_HIT_TOKEN": tokens - 250,
                            "PROMPT_CACHE_MISS_TOKEN": 200,
                            "RESPONSE_TOKEN": 50}}],
                    })
                self._reply(200, {"code": 0, "msg": "", "data": {
                    "biz_code": 0, "biz_msg": "", "biz_data": {
                        "start": start, "end": start + 86400, "bucket": 3600,
                        "models": ["deepseek-chat"], "series": series}}})
                return
            series = []
            for key_id, (_name, _req, _tokens, cost) in KEY_USAGE_TODAY.items():
                series.append({
                    "api_key": next(k for k in PLATFORM_KEYS if k["tracking_id"] == key_id),
                    "model": "deepseek-chat",
                    "buckets": [{"time": hour, "cost": f"{cost:.6f}"}],
                })
            self._reply(200, {"code": 0, "msg": "", "data": {
                "biz_code": 0, "biz_msg": "", "biz_data": {
                    "start": start, "end": start + 86400, "bucket": 3600, "models": ["deepseek-chat"],
                    "data": [{"currency": "CNY", "series": series}]}}})
            return
        if path == "/auth-api/v0/users/current":
            if token not in PLATFORM_TOKENS:
                self._reply(200, {"code": 40003, "msg": "token expired", "data": None})
                return
            email, _total, _granted = PLATFORM_TOKENS[token]
            self._reply(
                200,
                {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "biz_code": 0,
                        "biz_msg": "",
                        "biz_data": {"id": "12345", "email": email, "currency": "CNY"},
                    },
                },
            )
            return
        if path == "/api/v0/users/get_user_summary":
            if token == "tok-broken":
                self._reply(200, {"code": 0, "msg": "", "data": {"biz_code": 0, "biz_msg": "", "biz_data": {"normal_wallets": "oops"}}})
                return
            if token == "tok-null":
                self._reply(200, {"code": 0, "msg": "", "data": None})
                return
            if token not in PLATFORM_TOKENS:
                self._reply(200, {"code": 40003, "msg": "token expired", "data": None})
                return
            _email, total, granted = PLATFORM_TOKENS[token]
            self._reply(
                200,
                {
                    "code": 0,
                    "msg": "",
                    "data": {
                        "biz_code": 0,
                        "biz_msg": "",
                        "biz_data": {
                            "normal_wallets": [{"currency": "CNY", "balance": f"{total:.2f}"}],
                            "bonus_wallets": [{"currency": "CNY", "balance": f"{granted:.2f}"}],
                            "total_costs": [{"currency": "CNY", "amount": "1.23"}],
                        },
                    },
                },
            )
            return
        self._reply(404, {"error": {"message": f"no such path {path}"}})

    # -- POST ----------------------------------------------------------
    def do_POST(self) -> None:  # noqa: N802
        path = urlsplit(self.path).path
        length = int(self.headers.get("Content-Length") or 0)
        raw = self.rfile.read(length) if length else b"{}"
        try:
            payload = json.loads(raw.decode("utf-8"))
        except ValueError:
            payload = {}
        if path == "/auth-api/v0/users/login":
            password = str(payload.get("password") or "")
            if password in LOGIN_PASSWORDS:
                self._reply(
                    200,
                    {
                        "code": 0,
                        "msg": "",
                        "data": {
                            "biz_code": 0,
                            "biz_msg": "",
                            "biz_data": {
                                "user": {
                                    "id": "12345",
                                    "email": payload.get("email", ""),
                                    "token": LOGIN_PASSWORDS[password],
                                }
                            },
                        },
                    },
                )
                return
            self._reply(200, {"code": 40001, "msg": "密码错误或需要图形验证码", "data": None})
            return
        self._reply(404, {"error": {"message": f"no such path {path}"}})


class MockServer:
    """上下文管理器：with MockServer() as srv: srv.api_base / srv.platform_base"""

    def __init__(self) -> None:
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{port}"

    @property
    def api_base(self) -> str:
        return self.base

    @property
    def platform_base(self) -> str:
        return self.base

    def start(self) -> "MockServer":
        self.thread.start()
        return self

    def stop(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()

    def __enter__(self) -> "MockServer":
        return self.start()

    def __exit__(self, *_exc) -> None:
        self.stop()


if __name__ == "__main__":
    server = MockServer().start()
    print(f"mock DeepSeek API listening on {server.base}")
    print("  API Key : sk-good / sk-low / sk-zero")
    print("  令牌    : tok-good / tok-low")
    print("  登录    : 任意邮箱 + goodpass / lowpass")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        server.stop()
