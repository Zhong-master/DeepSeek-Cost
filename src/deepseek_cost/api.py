"""DeepSeek 余额/登录接口封装（只用标准库 urllib，无第三方依赖）。

三种取数方式：

1. ``apikey``    —— 官方接口 ``GET https://api.deepseek.com/user/balance``（Bearer API Key）
2. ``token``     —— 开放平台网页登录态 ``GET /api/v0/users/get_user_summary``（Bearer userToken）
3. ``account``   —— 邮箱 + 密码调用平台登录接口拿到 userToken，再走方式 2

接口地址可用环境变量覆盖（自建代理、私有部署或联调环境）：
``DEEPSEEK_COST_API_BASE`` / ``DEEPSEEK_COST_PLATFORM_BASE``。
"""

from __future__ import annotations

import json
import os
import socket
import ssl
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass, field
from typing import Any

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/126.0.0.0 Safari/537.36"
)
DEFAULT_TIMEOUT = 15

# 错误类型
KIND_AUTH = "auth"        # 凭据无效 / 过期
KIND_NETWORK = "network"  # 断网、超时、DNS
KIND_HTTP = "http"        # 其它 HTTP 错误
KIND_API = "api"          # 业务错误码
KIND_PARSE = "parse"      # 返回格式无法解析


class ApiError(Exception):
    def __init__(self, kind: str, message: str, code: Any = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.code = code


@dataclass
class Balance:
    total: float
    currency: str = "CNY"
    granted: float = 0.0
    topped_up: float = 0.0
    is_available: bool = True
    source: str = ""
    email: str = ""
    extra: dict = field(default_factory=dict)


def api_base() -> str:
    return os.environ.get("DEEPSEEK_COST_API_BASE", "https://api.deepseek.com").rstrip("/")


def platform_base() -> str:
    return os.environ.get("DEEPSEEK_COST_PLATFORM_BASE", "https://platform.deepseek.com").rstrip("/")


# --------------------------------------------------------------------------- #
# 基础 HTTP
# --------------------------------------------------------------------------- #
def _to_float(value: Any, default: float = 0.0) -> float:
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def _request(
    url: str,
    token: str | None = None,
    method: str = "GET",
    payload: dict | None = None,
    timeout: int = DEFAULT_TIMEOUT,
    platform_headers: bool = False,
    extra_headers: dict | None = None,
) -> dict:
    headers = {
        "Accept": "application/json, text/plain, */*",
        "User-Agent": USER_AGENT,
    }
    if platform_headers:
        headers["Referer"] = f"{platform_base()}/usage"
        headers["Origin"] = platform_base()
    if extra_headers:
        headers.update(extra_headers)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    body = None
    if payload is not None:
        body = json.dumps(payload).encode("utf-8")
        headers["Content-Type"] = "application/json"

    request = urllib.request.Request(url, data=body, headers=headers, method=method)
    context = ssl.create_default_context()
    try:
        with urllib.request.urlopen(request, timeout=timeout, context=context) as response:
            raw = response.read()
    except urllib.error.HTTPError as exc:  # 4xx / 5xx
        detail = ""
        try:
            raw = exc.read().decode("utf-8", "replace")
            parsed = json.loads(raw)
            detail = (
                parsed.get("error", {}).get("message")
                if isinstance(parsed.get("error"), dict)
                else parsed.get("msg") or parsed.get("message")
            ) or raw[:200]
        except Exception:  # noqa: BLE001 - 错误信息尽力而为
            detail = ""
        if exc.code in (401, 403):
            raise ApiError(KIND_AUTH, f"凭据无效或已过期（HTTP {exc.code}）{('：' + detail) if detail else ''}", exc.code)
        raise ApiError(KIND_HTTP, f"接口返回 HTTP {exc.code}{('：' + detail) if detail else ''}", exc.code)
    except (urllib.error.URLError, socket.timeout, TimeoutError, OSError) as exc:
        reason = getattr(exc, "reason", exc)
        raise ApiError(KIND_NETWORK, f"网络请求失败：{reason}") from exc

    if not raw:
        return {}
    try:
        data = json.loads(raw.decode("utf-8", "replace"))
    except ValueError as exc:
        raise ApiError(KIND_PARSE, f"接口返回不是合法 JSON：{exc}") from exc
    if not isinstance(data, dict):
        raise ApiError(KIND_PARSE, "接口返回格式不是对象")
    return data


def _field(obj: Any, *names: str) -> Any:
    """只在本层查找字段（不递归），用于区分「字段不存在」与「字段是空列表」。"""
    if not isinstance(obj, dict):
        return None
    wanted = {n.lower().replace("_", "") for n in names}
    for key, value in obj.items():
        if str(key).lower().replace("_", "") in wanted:
            return value
    return None


def _walk(obj: Any, names: tuple[str, ...], max_depth: int = 6) -> Any:
    """深度优先查找第一个命中的键（大小写与下划线不敏感）。"""
    wanted = {n.lower().replace("_", "") for n in names}

    def norm(key: str) -> str:
        return str(key).lower().replace("_", "")

    def visit(node: Any, depth: int) -> Any:
        if depth > max_depth:
            return None
        if isinstance(node, dict):
            for key, value in node.items():
                if norm(key) in wanted and value not in (None, "", [], {}):
                    return value
            for value in node.values():
                found = visit(value, depth + 1)
                if found is not None:
                    return found
        elif isinstance(node, list):
            for item in node:
                found = visit(item, depth + 1)
                if found is not None:
                    return found
        return None

    return visit(obj, 0)


# --------------------------------------------------------------------------- #
# 方式 1：API Key（官方 /user/balance）
# --------------------------------------------------------------------------- #
def fetch_api_key_balance(api_key: str, prefer_currency: str = "CNY") -> Balance:
    if not api_key.strip():
        raise ApiError(KIND_AUTH, "API Key 为空")
    data = _request(f"{api_base()}/user/balance", token=api_key.strip())
    infos = [i for i in (data.get("balance_infos") or []) if isinstance(i, dict)]
    if not infos:
        raise ApiError(KIND_PARSE, "接口未返回余额信息（balance_infos 为空或格式异常）")
    chosen = next(
        (i for i in infos if str(i.get("currency", "")).upper() == prefer_currency.upper()),
        infos[0],
    )
    total = _to_float(chosen.get("total_balance"))
    granted = _to_float(chosen.get("granted_balance"))
    topped = _to_float(chosen.get("topped_up_balance"))
    return Balance(
        total=total,
        currency=str(chosen.get("currency") or prefer_currency).upper(),
        granted=granted,
        topped_up=topped,
        is_available=bool(data.get("is_available", True)),
        source="apikey",
        extra={"balance_infos": infos},
    )


# --------------------------------------------------------------------------- #
# 方式 2：平台网页令牌
# --------------------------------------------------------------------------- #
def _platform_envelope(data: dict, what: str) -> Any:
    code = data.get("code")
    if code not in (None, 0, "0"):
        msg = str(data.get("msg") or data.get("message") or "")
        if str(code) in ("40002", "40003") or "token" in msg.lower() and "expire" in msg.lower():
            raise ApiError(KIND_AUTH, f"平台登录已过期（{code}）{('：' + msg) if msg else ''}", code)
        raise ApiError(KIND_API, f"{what}失败（{code}）{('：' + msg) if msg else ''}", code)
    inner = _walk(data, ("biz_data", "bizData", "data"))
    if inner is None or not isinstance(inner, dict):
        raise ApiError(KIND_PARSE, f"{what}返回数据为空或格式异常")
    biz_code = inner.get("biz_code", inner.get("bizCode"))
    if biz_code not in (None, 0, "0"):
        biz_msg = str(inner.get("biz_msg") or inner.get("bizMsg") or "")
        raise ApiError(KIND_API, f"{what}失败（{biz_code}）{('：' + biz_msg) if biz_msg else ''}", biz_code)
    nested = _walk(inner, ("biz_data", "bizData"))
    if nested is not None:
        return nested
    return inner


def fetch_platform_user(token: str) -> tuple[str, str]:
    """校验令牌并返回 (邮箱, 货币)。"""
    if not token.strip():
        raise ApiError(KIND_AUTH, "令牌为空")
    data = _request(
        f"{platform_base()}/auth-api/v0/users/current",
        token=token.strip(),
        platform_headers=True,
    )
    info = _platform_envelope(data, "获取用户信息")
    email = ""
    currency = "CNY"
    if isinstance(info, dict):
        email = str(info.get("email") or "")
        currency = str(info.get("currency") or "CNY").upper()
    return email, currency


def fetch_platform_balance(token: str, prefer_currency: str = "CNY") -> Balance:
    """平台账户汇总：normal_wallets（总额）/ bonus_wallets（赠送）。"""
    if not token.strip():
        raise ApiError(KIND_AUTH, "令牌为空")
    data = _request(
        f"{platform_base()}/api/v0/users/get_user_summary",
        token=token.strip(),
        platform_headers=True,
    )
    summary = _platform_envelope(data, "获取账户余额")
    if not isinstance(summary, dict):
        raise ApiError(KIND_PARSE, "账户余额返回格式异常")

    normal = _field(summary, "normal_wallets", "normalWallets")
    bonus = _field(summary, "bonus_wallets", "bonusWallets")
    # 字段缺失或类型不对时宁可报错，也不要当作 0 元（否则会误报“余量不足”）
    if normal is None and bonus is None:
        raise ApiError(KIND_PARSE, "账户余额返回格式异常（未找到 normal_wallets/bonus_wallets）")
    if normal is not None and not isinstance(normal, list):
        raise ApiError(KIND_PARSE, "normal_wallets 字段格式异常")
    if bonus is not None and not isinstance(bonus, list):
        raise ApiError(KIND_PARSE, "bonus_wallets 字段格式异常")

    def pick(wallets: Any) -> dict:
        if not isinstance(wallets, list) or not wallets:
            return {}
        dicts = [w for w in wallets if isinstance(w, dict)]
        for wallet in dicts:
            if str(wallet.get("currency", "")).upper() == prefer_currency.upper():
                return wallet
        return dicts[0] if dicts else {}

    normal_wallet = pick(normal)
    bonus_wallet = pick(bonus)
    granted = _to_float(bonus_wallet.get("balance"))
    if normal_wallet:
        total = _to_float(normal_wallet.get("balance"))
    else:
        total = granted
    currency = str(
        normal_wallet.get("currency") or bonus_wallet.get("currency") or prefer_currency
    ).upper()
    return Balance(
        total=total,
        currency=currency,
        granted=granted,
        topped_up=max(0.0, total - granted),
        is_available=total > 0,
        source="platform",
        extra={"summary": summary},
    )


# --------------------------------------------------------------------------- #
# 方式 3：邮箱 + 密码登录（平台私有接口，尽力而为）
# --------------------------------------------------------------------------- #
def login_account(email: str, password: str) -> tuple[str, str]:
    """返回 (userToken, 邮箱)。失败时抛出 ApiError（含平台返回的提示）。"""
    email = (email or "").strip()
    if not email or not password:
        raise ApiError(KIND_AUTH, "请输入邮箱和密码")
    payload = {
        "email": email,
        "password": password,
        "device_id": str(uuid.uuid5(uuid.NAMESPACE_URL, f"deepseek-cost:{email.lower()}")),
        "os": "web",
        "token": "",
    }
    data = _request(
        f"{platform_base()}/auth-api/v0/users/login",
        method="POST",
        payload=payload,
        platform_headers=True,
    )
    code = data.get("code")
    if code not in (None, 0, "0"):
        msg = str(data.get("msg") or data.get("message") or "登录被拒绝")
        raise ApiError(KIND_AUTH, f"账户登录失败：{msg}", code)
    token = _walk(data, ("user_token", "userToken", "access_token", "accessToken", "token"))
    if not isinstance(token, str) or len(token.strip()) < 8:
        raise ApiError(
            KIND_AUTH,
            "登录接口未返回可用令牌（可能需要图形验证码），请改用「浏览器令牌」或「API Key」方式。",
        )
    token = token.strip()
    try:
        server_email, _ = fetch_platform_user(token)
    except ApiError as exc:
        raise ApiError(KIND_AUTH, f"登录成功但令牌校验失败：{exc.message}") from exc
    return token, (server_email or email)


# --------------------------------------------------------------------------- #
# 统一入口
# --------------------------------------------------------------------------- #
def fetch_balance(mode: str, credential: str, prefer_currency: str = "CNY") -> Balance:
    if mode == "apikey":
        return fetch_api_key_balance(credential, prefer_currency)
    if mode in ("token", "account"):
        return fetch_platform_balance(credential, prefer_currency)
    raise ApiError(KIND_AUTH, "尚未登录")


def validate_login(mode: str, credential: str, prefer_currency: str = "CNY") -> tuple[Balance, str]:
    """校验凭据并返回 (余额, 邮箱)。"""
    if mode == "apikey":
        return fetch_api_key_balance(credential, prefer_currency), ""
    if mode in ("token", "account"):
        email, _ = fetch_platform_user(credential)
        return fetch_platform_balance(credential, prefer_currency), email
    raise ApiError(KIND_AUTH, "尚未登录")


# --------------------------------------------------------------------------- #
# 从网页 localStorage 里提取令牌（内嵌登录窗口用）
# --------------------------------------------------------------------------- #
def token_candidates(storage: dict) -> list[str]:
    def unwrap(raw: str) -> str:
        try:
            obj = json.loads(raw)
        except (ValueError, TypeError):
            return raw
        if isinstance(obj, dict) and isinstance(obj.get("value"), str) and obj["value"]:
            return obj["value"]
        return raw

    result: list[str] = []

    def add(value: Any) -> None:
        if not isinstance(value, str):
            return
        text = value.strip()
        if text and text not in result:
            result.append(text)

    raw_token = storage.get("userToken")
    if isinstance(raw_token, str) and raw_token:
        add(unwrap(raw_token))
    for key, value in storage.items():
        if isinstance(value, str) and "token" in str(key).lower() and len(value) >= 20:
            add(unwrap(value))
    for value in storage.values():
        if isinstance(value, str) and 40 <= len(value) <= 512:
            add(unwrap(value))
    return result


# --------------------------------------------------------------------------- #
# 按 API Key 的用量（平台私有接口，需要网页登录令牌）
# --------------------------------------------------------------------------- #
TOKEN_TYPES = (
    "PROMPT_TOKEN",
    "PROMPT_CACHE_HIT_TOKEN",
    "PROMPT_CACHE_MISS_TOKEN",
    "RESPONSE_TOKEN",
)


@dataclass
class ApiKeyInfo:
    tracking_id: str
    name: str
    key_type: str = ""
    last_use: int = 0
    masked_key: str = ""
    valid: bool = True


@dataclass
class KeyUsage:
    tracking_id: str = ""
    name: str = ""
    requests: int = 0
    tokens: int = 0
    cost: float = 0.0
    currency: str = "CNY"
    day: str = ""
    hours: int = 0
    keys_today: int = 0


def _platform_web_headers() -> dict:
    """用量类接口需要 x-client-platform: web。"""
    return {"x-client-platform": "web", "Accept": "application/json"}


def day_window(day_offset: int = 0, now: float | None = None) -> tuple[int, int, int, str]:
    """返回本地时区的 [当天 00:00, 次日 00:00) 时间戳、tz 偏移秒与日期字符串。"""
    moment = time.time() if now is None else now
    local = time.localtime(moment)
    midnight = int(
        time.mktime(
            (local.tm_year, local.tm_mon, local.tm_mday, 0, 0, 0, local.tm_wday, local.tm_yday, local.tm_isdst)
        )
    )
    start = midnight - int(day_offset) * 86400
    tz_seconds = -(time.altzone if local.tm_isdst else time.timezone)
    day = time.strftime("%Y-%m-%d", time.localtime(start))
    return start, start + 86400, tz_seconds, day


def fetch_api_keys(token: str) -> list[ApiKeyInfo]:
    """平台的 API Key 列表（密钥本身由服务端掩码，不会拿到明文）。"""
    if not token.strip():
        raise ApiError(KIND_AUTH, "令牌为空")
    data = _request(
        f"{platform_base()}/api/v0/users/get_api_keys",
        token=token.strip(),
        platform_headers=True,
        extra_headers=_platform_web_headers(),
    )
    payload = _platform_envelope(data, "获取 API Key 列表")
    raw = _field(payload, "api_keys") if isinstance(payload, dict) else None
    if raw is None:
        raise ApiError(KIND_PARSE, "API Key 列表返回格式异常")
    keys: list[ApiKeyInfo] = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        keys.append(
            ApiKeyInfo(
                tracking_id=str(item.get("tracking_id") or ""),
                name=str(item.get("name") or ""),
                key_type=str(item.get("key_type") or ""),
                last_use=int(_to_float(_field(item, "last_use"))),
                masked_key=str(_field(item, "sensitive_id") or ""),
                valid=bool(item.get("valid", True)),
            )
        )
    return keys


def fetch_key_usage(
    token: str,
    tracking_id: str = "",
    currency: str = "CNY",
    day_offset: int = 0,
) -> KeyUsage:
    """某个 API Key 当天的用量（请求数、token 总量、消耗金额）。

    ``tracking_id`` 为空时自动选择当天用量最大的 Key。
    """
    if not token.strip():
        raise ApiError(KIND_AUTH, "令牌为空")
    start, end, tz_seconds, day = day_window(day_offset)
    query = f"start={start}&end={end}&tz={tz_seconds}"
    headers = {"platform_headers": True, "extra_headers": _platform_web_headers()}
    amount = _platform_envelope(
        _request(
            f"{platform_base()}/api/v0/usage/by_api_key/amount?{query}",
            token=token.strip(), timeout=25, **headers
        ),
        "获取今日 token 用量",
    )
    cost = _platform_envelope(
        _request(
            f"{platform_base()}/api/v0/usage/by_api_key/cost?{query}",
            token=token.strip(), timeout=25, **headers
        ),
        "获取今日消费金额",
    )

    if not isinstance(amount, dict):
        raise ApiError(KIND_PARSE, "用量返回格式异常")
    series = _field(amount, "series")
    if series is None:
        raise ApiError(KIND_PARSE, "用量返回缺少 series 字段")
    series = series if isinstance(series, list) else []

    # 按 Key 汇总当天数据
    per_key: dict[str, dict] = {}
    for entry in series:
        if not isinstance(entry, dict):
            continue
        info = _field(entry, "api_key") or {}
        key_id = str(_field(info, "tracking_id") or "")
        name = str(_field(info, "name") or "")
        record = per_key.setdefault(key_id, {"name": name, "requests": 0, "tokens": 0, "cost": 0.0})
        record["name"] = record["name"] or name
        buckets = _field(entry, "buckets")
        for bucket in buckets if isinstance(buckets, list) else []:
            usage = _field(bucket, "usage") if isinstance(bucket, dict) else None
            if not isinstance(usage, dict):
                continue
            for key, value in usage.items():
                number = _to_float(value)
                if str(key).upper() == "REQUEST":
                    record["requests"] += int(number)
                else:
                    record["tokens"] += int(number)

    # 费用
    cost_currency = currency
    cost_series: list = []
    if isinstance(cost, dict):
        data_list = _field(cost, "data")
        if isinstance(data_list, list) and data_list and isinstance(data_list[0], dict):
            cost_currency = str(_field(data_list[0], "currency") or currency).upper()
            cost_series = _field(data_list[0], "series") or []
    for entry in cost_series if isinstance(cost_series, list) else []:
        if not isinstance(entry, dict):
            continue
        info = _field(entry, "api_key") or {}
        key_id = str(_field(info, "tracking_id") or "")
        name = str(_field(info, "name") or "")
        record = per_key.setdefault(key_id, {"name": name, "requests": 0, "tokens": 0, "cost": 0.0})
        record["name"] = record["name"] or name
        for bucket in _field(entry, "buckets") or []:
            if not isinstance(bucket, dict):
                continue
            record["cost"] += _to_float(_field(bucket, "cost"))

    if not per_key:
        # 该时间段确实没有调用：返回一个 0 用量的结果，而不是报错
        return KeyUsage(tracking_id=tracking_id, currency=cost_currency, day=day, keys_today=0)

    if tracking_id:
        # 明确指定了 Key：即使今天没调用也要如实返回 0，而不是退化成“用量最大的那个”
        chosen_id = tracking_id
        record = per_key.get(chosen_id) or {"name": "", "requests": 0, "tokens": 0, "cost": 0.0}
    else:
        chosen_id = max(per_key, key=lambda k: (per_key[k]["requests"], per_key[k]["tokens"]))
        record = per_key[chosen_id]
    return KeyUsage(
        tracking_id=chosen_id,
        name=record["name"],
        requests=int(record["requests"]),
        tokens=int(record["tokens"]),
        cost=float(record["cost"]),
        currency=cost_currency,
        day=day,
        hours=int(_to_float(_field(amount, "bucket")) // 3600) or 24,
        keys_today=len(per_key),
    )


def format_tokens(count: int) -> str:
    """把 token 数量格式化成顶栏可读的紧凑写法。"""
    value = float(count)
    if value >= 1_000_000_000:
        return f"{value / 1_000_000_000:.1f}B"
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return f"{int(value)}"


def describe_error(exc: Exception) -> str:
    if isinstance(exc, ApiError):
        return exc.message
    return str(exc)


def open_console_url() -> str:
    return f"{platform_base()}/usage"
