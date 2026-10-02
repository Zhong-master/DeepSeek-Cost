"""桌面通知（libnotify，失败时退回 notify-send 命令）。"""

from __future__ import annotations

import shutil
import subprocess
import threading

_initialised = False
_available: bool | None = None


def _init() -> bool:
    global _initialised, _available
    if _available is not None:
        return _available
    try:
        import gi

        gi.require_version("Notify", "0.7")
        from gi.repository import Notify  # noqa: PLC0415

        Notify.init("deepseek-cost")
        _available = True
    except Exception:  # noqa: BLE001 - 通知不可用不应影响主功能
        _available = False
    _initialised = True
    return _available


def send(title: str, body: str, icon: str = "dialog-warning", urgency: str = "normal") -> bool:
    """弹出一条桌面通知。"""
    if _init():
        try:
            from gi.repository import Notify  # noqa: PLC0415

            notification = Notify.Notification.new(title, body, icon)
            urgency_map = {
                "low": Notify.Urgency.LOW,
                "normal": Notify.Urgency.NORMAL,
                "critical": Notify.Urgency.CRITICAL,
            }
            try:
                notification.set_urgency(urgency_map.get(urgency, Notify.Urgency.NORMAL))
            except Exception:  # noqa: BLE001
                pass
            notification.show()
            return True
        except Exception:  # noqa: BLE001
            pass
    if shutil.which("notify-send"):
        try:
            subprocess.run(
                ["notify-send", "-u", urgency, "-i", icon, title, body],
                check=False,
                timeout=10,
            )
            return True
        except Exception:  # noqa: BLE001
            return False
    return False


def send_async(title: str, body: str, icon: str = "dialog-warning", urgency: str = "normal") -> None:
    """后台线程发通知：通知后端异常时也不会阻塞 GTK 主循环。"""
    threading.Thread(
        target=send, args=(title, body, icon, urgency), daemon=True, name="notify"
    ).start()
