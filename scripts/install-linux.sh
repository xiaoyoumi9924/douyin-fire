#!/usr/bin/env bash
# 抖音自动续火花 · Linux 一键安装（AppImage + systemd 用户服务）
#
#   curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install-linux.sh | bash
#   GitHub 很慢时把 releases/latest/download 换成
#   https://cdn.jsdelivr.net/gh/xiaoyoumi9924/douyin-fire@main/scripts
#
# 带参数（注意 bash 后面的 --）：
#   curl -fsSL .../install-linux.sh | bash -s -- --port 8765 --schedule 07:00
#
# 安装器会：下载 AppImage、解包出后台程序、注册一个 systemd 用户服务并启动，
# 最后打印本机面板地址。抖音登录、好友、消息仍然在面板里完成。

set -euo pipefail

REPO="${DOUYIN_REPO:-xiaoyoumi9924/douyin-fire}"
APP_NAME="Douyin Fire"
UNIT="douyin-fire-panel.service"

PORT="${DOUYIN_PORT:-8765}"
DATA_DIR="${DOUYIN_DATA_DIR:-${XDG_DATA_HOME:-$HOME/.local/share}/douyin-fire}"
BIN_DIR="${DOUYIN_BIN_DIR:-$HOME/.local/bin}"
VERSION=""
SOURCE=""
SCHEDULE=""
OPEN_AFTER=1
UNINSTALL=0
PURGE=0
TMP=""
APPIMAGE=""

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

step() { printf '%s==>%s %s\n' "$DIM" "$RESET" "$*"; }
ok()   { printf '%s✔%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
die()  { printf '%s错误：%s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }

usage() {
  cat <<'TEXT'
抖音自动续火花 · Linux 一键安装

  curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install-linux.sh | bash

参数：
  --port 8765         面板端口（默认 8765）
  --schedule 07:00    装完顺便把每日任务设成这个时间（可选）
  --no-open           装完不自动打开浏览器
  --version v0.1.7    指定版本（默认 GitHub 最新 release）
  --source PATH       用本地 .AppImage 安装（离线或测试用）
  --data-dir PATH     数据目录（默认 ~/.local/share/douyin-fire）
  --uninstall         停止并删除服务（数据保留）
  --purge-data        卸载时连数据一起删（谨慎）
  -h, --help          显示本帮助
TEXT
}

cleanup() {
  [ -n "$TMP" ] && [ -d "$TMP" ] && rm -rf "$TMP"
  return 0
}
trap cleanup EXIT

panel_url() { printf 'http://127.0.0.1:%s/' "$PORT"; }

health_check() {
  curl -fs -m 1 -o /dev/null "$(panel_url)api/state" 2>/dev/null
}

port_in_use() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn 2>/dev/null | awk '{print $4}' | grep -q ":$1\$"
  elif command -v lsof >/dev/null 2>&1; then
    lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
  else
    return 1
  fi
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --port) [ $# -ge 2 ] || die "--port 需要端口号"; PORT="$2"; shift 2 ;;
      --port=*) PORT="${1#*=}"; shift ;;
      --schedule) [ $# -ge 2 ] || die "--schedule 需要 HH:MM"; SCHEDULE="$2"; shift 2 ;;
      --schedule=*) SCHEDULE="${1#*=}"; shift ;;
      --version) [ $# -ge 2 ] || die "--version 需要版本号"; VERSION="$2"; shift 2 ;;
      --version=*) VERSION="${1#*=}"; shift ;;
      --source) [ $# -ge 2 ] || die "--source 需要路径"; SOURCE="$2"; shift 2 ;;
      --source=*) SOURCE="${1#*=}"; shift ;;
      --data-dir) [ $# -ge 2 ] || die "--data-dir 需要路径"; DATA_DIR="$2"; shift 2 ;;
      --data-dir=*) DATA_DIR="${1#*=}"; shift ;;
      --no-open) OPEN_AFTER=0; shift ;;
      --uninstall) UNINSTALL=1; shift ;;
      --purge-data) PURGE=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "不认识的参数：$1（用 --help 查看用法）" ;;
    esac
  done
}

latest_tag() {
  local tag
  tag="$(curl -fsSLI -o /dev/null -w '%{url_effective}' --max-time 20 \
        "https://github.com/$REPO/releases/latest" 2>/dev/null | sed 's#.*/tag/##')" || true
  [ -n "$tag" ] || die "取不到最新版本号，请检查网络，或用 --version 指定版本"
  printf '%s' "$tag"
}

asset_url() {
  local tag="$1" ver="$2" json url
  json="$(curl -fsSL --max-time 20 "https://api.github.com/repos/$REPO/releases/tags/$tag" 2>/dev/null)" || json=""
  if [ -n "$json" ]; then
    url="$(printf '%s' "$json" \
      | grep -o '"browser_download_url": *"[^"]*"' \
      | sed 's/.*"\(https[^"]*\)"/\1/' \
      | grep -i '\.AppImage$' | head -1)" || true
    if [ -n "$url" ]; then printf '%s' "$url"; return 0; fi
  fi
  printf 'https://github.com/%s/releases/download/%s/Douyin.Fire-%s.AppImage' "$REPO" "$tag" "$ver"
}

do_uninstall() {
  step "停止面板服务"
  systemctl --user disable --now "$UNIT" >/dev/null 2>&1 || true
  rm -f "$HOME/.config/systemd/user/$UNIT"
  systemctl --user daemon-reload >/dev/null 2>&1 || true
  rm -rf "$DATA_DIR/app"
  rm -f "$BIN_DIR/douyin-fire.AppImage"
  ok "已移除面板服务和应用"

  if [ "$PURGE" -eq 1 ]; then
    rm -rf "$DATA_DIR"
    ok "已删除数据目录 $DATA_DIR"
  else
    warn "数据目录保留在：${DATA_DIR}（要一起删就加 --purge-data）"
  fi
  ok "卸载完成"
}

install_appimage() {
  local src="$1"
  mkdir -p "$BIN_DIR" "$DATA_DIR"
  step "安装 AppImage 到 $BIN_DIR/douyin-fire.AppImage"
  if [ "$src" = "$BIN_DIR/douyin-fire.AppImage" ]; then
    APPIMAGE="$src"
  else
    cp -f "$src" "$BIN_DIR/douyin-fire.AppImage"
    APPIMAGE="$BIN_DIR/douyin-fire.AppImage"
  fi
  chmod +x "$APPIMAGE"

  step "解包出后台程序"
  rm -rf "$DATA_DIR/app"
  ( cd "$DATA_DIR" && "$APPIMAGE" --appimage-extract >/dev/null 2>&1 ) \
    || die "解包 AppImage 失败（可能是文件损坏）"
  [ -d "$DATA_DIR/squashfs-root" ] || die "解包后没有找到 squashfs-root"
  mv "$DATA_DIR/squashfs-root" "$DATA_DIR/app"
}

write_unit() {
  local backend="$DATA_DIR/app/resources/backend/douyin-fire-backend/douyin-fire-backend"
  local browsers="$DATA_DIR/app/resources/ms-playwright"
  [ -x "$backend" ] || die "解包后找不到后台程序：$backend"

  local unit_dir="$HOME/.config/systemd/user"
  mkdir -p "$unit_dir"
  cat > "$unit_dir/$UNIT" <<UNIT
[Unit]
Description=Douyin Fire panel
After=network.target

[Service]
Type=simple
WorkingDirectory="$DATA_DIR"
Environment="DOUYIN_DATA_DIR=$DATA_DIR"
Environment=DOUYIN_DESKTOP_MODE=1
Environment=UI_PORT=$PORT
Environment="PLAYWRIGHT_BROWSERS_PATH=$browsers"
ExecStart="$backend" serve
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
UNIT
  ok "已写入 $unit_dir/$UNIT"
}

apply_schedule() {
  local value="$1" hour minute
  case "$value" in
    [0-9][0-9]:[0-9][0-9]) ;;
    *) die "--schedule 要写成 HH:MM，例如 07:00" ;;
  esac
  hour=$((10#${value%%:*}))
  minute=$((10#${value##*:}))
  step "设置每日 $value 自动运行"
  curl -fsS -m 10 -X POST -H 'Content-Type: application/json' \
    -d "{\"hour\":$hour,\"minute\":$minute}" \
    "$(panel_url)api/schedule" >/dev/null \
    || die "设置每日任务失败，可以装完后在面板里手动点「保存时间」"
  ok "每天 $value 会自动运行（需要电脑开机并登录）"
}

main() {
  parse_args "$@"

  [ "$(uname -s)" = "Linux" ] || die "这个脚本只支持 Linux；macOS / Windows 请用对应的安装脚本"
  command -v systemctl >/dev/null 2>&1 || die "这台机器没有 systemd，暂时没法注册面板服务"
  [ "$(uname -m)" = "x86_64" ] || die "目前 Release 只提供 x86_64 的 Linux 包"

  systemctl --user show-environment >/dev/null 2>&1 \
    || die "systemd 用户实例没在运行；需要图形登录会话或用 loginctl enable-linger"

  if [ "$UNINSTALL" -eq 1 ]; then
    do_uninstall
    exit 0
  fi

  case "$PORT" in
    ''|*[!0-9]*) die "--port 必须是数字" ;;
  esac

  systemctl --user disable --now "$UNIT" >/dev/null 2>&1 || true
  sleep 0.5
  if port_in_use "$PORT"; then
    die "端口 $PORT 已被别的程序占用。先关掉它，或用 --port 换一个端口"
  fi

  local tag ver
  if [ -n "$VERSION" ]; then
    tag="v${VERSION#v}"; ver="${tag#v}"
  else
    tag="$(latest_tag)"; ver="${tag#v}"
  fi

  if [ -n "$SOURCE" ]; then
    [ -f "$SOURCE" ] || die "找不到安装源：$SOURCE"
    step "使用本地安装源：$SOURCE"
    install_appimage "$SOURCE"
  else
    local url
    url="$(asset_url "$tag" "$ver")"
    step "下载 $APP_NAME $tag（约 480 MB，请耐心等）"
    printf '    %s\n' "$url"
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/douyin-fire-install.XXXXXX")"
    curl -fL --retry 3 --connect-timeout 20 --progress-bar -o "$TMP/setup.AppImage" "$url" \
      || die "下载失败：$url"
    install_appimage "$TMP/setup.AppImage"
  fi

  write_unit
  step "启动面板服务"
  systemctl --user daemon-reload
  systemctl --user enable --now "$UNIT" >/dev/null 2>&1 \
    || die "启动失败，看看日志：journalctl --user -u $UNIT -n 30"

  step "等待面板启动"
  local checks=0
  while [ "$checks" -lt 60 ]; do
    health_check && break
    sleep 0.5; checks=$((checks + 1))
  done
  if ! health_check; then
    journalctl --user -u "$UNIT" -n 20 --no-pager 2>/dev/null >&2 || true
    die "面板没启动成功，看看日志：journalctl --user -u $UNIT"
  fi

  if [ -n "$SCHEDULE" ]; then
    apply_schedule "$SCHEDULE"
  fi

  local panel
  panel="$(panel_url)"
  printf '\n%s%s 安装完成%s\n' "$BOLD" "$GREEN" "$RESET"
  printf '   面板地址   %s%s%s\n' "$BOLD" "$panel" "$RESET"
  printf '   应用位置   %s\n' "$APPIMAGE"
  printf '   数据目录   %s\n' "$DATA_DIR"
  printf '   服务状态   systemctl --user status %s\n' "$UNIT"
  printf '\n首次使用还有四步（只需做一次）：\n'
  printf '   1. 打开上面的地址，用抖音扫码登录\n'
  printf '   2. 「好友管理」加好友（要填对方备注名）\n'
  printf '   3. 「消息管理」写每天要发的内容，保存\n'
  printf '   4. 「自动任务」选时间并点「保存时间」——之后每天自动发\n'
  printf '\n开机自启：sudo loginctl enable-linger %s（不登录桌面也能起）\n' "$USER"
  printf '卸载：curl -fsSL .../install-linux.sh | bash -s -- --uninstall\n'

  if [ "$OPEN_AFTER" -eq 1 ]; then
    command -v xdg-open >/dev/null 2>&1 && xdg-open "$panel" >/dev/null 2>&1 || true
  fi
}

main "$@"
