"""配置与运行时状态的持久化。

* ``~/.config/deepseek-cost/config.json`` —— 登录凭据与偏好设置（权限 0600）
* ``~/.config/deepseek-cost/state.json``  —— 最近一次余额、提醒状态等

两者都可用环境变量 ``DEEPSEEK_COST_DIR`` 指定到其它位置。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

APP_NAME = "deepseek-cost"

CONFIG_DEFAULTS: dict[str, Any] = {
    "version": 1,
    # 登录方式：""（未登录） | "account"（邮箱密码） | "token"（浏览器令牌） | "apikey"
    "auth_mode": "",
    "email": "",
    "password": "",
    "remember_password": False,
    "platform_token": "",
    "api_key": "",
    "currency": "CNY",
    # 低于该余额时弹出提醒
    "threshold": 50.0,
    # 自动刷新间隔（分钟）
    "interval_minutes": 5,
    "notify_enabled": True,
    # 余额持续偏低时，最短重复提醒间隔（小时）
    "notify_repeat_hours": 6.0,
    # 顶栏文字图标高度（像素）
    "panel_height": 22,
    # 今日用量（按 API Key）：是否在顶栏额外用黄色显示
    "usage_display": False,
    "usage_key_id": "",      # 选中的 API Key tracking_id（空 = 自动选当天用量最大的）
    "usage_key_name": "",
    "usage_show_cost": True,
    "usage_show_tokens": True,
    "usage_currency": "CNY",
}

STATE_DEFAULTS: dict[str, Any] = {
    "last_total": None,
    "last_currency": "CNY",
    "last_granted": None,
    "last_topped_up": None,
    "last_updated": 0.0,
    "last_error": "",
    "error_kind": "",
    "alerted": False,
    "last_alert_ts": 0.0,
    "last_usage_cost": None,
    "last_usage_tokens": None,
    "last_usage_requests": None,
    "last_usage_day": "",
    "last_usage_key": "",
}


def config_dir() -> Path:
    override = os.environ.get("DEEPSEEK_COST_DIR")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / APP_NAME


def cache_dir() -> Path:
    override = os.environ.get("DEEPSEEK_COST_CACHE")
    if override:
        return Path(override)
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / APP_NAME


def _read_json(path: Path) -> dict:
    try:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if isinstance(data, dict):
            return data
    except FileNotFoundError:
        pass
    except (OSError, ValueError):
        pass
    return {}


def _write_json_private(path: Path, data: dict) -> None:
    """原子写入，并把文件权限收紧到 0600（凭据文件）。"""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, indent=2, sort_keys=True)
            fh.write("\n")
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        os.chmod(path, 0o600)
    except OSError:
        # 某些文件系统（如 NTFS）不支持 Unix 权限，忽略即可
        pass


def build_credential_updates(
    mode: str,
    credential: str,
    password: str,
    email: str,
    remember_password: bool,
    extra: dict | None = None,
) -> dict:
    """把一次成功登录的结果转成要写入配置的字段。

    ``mode`` 保持调用方传进来的值：邮箱密码登录会保存 ``auth_mode="account"``，
    app 才能在令牌过期后用记住的密码自动重新登录（不要在这里改写成 "token"）。
    """
    updates = {
        "auth_mode": mode,
        "platform_token": credential if mode in ("token", "account") else "",
        "api_key": credential if mode == "apikey" else "",
        "email": email or "",
        "remember_password": bool(remember_password),
        "password": password if (remember_password and password) else "",
    }
    if extra:
        updates.update(extra)
    return updates


class Store:
    """配置 + 状态的小包装，键值访问。"""

    def __init__(self, directory: Path | None = None) -> None:
        self.dir = Path(directory) if directory else config_dir()
        self.dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(self.dir, 0o700)  # 已存在的目录也要收紧
        except OSError:
            pass
        self.config_path = self.dir / "config.json"
        self.state_path = self.dir / "state.json"
        self.lock_path = self.dir / "instance.lock"
        self.config: dict[str, Any] = {**CONFIG_DEFAULTS, **_read_json(self.config_path)}
        self.state: dict[str, Any] = {**STATE_DEFAULTS, **_read_json(self.state_path)}
        self._config_mtime = 0.0
        self._state_mtime = 0.0
        self._remember_mtimes()

    # -- 外部修改检测 ------------------------------------------------------
    def _remember_mtimes(self) -> None:
        try:
            self._config_mtime = self.config_path.stat().st_mtime
        except OSError:
            self._config_mtime = 0.0
        try:
            self._state_mtime = self.state_path.stat().st_mtime
        except OSError:
            self._state_mtime = 0.0

    def reload(self) -> bool:
        """磁盘上的配置被别的进程改过（例如 `deepseek-cost --login`）时重新载入。"""
        try:
            disk_mtime = self.config_path.stat().st_mtime
        except OSError:
            disk_mtime = 0.0
        if disk_mtime <= self._config_mtime:
            return False
        self.config = {**CONFIG_DEFAULTS, **_read_json(self.config_path)}
        self.state = {**STATE_DEFAULTS, **_read_json(self.state_path)}
        self._remember_mtimes()
        return True

    # -- 配置 -------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        return self.config.get(key, CONFIG_DEFAULTS.get(key, default))

    def set(self, **values: Any) -> None:
        self.config.update(values)

    def save_config(self) -> None:
        _write_json_private(self.config_path, self.config)
        self._remember_mtimes()

    # -- 状态 -------------------------------------------------------------
    def sget(self, key: str, default: Any = None) -> Any:
        return self.state.get(key, STATE_DEFAULTS.get(key, default))

    def sset(self, **values: Any) -> None:
        self.state.update(values)

    def save_state(self) -> None:
        _write_json_private(self.state_path, self.state)
        self._remember_mtimes()

    # -- 便捷属性 ---------------------------------------------------------
    @property
    def auth_mode(self) -> str:
        return str(self.config.get("auth_mode") or "")

    @property
    def threshold(self) -> float:
        try:
            return float(self.config.get("threshold", 50.0))
        except (TypeError, ValueError):
            return 50.0

    @property
    def interval_seconds(self) -> int:
        try:
            minutes = float(self.config.get("interval_minutes", 5))
        except (TypeError, ValueError):
            minutes = 5.0
        return max(30, int(minutes * 60))

    @property
    def panel_height(self) -> int:
        try:
            height = int(self.config.get("panel_height", 22))
        except (TypeError, ValueError):
            height = 22
        return max(12, min(48, height))

    def credential(self) -> str:
        """当前生效的凭据（令牌或 API Key）。"""
        mode = self.auth_mode
        if mode == "apikey":
            return str(self.config.get("api_key") or "").strip()
        if mode in ("token", "account"):
            return str(self.config.get("platform_token") or "").strip()
        return ""

    def is_configured(self) -> bool:
        return bool(self.auth_mode and self.credential())

    def clear_credentials(self) -> None:
        self.config.update(
            {"auth_mode": "", "platform_token": "", "api_key": "", "password": ""}
        )

    def masked_secret(self) -> str:
        cred = self.credential()
        if not cred:
            return "（空）"
        if len(cred) <= 10:
            return cred[:2] + "***"
        return f"{cred[:6]}…{cred[-4:]}"
