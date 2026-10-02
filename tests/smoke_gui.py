#!/usr/bin/env python3
"""GUI 端到端冒烟测试：真的把指示器跑起来，检查顶栏文字、菜单、点击刷新与低余量提醒。

需要图形会话（DISPLAY）与 ffmpeg。全部数据来自本地假接口，不联网、不消耗额度。

运行： python3 tests/smoke_gui.py
截图输出到 $TMPDIR/deepseek-cost-smoke/
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests"))

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio  # noqa: E402

from deepseek_cost.config import Store  # noqa: E402
from deepseek_cost.render import COLOR_LOW, COLOR_NORMAL, COLOR_USAGE  # noqa: E402
from mock_server import MockServer  # noqa: E402

ARTIFACTS = Path(tempfile.gettempdir()) / "deepseek-cost-smoke"
RESULTS: list[tuple[bool, str]] = []


def screen_size() -> tuple[int, int]:
    """读取当前 X 屏幕分辨率（CI 里是 xvfb），失败则退化为 1920x1080。"""
    try:
        out = subprocess.run(["xrandr", "--current"], capture_output=True, text=True, timeout=10).stdout
        for line in out.splitlines():
            if "*" in line and "x" in line.split()[0]:
                width, height = line.split()[0].split("x")
                return int(width), int(height)
    except Exception:  # noqa: BLE001
        pass
    return 1920, 1080


SCREEN_W, SCREEN_H = screen_size()


def check(ok: bool, message: str) -> None:
    RESULTS.append((bool(ok), message))
    print(("  ✓ " if ok else "  ✗ ") + message, flush=True)


def wait_for(predicate, timeout: float, interval: float = 0.4):
    deadline = time.time() + timeout
    while time.time() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    return None


def grab(name: str, crop: str | None = None) -> Path:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    full = ARTIFACTS / f"{name}.png"
    display = os.environ.get("DISPLAY", ":0")
    subprocess.run(
        ["ffmpeg", "-loglevel", "error", "-f", "x11grab",
         "-video_size", f"{SCREEN_W}x{SCREEN_H}",
         "-i", display, "-frames:v", "1", "-y", str(full)],
        capture_output=True,
    )
    if crop and full.exists():
        subprocess.run(
            ["ffmpeg", "-loglevel", "error", "-i", str(full), "-vf", crop, "-y",
             str(ARTIFACTS / f"{name}-crop.png")],
            capture_output=True,
        )
    return full


def count_color(path: Path, rgb: tuple[float, float, float], tolerance: int = 30) -> int:
    """统计图像顶部 34 像素内、靠右区域中接近指定颜色的像素数（-1 表示缺少 Pillow）。"""
    try:
        from PIL import Image
    except ImportError:
        return -1
    target = tuple(int(c * 255) for c in rgb)
    with Image.open(path) as image:
        image = image.convert("RGB")
        width, height = image.size
        pixels = image.load()
        count = 0
        for y in range(min(height, 34)):
            for x in range(max(0, width - 900), width):
                r, g, b = pixels[x, y]
                if (abs(r - target[0]) <= tolerance and abs(g - target[1]) <= tolerance
                        and abs(b - target[2]) <= tolerance):
                    count += 1
        return count


def bus_call(dest: str, path: str, interface: str, method: str, signature: str, args: tuple):
    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    return bus.call_sync(dest, path, interface, method, GLib.Variant(signature, args), None, 0, 8000, None)


def find_indicator(indicator_id: str) -> tuple[str, str] | None:
    """只认本次运行的 indicator id，避免误测其它正在运行的实例。"""
    reply = bus_call(
        "org.kde.StatusNotifierWatcher", "/StatusNotifierWatcher",
        "org.freedesktop.DBus.Properties", "Get", "(ss)",
        ("org.kde.StatusNotifierWatcher", "RegisteredStatusNotifierItems"),
    )
    for item in reply.unpack()[0]:
        if "@" not in item:
            continue
        name, path = item.split("@", 1)
        try:
            props = bus_call(name, path, "org.freedesktop.DBus.Properties", "GetAll", "(s)",
                             ("org.kde.StatusNotifierItem",)).unpack()[0]
        except Exception:  # noqa: BLE001
            continue
        if props.get("Id") == indicator_id:
            return name, path
    return None


def start_app(api_key: str, workdir: Path, cache: Path, server: MockServer,
              indicator_id: str, threshold: float = 50.0, overrides: dict | None = None):
    store = Store(workdir)
    store.set(auth_mode="apikey", api_key=api_key, threshold=threshold, interval_minutes=5)
    if overrides:
        store.set(**overrides)
    store.save_config()
    log_path = workdir / "app.log"
    env = {
        **os.environ,
        "PYTHONPATH": str(SRC),
        "DEEPSEEK_COST_DIR": str(workdir),
        "DEEPSEEK_COST_CACHE": str(cache),
        "DEEPSEEK_COST_API_BASE": server.api_base,
        "DEEPSEEK_COST_PLATFORM_BASE": server.platform_base,
        "DEEPSEEK_COST_INDICATOR_ID": indicator_id,
    }
    log_file = open(log_path, "w", encoding="utf-8")  # noqa: SIM115
    proc = subprocess.Popen(
        [sys.executable, "-m", "deepseek_cost", "--debug"],
        env=env, stdout=log_file, stderr=subprocess.STDOUT,
    )
    return proc, log_path


def stop_app(proc) -> None:
    proc.terminate()
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        proc.kill()


def scenario_normal(server: MockServer) -> None:
    print("\n[1] 正常余额（¥123.45）—— 顶栏文字 / 菜单 / 点击刷新", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir, cache = Path(tmp) / "conf", Path(tmp) / "cache"
        indicator_id = f"dsc-smoke-normal-{os.getpid()}"
        proc, log_path = start_app("sk-good", workdir, cache, server, indicator_id)
        try:
            def log_text() -> str:
                return log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

            check(wait_for(lambda: "刷新成功" in log_text(), 25) is not None,
                  "首次自动刷新成功（日志出现「刷新成功」）")
            check("123.45" in log_text(), "日志中的余额为 ¥123.45")

            icon_files = wait_for(lambda: list((cache / "icons").glob("panel-*.png")), 10)
            check(bool(icon_files), f"已生成顶栏文字图标（{len(icon_files or [])} 个文件）")

            item = wait_for(lambda: find_indicator(indicator_id), 20)
            check(item is not None, "指示器已注册到 StatusNotifierWatcher（顶栏会显示）")
            if not item:
                return
            bus_name, object_path = item
            props = bus_call(bus_name, object_path, "org.freedesktop.DBus.Properties", "GetAll", "(s)",
                             ("org.kde.StatusNotifierItem",)).unpack()[0]
            check(props.get("Status") == "Active", f"状态 Active（实际 {props.get('Status')}）")
            icon_name = str(props.get("IconName", ""))
            check(icon_name.startswith("/") and Path(icon_name).exists(),
                  f"图标为绝对路径的真实 PNG：{icon_name}")
            check(icon_name.startswith(str(cache)),
                  f"图标属于本次运行的实例（路径应位于 {cache}）")
            menu_path = props.get("Menu", "/MenuBar")

            layout = bus_call(bus_name, menu_path, "com.canonical.dbusmenu", "GetLayout", "(iias)",
                              (0, -1, [])).unpack()[1]
            entries: list[tuple[int, str]] = []

            def walk(node) -> None:
                item_id, props_dict, children = node[0], node[1], node[2]
                if props_dict.get("label"):
                    entries.append((item_id, props_dict["label"]))
                for child in children:
                    walk(child)

            walk(layout)
            labels = [label for _id, label in entries]
            balance_entry = next(((i, l) for i, l in entries if "¥123.45" in l), None)
            check(balance_entry is not None and "刷新" in balance_entry[1],
                  f"菜单里显示余额且提示可点击刷新：{balance_entry[1] if balance_entry else labels[:3]}")
            check(any("立即刷新" in l for l in labels) and any("设置" in l for l in labels),
                  f"菜单包含「立即刷新」「设置」等操作项（共 {len(labels)} 项）")

            shot = grab("panel-normal", crop=f"crop={SCREEN_W}:34:0:0")
            green = count_color(shot, COLOR_NORMAL, tolerance=18)
            check(green >= 20, f"顶栏截图检测到本次实例的余额文字像素（{green} 个匹配色像素，启发式检查）")

            if balance_entry is None:
                return
            before = log_text().count("刷新成功")
            bus_call(bus_name, menu_path, "com.canonical.dbusmenu", "Event", "(isvu)",
                     (balance_entry[0], "clicked", GLib.Variant("i", 0), 0))
            clicked = wait_for(lambda: log_text().count("刷新成功") > before, 20)
            check(clicked is not None, "模拟点击菜单第一项 → 触发了一次手动刷新")
            print(f"      截图：{shot}", flush=True)
        finally:
            stop_app(proc)


def scenario_low(server: MockServer) -> None:
    print("\n[2] 低余额（¥12.34 < 阈值 50）—— 余量提醒", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir, cache = Path(tmp) / "conf", Path(tmp) / "cache"
        indicator_id = f"dsc-smoke-low-{os.getpid()}"
        proc, log_path = start_app("sk-low", workdir, cache, server, indicator_id)
        try:
            def log_text() -> str:
                return log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

            check(wait_for(lambda: "已弹出余量不足提醒" in log_text(), 25) is not None,
                  "低于阈值时触发了余量提醒（日志确认）")
            shot = grab("notify-low")
            check(shot.exists() and shot.stat().st_size > 5000, f"已截图当前桌面：{shot}")

            check(wait_for(lambda: find_indicator(indicator_id), 15) is not None,
                  "低余额实例也已注册到 StatusNotifierWatcher")
            icon_files = sorted((cache / "icons").glob("panel-*.png"))
            check(bool(icon_files), "低余额状态下生成了顶栏图标")
            if icon_files:
                red = count_color(icon_files[-1], COLOR_LOW, tolerance=45)
                check(bool(red) and red > 20, f"图标使用警示红色（{red} 个像素）")

            status = Store(workdir)
            check(status.sget("alerted") is True, "状态文件记录 alerted=true，不会重复刷屏")
            check(float(status.sget("last_total") or 0) < 50, "状态文件记录的最新余额低于阈值")
        finally:
            stop_app(proc)


def scenario_relogin(server: MockServer) -> None:
    """C1 回归：auth_mode=account + 记住密码时，令牌过期应自动重新登录。"""
    print("\n[3] 令牌过期 + 记住的密码 —— 自动重新登录", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir, cache = Path(tmp) / "conf", Path(tmp) / "cache"
        indicator_id = f"dsc-smoke-relogin-{os.getpid()}"
        proc, log_path = start_app(
            "ignored", workdir, cache, server, indicator_id,
            overrides={
                "auth_mode": "account",
                "platform_token": "tok-expired",   # 第一把令牌已失效
                "password": "goodpass",            # 记住的密码可以去换新令牌
                "email": "someone@example.com",
                "remember_password": True,
            },
        )
        try:
            def log_text() -> str:
                return log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

            check(wait_for(lambda: "令牌已过期" in log_text(), 25) is not None,
                  "检测到令牌失效（日志出现「令牌已过期」）")
            check(wait_for(lambda: "自动续期登录令牌" in log_text(), 25) is not None,
                  "用记住的密码自动换到了新令牌")
            check(wait_for(lambda: "刷新成功：CNY 123.45" in log_text(), 25) is not None,
                  "自动重登录后取回余额 ¥123.45")
            store = Store(workdir)
            check(store.credential() == "tok-good", "新令牌已写回配置文件")
            check(store.auth_mode == "account", "auth_mode 仍为 account（下次过期还能自动续期）")
        finally:
            stop_app(proc)


def scenario_usage(server: MockServer) -> None:
    """今日用量：黄色第二个指示器 + 菜单里的金额/token。"""
    print("\n[4] 自选 API Key 今日用量（黄色）", flush=True)
    with tempfile.TemporaryDirectory() as tmp:
        workdir, cache = Path(tmp) / "conf", Path(tmp) / "cache"
        indicator_id = f"dsc-smoke-usage-{os.getpid()}"
        proc, log_path = start_app(
            "tok-good", workdir, cache, server, indicator_id,
            overrides={
                "auth_mode": "token",
                "platform_token": "tok-good",
                "usage_display": True,
                "usage_key_id": "",
                "usage_show_cost": True,
                "usage_show_tokens": True,
            },
        )
        try:
            def log_text() -> str:
                return log_path.read_text(encoding="utf-8", errors="replace") if log_path.exists() else ""

            check(wait_for(lambda: "刷新成功" in log_text(), 25) is not None, "主余额刷新成功")
            item = wait_for(lambda: find_indicator(indicator_id + "-usage"), 25)
            check(item is not None, "黄色用量指示器已注册到 StatusNotifierWatcher")
            if not item:
                return
            bus_name, object_path = item
            props = bus_call(bus_name, object_path, "org.freedesktop.DBus.Properties", "GetAll", "(s)",
                             ("org.kde.StatusNotifierItem",)).unpack()[0]
            icon_name = str(props.get("IconName", ""))
            check(icon_name.startswith(str(cache)) and Path(icon_name).exists(),
                  f"用量图标属于本次实例：{icon_name}")
            yellow = count_color_png(Path(icon_name), COLOR_USAGE, tolerance=45)
            check(yellow > 20, f"用量图标是黄色文字（{yellow} 个黄色像素）")

            layout = bus_call(bus_name, props.get("Menu", "/MenuBar"), "com.canonical.dbusmenu",
                              "GetLayout", "(iias)", (0, -1, [])).unpack()[1]
            labels: list[str] = []

            def walk(node) -> None:
                if node[1].get("label"):
                    labels.append(node[1]["label"])
                for child in node[2]:
                    walk(child)

            walk(layout)
            joined = " | ".join(labels)
            check(any("今日（Beta）" in l for l in labels), "菜单按当天用量自动选中了 Beta（请求数最多）")
            check(any("¥1.50" in l and "2,000,000" in l for l in labels),
                  f"菜单显示今日金额与 token 总量：{[l for l in labels if '今日（' in l]}")
            check(any(l.strip() == "Alpha" for l in labels) and any("Zhongz" not in l for l in labels),
                  "菜单里列出了可选的 API Key")
            check("在顶栏显示今日用量（黄色）" in joined, "菜单有开关项")

            shot = grab("panel-usage", crop=f"crop={SCREEN_W}:34:0:0")
            panel_yellow = count_color(shot, COLOR_USAGE, tolerance=25)
            check(panel_yellow >= 20, f"顶栏截图检测到黄色用量文字（{panel_yellow} 个像素，启发式检查）")
            print(f"      截图：{shot}", flush=True)
        finally:
            stop_app(proc)


def count_color_png(path: Path, rgb: tuple[float, float, float], tolerance: int = 30) -> int:
    """统计 PNG 本身有多少像素接近指定颜色。"""
    try:
        from PIL import Image
    except ImportError:
        return -1
    target = tuple(int(c * 255) for c in rgb)
    with Image.open(path) as image:
        image = image.convert("RGB")
        pixels = image.load()
        count = 0
        for y in range(image.height):
            for x in range(image.width):
                r, g, b = pixels[x, y]
                if (abs(r - target[0]) <= tolerance and abs(g - target[1]) <= tolerance
                        and abs(b - target[2]) <= tolerance):
                    count += 1
        return count


def main() -> int:
    if not os.environ.get("DISPLAY"):
        print("没有 DISPLAY，跳过 GUI 冒烟测试")
        return 0
    if shutil.which("ffmpeg") is None:
        print("缺少 ffmpeg，跳过 GUI 冒烟测试")
        return 0
    server = MockServer().start()
    try:
        scenario_normal(server)
        scenario_low(server)
        scenario_relogin(server)
        scenario_usage(server)
    finally:
        server.stop()

    failed = [message for ok, message in RESULTS if not ok]
    print(f"\n===== GUI 冒烟测试（屏幕 {SCREEN_W}x{SCREEN_H}）：{len(RESULTS) - len(failed)}/{len(RESULTS)} 通过 =====")
    for message in failed:
        print(f"  失败：{message}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
