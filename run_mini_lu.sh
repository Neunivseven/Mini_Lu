#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || realpath "$0" 2>/dev/null || echo "$0")")" && pwd)"
cd "$HERE"
# Wayland 下若窗口异常，可取消下一行注释强制用 xcb
# export QT_QPA_PLATFORM=xcb
exec "$HERE/Mini_Lu" "$@"
