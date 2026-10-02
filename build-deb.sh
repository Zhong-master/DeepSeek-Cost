#!/usr/bin/env bash
# 打包成可分发到其它 Ubuntu 系统的 .deb
#
#   ./build-deb.sh                  # 生成 dist/deepseek-cost_<版本>_all.deb
#   ./build-deb.sh --install        # 打包后顺便用 apt 安装到本机
#
# 依赖：dpkg-deb（dpkg 自带）。生成的是架构无关（all）包，安装到 /usr。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$HERE/src/deepseek_cost/__init__.py")"
[ -n "$VERSION" ] || { echo "读取版本号失败" >&2; exit 1; }

PKG="deepseek-cost"
ARCH="all"
DIST="$HERE/dist"
DEB="$DIST/${PKG}_${VERSION}_${ARCH}.deb"
# 在临时目录里组装与打包：某些文件系统（NTFS/exFAT/网络盘）上 dpkg-deb 可能
# 写出损坏的归档，先落地到本地临时目录并校验，再复制到 dist/。
WORK="$(mktemp -d "${TMPDIR:-/tmp}/${PKG}-deb.XXXXXX")"
BUILD="$WORK/pkg"
TMP_DEB="$WORK/${PKG}_${VERSION}_${ARCH}.deb"
trap 'rm -rf "$WORK"' EXIT

INSTALL_AFTER=0
[ "${1:-}" = "--install" ] && INSTALL_AFTER=1

say() { printf '\033[1;34m==>\033[0m %s\n' "$*"; }

say "在临时目录组装软件包：$WORK"
mkdir -p "$DIST"
mkdir -p "$BUILD/DEBIAN" \
         "$BUILD/usr/bin" \
         "$BUILD/usr/share/deepseek-cost/src" \
         "$BUILD/usr/share/applications" \
         "$BUILD/usr/share/icons/hicolor/scalable/apps" \
         "$BUILD/usr/share/doc/$PKG" \
         "$BUILD/etc/xdg/autostart"

say "复制程序文件"
cp -r "$HERE/src/deepseek_cost" "$BUILD/usr/share/deepseek-cost/src/"
find "$BUILD/usr/share/deepseek-cost" -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true
# 源码必须全局可读（/usr 下的文件权限要规范）
find "$BUILD/usr/share/deepseek-cost" -type d -exec chmod 755 {} +
find "$BUILD/usr/share/deepseek-cost" -type f -exec chmod 644 {} +
install -m 755 "$HERE/bin/deepseek-cost" "$BUILD/usr/bin/deepseek-cost"
install -m 644 "$HERE/assets/deepseek-cost.svg" "$BUILD/usr/share/icons/hicolor/scalable/apps/deepseek-cost.svg"
install -m 644 "$HERE/README.md" "$BUILD/usr/share/doc/$PKG/README.md"
install -m 644 "$HERE/LICENSE" "$BUILD/usr/share/doc/$PKG/copyright" 2>/dev/null || true

cat > "$BUILD/usr/share/applications/deepseek-cost.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=DeepSeek 余额
Name[en]=DeepSeek Balance
Comment=在系统栏实时显示 DeepSeek API 剩余费用
Exec=/usr/bin/deepseek-cost
Icon=deepseek-cost
Terminal=false
Categories=Utility;
StartupNotify=false
EOF

cat > "$BUILD/usr/share/applications/deepseek-cost-setup.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=DeepSeek 余额 · 登录/设置
Comment=登录 DeepSeek 账户并配置余量提醒与今日用量
Exec=/usr/bin/deepseek-cost --login
Icon=deepseek-cost
Terminal=false
Categories=Utility;Settings;
StartupNotify=false
EOF

# 系统级开机自启：GNOME/KDE 都会读 /etc/xdg/autostart
cat > "$BUILD/etc/xdg/autostart/deepseek-cost.desktop" <<'EOF'
[Desktop Entry]
Type=Application
Name=DeepSeek 余额
Comment=在系统栏实时显示 DeepSeek API 剩余费用（开机自启）
Exec=/usr/bin/deepseek-cost
Icon=deepseek-cost
Terminal=false
Categories=Utility;
StartupNotify=false
X-GNOME-Autostart-enabled=true
X-GNOME-Autostart-Delay=10
EOF

say "生成 DEBIAN/control"
warn() { printf '\033[1;33m提示:\033[0m %s\n' "$*"; }
warn "如需改成你自己的署名，请修改本脚本里的 Maintainer / Homepage 字段"
INSTALLED_SIZE="$(du -sk "$BUILD/usr" | cut -f1)"
cat > "$BUILD/DEBIAN/control" <<EOF
Package: $PKG
Version: $VERSION
Section: utils
Priority: optional
Architecture: $ARCH
Depends: python3 (>= 3.8), python3-gi, gir1.2-gtk-3.0, gir1.2-ayatanaappindicator3-0.1, libayatana-appindicator3-1, python3-gi-cairo
Recommends: gir1.2-notify-0.7, libnotify-bin, gir1.2-webkit2-4.1
Installed-Size: $INSTALLED_SIZE
Maintainer: Zhong-master <damowangazhong@gmail.com>
Homepage: https://github.com/Zhong-master/DeepSeek-Cost
Description: Show DeepSeek API remaining balance and today's usage in the Ubuntu system tray
 在 Ubuntu/GNOME 系统栏右侧实时显示 DeepSeek API 剩余费用，可按 API Key 显示今日消耗金额与
 token 总量（黄色）。每 5 分钟自动刷新，点击顶栏文字可立即刷新；余额低于阈值时弹出桌面提醒。
 原生 GTK3 + AppIndicator 实现，常驻内存约 31MB（PSS），空闲 CPU 接近 0。
EOF

cat > "$BUILD/DEBIAN/postinst" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "configure" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
    command -v gtk-update-icon-cache >/dev/null 2>&1 && gtk-update-icon-cache -q -t -f /usr/share/icons/hicolor || true
fi
exit 0
EOF

cat > "$BUILD/DEBIAN/prerm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    pkill -f "deepseek_cost" 2>/dev/null || true
    sleep 1
    pkill -9 -f "deepseek_cost" 2>/dev/null || true
fi
exit 0
EOF

cat > "$BUILD/DEBIAN/postrm" <<'EOF'
#!/bin/sh
set -e
if [ "$1" = "remove" ] || [ "$1" = "purge" ]; then
    command -v update-desktop-database >/dev/null 2>&1 && update-desktop-database -q /usr/share/applications || true
fi
exit 0
EOF

chmod 644 "$BUILD/usr/share/applications/"*.desktop "$BUILD/etc/xdg/autostart/"*.desktop
chmod 755 "$BUILD/DEBIAN/postinst" "$BUILD/DEBIAN/prerm" "$BUILD/DEBIAN/postrm"

say "构建 $DEB"
dpkg-deb --build --root-owner-group "$BUILD" "$TMP_DEB" >/dev/null
dpkg-deb --info "$TMP_DEB" >/dev/null || { echo "打包失败：归档校验不通过" >&2; exit 1; }
install -m 644 "$TMP_DEB" "$DEB"
dpkg-deb --info "$DEB" >/dev/null || { echo "写入 $DEB 后校验失败（文件系统可能不支持）" >&2; exit 1; }
echo
dpkg-deb --info "$DEB" | sed 's/^/    /'
echo
say "完成：$DEB  （$(du -h "$DEB" | cut -f1)）"
echo "    安装： sudo apt install ./$(basename "$DEB")     # 会自动装依赖"
echo "    卸载： sudo apt remove deepseek-cost"
echo "    卸载并删除登录配置： rm -rf ~/.config/deepseek-cost"

if [ "$INSTALL_AFTER" = "1" ]; then
    say "安装到本机"
    sudo apt-get install -y "$DEB" || sudo dpkg -i "$DEB"
fi
