#!/usr/bin/env bash
# 抖音自动续火花 · macOS 一键安装
#
#   curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.sh | bash
#   GitHub 很慢时把 releases/latest/download 换成
#   https://cdn.jsdelivr.net/gh/xiaoyoumi9924/douyin-fire@main/scripts
#
# 带参数时（注意 bash 后面的 --）：
#   curl -fsSL .../install.sh | bash -s -- --port 8765 --schedule 07:00
#
# 安装器只做三件事：把应用装进「应用程序」、注册一个开机自启的面板服务、
# 然后打印本机面板地址。抖音登录、好友、消息仍然在面板里完成。
#
# 常用参数：
#   --port 8765         面板端口（默认 8765）
#   --schedule 07:00    装完顺便把每日任务设成这个时间（可选）
#   --no-open           装完不自动打开浏览器
#   --version v0.1.7    指定版本（默认 GitHub 最新 release）
#   --source PATH       从本地 .app / .dmg 安装（离线或测试用）
#   --uninstall         卸载面板服务和应用（数据保留）
#   --purge-data        卸载时连数据一起删（谨慎）
#   -h, --help          显示帮助
#
# 环境变量：DOUYIN_REPO / DOUYIN_PORT / DOUYIN_DATA_DIR / DOUYIN_INSTALL_DIR

set -euo pipefail

REPO="${DOUYIN_REPO:-xiaoyoumi9924/douyin-fire}"
APP_NAME="Douyin Fire"
LABEL="com.meiyh9924.douyin-fire.panel"
SCHEDULE_LABEL="com.meiyh.douyin-auto-fire"

PORT="${DOUYIN_PORT:-8765}"
DATA_DIR="${DOUYIN_DATA_DIR:-$HOME/Library/Application Support/Douyin Fire}"
INSTALL_DIR="${DOUYIN_INSTALL_DIR:-/Applications}"
VERSION=""
SOURCE=""
SCHEDULE=""
OPEN_AFTER=1
UNINSTALL=0
PURGE=0
TMP=""
DMG_PATH=""
SRC_APP=""
APP_PATH=""
PLIST_PATH=""

if [ -t 1 ]; then
  BOLD=$'\033[1m'; DIM=$'\033[2m'; GREEN=$'\033[32m'
  YELLOW=$'\033[33m'; RED=$'\033[31m'; RESET=$'\033[0m'
else
  BOLD=""; DIM=""; GREEN=""; YELLOW=""; RED=""; RESET=""
fi

say()  { printf '%s\n' "$*"; }
step() { printf '%s==>%s %s\n' "$DIM" "$RESET" "$*"; }
ok()   { printf '%s✔%s %s\n' "$GREEN" "$RESET" "$*"; }
warn() { printf '%s!%s %s\n' "$YELLOW" "$RESET" "$*"; }
die()  { printf '%s错误：%s%s\n' "$RED" "$*" "$RESET" >&2; exit 1; }

usage() {
  cat <<'TEXT'
抖音自动续火花 · macOS 一键安装

  curl -fsSL https://github.com/xiaoyoumi9924/douyin-fire/releases/latest/download/install.sh | bash

参数：
  --port 8765         面板端口（默认 8765）
  --schedule 07:00    装完顺便把每日任务设成这个时间（可选）
  --no-open           装完不自动打开浏览器
  --version v0.1.7    指定版本（默认 GitHub 最新 release）
  --source PATH       从本地 .app / .dmg 安装（离线或测试用）
  --data-dir PATH     数据目录（默认 ~/Library/Application Support/Douyin Fire）
  --install-dir PATH  安装目录（默认 /Applications）
  --uninstall         卸载面板服务和应用（数据保留）
  --purge-data        卸载时连数据一起删（谨慎）
  -h, --help          显示本帮助

装完会输出一个本机地址，用浏览器打开就是控制面板；面板会开机自启。
第一次仍需要在面板里扫码登录、加好友、写消息、保存时间。
TEXT
}

cleanup() {
  if [ -n "$TMP" ] && [ -d "$TMP" ]; then
    if mount 2>/dev/null | grep -Fq "on $TMP/mnt "; then
      hdiutil detach "$TMP/mnt" -quiet >/dev/null 2>&1 || true
    fi
    rm -rf "$TMP"
  fi
}
trap cleanup EXIT

xml_escape() {
  printf '%s' "$1" | sed -e 's/&/\&amp;/g' -e 's/</\&lt;/g' -e 's/>/\&gt;/g'
}

panel_url() { printf 'http://127.0.0.1:%s/' "$PORT"; }

health_check() {
  curl -fs -m 1 -o /dev/null "$(panel_url)api/state" 2>/dev/null
}

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
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
      | grep -i '\.dmg$' | grep -i 'arm64' | head -1)" || true
    if [ -n "$url" ]; then printf '%s' "$url"; return 0; fi
  fi
  printf 'https://github.com/%s/releases/download/%s/Douyin.Fire-%s-arm64.dmg' "$REPO" "$tag" "$ver"
}

parse_args() {
  while [ $# -gt 0 ]; do
    case "$1" in
      --port)
        [ $# -ge 2 ] || die "--port 需要端口号"
        PORT="$2"; shift 2 ;;
      --port=*) PORT="${1#*=}"; shift ;;
      --schedule)
        [ $# -ge 2 ] || die "--schedule 需要 HH:MM"
        SCHEDULE="$2"; shift 2 ;;
      --schedule=*) SCHEDULE="${1#*=}"; shift ;;
      --version)
        [ $# -ge 2 ] || die "--version 需要版本号"
        VERSION="$2"; shift 2 ;;
      --version=*) VERSION="${1#*=}"; shift ;;
      --source)
        [ $# -ge 2 ] || die "--source 需要路径"
        SOURCE="$2"; shift 2 ;;
      --source=*) SOURCE="${1#*=}"; shift ;;
      --data-dir)
        [ $# -ge 2 ] || die "--data-dir 需要路径"
        DATA_DIR="$2"; shift 2 ;;
      --data-dir=*) DATA_DIR="${1#*=}"; shift ;;
      --install-dir)
        [ $# -ge 2 ] || die "--install-dir 需要路径"
        INSTALL_DIR="$2"; shift 2 ;;
      --install-dir=*) INSTALL_DIR="${1#*=}"; shift ;;
      --no-open) OPEN_AFTER=0; shift ;;
      --uninstall) UNINSTALL=1; shift ;;
      --purge-data) PURGE=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "不认识的参数：$1（用 --help 查看用法）" ;;
    esac
  done
}

do_uninstall() {
  local app="$INSTALL_DIR/$APP_NAME.app"
  step "停止面板服务"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  rm -f "$HOME/Library/LaunchAgents/$LABEL.plist"

  local sched="$HOME/Library/LaunchAgents/$SCHEDULE_LABEL.plist"
  if [ -f "$sched" ] && grep -Fq "$app" "$sched"; then
    launchctl bootout "gui/$(id -u)/$SCHEDULE_LABEL" 2>/dev/null || true
    rm -f "$sched"
    ok "已移除每日任务"
  fi

  step "删除应用"
  rm -rf "$app"
  ok "已删除 $app"

  if [ "$PURGE" -eq 1 ]; then
    step "删除数据目录"
    rm -rf "$DATA_DIR"
    ok "已删除 $DATA_DIR"
  else
    warn "数据目录保留在：${DATA_DIR}（要一起删就加 --purge-data）"
  fi
  ok "卸载完成"
}

download_dmg() {
  local tag="$1" ver="$2" url
  url="$(asset_url "$tag" "$ver")"
  step "下载 $APP_NAME ${tag}（约 800 MB，请耐心等）"
  say "    $url"
  TMP="$(mktemp -d "${TMPDIR:-/tmp}/douyin-fire-install.XXXXXX")"
  local dmg="$TMP/setup.dmg"
  curl -fL --retry 3 --connect-timeout 20 --progress-bar -o "$dmg" "$url" \
    || die "下载失败：$url"
  [ -s "$dmg" ] || die "下载的文件是空的：$url"
  DMG_PATH="$dmg"
}

app_from_source() {
  local src="$1"
  local found=""
  if [ -d "$src" ] && [ "${src%/}" != "${src%.app}" ]; then
    SRC_APP="$src"; return 0
  fi
  if [ -d "$src" ]; then
    found="$(find "$src" -maxdepth 2 -name '*.app' -print -quit 2>/dev/null)" || true
    [ -n "$found" ] || die "目录里没有 .app：$src"
    SRC_APP="$found"; return 0
  fi
  if [ -f "$src" ]; then
    TMP="$(mktemp -d "${TMPDIR:-/tmp}/douyin-fire-install.XXXXXX")"
    mkdir -p "$TMP/mnt"
    hdiutil attach -nobrowse -readonly -quiet -mountpoint "$TMP/mnt" "$src" >/dev/null \
      || die "打不开安装包：$src"
    found="$(find "$TMP/mnt" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)" || true
    [ -n "$found" ] || die "安装包里没有 .app：$src"
    SRC_APP="$found"; return 0
  fi
  die "找不到安装源：$src"
}

install_app() {
  local src_app="$1"
  local dest="$INSTALL_DIR/$APP_NAME.app"
  mkdir -p "$INSTALL_DIR"
  if [ -e "$dest" ]; then
    step "替换旧版本"
    rm -rf "$dest" || die "删不掉旧版本（应用可能正在运行，先退出再试）：$dest"
  fi
  step "安装到 $dest"
  ditto "$src_app" "$dest" || die "复制应用失败"
  xattr -dr com.apple.quarantine "$dest" 2>/dev/null || true
  APP_PATH="$dest"
}

write_agent() {
  local app="$1"
  local plist="$HOME/Library/LaunchAgents/$LABEL.plist"
  local bin="$app/Contents/Resources/backend/douyin-fire-backend/douyin-fire-backend"
  [ -x "$bin" ] || die "应用里找不到后台程序：$bin"
  mkdir -p "$HOME/Library/LaunchAgents" "$DATA_DIR/logs"

  local bin_x data_x pw_x log_x err_x
  bin_x="$(xml_escape "$bin")"
  data_x="$(xml_escape "$DATA_DIR")"
  pw_x="$(xml_escape "$app/Contents/Resources/ms-playwright")"
  log_x="$(xml_escape "$DATA_DIR/logs/panel.log")"
  err_x="$(xml_escape "$DATA_DIR/logs/panel.err.log")"

  cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
	<key>Label</key>
	<string>$LABEL</string>
	<key>ProgramArguments</key>
	<array>
		<string>$bin_x</string>
		<string>serve</string>
	</array>
	<key>WorkingDirectory</key>
	<string>$data_x</string>
	<key>RunAtLoad</key>
	<true/>
	<key>KeepAlive</key>
	<true/>
	<key>ThrottleInterval</key>
	<integer>10</integer>
	<key>EnvironmentVariables</key>
	<dict>
		<key>PATH</key>
		<string>/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
		<key>DOUYIN_DATA_DIR</key>
		<string>$data_x</string>
		<key>DOUYIN_DESKTOP_MODE</key>
		<string>1</string>
		<key>UI_PORT</key>
		<string>$PORT</string>
		<key>PLAYWRIGHT_BROWSERS_PATH</key>
		<string>$pw_x</string>
		<key>LANG</key>
		<string>zh_CN.UTF-8</string>
	</dict>
	<key>StandardOutPath</key>
	<string>$log_x</string>
	<key>StandardErrorPath</key>
	<string>$err_x</string>
</dict>
</plist>
PLIST

  plutil -lint "$plist" >/dev/null || die "面板服务配置格式不对：$plist"
  PLIST_PATH="$plist"
}

start_agent() {
  local plist="$1"
  step "注册开机自启的面板服务"
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 0.5
  local attempt
  for attempt in 1 2 3; do
    if launchctl bootstrap "gui/$(id -u)" "$plist" 2>/dev/null; then
      return 0
    fi
    sleep 1
  done
  die "注册面板服务失败（重新登录一次再试，或 sudo launchctl bootstrap gui/$(id -u) ${plist}）"
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
  ok "每天 $value 会自动运行（电脑需开机并登录）"
}

main() {
  parse_args "$@"

  [ "$(uname -s)" = "Darwin" ] || die "这个脚本目前只支持 macOS；Windows / Linux 请从 Releases 下载安装包"
  [ "$(uname -m)" = "arm64" ] || die "目前 Release 只提供 Apple Silicon（arm64）版本；Intel Mac 暂不支持"

  if [ "$UNINSTALL" -eq 1 ]; then
    do_uninstall
    exit 0
  fi

  case "$PORT" in
    ''|*[!0-9]*) die "--port 必须是数字" ;;
  esac

  local tag ver
  if [ -n "$VERSION" ]; then
    tag="v${VERSION#v}"
    ver="${tag#v}"
  else
    tag="$(latest_tag)"
    ver="${tag#v}"
  fi

  # 先停掉旧服务，避免升级时端口僵持
  launchctl bootout "gui/$(id -u)/$LABEL" 2>/dev/null || true
  sleep 0.5
  local wait=0
  while port_in_use "$PORT" && [ "$wait" -lt 10 ]; do
    sleep 0.5; wait=$((wait + 1))
  done
  if port_in_use "$PORT"; then
    die "端口 $PORT 已被别的程序占用（可能是旧面板）。先关掉它，或用 --port 换一个端口"
  fi

  if [ ! -d "$INSTALL_DIR" ]; then
    mkdir -p "$INSTALL_DIR" 2>/dev/null || true
  fi
  if [ ! -w "$INSTALL_DIR" ]; then
    if [ "$INSTALL_DIR" = "/Applications" ]; then
      INSTALL_DIR="$HOME/Applications"
      mkdir -p "$INSTALL_DIR" || die "建不了安装目录：$INSTALL_DIR"
      warn "没有 /Applications 的写权限，改装到 $INSTALL_DIR"
    else
      die "安装目录不可写：$INSTALL_DIR"
    fi
  fi

  if [ -n "$SOURCE" ]; then
    step "使用本地安装源：$SOURCE"
    app_from_source "$SOURCE"
  else
    local dmg
    download_dmg "$tag" "$ver"
    dmg="$DMG_PATH"
    mkdir -p "$TMP/mnt"
    step "挂载安装包"
    hdiutil attach -nobrowse -readonly -quiet -mountpoint "$TMP/mnt" "$dmg" >/dev/null \
      || die "挂载安装包失败"
    SRC_APP="$(find "$TMP/mnt" -maxdepth 1 -name '*.app' -print -quit 2>/dev/null)" || true
    [ -n "$SRC_APP" ] || die "安装包里没有 $APP_NAME.app"
  fi

  install_app "$SRC_APP"
  write_agent "$APP_PATH"
  start_agent "$PLIST_PATH"

  step "等待面板启动"
  local checks=0
  while [ "$checks" -lt 60 ]; do
    health_check && break
    sleep 0.5
    checks=$((checks + 1))
  done
  if ! health_check; then
    printf '%s面板没能启动，最后几行日志：%s\n' "$RED" "$RESET" >&2
    tail -n 20 "$DATA_DIR/logs/panel.err.log" "$DATA_DIR/logs/panel.log" 2>/dev/null >&2 || true
    die "面板没启动成功，日志在 $DATA_DIR/logs/"
  fi

  if [ -n "$SCHEDULE" ]; then
    apply_schedule "$SCHEDULE"
  fi

  local url
  url="$(panel_url)"
  printf '\n%s%s 安装完成%s\n' "$BOLD" "$GREEN" "$RESET"
  printf '   面板地址   %s%s%s\n' "$BOLD" "$url" "$RESET"
  printf '   应用位置   %s\n' "$APP_PATH"
  printf '   数据目录   %s\n' "$DATA_DIR"
  printf '   运行日志   %s\n' "$DATA_DIR/logs/panel.log"
  printf '\n面板会开机自启，关掉终端不影响。首次使用还有四步（只需做一次）：\n'
  printf '   1. 打开上面的地址，用抖音扫码登录\n'
  printf '   2. 「好友管理」加好友（要填对方备注名）\n'
  printf '   3. 「消息管理」写每天要发的内容，保存\n'
  printf '   4. 「自动任务」选时间并点「保存时间」——之后每天自动发\n'
  printf '\n卸载：bash install.sh --uninstall（数据保留）\n'

  if [ "$OPEN_AFTER" -eq 1 ]; then
    open "$url" 2>/dev/null || true
  fi
}

main "$@"
