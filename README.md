# DeepSeek 余额指示器（Ubuntu 顶栏）

[![tests](https://github.com/OWNER/deepseek-cost/actions/workflows/tests.yml/badge.svg)](https://github.com/OWNER/deepseek-cost/actions/workflows/tests.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)](LICENSE)

> 开源前把上面的 `OWNER` 换成你的 GitHub 用户名（README 与 `build-deb.sh` 里的占位符同理）。

在 Ubuntu/GNOME 系统栏**右侧**直接显示 DeepSeek API 的剩余费用（例如绿色的 `¥123.45`），
并且**可按 API Key 用黄色显示今日消耗金额与 token 总量**（例如 `¥1.50 · 2.0M`）。
余额低于阈值（默认 50）时弹出桌面提醒。原生 GTK3 + AppIndicator（Linux 标准托盘协议），
常驻内存约 **31 MB（PSS）**，空闲 CPU **≈0%**。

![顶栏效果](docs/panel.png)

![今日用量（黄色）](docs/panel-usage.png)

![余量提醒](docs/notification.png)

## 功能

| 需求 | 实现 |
| --- | --- |
| 系统栏右侧实时显示剩余费用 | 把文字渲染成 PNG 交给 SNI 托盘协议，文字**直接显示在顶栏**（绿色=正常、红色=低于阈值、灰色=数据过期、橙色=未登录/失效） |
| 首次使用登录个人 DeepSeek 账户 | 登录窗口内嵌官方登录页（WebKitGTK），登录后自动读取登录态令牌；也支持粘贴浏览器令牌或官方 API Key |
| 每 5 分钟刷新一次 | GLib 定时器，默认 300 秒（可在设置里改 1–240 分钟），日志可查 `定时刷新已设置：每 300 秒` |
| 点击信息直接刷新 | 菜单第一项就是「¥123.45 · 点击刷新」，点它立即刷新；中键点击图标 = 立即刷新；菜单里也有「立即刷新」/「立即刷新用量」 |
| 按 API Key 看今日用量（费用 + token），黄色，用户可选 | 第二个（黄色）顶栏指示器显示 `金额·token`；菜单「选择 API Key」可切到任意 Key（或自动选当天用量最大的）；设置里还能只显示金额/只显示 token、或整体关掉 |
| 软件要小、原生 Linux | Python3 + PyGObject + GTK3 + AyatanaAppIndicator3，无 Electron/浏览器内核常驻；无第三方 pip 依赖（只用标准库 urllib） |
| 开机自启 | 源码安装写 `~/.config/autostart/`；.deb 安装写 `/etc/xdg/autostart/`（各桌面环境通用） |
| 不占用过多计算资源 | 空闲 20 秒 CPU tick 增量 = 0；内存 PSS 约 31 MB（RSS 65 MB，多为共享库）；刷新时只做 1–3 次 HTTPS 请求 |
| 低于 50 弹余量提醒 | libnotify 桌面通知；首次跌破提醒一次，持续偏低每 6 小时再提醒，充值回升后自动重新武装 |
| 可分发软件包 | `./build-deb.sh` 生成 `dist/deepseek-cost_1.1.2_all.deb`（架构无关，32 KB），`sudo apt install ./xxx.deb` 即可装到任何 Ubuntu |

菜单内容示意：

```
¥123.45 · 点击刷新              ← 点它立即刷新
更新于 12:34:56 · 下次 4 分 58 秒后
赠送 ¥10.00 · 充值 ¥113.45 · 提醒阈值 ¥50.00
──────────────────────────
☑ 在顶栏显示今日用量（黄色）
选择 API Key ▸  自动（当天用量最大的 Key）/ 默认 Key / 备用 Key / …
今日（默认 Key）：10 次请求 · 2,000,000 tokens · ¥1.50    ← 黄色
立即刷新用量
──────────────────────────
立即刷新
设置…
重新登录…
打开 DeepSeek 控制台
──────────────────────────
退出
```

## 安装

### 方式一：.deb 软件包（推荐分发）

```bash
./build-deb.sh                                        # 生成 dist/deepseek-cost_1.1.2_all.deb
sudo apt install ./dist/deepseek-cost_1.1.2_all.deb   # 自动安装依赖
sudo apt remove deepseek-cost                         # 卸载（登录配置保留在 ~/.config/deepseek-cost）
```

包里包含：`/usr/bin/deepseek-cost`、`/usr/share/deepseek-cost/src`、
两个应用菜单项、图标，以及**系统级开机自启** `/etc/xdg/autostart/deepseek-cost.desktop`。
依赖（`Depends`）安装时自动拉取：`python3-gi`、`gir1.2-gtk-3.0`、
`gir1.2-ayatanaappindicator3-0.1`、`libayatana-appindicator3-1`、`python3-gi-cairo`；
`Recommends` 里是桌面通知与内嵌登录页（`gir1.2-notify-0.7`、`libnotify-bin`、`gir1.2-webkit2-4.1`）。

### 方式二：源码安装到 ~/.local

```bash
./install.sh                   # 装到 ~/.local，写入 ~/.config/autostart（缺依赖会自动 apt 安装）
./install.sh --no-deps         # 不自动装系统依赖
./install.sh --no-start        # 装完不立即启动
PREFIX=/usr/local ./install.sh
```

> 两种方式不要同时装：如果之前用过 `install.sh`，先 `./uninstall.sh`（不会删登录配置）再装 .deb。

## 使用

```bash
deepseek-cost                 # 启动顶栏指示器（已在运行不会重复启动）
deepseek-cost --login         # 登录 / 改凭据 / 改阈值与间隔 / 开关今日用量
deepseek-cost --once          # 终端里查询一次余额（脚本、cron 都能用）
deepseek-cost --reset         # 清除本机保存的凭据
./uninstall.sh [--purge]      # 源码安装的卸载（--purge 连配置一起删）
```

`--once` 输出示例：

```
$ deepseek-cost --once
¥123.45 [CNY] 赠送 10.00 充值 113.45 来源 platform
```

三种登录方式任选其一：

1. **账户登录（推荐）**：登录窗口内嵌 `platform.deepseek.com` 官方登录页，登录成功后自动从页面
   `localStorage` 提取 `userToken` 并校验（令牌不会显示在界面上）。也可展开「邮箱 + 密码」直接登录
   （若平台要求图形验证码，请改用网页登录）。用这种方式登录并勾选「记住密码」后，令牌过期会自动续期。
2. **浏览器令牌**：自己浏览器登录后按 F12 → Application → Local Storage → Platform → `userToken`，粘贴进来。
3. **API Key**：`platform.deepseek.com/api_keys` 创建 `sk-…`，走官方 `GET /user/balance` 接口。
   **只读余额，不消耗任何额度。**

> 凭据只保存在本机配置文件（`~/.config/deepseek-cost/config.json`，权限 600），日志与错误信息不会打印令牌。
> 余额三种方式都能看；**按 Key 的今日用量只有 1、2 两种网页登录方式能看**（平台私有接口需要网页登录令牌，
> API Key 无法访问）。用 API Key 登录时黄色指示器会显示「今日不可用」并给出原因。

## 今日用量是怎么来的

官方没有公开按 Key 的用量 API，这里用的是平台用量页自己调用的私有只读接口：

* `GET /api/v0/users/get_api_keys` —— Key 列表（`tracking_id` / 名称 / 类型；密钥由服务端掩码）
* `GET /api/v0/usage/by_api_key/amount?start=<unix>&end=<unix>&tz=<秒>` —— 按 Key、按小时/天的 token 与请求数
* `GET /api/v0/usage/by_api_key/cost?start=<unix>&end=<unix>&tz=<秒>` —— 按 Key、按小时/天的消费金额

`start/end` 取本地时区当天 00:00 到次日 00:00，`tz` 是本地 UTC 偏移秒数，所以「今日」就是**你本地的今天**。
若这组接口不可用（平台改版），程序会把黄色指示器置灰并把原因写进菜单，不影响余额显示。

## 实现说明

* **为什么文字能显示在顶栏**：Ubuntu 的 `ubuntu-appindicators` GNOME 扩展对「宽度 ≥ 1.5 × 高度」的图标
  按原图尺寸直接铺在面板上（indicator-multiload 的机制），本程序用 cairo + Pango 把 `¥123.45`
  渲染成 22px 高的透明 PNG（带深色描边，深浅主题都清晰），因此文字本身就成了托盘图标。
  每次刷新写入新的文件名，绕过 shell 侧图片缓存；文件名带进程号，多实例不会互删。
* **两个指示器**：绿色/红色的是余额，黄色的是今日用量；用量指示器只在开关打开时创建，关掉即从托盘移除。
* **刷新流程**：定时器/点击 → 子线程发 HTTPS 请求 → `GLib.idle_add` 回到主线程更新图标、菜单与通知，
  主循环全程不阻塞。401/令牌过期会提示「登录失效」，网络异常保留上一次数值并置灰，不会崩。
* **令牌过期自动续期**：用「邮箱 + 密码」登录并勾选「记住密码」时，配置里保存 `auth_mode="account"`；
  接口返回令牌失效时会用保存的密码自动换新令牌再取余额。
* **接口格式异常不会误报**：`normal_wallets` 等字段缺失/类型不对时直接报错并把图标置灰，
  绝不把 0 当成余额（否则会误弹「余量不足」）。
* **低余量提醒判定**是纯函数（`alerts.py::decide_alert`），单独做了单元测试。
* **单实例**：`flock` 锁文件，重复启动静默退出；用 `--login` 改了配置后，运行中的指示器会在下次刷新前自动重新载入。
* **信号处理**：SIGTERM/SIGINT 会先关掉登录/设置窗口再退出（否则嵌套主循环会卡住），并带 1.5 秒兜底强制退出。

## 测试

```bash
bash tests/run_tests.sh      # 59 个单元/集成用例：配置、格式、渲染、告警判定、令牌提取、
                             # 三种登录方式、按 Key 今日用量解析、解析健壮性、单实例、CLI 端到端（全离线）
python3 tests/mock_server.py # 单独启动假接口，便于手工联调（sk-good / sk-low / tok-good / tok-low）
python3 tests/smoke_gui.py   # 32 项 GUI 端到端检查：真的启动指示器 → 校验 SNI 注册与图标归属、
                             # 菜单文案、模拟点击刷新、截图检查顶栏像素、低余额通知、
                             # 令牌过期自动重登录、黄色今日用量指示器
```

实测结果（本机）：

```
Ran 59 tests ... OK                                  # 单元/集成
===== GUI 冒烟测试：32/32 通过 =====                    # 含"正式实例正在运行"时的隔离验证
```

## 依赖

必需（`install.sh` 会自动 `apt-get install`，.deb 会作为 Depends 自动拉取）：

```
python3-gi  gir1.2-gtk-3.0  gir1.2-ayatanaappindicator3-0.1  libayatana-appindicator3-1  python3-gi-cairo
```

可选：

```
gir1.2-notify-0.7 / libnotify-bin   # 桌面通知（缺省时自动退回 notify-send 命令）
gir1.2-webkit2-4.1                  # 应用内登录官方网页（缺省时改用「浏览器令牌」方式）
```

同时需要 GNOME 扩展 `ubuntu-appindicators@ubuntu.com`（Ubuntu 默认启用）。

## 常见问题

* **顶栏看不到文字**：确认扩展已启用 `gnome-extensions list --enabled | grep appindicators`；
  在扩展设置里把图标大小调大一点；或运行 `deepseek-cost --debug` 看日志。
* **黄色用量显示「今日不可用」**：当前是 API Key 登录方式，改用网页登录（菜单「重新登录…」）即可。
* **提示「登录失效」**：网页登录态会过期，点菜单「重新登录…」重新登一次即可
  （勾了「记住密码」的话程序会先用密码自动续期）。
* **余额/用量不准**：网页端数据本身有几秒到几分钟延迟；点击顶栏文字可立即刷新。
* **想换提醒阈值/刷新间隔/面板字号**：菜单「设置…」；想换 Key：菜单「选择 API Key」。
* **卸载干净**：源码安装 `./uninstall.sh --purge`；.deb 安装 `sudo apt remove deepseek-cost` 后再 `rm -rf ~/.config/deepseek-cost`。

## 目录结构

```
bin/deepseek-cost              启动器（设置 PYTHONPATH 后调用 python3 -m deepseek_cost）
src/deepseek_cost/
  __main__.py                  命令行入口（--once/--login/--reset/--debug）
  app.py                       指示器（余额 + 黄色今日用量）、菜单、定时刷新、通知
  api.py                       登录与取数：余额、按 Key 今日用量（仅标准库 urllib）
  ui.py                        登录窗口（WebKitGTK 内嵌登录页 / 令牌 / API Key）与设置窗口
  render.py                    文字 → 顶栏 PNG（cairo + Pango）
  config.py                    配置与状态持久化（0600）
  alerts.py                    低余量提醒判定（纯函数）
  notify.py                    桌面通知（异步）
  util.py                      打开链接、单实例锁、时间格式化
assets/deepseek-cost.svg       应用图标
install.sh / uninstall.sh      源码安装 / 卸载
build-deb.sh                   .deb 打包（输出到 dist/，发布前改掉 Maintainer/Homepage 占位符）
tests/                         假接口 + 59 个用例 + 32 项 GUI 冒烟检查
.github/workflows/tests.yml    CI：单元测试 + xvfb 下的 GUI 冒烟
CHANGELOG.md                   版本变更记录
CONTRIBUTING.md                开发约定与发布流程
.gitignore                    忽略 dist/、__pycache__、本地配置等
LICENSE                        MIT
```

## 参与开发

```bash
bash tests/run_tests.sh        # 全离线，不需要账号
python3 tests/mock_server.py   # 起了假接口后可以手工联调
```

提交前请保证两套测试全绿、`flake8 --select=F,E9` 无告警；细节见 [CONTRIBUTING.md](CONTRIBUTING.md)。

## 许可

MIT（见 [LICENSE](LICENSE)）。本项目通过 DeepSeek 开放平台的私有只读接口显示账号自身的余额与用量，
与 DeepSeek 官方无关，请遵守 DeepSeek 的服务条款。
