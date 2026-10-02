#!/usr/bin/env bash
# DeepSeek 余额指示器 —— 安装脚本
#
#   ./install.sh              # 安装到 ~/.local，并写入开机自启
#   ./install.sh --no-deps    # 不自动安装缺少的系统依赖
#   ./install.sh --no-start   # 安装后不立即启动
#   PREFIX=/usr/local ./install.sh
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="${PREFIX:-$HOME/.local}"
# 用 sudo 安装时，自启要写回真实用户的家目录（而不是 /root）
TARGET_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    TARGET_HOME="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)"
    [ -n "$TARGET_HOME" ] || TARGET_HOME="$HOME"
fi
APP_DIR="$PREFIX/share/deepseek-cost"
BIN_DIR="$PREFIX/bin"
LAUNCHER="$BIN_DIR/deepseek-cost"
APPS_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/scalable/apps"
AUTOSTART_DIR="$TARGET_HOME/.config/autostart"

INSTALL_DEPS=1
START=1
for arg in "$@"; do
    case "$arg" in
        --no-deps) INSTALL_DEPS=0 ;;
        --no-start) START=0 ;;
        -h|--help) sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "未知参数：$arg" >&2; exit 2 ;;
    esac
done

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m警告:\033[0m %s\n' "$*" >&2; }

# ---------------------------------------------------------------- 系统依赖
REQUIRED=()
OPTIONAL=()

check_py() {  # check_py <python 代码> -> 成功返回 0
    python3 -c "$1" >/dev/null 2>&1
}

check_py 'import gi; gi.require_version("Gtk","3.0"); from gi.repository import Gtk' \
    || REQUIRED+=("python3-gi" "gir1.2-gtk-3.0")
check_py 'import gi; gi.require_version("AyatanaAppIndicator3","0.1"); from gi.repository import AyatanaAppIndicator3' \
    || REQUIRED+=("gir1.2-ayatanaappindicator3-0.1" "libayatana-appindicator3-1")
check_py 'import gi; gi.require_version("PangoCairo","1.0"); from gi.repository import PangoCairo; import cairo; PangoCairo.create_layout(cairo.Context(cairo.ImageSurface(cairo.FORMAT_ARGB32,2,2)))' \
    || REQUIRED+=("python3-gi-cairo")

check_py 'import gi; gi.require_version("Notify","0.7"); from gi.repository import Notify' \
    || OPTIONAL+=("gir1.2-notify-0.7" "libnotify-bin")
check_py 'import gi; gi.require_version("WebKit2","4.1"); from gi.repository import WebKit2' \
    || OPTIONAL+=("gir1.2-webkit2-4.1")

if [ "${#REQUIRED[@]}" -gt 0 ]; then
    warn "缺少必需依赖：${REQUIRED[*]}"
    if [ "$INSTALL_DEPS" = "1" ] && command -v apt-get >/dev/null 2>&1; then
        say "使用 apt 安装必需依赖（可能需要 sudo 密码）"
        sudo apt-get update -qq || true
        sudo apt-get install -y "${REQUIRED[@]}"
    else
        warn "请手动执行：sudo apt-get install -y ${REQUIRED[*]}"
        [ "$INSTALL_DEPS" = "0" ] || exit 1
    fi
fi

if [ "${#OPTIONAL[@]}" -gt 0 ]; then
    warn "缺少可选依赖：${OPTIONAL[*]}"
    echo "    有 gir1.2-notify-0.7 才能弹系统通知；有 gir1.2-webkit2-4.1 才能在应用内登录网页。"
    echo "    （缺少时程序仍可运行，只是提醒方式/登录方式受限）"
fi

# ---------------------------------------------------------------- 复制文件
say "安装到 $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$APPS_DIR" "$ICON_DIR" "$AUTOSTART_DIR"
rm -rf "$APP_DIR/src" "$APP_DIR/bin"
cp -r "$HERE/src" "$APP_DIR/src"
cp -r "$HERE/bin" "$APP_DIR/bin"
find "$APP_DIR/src" -type d -exec chmod 755 {} + 2>/dev/null || true
find "$APP_DIR/src" -type f -exec chmod 644 {} + 2>/dev/null || true
cp "$HERE/assets/deepseek-cost.svg" "$ICON_DIR/deepseek-cost.svg" 2>/dev/null || true
install -m 755 "$HERE/bin/deepseek-cost" "$LAUNCHER"

# ---------------------------------------------------------------- 桌面项
cat > "$APPS_DIR/deepseek-cost.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DeepSeek 余额
Name[en]=DeepSeek Balance
Comment=在系统栏实时显示 DeepSeek API 剩余费用
Exec=$LAUNCHER
Icon=deepseek-cost
Terminal=false
Categories=Utility;
StartupNotify=false
EOF

cat > "$APPS_DIR/deepseek-cost-setup.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DeepSeek 余额 · 登录/设置
Comment=登录 DeepSeek 账户并配置余量提醒
Exec=$LAUNCHER --login
Icon=deepseek-cost
Terminal=false
Categories=Utility;Settings;
StartupNotify=false
EOF

cat > "$AUTOSTART_DIR/deepseek-cost.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=DeepSeek 余额
Comment=在系统栏实时显示 DeepSeek API 剩余费用（开机自启）
Exec=$LAUNCHER
Icon=deepseek-cost
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=10
EOF

chmod 644 "$APPS_DIR"/deepseek-cost*.desktop "$AUTOSTART_DIR/deepseek-cost.desktop" 2>/dev/null || true
say "已写入开机自启：$AUTOSTART_DIR/deepseek-cost.desktop"

if [ "$TARGET_HOME" != "$HOME" ]; then
    warn "检测到以 root 运行：自启写到了 $TARGET_HOME，请确认这就是要自启的用户"
fi

if command -v desktop-file-validate >/dev/null 2>&1; then
    desktop-file-validate "$AUTOSTART_DIR/deepseek-cost.desktop" || true
fi
command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true

# ---------------------------------------------------------------- 收尾
if ! echo ":$PATH:" | grep -q ":$BIN_DIR:"; then
    warn "$BIN_DIR 不在 PATH 中，可将下面一行加入 ~/.bashrc："
    echo "    export PATH=\"$BIN_DIR:\$PATH\""
fi

say "安装完成 ✔"

if pgrep -f "python3 -m deepseek_cost" >/dev/null 2>&1; then
    say "检测到指示器已在运行，跳过启动"
elif [ "$START" = "1" ] && [ -n "${DISPLAY:-}" ]; then
    say "启动 DeepSeek 余额指示器（首次运行会弹出登录窗口）"
    nohup "$LAUNCHER" >/dev/null 2>&1 &
    disown 2>/dev/null || true
fi

cat <<EOF

常用命令：
  deepseek-cost                 # 启动顶栏指示器（已在运行时不会重复启动）
  deepseek-cost --login         # 登录 / 修改凭据与设置
  deepseek-cost --once          # 在终端查询一次余额
  deepseek-cost --reset         # 清除本机保存的凭据
  $HERE/uninstall.sh            # 卸载

EOF
