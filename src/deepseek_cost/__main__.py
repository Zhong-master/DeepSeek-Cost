"""命令行入口。"""

from __future__ import annotations

import argparse
import os
import sys

from . import APP_TITLE, __version__, api
from .api import ApiError
from .config import Store, cache_dir
from .render import format_amount
from .util import SingleInstance


def _print_result(store: Store) -> int:
    mode = store.auth_mode
    credential = store.credential()
    if not store.is_configured():
        print("尚未登录，请先运行：deepseek-cost --login", file=sys.stderr)
        return 2
    try:
        balance = api.fetch_balance(mode, credential, str(store.get("currency", "CNY")))
    except ApiError as exc:
        print(f"获取余额失败（{exc.kind}）：{exc.message}", file=sys.stderr)
        return 1
    threshold = store.threshold
    flag = "（低于提醒阈值）" if balance.total < threshold else ""
    print(
        f"{format_amount(balance.total, balance.currency)} "
        f"[{balance.currency}] 赠送 {balance.granted:.2f} 充值 {balance.topped_up:.2f} "
        f"来源 {balance.source or mode} {flag}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="deepseek-cost",
        description=f"{APP_TITLE}：在 Ubuntu 顶栏实时显示 DeepSeek API 剩余费用",
    )
    parser.add_argument("--once", action="store_true", help="只查询一次余额并打印到终端后退出")
    parser.add_argument("--login", action="store_true", help="打开登录/设置窗口（需图形界面）")
    parser.add_argument("--reset", action="store_true", help="清除本机保存的登录凭据与状态后退出")
    parser.add_argument("--debug", action="store_true", help="输出调试日志到标准错误")
    parser.add_argument("--version", action="version", version=f"{APP_TITLE} {__version__}")
    args = parser.parse_args(argv)

    store = Store()

    if args.reset:
        store.clear_credentials()
        store.save_config()
        store.sset(last_total=None, last_error="", error_kind="", alerted=False)
        store.save_state()
        print("已清除本机保存的凭据。")
        return 0

    if args.once:
        return _print_result(store)

    if args.login:
        import gi

        gi.require_version("Gtk", "3.0")
        from gi.repository import Gtk

        from .ui import LoginDialog

        Gtk.init([])
        dialog = LoginDialog(store)
        if dialog.run() == Gtk.ResponseType.OK:
            print(f"已保存登录信息（{store.auth_mode}）。")
            return 0
        print("已取消。")
        return 1

    # 默认：顶栏指示器
    lock = SingleInstance(store.lock_path)
    if not lock.acquire():
        print("DeepSeek 余额指示器已在运行。", file=sys.stderr)
        # 开机自启（XSMP 会设置 DESKTOP_AUTOSTART_ID）时保持安静；
        # 用户手点图标/菜单时给一条提示，免得看起来像“点了没反应/启动失败”。
        if not os.environ.get("DESKTOP_AUTOSTART_ID"):
            try:
                from . import notify

                notify.send_async(
                    APP_TITLE,
                    "指示器已在运行，余额与今日用量就在系统栏右侧。",
                    icon="dialog-information",
                )
            except Exception:  # noqa: BLE001
                pass
        return 0

    try:
        from .app import run

        cache_dir().mkdir(parents=True, exist_ok=True)
        return run(store, debug=args.debug)
    finally:
        lock.release()


if __name__ == "__main__":
    raise SystemExit(main())
