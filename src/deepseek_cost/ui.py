"""登录窗口（内嵌官方登录页 / 令牌 / API Key）与设置窗口。"""

from __future__ import annotations

import json
import threading

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Pango", "1.0")
from gi.repository import GLib, Gtk, Pango  # noqa: E402

from . import APP_TITLE, api, util  # noqa: E402
from .api import ApiError  # noqa: E402
from .config import build_credential_updates  # noqa: E402
from .render import format_amount  # noqa: E402

LOGIN_PAGE_URL = "https://platform.deepseek.com/"
API_KEYS_URL = "https://platform.deepseek.com/api_keys"
TOKEN_HELP = (
    "获取浏览器令牌：登录 platform.deepseek.com 后按 F12 → Application/存储 → Local Storage\n"
    "→ https://platform.deepseek.com → 复制 userToken 的值，粘贴到下面。"
)
APIKEY_HELP = (
    "使用官方 API Key（sk-…）：在 platform.deepseek.com/api_keys 创建后粘贴到下面。\n"
    "这种方式只读取余额，不消耗任何额度。"
)

_LOCAL_STORAGE_JS = r"""
(() => {
  try {
    const pairs = {};
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      pairs[k] = localStorage.getItem(k);
    }
    return JSON.stringify({ok: true, data: pairs});
  } catch (e) {
    return JSON.stringify({ok: false, error: String(e)});
  }
})()
"""


class WebLoginWindow(Gtk.Window):
    """内嵌 DeepSeek 官方登录页，登录成功后自动读取 localStorage 里的令牌。"""

    def __init__(self, parent: Gtk.Window | None, on_token, on_status=None) -> None:
        super().__init__(title="登录 DeepSeek 账户")
        self.set_default_size(520, 680)
        if parent is not None:
            self.set_transient_for(parent)
        self.on_token = on_token
        self.on_status = on_status
        self._token_received = False
        self._last_signature = ""
        self._checking = False
        self._pending: list[str] = []

        gi.require_version("WebKit2", "4.1")
        from gi.repository import WebKit2  # noqa: PLC0415

        self.webview = WebKit2.WebView()
        settings = self.webview.get_settings()
        try:
            settings.set_property("enable-developer-extras", False)
        except Exception:  # noqa: BLE001
            pass

        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        bar.set_margin_top(6)
        bar.set_margin_bottom(6)
        bar.set_margin_start(6)
        bar.set_margin_end(6)
        self.status = Gtk.Label(label="正在打开官方登录页…", xalign=0)
        self.status.set_ellipsize(Pango.EllipsizeMode.END)
        self.status.set_max_width_chars(46)
        bar.pack_start(self.status, True, True, 0)
        open_button = Gtk.Button(label="用系统浏览器打开")
        open_button.set_tooltip_text("如果内嵌页面无法显示，可用系统浏览器登录后改用「浏览器令牌」方式")
        open_button.connect("clicked", self._on_open_browser)
        bar.pack_end(open_button, False, False, 0)
        box.pack_start(bar, False, False, 0)
        scroller = Gtk.ScrolledWindow()
        scroller.add(self.webview)
        box.pack_start(scroller, True, True, 0)
        self.add(box)

        self.connect("destroy", self._on_destroy)
        self.webview.load_uri(LOGIN_PAGE_URL)
        self._timer_id = GLib.timeout_add(1500, self._poll)

    # -- 状态 -------------------------------------------------------------
    def _set_status(self, text: str) -> None:
        self.status.set_text(text)
        if self.on_status:
            self.on_status(text)

    def _on_open_browser(self, _button) -> None:
        util.open_url(LOGIN_PAGE_URL)

    def _on_destroy(self, _widget) -> None:
        if getattr(self, "_timer_id", None):
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    # -- localStorage 轮询 -------------------------------------------------
    def _poll(self) -> bool:
        if self._token_received:
            return False
        if not self._checking:
            self._run_js(_LOCAL_STORAGE_JS)
        return True

    def _run_js(self, script: str) -> None:
        self._checking = True
        try:
            if hasattr(self.webview, "evaluate_javascript"):
                self.webview.evaluate_javascript(
                    script, -1, None, None, None, self._on_js_evaluated, None
                )
            else:  # 老版本 WebKitGTK
                self.webview.run_javascript(script, None, self._on_js_ran, None)
        except Exception as exc:  # noqa: BLE001
            self._checking = False
            self._set_status(f"页面尚未就绪：{exc}")

    def _extract(self, result) -> str | None:
        """兼容两代 WebKit API：evaluate_javascript_finish 返回 JSCValue，
        run_javascript_finish 返回 JavascriptResult。"""
        if result is None:
            return None
        value = result
        if hasattr(result, "get_js_value"):  # 老 API
            value = result.get_js_value()
        if value is None:
            return None
        try:
            return value.to_string()
        except Exception:  # noqa: BLE001
            return None

    def _on_js_evaluated(self, webview, result, _user_data) -> None:
        try:
            text = self._extract(webview.evaluate_javascript_finish(result))
        except Exception:  # noqa: BLE001
            text = None
        self._handle_storage(text)

    def _on_js_ran(self, webview, result, _user_data) -> None:
        try:
            text = self._extract(webview.run_javascript_finish(result))
        except Exception:  # noqa: BLE001
            text = None
        self._handle_storage(text)

    def _handle_storage(self, text: str | None) -> None:
        self._checking = False
        if self._token_received:
            return
        if not text:
            self._set_status("等待登录完成…")
            return
        try:
            payload = json.loads(text)
        except ValueError:
            self._set_status("等待登录完成…")
            return
        if not payload.get("ok"):
            self._set_status("页面尚未就绪，等待登录…")
            return
        storage = payload.get("data") or {}
        signature = "|".join(f"{k}={str(storage[k])[:16]}" for k in sorted(storage))
        if signature == self._last_signature:
            self._set_status("等待登录完成…")
            return
        self._last_signature = signature
        candidates = api.token_candidates(storage)
        if not candidates:
            self._set_status("等待登录完成…")
            return
        self._set_status("检测到登录信息，校验中…")
        self._validate(candidates)

    def _validate(self, candidates: list[str]) -> None:
        # 最多试 8 个候选，避免把无关的 localStorage 值批量发出去
        self._pending = list(candidates)[:8]

        def work() -> None:
            for token in self._pending:
                try:
                    email, _ = api.fetch_platform_user(token)
                except ApiError:
                    continue
                GLib.idle_add(self._on_valid, token, email)
                return
            GLib.idle_add(self._set_status, "校验未通过，稍后自动重试…")

        threading.Thread(target=work, daemon=True).start()

    def _on_valid(self, token: str, email: str) -> bool:
        if self._token_received:
            return False
        self._token_received = True
        self._set_status(f"已获取登录信息 ✓ {email}")
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None
        try:
            self.on_token(token, email)
        finally:
            self.destroy()
        return False


class LoginDialog(Gtk.Dialog):
    """首次使用 / 重新登录时的凭据录入窗口。"""

    def __init__(self, store, parent: Gtk.Window | None = None) -> None:
        super().__init__(title=f"{APP_TITLE} · 登录 DeepSeek", transient_for=parent, flags=0)
        self.store = store
        self.web_token = ""
        self.web_email = ""
        self._busy = False

        self.set_default_size(560, 520)
        self.add_button("取消", Gtk.ResponseType.CANCEL)
        self._ok_button = self.add_button("验证并保存", Gtk.ResponseType.OK)
        self._ok_button.get_style_context().add_class("suggested-action")

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(14)
        content.set_margin_end(14)

        title = Gtk.Label(xalign=0)
        title.set_markup("<b>登录 DeepSeek 账户</b>")
        content.pack_start(title, False, False, 0)
        subtitle = Gtk.Label(
            xalign=0,
            label="三种方式任选其一，登录信息只保存在本机（~/.config/deepseek-cost，权限 600）。",
        )
        subtitle.set_line_wrap(True)
        content.pack_start(subtitle, False, False, 0)

        notebook = Gtk.Notebook()
        notebook.append_page(self._build_web_page(), Gtk.Label(label="账户登录"))
        notebook.append_page(self._build_token_page(), Gtk.Label(label="浏览器令牌"))
        notebook.append_page(self._build_apikey_page(), Gtk.Label(label="API Key"))
        self.notebook = notebook
        content.pack_start(notebook, True, True, 0)

        content.pack_start(Gtk.Separator(), False, False, 4)
        content.pack_start(self._build_preferences(), False, False, 0)

        self.message = Gtk.Label(xalign=0)
        self.message.set_line_wrap(True)
        content.pack_start(self.message, False, False, 0)

        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.spinner = Gtk.Spinner()
        box.pack_start(self.spinner, False, False, 0)
        self.hint = Gtk.Label(xalign=0, label="")
        box.pack_start(self.hint, True, True, 0)
        content.pack_start(box, False, False, 0)

        self.connect("response", self._on_response)
        self.show_all()

    # -- 页面 -------------------------------------------------------------
    def _build_web_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        label = Gtk.Label(
            xalign=0,
            label="推荐：点下面的按钮打开 DeepSeek 官方登录页（内嵌浏览器），"
            "用你的账号登录后会自动获取登录令牌。",
        )
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)

        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.web_button = Gtk.Button(label="打开官方登录页")
        self.web_button.connect("clicked", self._open_web_login)
        row.pack_start(self.web_button, False, False, 0)
        self.web_status = Gtk.Label(xalign=0, label="未登录")
        row.pack_start(self.web_status, True, True, 0)
        box.pack_start(row, False, False, 0)

        expander = Gtk.Expander(label="或者：用邮箱 + 密码登录（若平台要求验证码请改用网页登录）")
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.set_border_width(8)
        grid.attach(Gtk.Label(label="邮箱", xalign=1), 0, 0, 1, 1)
        self.email_entry = Gtk.Entry()
        self.email_entry.set_placeholder_text("you@example.com")
        self.email_entry.set_text(str(self.store.get("email") or ""))
        self.email_entry.set_hexpand(True)
        grid.attach(self.email_entry, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="密码", xalign=1), 0, 1, 1, 1)
        self.password_entry = Gtk.Entry()
        self.password_entry.set_visibility(False)
        self.password_entry.set_input_purpose(Gtk.InputPurpose.PASSWORD)
        self.password_entry.set_hexpand(True)
        password_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        password_box.pack_start(self.password_entry, True, True, 0)
        toggle = Gtk.ToggleButton(label="显示")
        toggle.connect("toggled", lambda b: self.password_entry.set_visibility(b.get_active()))
        password_box.pack_start(toggle, False, False, 0)
        grid.attach(password_box, 1, 1, 1, 1)
        self.remember_check = Gtk.CheckButton(label="记住密码（令牌过期后自动重新登录）")
        self.remember_check.set_active(bool(self.store.get("remember_password")))
        grid.attach(self.remember_check, 1, 2, 1, 1)
        expander.add(grid)
        box.pack_start(expander, False, False, 0)
        return box

    def _build_token_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        label = Gtk.Label(xalign=0, label=TOKEN_HELP)
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)
        self.token_entry = Gtk.Entry()
        self.token_entry.set_placeholder_text("粘贴 userToken")
        self.token_entry.set_text(str(self.store.get("platform_token") or ""))
        box.pack_start(self.token_entry, False, False, 0)
        return box

    def _build_apikey_page(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        box.set_border_width(10)
        label = Gtk.Label(xalign=0, label=APIKEY_HELP)
        label.set_line_wrap(True)
        box.pack_start(label, False, False, 0)
        self.apikey_entry = Gtk.Entry()
        self.apikey_entry.set_placeholder_text("sk-…")
        self.apikey_entry.set_text(str(self.store.get("api_key") or ""))
        box.pack_start(self.apikey_entry, False, False, 0)
        open_button = Gtk.Button(label="打开 API Keys 页面")
        open_button.connect("clicked", lambda _b: util.open_url(API_KEYS_URL))
        box.pack_start(open_button, False, False, 0)
        return box

    def _build_preferences(self) -> Gtk.Widget:
        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.attach(Gtk.Label(label="余量提醒阈值", xalign=1), 0, 0, 1, 1)
        self.threshold_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1)
        self.threshold_spin.set_digits(2)
        self.threshold_spin.set_value(float(self.store.threshold))
        grid.attach(self.threshold_spin, 1, 0, 1, 1)
        grid.attach(Gtk.Label(label="余额低于此值时弹窗提醒", xalign=0), 2, 0, 1, 1)

        grid.attach(Gtk.Label(label="自动刷新间隔（分钟）", xalign=1), 0, 1, 1, 1)
        self.interval_spin = Gtk.SpinButton.new_with_range(1, 240, 1)
        self.interval_spin.set_value(float(self.store.get("interval_minutes", 5)))
        grid.attach(self.interval_spin, 1, 1, 1, 1)
        grid.attach(Gtk.Label(label="默认 5 分钟，点击顶栏文字可立即刷新", xalign=0), 2, 1, 1, 1)

        self.notify_check = Gtk.CheckButton(label="低于阈值时弹出桌面通知")
        self.notify_check.set_active(bool(self.store.get("notify_enabled", True)))
        grid.attach(self.notify_check, 1, 2, 2, 1)
        return grid

    # -- 交互 -------------------------------------------------------------
    def _open_web_login(self, _button) -> None:
        WebLoginWindow(self, self._on_web_token, self.web_status.set_text)

    def _on_web_token(self, token: str, email: str) -> None:
        self.web_token = token
        self.web_email = email
        self.web_status.set_text(f"已登录：{email or '（已获取令牌）'}")
        self.message.set_markup(
            "<span foreground='#2e7d32'>已获取登录令牌，点击「验证并保存」完成设置。</span>"
        )

    def _set_busy(self, busy: bool, hint: str = "") -> None:
        self._busy = busy
        if busy:
            self.spinner.start()
            self._ok_button.set_sensitive(False)
        else:
            self.spinner.stop()
            self._ok_button.set_sensitive(True)
        self.hint.set_text(hint)

    def _on_response(self, _dialog, response_id) -> None:
        if response_id != Gtk.ResponseType.OK:
            return
        if self._busy:
            self.stop_emission_by_name("response")
            return
        page = self.notebook.get_current_page()
        mode = ""
        credential = ""
        password = ""
        email = ""
        if page == 0:
            if self.web_token:
                mode, credential = "token", self.web_token
            else:
                email = self.email_entry.get_text().strip()
                password = self.password_entry.get_text()
                if email and password:
                    mode, credential = "account", ""
                else:
                    self._fail("请先点击「打开官方登录页」完成登录，或填写邮箱 + 密码。")
                    self.stop_emission_by_name("response")
                    return
        elif page == 1:
            mode, credential = "token", self.token_entry.get_text().strip()
        else:
            mode, credential = "apikey", self.apikey_entry.get_text().strip()
        if mode != "account" and not credential:
            self._fail("请填写凭据后再保存。")
            self.stop_emission_by_name("response")
            return

        self._set_busy(True, "正在验证凭据并获取余额…")
        self.message.set_text("")
        settings = {
            "threshold": float(self.threshold_spin.get_value()),
            "interval_minutes": int(self.interval_spin.get_value()),
            "notify_enabled": bool(self.notify_check.get_active()),
            "remember_password": bool(self.remember_check.get_active()),
            "email": email or self.web_email or str(self.store.get("email") or ""),
        }
        threading.Thread(
            target=self._validate_worker,
            args=(mode, credential, password, email, settings),
            daemon=True,
        ).start()
        self.stop_emission_by_name("response")

    def _validate_worker(self, mode: str, credential: str, password: str, email: str, settings: dict) -> None:
        try:
            if mode == "account":
                token, server_email = api.login_account(email, password)
                credential = token
                email = server_email or email
                # 保持 mode="account"：这样令牌过期后 app 能用记住的密码自动重新登录
            balance, server_email = api.validate_login(mode, credential)
            GLib.idle_add(self._on_validated, mode, credential, password, email or server_email, settings, balance, None)
        except Exception as exc:  # noqa: BLE001
            GLib.idle_add(self._on_validated, mode, credential, password, email, settings, None, exc)

    def _on_validated(self, mode, credential, password, email, settings, balance, error) -> bool:
        self._set_busy(False, "")
        if error is not None:
            self._fail(api.describe_error(error))
            return False
        remember = bool(settings["remember_password"])
        updates = build_credential_updates(
            mode=mode,
            credential=credential,
            password=password,
            email=email or "",
            remember_password=remember,
            extra={
                "threshold": settings["threshold"],
                "interval_minutes": settings["interval_minutes"],
                "notify_enabled": settings["notify_enabled"],
            },
        )
        self.store.set(**updates)
        self.store.save_config()
        summary = (
            f"登录成功：{format_amount(balance.total, balance.currency)}"
            f"（{balance.currency}），设置已保存。"
        )
        self.message.set_markup(
            f"<span foreground='#2e7d32'>✓ {GLib.markup_escape_text(summary)}</span>"
        )
        self.response(Gtk.ResponseType.OK)
        return False

    def _fail(self, text: str) -> None:
        self.message.set_markup(f"<span foreground='#c62828'>✗ {GLib.markup_escape_text(text)}</span>")


class SettingsDialog(Gtk.Dialog):
    """设置窗口：阈值、刷新间隔、面板字号、重新登录、清除凭据。"""

    def __init__(self, store, on_relogin, on_clear, on_saved=None, parent: Gtk.Window | None = None) -> None:
        super().__init__(title=f"{APP_TITLE} · 设置", transient_for=parent, flags=0)
        self.store = store
        self.on_relogin = on_relogin
        self.on_clear = on_clear
        self.on_saved = on_saved
        self.set_default_size(480, 360)
        self.add_button("关闭", Gtk.ResponseType.CLOSE)
        save_button = self.add_button("保存", Gtk.ResponseType.OK)
        save_button.get_style_context().add_class("suggested-action")

        content = self.get_content_area()
        content.set_spacing(8)
        content.set_margin_top(12)
        content.set_margin_bottom(12)
        content.set_margin_start(14)
        content.set_margin_end(14)

        info = Gtk.Label(xalign=0)
        info.set_markup(
            f"<b>登录方式</b>：{self._mode_text()}\n"
            f"<b>当前凭据</b>：{GLib.markup_escape_text(store.masked_secret())}"
        )
        content.pack_start(info, False, False, 0)

        grid = Gtk.Grid(column_spacing=8, row_spacing=6)
        grid.attach(Gtk.Label(label="余量提醒阈值", xalign=1), 0, 0, 1, 1)
        self.threshold_spin = Gtk.SpinButton.new_with_range(0, 1000000, 1)
        self.threshold_spin.set_digits(2)
        self.threshold_spin.set_value(float(store.threshold))
        grid.attach(self.threshold_spin, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="刷新间隔（分钟）", xalign=1), 0, 1, 1, 1)
        self.interval_spin = Gtk.SpinButton.new_with_range(1, 240, 1)
        self.interval_spin.set_value(float(store.get("interval_minutes", 5)))
        grid.attach(self.interval_spin, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="顶栏文字高度（像素）", xalign=1), 0, 2, 1, 1)
        self.height_spin = Gtk.SpinButton.new_with_range(14, 32, 1)
        self.height_spin.set_value(float(store.panel_height))
        grid.attach(self.height_spin, 1, 2, 1, 1)

        self.notify_check = Gtk.CheckButton(label="低于阈值时弹出桌面通知")
        self.notify_check.set_active(bool(store.get("notify_enabled", True)))
        grid.attach(self.notify_check, 1, 3, 1, 1)

        self.usage_check = Gtk.CheckButton(label="在顶栏用黄色显示所选 API Key 的今日用量")
        self.usage_check.set_active(bool(store.get("usage_display", False)))
        grid.attach(self.usage_check, 1, 4, 2, 1)
        self.usage_cost_check = Gtk.CheckButton(label="显示今日消耗金额")
        self.usage_cost_check.set_active(bool(store.get("usage_show_cost", True)))
        grid.attach(self.usage_cost_check, 1, 5, 1, 1)
        self.usage_token_check = Gtk.CheckButton(label="显示今日 token 总量")
        self.usage_token_check.set_active(bool(store.get("usage_show_tokens", True)))
        grid.attach(self.usage_token_check, 2, 5, 1, 1)
        content.pack_start(grid, False, False, 0)

        buttons = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        relogin = Gtk.Button(label="重新登录…")
        relogin.connect("clicked", self._on_relogin_clicked)
        buttons.pack_start(relogin, False, False, 0)
        clear = Gtk.Button(label="清除本机凭据")
        clear.connect("clicked", self._on_clear_clicked)
        buttons.pack_start(clear, False, False, 0)
        content.pack_start(buttons, False, False, 0)

        self.connect("response", self._on_response)
        self.show_all()

    def _on_relogin_clicked(self, _button) -> None:
        self.on_relogin()
        self.response(Gtk.ResponseType.CLOSE)

    def _mode_text(self) -> str:
        return {
            "account": "邮箱 + 密码",
            "token": "浏览器令牌",
            "apikey": "API Key",
        }.get(self.store.auth_mode, "未登录")

    def _on_clear_clicked(self, _button) -> None:
        dialog = Gtk.MessageDialog(
            transient_for=self,
            flags=Gtk.DialogFlags.MODAL,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="确定要清除本机保存的登录凭据吗？",
        )
        if dialog.run() == Gtk.ResponseType.YES:
            self.on_clear()
        dialog.destroy()

    def _on_response(self, _dialog, response_id) -> None:
        if response_id == Gtk.ResponseType.OK:
            self.store.set(
                threshold=float(self.threshold_spin.get_value()),
                interval_minutes=int(self.interval_spin.get_value()),
                panel_height=int(self.height_spin.get_value()),
                notify_enabled=bool(self.notify_check.get_active()),
                usage_display=bool(self.usage_check.get_active()),
                usage_show_cost=bool(self.usage_cost_check.get_active()),
                usage_show_tokens=bool(self.usage_token_check.get_active()),
            )
            self.store.save_config()
            if self.on_saved:
                self.on_saved()
