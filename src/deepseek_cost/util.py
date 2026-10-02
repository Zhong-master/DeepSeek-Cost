"""零散工具：打开链接、单实例锁、时间格式化。"""

from __future__ import annotations

import fcntl
import os
import subprocess
import time
from pathlib import Path


def open_url(url: str) -> bool:
    """用系统默认程序打开链接。"""
    try:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gdk, Gio  # noqa: PLC0415

        display = Gdk.Display.get_default()
        if display is not None and Gio.AppInfo.launch_default_for_uri(url, None):
            return True
    except Exception:  # noqa: BLE001
        pass
    for command in (["xdg-open", url], ["gio", "open", url]):
        try:
            subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            return True
        except OSError:
            continue
    return False


class SingleInstance:
    """基于 flock 的单实例保护；进程退出（含被 kill）时自动释放。"""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.handle = None
        self.is_owner = False

    def acquire(self) -> bool:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # 用 "a+" 而不是 "w"，避免截断另一个实例写的 pid
        self.handle = open(self.path, "a+", encoding="utf-8")
        try:
            fcntl.flock(self.handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.handle.close()
            self.handle = None
            return False
        self.is_owner = True
        self.handle.seek(0)
        self.handle.truncate()
        self.handle.write(f"{os.getpid()}\n")
        self.handle.flush()
        return True

    def release(self) -> None:
        if self.handle is not None:
            try:
                if self.is_owner:
                    fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
            except OSError:
                pass
            self.handle.close()
            self.handle = None
            self.is_owner = False


def format_clock(timestamp: float | None) -> str:
    if not timestamp:
        return "—"
    return time.strftime("%H:%M:%S", time.localtime(timestamp))


def format_clock_offset(timestamp: float | None) -> str:
    if not timestamp:
        return "—"
    now = time.time()
    delta = int(round(timestamp - now))
    if delta <= 0:
        return "即将"
    if delta < 60:
        return f"{delta} 秒后"
    return f"{delta // 60} 分 {delta % 60} 秒后"
