#!/bin/bash
# 双击打开「抖音自动续火花」控制面板。
# 关掉这个终端窗口 = 关掉面板（定时发送不受影响，那是另一个 launchd 任务）。

cd "$(dirname "$0")" || exit 1

PORT="${UI_PORT:-8765}"
URL="http://127.0.0.1:${PORT}/"

if curl -sf -o /dev/null "${URL}api/state"; then
  echo "控制面板已经在运行，直接打开 ${URL}"
  open "$URL"
  exit 0
fi

echo "正在启动控制面板……"
".venv/bin/python" ui/server.py &
SERVER_PID=$!

for _ in $(seq 1 60); do
  if curl -sf -o /dev/null "${URL}api/state"; then break; fi
  sleep 0.25
done

open "$URL"
echo "面板地址：${URL}    关闭本窗口即停止面板。"

wait $SERVER_PID
