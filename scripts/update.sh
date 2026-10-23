#!/bin/bash
# 一键更新 douyin-auto-fire（本机优先走 GitHub tarball，国内网络比 git 协议稳）。
# 用法：scripts/update.sh [tarball 地址]
# 你的 config.json / .env / storage-state.json / artifacts 不会被覆盖。

set -euo pipefail
cd "$(dirname "$0")/.."
PROJECT_ROOT="$(pwd)"

URL="${1:-https://codeload.github.com/unmev/douyin-auto-fire/tar.gz/refs/heads/main}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

echo "下载: $URL"
curl -fsSL --max-time 180 -o "$TMP/repo.tgz" "$URL"

echo "解包……"
tar -xzf "$TMP/repo.tgz" -C "$TMP"
SRC="$(find "$TMP" -maxdepth 1 -type d -name 'douyin-auto-fire-*' | head -1)"
if [ -z "$SRC" ]; then
  echo "错误: 解包后找不到项目目录" >&2
  exit 1
fi

echo "同步代码（保留本地配置）……"
rsync -a --exclude '.venv' --exclude 'artifacts' --exclude 'config.json' \
      --exclude '.env' --exclude 'storage-state.json' \
      "$SRC"/ "$PROJECT_ROOT"/

echo "更新 Python 依赖……"
"$PROJECT_ROOT/.venv/bin/pip" install -q -r "$PROJECT_ROOT/requirements.txt"

echo "完成。建议先验证："
echo "  cd \"$PROJECT_ROOT\" && .venv/bin/python run.py --dry-run"
