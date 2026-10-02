"""顶栏指示器主程序：定时刷新、点击刷新、低余量提醒。"""

from __future__ import annotations

import os
import signal
import sys
import threading
import time

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("AyatanaAppIndicator3", "0.1")
from gi.repository import GLib, Gtk  # noqa: E402

from gi.repository import AyatanaAppIndicator3 as AppIndicator  # noqa: E402

from . import APP_TITLE, __version__, api, notify, util  # noqa: E402
from .alerts import decide_alert  # noqa: E402
from .api import KIND_AUTH, KIND_NETWORK, ApiError  # noqa: E402
from .config import Store, cache_dir  # noqa: E402
from .render import (  # noqa: E402
    COLOR_LOW,
    COLOR_NORMAL,
    COLOR_STALE,
    COLOR_USAGE,
    COLOR_WARN,
    PanelIcon,
    format_amount,
)
from .ui import LoginDialog, SettingsDialog  # noqa: E402

# 托盘 ID：同一台机器上可以并存多个实例（测试时用环境变量区分）
INDICATOR_ID = os.environ.get("DEEPSEEK_COST_INDICATOR_ID", "deepseek-cost-indicator")
_DEBUG = False


def _set_label(item: Gtk.MenuItem, text: str) -> None:
    """安全的设置菜单项文字（Gtk.MenuItem 本身没有 set_text）。"""
    child = item.get_child()
    if child is not None and hasattr(child, "set_text"):
        child.set_text(text)
    else:
        item.set_label(text)


def _set_markup(item: Gtk.MenuItem, markup: str) -> None:
    """给菜单项设置带颜色/样式的文字（AccelLabel 支持 markup）。"""
    child = item.get_child()
    if child is not None and hasattr(child, "set_markup"):
        child.set_markup(markup)
    else:
        item.set_label(GLib.markup_escape_text(markup))


def _num(value, default=None):
    """把配置/状态里的值安全地转成 float（文件被改坏时返回默认值而不是崩溃）。"""
    try:
        if value is None or value == "":
            return default
        return float(value)
    except (TypeError, ValueError):
        return default


def log(message: str, always: bool = False) -> None:
    """默认只在 --debug 下输出；always=True 用于值得记录的错误/状态变化。"""
    if _DEBUG or always:
        sys.stderr.write(f"[deepseek-cost] {message}\n")
        sys.stderr.flush()


class BalanceApp:
    """应用主体。注意所有 GTK 调用都在主线程里。"""

    def __init__(self, store: Store, debug: bool = False) -> None:
        self.store = store
        self.debug = debug
        self.icon = PanelIcon(cache_dir() / "icons", keep=4)
        self.usage_icon = PanelIcon(cache_dir() / "usage-icons", keep=4)
        self.usage_indicator = None
        self.keys: list[api.ApiKeyInfo] = []
        self.usage: api.KeyUsage | None = None
        self.usage_error = ""
        self.refreshing = False
        self.scale = self._detect_scale()
        self._timer_id = None
        self._last_notified_error = ""
        self._build_indicator()
        self._sync_usage_indicator()
        self._reset_timer()
        self._render_icon_for_state()

    # ------------------------------------------------------------------ UI
    def _detect_scale(self) -> int:
        try:
            from gi.repository import Gdk  # noqa: PLC0415

            display = Gdk.Display.get_default()
            if display is None:
                return 1
            monitor = display.get_primary_monitor()
            return int(monitor.get_scale_factor()) if monitor else 1
        except Exception:  # noqa: BLE001
            return 1

    def _build_indicator(self) -> None:
        initial = self._initial_icon_path()
        self.indicator = AppIndicator.Indicator.new(
            INDICATOR_ID, initial, AppIndicator.IndicatorCategory.APPLICATION_STATUS
        )
        self.indicator.set_title(f"{APP_TITLE} {__version__}")
        self.indicator.set_icon_full(initial, APP_TITLE)
        self.indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)

        menu = Gtk.Menu()

        self.balance_item = Gtk.MenuItem(label="DeepSeek 余额：正在获取…")
        self.balance_item.connect("activate", lambda _w: self.request_refresh(manual=True))
        menu.append(self.balance_item)

        self.status_item = Gtk.MenuItem(label="尚未刷新")
        self.status_item.set_sensitive(False)
        menu.append(self.status_item)

        self.detail_item = Gtk.MenuItem(label="")
        self.detail_item.set_sensitive(False)
        self.detail_item.set_no_show_all(True)
        menu.append(self.detail_item)

        self.error_item = Gtk.MenuItem(label="")
        self.error_item.set_sensitive(False)
        self.error_item.set_no_show_all(True)
        menu.append(self.error_item)

        menu.append(Gtk.SeparatorMenuItem())

        self.usage_toggle = Gtk.CheckMenuItem(label="在顶栏显示今日用量（黄色）")
        self.usage_toggle.set_active(bool(self.store.get("usage_display")))
        self.usage_toggle.connect("toggled", self._on_usage_toggle)
        menu.append(self.usage_toggle)

        self.usage_key_item = Gtk.MenuItem(label="选择 API Key")
        self.usage_key_menu = Gtk.Menu()
        self.usage_key_item.set_submenu(self.usage_key_menu)
        menu.append(self.usage_key_item)

        self.usage_info_item = Gtk.MenuItem(label="今日用量：未获取")
        self.usage_info_item.set_sensitive(False)
        menu.append(self.usage_info_item)

        self.usage_refresh_item = Gtk.MenuItem(label="立即刷新用量")
        self.usage_refresh_item.connect("activate", lambda _w: self.request_refresh(manual=True))
        menu.append(self.usage_refresh_item)

        menu.append(Gtk.SeparatorMenuItem())

        self.refresh_item = Gtk.MenuItem(label="立即刷新")
        self.refresh_item.connect("activate", lambda _w: self.request_refresh(manual=True))
        menu.append(self.refresh_item)

        self.settings_item = Gtk.MenuItem(label="设置…")
        self.settings_item.connect("activate", lambda _w: self.open_settings())
        menu.append(self.settings_item)

        self.relogin_item = Gtk.MenuItem(label="重新登录…")
        self.relogin_item.connect("activate", lambda _w: self.open_login(force=True))
        menu.append(self.relogin_item)

        self.console_item = Gtk.MenuItem(label="打开 DeepSeek 控制台")
        self.console_item.connect("activate", lambda _w: util.open_url(api.open_console_url()))
        menu.append(self.console_item)

        menu.append(Gtk.SeparatorMenuItem())

        quit_item = Gtk.MenuItem(label="退出")
        quit_item.connect("activate", lambda _w: self.quit())
        menu.append(quit_item)

        menu.show_all()
        self.error_item.hide()
        self.detail_item.hide()

        self.indicator.set_menu(menu)
        self.menu = menu
        # 中键点击 = 立即刷新（不需要先打开菜单）
        try:
            self.indicator.set_secondary_activate_target(self.refresh_item)
        except Exception as exc:  # noqa: BLE001
            log(f"set_secondary_activate_target 不可用：{exc}", always=True)

    # ------------------------------------------------------ 今日用量（黄色）
    def _usage_wanted(self) -> bool:
        return bool(self.store.get("usage_display")) and self.store.is_configured()

    def _sync_usage_indicator(self) -> None:
        """按配置创建/销毁第二个（黄色）用量指示器。"""
        want = self._usage_wanted()
        if want and self.usage_indicator is None:
            initial = self.usage_icon.update(
                "今日…", height=self.store.panel_height, scale=self.scale, color=COLOR_USAGE
            )
            indicator = AppIndicator.Indicator.new(
                f"{INDICATOR_ID}-usage", initial, AppIndicator.IndicatorCategory.APPLICATION_STATUS
            )
            indicator.set_title(f"{APP_TITLE} 今日用量")
            indicator.set_icon_full(initial, "今日用量")
            indicator.set_status(AppIndicator.IndicatorStatus.ACTIVE)
            indicator.set_menu(self.menu)
            self.usage_indicator = indicator
            log("已创建今日用量指示器（黄色）")
        elif not want and self.usage_indicator is not None:
            try:
                self.usage_indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
            except Exception:  # noqa: BLE001
                pass
            self.usage_indicator = None
        self._render_usage_icon()
        self._update_usage_menu()

    def _usage_text(self) -> str:
        usage = self.usage
        if usage is None:
            return "今日不可用" if self.usage_error else "今日获取中"
        parts = []
        if self.store.get("usage_show_cost", True):
            parts.append(format_amount(usage.cost, usage.currency))
        if self.store.get("usage_show_tokens", True):
            parts.append(api.format_tokens(usage.tokens))
        if not parts:
            parts.append(f"{usage.requests}次")
        return "·".join(parts)

    def _render_usage_icon(self) -> None:
        if self.usage_indicator is None:
            return
        path = self.usage_icon.update(
            self._usage_text(), height=self.store.panel_height, scale=self.scale, color=COLOR_USAGE
        )
        self.usage_indicator.set_icon_full(path, f"今日用量 {self._usage_text()}")

    def _on_usage_toggle(self, widget) -> None:
        enabled = bool(widget.get_active())
        self.store.set(usage_display=enabled)
        self.store.save_config()
        self._sync_usage_indicator()
        if enabled:
            self.request_refresh(manual=True)

    def _on_key_selected(self, widget, tracking_id: str, name: str) -> None:
        if not widget.get_active():
            return
        self.store.set(usage_key_id=tracking_id, usage_key_name=name)
        self.store.save_config()
        self.usage = None
        self._render_usage_icon()
        self.request_refresh(manual=True)

    def _rebuild_key_menu(self) -> None:
        for child in self.usage_key_menu.get_children():
            self.usage_key_menu.remove(child)
        current = str(self.store.get("usage_key_id") or "")
        auto = Gtk.RadioMenuItem(label="自动（当天用量最大的 Key）")
        auto.set_active(not current)
        auto.connect("toggled", self._on_key_selected, "", "")
        self.usage_key_menu.append(auto)
        for key in self.keys:
            label = key.name or key.tracking_id[:8]
            if key.key_type and key.key_type != "NORMAL":
                label = f"{label}（{key.key_type}）"
            item = Gtk.RadioMenuItem.new_with_label_from_widget(auto, label)
            item.set_active(bool(current) and current == key.tracking_id)
            item.connect("toggled", self._on_key_selected, key.tracking_id, key.name)
            self.usage_key_menu.append(item)
        refresh = Gtk.MenuItem(label="刷新 API Key 列表")
        refresh.connect("activate", lambda _w: self._reload_keys())
        self.usage_key_menu.append(Gtk.SeparatorMenuItem())
        self.usage_key_menu.append(refresh)
        self.usage_key_menu.show_all()

    def _reload_keys(self) -> None:
        self.keys = []
        self.request_refresh(manual=True)

    def _update_usage_menu(self) -> None:
        enabled = self._usage_wanted()
        self.usage_key_item.set_sensitive(enabled and bool(self.keys))
        usage = self.usage
        if self.usage_error:
            _set_markup(
                self.usage_info_item,
                f"<span foreground='#c62828'>今日用量：{GLib.markup_escape_text(self.usage_error)}</span>",
            )
        elif usage is None:
            _set_markup(self.usage_info_item, "<span foreground='#888888'>今日用量：未获取</span>")
        else:
            key_name = GLib.markup_escape_text(usage.name or self.store.get("usage_key_name") or "全部 Key")
            text = (
                f"今日（{key_name}）：{usage.requests} 次请求 · "
                f"{usage.tokens:,} tokens · {format_amount(usage.cost, usage.currency)}"
            )
            _set_markup(
                self.usage_info_item,
                f"<span foreground='#e6b422'>{GLib.markup_escape_text(text)}</span>",
            )
        self.usage_info_item.show()

    def _initial_icon_path(self) -> str:
        text = "未登录" if not self.store.is_configured() else "获取中"
        color = COLOR_WARN if not self.store.is_configured() else COLOR_STALE
        return self.icon.update(text, height=self.store.panel_height, scale=self.scale, color=color)

    # -------------------------------------------------------------- 刷新逻辑
    def _reset_timer(self) -> None:
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        seconds = self.store.interval_seconds
        self.next_refresh_at = time.time() + seconds
        self._timer_id = GLib.timeout_add_seconds(seconds, self._on_timer)
        log(f"定时刷新已设置：每 {seconds} 秒")

    def _on_timer(self) -> bool:
        self._timer_id = None
        self.request_refresh(manual=False)
        self._reset_timer()
        return False

    def request_refresh(self, manual: bool = False, delay_ms: int = 0) -> None:
        if delay_ms:
            GLib.timeout_add(delay_ms, self._once_refresh, manual)
            return
        # 别的进程（例如 deepseek-cost --login）改过配置就重新载入
        if self.store.reload():
            log("检测到配置文件被外部更新，已重新载入")
            self._render_icon_for_state()
            self._update_menu()
            self._reset_timer()
        if not self.store.is_configured():
            if manual:
                self.open_login()
            return
        if self.refreshing:
            log("上一次刷新尚未结束，跳过本次")
            return
        self.refreshing = True
        _set_label(self.status_item, "正在刷新…")
        threading.Thread(target=self._worker, args=(manual,), daemon=True).start()

    def _once_refresh(self, manual: bool) -> bool:
        self.request_refresh(manual=manual)
        return False

    def _worker(self, manual: bool) -> None:
        mode = self.store.auth_mode
        credential = self.store.credential()
        prefer = str(self.store.get("currency", "CNY"))
        new_token = ""
        try:
            try:
                balance = api.fetch_balance(mode, credential, prefer)
            except ApiError as exc:
                if exc.kind == KIND_AUTH and mode == "account" and self.store.get("password"):
                    log("令牌已过期，尝试用保存的密码重新登录…", always=True)
                    token, _email = api.login_account(
                        str(self.store.get("email") or ""), str(self.store.get("password") or "")
                    )
                    balance = api.fetch_platform_balance(token, prefer)
                    new_token = token
                else:
                    raise
            usage, keys, usage_error = self._fetch_usage(mode, new_token or credential, prefer)
            GLib.idle_add(self._on_success, balance, manual, new_token, usage, keys, usage_error)
        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(self._on_error, exc)

    def _fetch_usage(self, mode, credential, prefer):
        """按需抓取“选定的 API Key 今日用量”。返回 (usage, keys, error)。"""
        if not self._usage_wanted():
            return None, None, ""
        if mode not in ("token", "account"):
            return None, None, "按 Key 用量需要网页登录令牌（API Key 方式不支持）"
        try:
            keys = self.keys
            if not keys:
                keys = api.fetch_api_keys(credential)
            wanted_id = str(self.store.get("usage_key_id") or "")
            usage = api.fetch_key_usage(
                credential, wanted_id, str(self.store.get("usage_currency") or prefer)
            )
            if not usage.name and wanted_id:
                for key in keys:
                    if key.tracking_id == wanted_id:
                        usage.name = key.name
                        break
            return usage, keys, ""
        except Exception as exc:  # noqa: BLE001
            return None, None, api.describe_error(exc)

    # -------------------------------------------------------------- 结果处理
    def _on_success(
        self,
        balance: api.Balance,
        manual: bool,
        new_token: str = "",
        usage: api.KeyUsage | None = None,
        keys: list | None = None,
        usage_error: str = "",
    ) -> bool:
        self.refreshing = False
        if keys:
            self.keys = keys
        if self._usage_wanted():
            self.usage = usage
            self.usage_error = usage_error
            self._rebuild_key_menu()
            self._render_usage_icon()
            self._update_usage_menu()
            if usage is not None:
                self.store.sset(
                    last_usage_cost=usage.cost,
                    last_usage_tokens=usage.tokens,
                    last_usage_requests=usage.requests,
                    last_usage_day=usage.day,
                    last_usage_key=usage.name,
                )
            if usage_error:
                log(f"今日用量获取失败：{usage_error}", always=True)
        if new_token:
            # 配置写回统一放在主线程做
            self.store.set(platform_token=new_token)
            self.store.save_config()
            log("已用记住的密码自动续期登录令牌", always=True)
        now = time.time()
        self.store.sset(
            last_total=balance.total,
            last_currency=balance.currency,
            last_granted=balance.granted,
            last_topped_up=balance.topped_up,
            last_updated=now,
            last_error="",
            error_kind="",
        )
        if self._last_notified_error:
            self._last_notified_error = ""
        self.store.save_state()
        self._render_icon_for_state()
        self._update_menu()
        self._sync_usage_indicator()
        self._maybe_alert(balance)
        log(f"刷新成功：{balance.currency} {balance.total:.2f}（{balance.source}）")
        return False

    def _on_error(self, exc: Exception) -> bool:
        self.refreshing = False
        kind = exc.kind if isinstance(exc, ApiError) else "error"
        message = api.describe_error(exc)
        self.store.sset(last_error=message, error_kind=kind)
        self.store.save_state()
        log(f"刷新失败（{kind}）：{message}", always=True)
        self._render_icon_for_state()
        self._update_menu()
        if kind == KIND_AUTH and self._last_notified_error != KIND_AUTH:
            self._last_notified_error = KIND_AUTH
            notify.send_async(
                f"{APP_TITLE}：登录失效",
                f"{message}\n点击顶栏文字并选择「重新登录…」更新凭据。",
                icon="dialog-warning",
                urgency="critical",
            )
        return False

    # -------------------------------------------------------------- 渲染
    def _render_icon_for_state(self) -> None:
        text, color = self._panel_content()
        path = self.icon.update(
            text, height=self.store.panel_height, scale=self.scale, color=color
        )
        self.indicator.set_icon_full(path, f"{APP_TITLE} {text}")
        _set_label(self.balance_item, self._balance_item_text())

    def _panel_content(self) -> tuple[str, tuple[float, float, float]]:
        if not self.store.is_configured():
            return "未登录", COLOR_WARN
        total = _num(self.store.sget("last_total"))
        currency = str(self.store.sget("last_currency") or "CNY")
        error = str(self.store.sget("last_error") or "")
        if total is None:
            if error:
                kind = str(self.store.sget("error_kind") or "")
                if kind == KIND_AUTH:
                    return "登录失效", COLOR_WARN
                if kind == KIND_NETWORK:
                    return "网络异常", COLOR_WARN
                return "获取失败", COLOR_WARN
            return "获取中", COLOR_STALE
        text = format_amount(total, currency)
        if error:
            return text, COLOR_STALE
        if total < self.store.threshold:
            return text, COLOR_LOW
        return text, COLOR_NORMAL

    def _balance_item_text(self) -> str:
        text, _color = self._panel_content()
        if not self.store.is_configured():
            return f"{text} · 点击登录"
        return f"{text} · 点击刷新"

    def _update_menu(self) -> None:
        total = _num(self.store.sget("last_total"))
        currency = str(self.store.sget("last_currency") or "CNY")
        updated = _num(self.store.sget("last_updated"), 0.0) or 0.0
        error = str(self.store.sget("last_error") or "")

        _set_label(self.balance_item, self._balance_item_text())

        if updated:
            _set_label(self.status_item, 
                f"更新于 {util.format_clock(updated)} · 下次 {util.format_clock_offset(self.next_refresh_at)}"
            )
        else:
            _set_label(self.status_item, "尚未成功获取余额")

        if total is not None:
            granted = _num(self.store.sget("last_granted"), 0.0) or 0.0
            topped = _num(self.store.sget("last_topped_up"), 0.0) or 0.0
            _set_label(self.detail_item, 
                f"赠送 {format_amount(granted, currency)} · 充值 {format_amount(topped, currency)}"
                f" · 提醒阈值 {format_amount(self.store.threshold, currency)}"
            )
            self.detail_item.show()
        else:
            self.detail_item.hide()

        if error:
            _set_label(self.error_item, f"⚠ {error}")
            self.error_item.show()
        else:
            self.error_item.hide()
            self.menu.show_all()
        if not error:
            self.error_item.hide()
        if total is None:
            self.detail_item.hide()

    # -------------------------------------------------------------- 提醒
    def _maybe_alert(self, balance: api.Balance) -> None:
        now = time.time()
        repeat = (_num(self.store.get("notify_repeat_hours"), 6.0) or 6.0) * 3600.0
        should_notify, alerted, last_ts = decide_alert(
            total=balance.total,
            threshold=self.store.threshold,
            alerted=bool(self.store.sget("alerted")),
            last_alert_ts=_num(self.store.sget("last_alert_ts"), 0.0) or 0.0,
            repeat_seconds=repeat,
            now=now,
            notify_enabled=bool(self.store.get("notify_enabled", True)),
        )
        if not should_notify:
            if alerted != bool(self.store.sget("alerted")):
                self.store.sset(alerted=alerted)
                self.store.save_state()
            return
        notify.send_async(
            f"{APP_TITLE}：余量不足",
            f"当前余额 {format_amount(balance.total, balance.currency)}，"
            f"已低于提醒阈值 {format_amount(self.store.threshold, balance.currency)}，请及时充值。",
            icon="dialog-warning",
            urgency="critical",
        )
        self.store.sset(alerted=alerted, last_alert_ts=last_ts)
        self.store.save_state()
        log("已弹出余量不足提醒", always=True)

    # -------------------------------------------------------------- 窗口
    def open_login(self, force: bool = False) -> None:
        dialog = LoginDialog(self.store, parent=None)
        response = dialog.run()
        dialog.destroy()
        if response == Gtk.ResponseType.OK:
            self.keys = []
            self.usage = None
            self.usage_error = ""
            self._render_icon_for_state()
            self._update_menu()
            self._sync_usage_indicator()
            self._reset_timer()
            self.request_refresh(manual=True)
        elif force:
            self._render_icon_for_state()

    def open_settings(self) -> None:
        dialog = SettingsDialog(
            self.store,
            on_relogin=lambda: setattr(self, "_relogin_requested", True),
            on_clear=self._clear_credentials,
            on_saved=self._on_settings_saved,
        )
        dialog.run()
        relogin = getattr(self, "_relogin_requested", False)
        self._relogin_requested = False
        dialog.destroy()
        if relogin:
            self.open_login(force=True)

    def _on_settings_saved(self) -> None:
        self._render_icon_for_state()
        self._update_menu()
        self.usage_toggle.set_active(bool(self.store.get("usage_display")))
        self._sync_usage_indicator()
        self._reset_timer()
        if self._usage_wanted():
            self.request_refresh(manual=True)

    def _clear_credentials(self) -> None:
        self.store.clear_credentials()
        self.store.save_config()
        self.store.sset(
            last_total=None, last_error="", error_kind="", alerted=False,
            last_usage_cost=None, last_usage_tokens=None, last_usage_requests=None,
        )
        self.store.save_state()
        self.keys = []
        self.usage = None
        self._render_icon_for_state()
        self._update_menu()
        self._sync_usage_indicator()
        log("已清除本机凭据", always=True)

    # -------------------------------------------------------------- 生命周期
    def quit(self) -> None:
        for indicator in (self.indicator, self.usage_indicator):
            try:
                if indicator is not None:
                    indicator.set_status(AppIndicator.IndicatorStatus.PASSIVE)
            except Exception:  # noqa: BLE001
                pass
        Gtk.main_quit()

    def _on_signal(self, _signum=None) -> bool:
        # 登录/设置窗口是嵌套主循环，必须先销毁它们，否则 Gtk.main_quit() 退不出去
        try:
            for window in Gtk.Window.list_toplevels():
                window.destroy()
        except Exception:  # noqa: BLE001
            pass
        self.quit()
        # 兜底：万一还有嵌套循环，1.5 秒后强制退出（flock 由内核释放）
        GLib.timeout_add(1500, lambda: (os._exit(0), False)[1])
        return False

    def run(self) -> None:
        for sig in (signal.SIGINT, signal.SIGTERM):
            try:
                GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, sig, self._on_signal)
            except Exception:  # noqa: BLE001
                pass
        if not self.store.is_configured():
            GLib.timeout_add(500, self._first_run_login)
        else:
            self.request_refresh(delay_ms=1500)
        log(f"已启动（模式={self.store.auth_mode or '未登录'}，间隔={self.store.interval_seconds}s）")
        Gtk.main()

    def _first_run_login(self) -> bool:
        self.open_login(force=True)
        return False


def run(store: Store, debug: bool = False) -> int:
    global _DEBUG
    _DEBUG = bool(debug)
    Gtk.init([])
    app = BalanceApp(store, debug=debug)
    app.run()
    return 0
