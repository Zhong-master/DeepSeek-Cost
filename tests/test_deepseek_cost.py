"""DeepSeek 余额指示器 —— 自动化测试（不联网，全部走本地假接口）。

运行： bash tests/run_tests.sh
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import time
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))
sys.path.insert(0, str(ROOT / "tests"))

from deepseek_cost import api  # noqa: E402
from deepseek_cost.alerts import decide_alert  # noqa: E402
from deepseek_cost.config import Store, build_credential_updates  # noqa: E402
from deepseek_cost.render import COLOR_USAGE, PanelIcon, format_amount, render_text_png  # noqa: E402
from deepseek_cost.util import SingleInstance  # noqa: E402
from PIL import Image  # noqa: E402

from mock_server import MockServer  # noqa: E402


def png_size(path: Path) -> tuple[int, int]:
    data = path.read_bytes()
    assert data[:8] == b"\x89PNG\r\n\x1a\n", "不是合法 PNG"
    width, height = struct.unpack(">II", data[16:24])
    return width, height


class ConfigTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name))

    def test_defaults(self) -> None:
        self.assertEqual(self.store.threshold, 50.0)
        self.assertEqual(self.store.interval_seconds, 300)
        self.assertEqual(self.store.auth_mode, "")
        self.assertFalse(self.store.is_configured())

    def test_roundtrip_and_permissions(self) -> None:
        self.store.set(auth_mode="apikey", api_key="sk-test-123456", threshold=88.5)
        self.store.save_config()
        mode = oct(self.store.config_path.stat().st_mode & 0o777)
        self.assertEqual(mode, "0o600", f"凭据文件权限应为 600，实际 {mode}")
        again = Store(Path(self.tmp.name))
        self.assertEqual(again.auth_mode, "apikey")
        self.assertEqual(again.threshold, 88.5)
        self.assertEqual(again.credential(), "sk-test-123456")

    def test_state_roundtrip(self) -> None:
        self.store.sset(last_total=42.0, alerted=True, last_alert_ts=123.0)
        self.store.save_state()
        again = Store(Path(self.tmp.name))
        self.assertEqual(again.sget("last_total"), 42.0)
        self.assertTrue(again.sget("alerted"))

    def test_clear_credentials(self) -> None:
        self.store.set(auth_mode="token", platform_token="tok-0123456789abcdef", password="pw")
        self.assertTrue(self.store.is_configured())
        masked = self.store.masked_secret()
        self.assertNotIn("0123456789abcdef", masked)
        self.store.clear_credentials()
        self.assertFalse(self.store.is_configured())
        self.assertEqual(self.store.masked_secret(), "（空）")

    def test_interval_clamped(self) -> None:
        self.store.set(interval_minutes=0.1)
        self.assertEqual(self.store.interval_seconds, 30)


class FormatTests(unittest.TestCase):
    def test_small_amounts(self) -> None:
        self.assertEqual(format_amount(123.45, "CNY"), "¥123.45")
        self.assertEqual(format_amount(0.0, "CNY"), "¥0.00")
        self.assertEqual(format_amount(49.5, "CNY"), "¥49.50")

    def test_usd_symbol(self) -> None:
        self.assertEqual(format_amount(12.3, "USD"), "$12.30")

    def test_large_amounts_compact(self) -> None:
        self.assertEqual(format_amount(12345.6, "CNY"), "¥1.2万")
        self.assertEqual(format_amount(12345.6, "USD"), "$12.3k")
        self.assertEqual(format_amount(1234.5, "USD"), "$1,234.50")


class RenderTests(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.dir = Path(self.tmp.name)

    def test_png_is_wide_text_banner(self) -> None:
        target = self.dir / "a.png"
        width, height = render_text_png(target, "¥123.45", height=22)
        file_width, file_height = png_size(target)
        self.assertEqual((width, height), (file_width, file_height))
        self.assertEqual(file_height, 22)
        self.assertGreater(file_width / file_height, 1.5, "宽度需 >= 1.5 倍高度才会被顶栏按原尺寸显示")

    def test_scale_factor(self) -> None:
        target = self.dir / "b.png"
        _width, height = render_text_png(target, "¥9.99", height=22, scale=2)
        self.assertEqual(png_size(target)[1], 44)
        self.assertEqual(height, 44)

    def test_color_changes_pixels(self) -> None:
        normal = self.dir / "n.png"
        low = self.dir / "l.png"
        render_text_png(normal, "¥12.34", color=(0.55, 0.93, 0.62))
        render_text_png(low, "¥12.34", color=(1.0, 0.42, 0.42))
        self.assertNotEqual(normal.read_bytes(), low.read_bytes())

    def test_chinese_text_renders(self) -> None:
        target = self.dir / "c.png"
        width, height = render_text_png(target, "登录失效", height=22)
        self.assertGreater(width, 40)
        self.assertEqual(height, 22)

    def test_panel_icon_rotation(self) -> None:
        icon = PanelIcon(self.dir / "icons", keep=3)
        for i in range(6):
            icon.update(f"¥{i}.00", height=22)
        files = list((self.dir / "icons").glob("panel-*.png"))
        self.assertEqual(len(files), 3, f"应只保留 3 个图标文件，实际 {len(files)}")
        self.assertTrue(Path(icon.path).exists())


class AlertTests(unittest.TestCase):
    def test_first_crossing_alerts(self) -> None:
        notify, alerted, ts = decide_alert(49.9, 50.0, False, 0.0, 21600.0, 1000.0)
        self.assertTrue(notify)
        self.assertTrue(alerted)
        self.assertEqual(ts, 1000.0)

    def test_no_repeat_within_window(self) -> None:
        notify, alerted, ts = decide_alert(49.9, 50.0, True, 1000.0, 21600.0, 1001.0)
        self.assertFalse(notify)
        self.assertTrue(alerted)
        self.assertEqual(ts, 1000.0)

    def test_repeat_after_window(self) -> None:
        notify, _alerted, ts = decide_alert(10.0, 50.0, True, 1000.0, 3600.0, 5000.0)
        self.assertTrue(notify)
        self.assertEqual(ts, 5000.0)

    def test_rearm_after_topup(self) -> None:
        notify, alerted, _ts = decide_alert(88.0, 50.0, True, 1000.0, 3600.0, 2000.0)
        self.assertFalse(notify)
        self.assertFalse(alerted, "余额回到阈值以上后应解除提醒状态")

    def test_disabled_and_unknown(self) -> None:
        self.assertFalse(decide_alert(1.0, 50.0, False, 0.0, 0.0, 1.0, notify_enabled=False)[0])
        self.assertFalse(decide_alert(None, 50.0, False, 0.0, 0.0, 1.0)[0])


class TokenCandidateTests(unittest.TestCase):
    def test_user_token_plain(self) -> None:
        self.assertEqual(api.token_candidates({"userToken": "abc" * 20})[0], "abc" * 20)

    def test_user_token_json_wrapped(self) -> None:
        raw = json.dumps({"value": "tok." + "x" * 60, "expire": 0})
        self.assertEqual(api.token_candidates({"userToken": raw})[0], "tok." + "x" * 60)

    def test_long_value_fallback(self) -> None:
        long_value = "y" * 80
        self.assertIn(long_value, api.token_candidates({"whatever": long_value}))

    def test_empty_storage(self) -> None:
        self.assertEqual(api.token_candidates({}), [])


class ApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer().start()
        cls._env_backup = {
            key: os.environ.get(key)
            for key in ("DEEPSEEK_COST_API_BASE", "DEEPSEEK_COST_PLATFORM_BASE")
        }
        os.environ["DEEPSEEK_COST_API_BASE"] = cls.server.api_base
        os.environ["DEEPSEEK_COST_PLATFORM_BASE"] = cls.server.platform_base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        for key, value in cls._env_backup.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def test_api_key_balance(self) -> None:
        balance = api.fetch_api_key_balance("sk-good")
        self.assertAlmostEqual(balance.total, 123.45, places=2)
        self.assertAlmostEqual(balance.granted, 10.0, places=2)
        self.assertAlmostEqual(balance.topped_up, 113.45, places=2)
        self.assertEqual(balance.currency, "CNY")
        self.assertTrue(balance.is_available)

    def test_api_key_low_balance(self) -> None:
        self.assertAlmostEqual(api.fetch_api_key_balance("sk-low").total, 12.34, places=2)

    def test_api_key_invalid_raises_auth(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_api_key_balance("sk-nope")
        self.assertEqual(ctx.exception.kind, api.KIND_AUTH)

    def test_platform_user_and_balance(self) -> None:
        email, currency = api.fetch_platform_user("tok-good")
        self.assertEqual(email, "test@example.com")
        self.assertEqual(currency, "CNY")
        balance = api.fetch_platform_balance("tok-good")
        self.assertAlmostEqual(balance.total, 123.45, places=2)
        self.assertAlmostEqual(balance.granted, 10.0, places=2)
        self.assertAlmostEqual(balance.topped_up, 113.45, places=2)

    def test_platform_expired_token(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_platform_balance("tok-expired")
        self.assertEqual(ctx.exception.kind, api.KIND_AUTH)

    def test_login_account(self) -> None:
        token, email = api.login_account("someone@example.com", "goodpass")
        self.assertEqual(token, "tok-good")
        self.assertEqual(email, "test@example.com")

    def test_login_account_bad_password(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.login_account("someone@example.com", "wrong")
        self.assertEqual(ctx.exception.kind, api.KIND_AUTH)
        self.assertIn("密码错误", ctx.exception.message)

    def test_fetch_balance_dispatch(self) -> None:
        self.assertAlmostEqual(api.fetch_balance("apikey", "sk-good").total, 123.45, places=2)
        self.assertAlmostEqual(api.fetch_balance("token", "tok-low").total, 12.34, places=2)

    def test_validate_login_returns_email(self) -> None:
        balance, email = api.validate_login("token", "tok-good")
        self.assertAlmostEqual(balance.total, 123.45, places=2)
        self.assertEqual(email, "test@example.com")

    def test_network_error_kind(self) -> None:
        os.environ["DEEPSEEK_COST_API_BASE"] = "http://127.0.0.1:1"
        try:
            with self.assertRaises(api.ApiError) as ctx:
                api.fetch_api_key_balance("sk-good")
            self.assertEqual(ctx.exception.kind, api.KIND_NETWORK)
        finally:
            os.environ["DEEPSEEK_COST_API_BASE"] = self.server.api_base


class SingleInstanceTests(unittest.TestCase):
    def test_second_instance_is_rejected_then_released(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "instance.lock"
            first = SingleInstance(path)
            second = SingleInstance(path)
            self.assertTrue(first.acquire())
            self.assertFalse(second.acquire())
            first.release()
            third = SingleInstance(path)
            self.assertTrue(third.acquire())
            third.release()


class CliTests(unittest.TestCase):
    """端到端：命令行 --once 走真实代码路径（只访问本地假接口）。"""

    def test_once_prints_balance(self) -> None:
        with MockServer() as server, tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.set(auth_mode="apikey", api_key="sk-good")
            store.save_config()
            env = {
                **os.environ,
                "PYTHONPATH": str(SRC),
                "DEEPSEEK_COST_DIR": tmp,
                "DEEPSEEK_COST_API_BASE": server.api_base,
                "DEEPSEEK_COST_PLATFORM_BASE": server.platform_base,
            }
            result = subprocess.run(
                [sys.executable, "-m", "deepseek_cost", "--once"],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("¥123.45", result.stdout)

    def test_low_balance_marks_threshold(self) -> None:
        with MockServer() as server, tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.set(auth_mode="apikey", api_key="sk-low", threshold=50.0)
            store.save_config()
            env = {
                **os.environ,
                "PYTHONPATH": str(SRC),
                "DEEPSEEK_COST_DIR": tmp,
                "DEEPSEEK_COST_API_BASE": server.api_base,
            }
            result = subprocess.run(
                [sys.executable, "-m", "deepseek_cost", "--once"],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("¥12.34", result.stdout)
            self.assertIn("低于提醒阈值", result.stdout)

    def test_once_without_login_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            env = {**os.environ, "PYTHONPATH": str(SRC), "DEEPSEEK_COST_DIR": tmp}
            result = subprocess.run(
                [sys.executable, "-m", "deepseek_cost", "--once"],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(result.returncode, 2)
            self.assertIn("尚未登录", result.stderr)

    def test_reset_clears_credentials(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            store.set(auth_mode="apikey", api_key="sk-good")
            store.save_config()
            env = {**os.environ, "PYTHONPATH": str(SRC), "DEEPSEEK_COST_DIR": tmp}
            result = subprocess.run(
                [sys.executable, "-m", "deepseek_cost", "--reset"],
                capture_output=True, text=True, env=env, timeout=60,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse(Store(Path(tmp)).is_configured())


class CredentialUpdateTests(unittest.TestCase):
    """邮箱+密码登录必须保存 auth_mode="account"，自动重登录才可用。"""

    def test_account_login_keeps_account_mode_and_password(self) -> None:
        updates = build_credential_updates(
            mode="account", credential="tok-abc", password="secret",
            email="a@b.c", remember_password=True,
        )
        self.assertEqual(updates["auth_mode"], "account")
        self.assertEqual(updates["platform_token"], "tok-abc")
        self.assertEqual(updates["password"], "secret")
        self.assertEqual(updates["api_key"], "")

    def test_account_login_without_remember_does_not_store_password(self) -> None:
        updates = build_credential_updates(
            mode="account", credential="tok-abc", password="secret",
            email="a@b.c", remember_password=False,
        )
        self.assertEqual(updates["auth_mode"], "account")
        self.assertEqual(updates["password"], "")
        self.assertEqual(updates["platform_token"], "tok-abc")

    def test_web_token_mode_never_stores_password(self) -> None:
        updates = build_credential_updates(
            mode="token", credential="tok-web", password="", email="", remember_password=True,
        )
        self.assertEqual(updates["auth_mode"], "token")
        self.assertEqual(updates["password"], "")

    def test_apikey_mode_clears_platform_token(self) -> None:
        updates = build_credential_updates(
            mode="apikey", credential="sk-x", password="pw", email="", remember_password=True,
        )
        self.assertEqual(updates["api_key"], "sk-x")
        self.assertEqual(updates["platform_token"], "")


class ConfigReloadTests(unittest.TestCase):
    """运行中的实例应能感知 --login 写的新配置。"""

    def test_reload_detects_external_change(self) -> None:
        import time as _time
        with tempfile.TemporaryDirectory() as tmp:
            store = Store(Path(tmp))
            self.assertFalse(store.reload(), "没有外部改动时不应重载")
            store.set(auth_mode="apikey", api_key="sk-old")
            store.save_config()
            other = Store(Path(tmp))            # 另一个进程
            other.set(auth_mode="token", platform_token="tok-new")
            other.save_config()
            _time.sleep(0.01)
            self.assertTrue(store.reload())
            self.assertEqual(store.auth_mode, "token")
            self.assertEqual(store.credential(), "tok-new")

    def test_corrupt_state_values_do_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "state.json"
            path.write_text('{"last_total": "not-a-number", "last_granted": null}', encoding="utf-8")
            store = Store(Path(tmp))
            from deepseek_cost.app import _num
            self.assertIsNone(_num(store.sget("last_total")))
            self.assertEqual(_num(store.sget("last_total"), 0.0), 0.0)
            self.assertEqual(_num("1.5"), 1.5)


class ParseStrictnessTests(unittest.TestCase):
    """接口返回格式异常时必须报错，不能当成 0 元余额。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer().start()
        cls._env = {k: os.environ.get(k) for k in ("DEEPSEEK_COST_API_BASE", "DEEPSEEK_COST_PLATFORM_BASE")}
        os.environ["DEEPSEEK_COST_API_BASE"] = cls.server.api_base
        os.environ["DEEPSEEK_COST_PLATFORM_BASE"] = cls.server.platform_base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_broken_balance_infos_raises_parse(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_api_key_balance("sk-broken")
        self.assertEqual(ctx.exception.kind, api.KIND_PARSE)

    def test_broken_wallets_raises_parse(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_platform_balance("tok-broken")
        self.assertEqual(ctx.exception.kind, api.KIND_PARSE)

    def test_null_data_raises_parse(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_platform_balance("tok-null")
        self.assertEqual(ctx.exception.kind, api.KIND_PARSE)


class PanelIconIsolationTests(unittest.TestCase):
    """清理旧图标时不能删掉其它进程正在显示的图标。"""

    def test_foreign_icon_survives_cleanup(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            foreign = directory / "panel-999999-1.png"
            render_text_png(foreign, "¥9.99", height=22)
            icon = PanelIcon(directory, keep=2)
            for i in range(6):
                icon.update(f"¥{i}.00", height=22)
            self.assertTrue(foreign.exists(), "其它进程的图标被误删了")
            own = list(directory.glob(f"panel-{os.getpid()}-*.png"))
            self.assertEqual(len(own), 2, f"本进程应保留 2 个图标，实际 {len(own)}")


class UsageTests(unittest.TestCase):
    """今日用量（按 API Key）相关：窗口、解析、格式化、颜色。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls.server = MockServer().start()
        cls._env = {k: os.environ.get(k) for k in ("DEEPSEEK_COST_API_BASE", "DEEPSEEK_COST_PLATFORM_BASE")}
        os.environ["DEEPSEEK_COST_API_BASE"] = cls.server.api_base
        os.environ["DEEPSEEK_COST_PLATFORM_BASE"] = cls.server.platform_base

    @classmethod
    def tearDownClass(cls) -> None:
        cls.server.stop()
        for k, v in cls._env.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def test_day_window_is_local_midnight(self) -> None:
        start, end, tz, day = api.day_window(now=1790956800 + 3600)  # 本地 2026-10-03 01:00
        local = time.localtime(start)
        self.assertEqual((local.tm_hour, local.tm_min, local.tm_sec), (0, 0, 0))
        self.assertEqual(end - start, 86400)
        self.assertEqual(day, time.strftime("%Y-%m-%d", time.localtime(start)))
        self.assertEqual(tz % 3600, 0)

    def test_day_window_offset(self) -> None:
        _s, _e, _tz, today = api.day_window(0, now=1790956800 + 3600)
        _s2, _e2, _tz2, yesterday = api.day_window(1, now=1790956800 + 3600)
        self.assertNotEqual(today, yesterday)

    def test_fetch_api_keys(self) -> None:
        keys = api.fetch_api_keys("tok-good")
        names = [k.name for k in keys]
        self.assertEqual(names, ["Alpha", "Beta", "Gamma"])
        self.assertEqual(keys[0].tracking_id, "key-alpha")
        self.assertTrue(keys[0].masked_key.startswith("sk-"))

    def test_today_usage_auto_picks_busiest_key(self) -> None:
        usage = api.fetch_key_usage("tok-good")
        self.assertEqual(usage.name, "Beta")          # 请求数最多
        self.assertEqual(usage.requests, 10)
        self.assertEqual(usage.tokens, 2_000_000)
        self.assertAlmostEqual(usage.cost, 1.5, places=4)
        self.assertEqual(usage.currency, "CNY")
        self.assertEqual(usage.keys_today, 2)

    def test_today_usage_specific_key(self) -> None:
        usage = api.fetch_key_usage("tok-good", "key-alpha")
        self.assertEqual(usage.name, "Alpha")
        self.assertEqual(usage.requests, 3)
        self.assertEqual(usage.tokens, 1250)
        self.assertAlmostEqual(usage.cost, 0.0123, places=4)

    def test_today_usage_key_without_traffic_returns_zero(self) -> None:
        usage = api.fetch_key_usage("tok-good", "key-gamma")
        self.assertEqual(usage.tracking_id, "key-gamma")
        self.assertEqual(usage.requests, 0)
        self.assertEqual(usage.tokens, 0)
        self.assertEqual(usage.cost, 0.0)

    def test_today_usage_requires_platform_token(self) -> None:
        with self.assertRaises(api.ApiError) as ctx:
            api.fetch_key_usage("tok-expired")
        self.assertEqual(ctx.exception.kind, api.KIND_AUTH)

    def test_format_tokens(self) -> None:
        self.assertEqual(api.format_tokens(999), "999")
        self.assertEqual(api.format_tokens(12_300), "12.3k")
        self.assertEqual(api.format_tokens(2_000_000), "2.0M")
        self.assertEqual(api.format_tokens(1_500_000_000), "1.5B")

    def test_usage_icon_is_yellow(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            icon = PanelIcon(Path(tmp), keep=2)
            path = icon.update("¥1.50·2.0M", height=22, color=COLOR_USAGE)
            self.assertTrue(Path(path).exists())
            self.assertGreater(Path(path).stat().st_size, 200)
            yellow = Image.open(path).convert("RGB")
            pixels = [yellow.getpixel((x, y)) for y in range(yellow.height) for x in range(yellow.width)]
            bright_yellow = [p for p in pixels if p[0] > 200 and p[1] > 160 and p[2] < 120]
            self.assertGreater(len(bright_yellow), 30, "黄色用量文字应渲染为黄橙色像素")


class LauncherTests(unittest.TestCase):
    """启动器在软链接 / 非标准路径下也必须能找到程序文件。"""

    def _make_tree(self, tmp: Path) -> tuple[Path, Path]:
        import shutil
        app = tmp / "opt" / "deepseek-cost"
        (app / "share" / "deepseek-cost").mkdir(parents=True)
        shutil.copytree(SRC / "deepseek_cost", app / "share" / "deepseek-cost" / "src" / "deepseek_cost")
        (app / "bin").mkdir()
        launcher = app / "bin" / "deepseek-cost"
        shutil.copy(ROOT / "bin" / "deepseek-cost", launcher)
        launcher.chmod(0o755)
        return app, launcher

    def test_direct_launcher(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            _app, launcher = self._make_tree(Path(tmp))
            result = subprocess.run([str(launcher), "--version"], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("DeepSeek 余额", result.stdout)

    def test_launcher_through_symlink(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _app, launcher = self._make_tree(tmp_path)
            link = tmp_path / "old-place" / "deepseek-cost"
            link.parent.mkdir()
            link.symlink_to(launcher)
            result = subprocess.run([str(link), "--version"], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, f"软链接调用失败：{result.stderr}")
            self.assertIn("DeepSeek 余额", result.stdout)

    def test_launcher_falls_back_to_system_src(self) -> None:
        """副本被搬到任意位置（旁边没有 src）时，应回退到 /usr/share/deepseek-cost/src。"""
        with tempfile.TemporaryDirectory() as tmp:
            if not (Path("/usr/share/deepseek-cost/src/deepseek_cost/__main__.py")).exists():
                self.skipTest("本机没有安装 .deb 版本")
            lonely = Path(tmp) / "deepseek-cost"
            lonely.write_bytes((ROOT / "bin" / "deepseek-cost").read_bytes())
            lonely.chmod(0o755)
            result = subprocess.run([str(lonely), "--version"], capture_output=True, text=True, timeout=60)
            self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main(verbosity=2)
