#!/usr/bin/env bash
# DeepSeek 余额指示器 —— 卸载脚本
#   ./uninstall.sh            # 移除程序与开机自启，保留登录配置
#   ./uninstall.sh --purge    # 连登录配置一起删除
set -euo pipefail

PREFIX="${PREFIX:-$HOME/.local}"
APP_DIR="$PREFIX/share/deepseek-cost"
LAUNCHER="$PREFIX/bin/deepseek-cost"
APPS_DIR="$PREFIX/share/applications"
ICON_DIR="$PREFIX/share/icons/hicolor/scalable/apps"
CONFIG_HOME="${XDG_CONFIG_HOME:-$HOME/.config}"
CACHE_HOME="${XDG_CACHE_HOME:-$HOME/.cache}"
TARGET_HOME="$HOME"
if [ -n "${SUDO_USER:-}" ] && [ "${SUDO_USER}" != "root" ]; then
    TARGET_HOME="$(getent passwd "$SUDO_USER" 2>/dev/null | cut -d: -f6 || true)"
    [ -n "$TARGET_HOME" ] || TARGET_HOME="$HOME"
fi
AUTOSTART_DIR="$TARGET_HOME/.config/autostart"
CONFIG_DIR="${XDG_CONFIG_HOME:-$TARGET_HOME/.config}/deepseek-cost"
CACHE_DIR="${XDG_CACHE_HOME:-$TARGET_HOME/.cache}/deepseek-cost"

PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

say "停止正在运行的指示器"
pkill -f "deepseek_cost" 2>/dev/null || true
for _ in $(seq 1 10); do
    pgrep -f "deepseek_cost" >/dev/null 2>&1 || break
    sleep 0.5
done
if pgrep -f "deepseek_cost" >/dev/null 2>&1; then
    say "仍未退出，强制结束"
    pkill -9 -f "deepseek_cost" 2>/dev/null || true
    sleep 1
fi

say "删除程序文件"
rm -rf "$APP_DIR"
rm -f "$LAUNCHER"
rm -f "$APPS_DIR/deepseek-cost.desktop" "$APPS_DIR/deepseek-cost-setup.desktop"
rm -f "$ICON_DIR/deepseek-cost.svg"
rm -f "$AUTOSTART_DIR/deepseek-cost.desktop"
rm -rf "$CACHE_DIR"

if [ "$PURGE" = "1" ]; then
    say "删除登录配置（$CONFIG_DIR）"
    rm -rf "$CONFIG_DIR"
else
    echo "    登录配置保留在 $CONFIG_DIR（如需删除请加 --purge）"
fi

command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database "$APPS_DIR" >/dev/null 2>&1 || true
command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f "$PREFIX/share/icons/hicolor" >/dev/null 2>&1 || true
say "卸载完成 ✔"
