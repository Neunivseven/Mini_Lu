#!/usr/bin/env bash
# 将 Mini_Lu 注册到当前用户的应用菜单（含图标主题）
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
APP_DIR="$HERE"
RUN="$APP_DIR/run_mini_lu.sh"
ICON_SRC=""
for c in \
  "$APP_DIR/assets/icons/app_icon_256.png" \
  "$APP_DIR/assets/icons/app_icon.png" \
  "$APP_DIR/assets/icons/app_icon_512.png"
do
  if [[ -f "$c" ]]; then ICON_SRC="$c"; break; fi
done

if [[ ! -x "$RUN" ]]; then
  echo "错误: 找不到可执行启动脚本: $RUN" >&2
  exit 1
fi
chmod +x "$RUN" "$APP_DIR/Mini_Lu" 2>/dev/null || true
[[ -f "$APP_DIR/启动Mini_Lu.sh" ]] && chmod +x "$APP_DIR/启动Mini_Lu.sh" || true

APPS="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
ICONS="${XDG_DATA_HOME:-$HOME/.local/share}/icons/hicolor"
mkdir -p "$APPS"
mkdir -p "$ICONS/256x256/apps" "$ICONS/128x128/apps" "$ICONS/48x48/apps" "$ICONS/scalable/apps"

if [[ -n "$ICON_SRC" ]]; then
  install -m 644 "$ICON_SRC" "$ICONS/256x256/apps/mini-lu.png"
  # 部分菜单只扫 48/128
  install -m 644 "$ICON_SRC" "$ICONS/128x128/apps/mini-lu.png"
  install -m 644 "$ICON_SRC" "$ICONS/48x48/apps/mini-lu.png"
  echo "已安装图标 → $ICONS/*/apps/mini-lu.png"
else
  echo "警告: 未找到 app_icon_*.png，菜单可能无图标" >&2
fi

DESKTOP_OUT="$APPS/mini-lu.desktop"
cat > "$DESKTOP_OUT" <<EOF
[Desktop Entry]
Type=Application
Version=1.0
Name=Mini_Lu
Name[zh_CN]=Mini_Lu 桌宠
GenericName=Desktop Pet
GenericName[zh_CN]=桌面宠物
Comment=Desktop pet with chat agent
Comment[zh_CN]=桌面宠物与对话助手
Exec="$RUN"
Path=$APP_DIR
Icon=mini-lu
Terminal=false
Categories=Utility;
Keywords=pet;agent;chat;desktop;Mini_Lu;桌宠;助手;
StartupNotify=true
StartupWMClass=Mini_Lu
EOF
chmod 644 "$DESKTOP_OUT"
# 部分环境要求 .desktop 可执行才显示「允许启动」
chmod +x "$DESKTOP_OUT" 2>/dev/null || true

if command -v update-desktop-database >/dev/null 2>&1; then
  update-desktop-database "$APPS" >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
  gtk-update-icon-cache -f -t "$ICONS" >/dev/null 2>&1 || true
fi
if command -v desktop-file-validate >/dev/null 2>&1; then
  desktop-file-validate "$DESKTOP_OUT" || true
fi

echo
echo "已写入: $DESKTOP_OUT"
echo "请在应用菜单搜索「Mini_Lu」或「桌宠」。"
echo "若仍看不到：注销重登，或按 Alt+F2 输入 r 回车（GNOME）刷新 Shell。"
echo "卸载菜单项: rm -f \"$DESKTOP_OUT\" \"$ICONS\"/*/apps/mini-lu.png"
