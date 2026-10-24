#!/usr/bin/env python3
"""抖音自动续火花 · 本地控制面板。

开发模式可运行 `.venv/bin/python ui/server.py` 并访问 127.0.0.1:8765；桌面版
由 Electron 启动同一个面板，服务只绑定 127.0.0.1 的临时端口。项目开发时
数据默认保存在项目根目录，安装版通过 DOUYIN_DATA_DIR 使用系统用户数据目录。
"""

from __future__ import annotations

import json
import base64
import binascii
import mimetypes
import os
import queue
import re
import signal
import shlex
import shutil
import subprocess
import sys
import threading
import uuid
from datetime import datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse
from xml.sax.saxutils import escape as xml_escape

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Development keeps using the checkout as before. Packaged builds set this to
# the platform's per-user application-data directory so credentials and task
# data never live inside the read-only app bundle.
DATA_ROOT = Path(os.environ.get("DOUYIN_DATA_DIR", ROOT)).expanduser().resolve()
DATA_ROOT.mkdir(parents=True, exist_ok=True)
os.environ.setdefault("DOUYIN_DATA_DIR", str(DATA_ROOT))
os.environ.setdefault("DOUYIN_ENV_FILE", str(DATA_ROOT / ".env"))

from app.accounts import Account, AccountPaths, account_paths, load_account_records, validate_account_id
from app.config import ConfigError, load_task
from app.history import History, message_id
from app.models import Settings

PYTHON = Path(sys.executable) if getattr(sys, "frozen", False) else ROOT / ".venv" / "bin" / "python"
PACKAGED = bool(getattr(sys, "frozen", False))
DESKTOP_MODE = PACKAGED or os.getenv("DOUYIN_DESKTOP_MODE") == "1"
BACKEND_PREFIX = [str(PYTHON)] if PACKAGED else ([sys.executable, str(ROOT / "desktop_backend.py")] if DESKTOP_MODE else [str(PYTHON)])
REGISTRY = DATA_ROOT / "config" / "accounts.json"
CONFIG = DATA_ROOT / "config.json"
ARTIFACTS = DATA_ROOT / "artifacts"
RESULT = ARTIFACTS / "result.json"
RUN_HISTORY = ARTIFACTS / "run-history.jsonl"
STATE_FILE = DATA_ROOT / "storage-state.json"
PLIST = Path.home() / "Library/LaunchAgents" / "com.meiyh.douyin-auto-fire.plist"
SCHEDULE_FILE = DATA_ROOT / ".schedule.json"
LABEL = "com.meiyh.douyin-auto-fire"
PORT = int(os.getenv("UI_PORT", "8765"))
HOST = "127.0.0.1"
MAX_POST_BODY_BYTES = 12 * 1024 * 1024
MAX_IMAGE_BYTES = 8 * 1024 * 1024
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".gif", ".webp"}

TARGET_RE = re.compile(r"处理好友:\s*(好友\d+)")
TARGET_FAIL_RE = re.compile(r"(?:好友处理失败|登录状态失效|打开聊天失败).*?(好友\d+)")
SUMMARY_RE = re.compile(r"执行结束:\s*成功\s*(\d+)，失败\s*(\d+)(?:，待核对\s*(\d+))?")
ERROR_RE = re.compile(r"^错误:\s*(.+)$")


# ---------------------------------------------------------------- 工具函数


def read_json(path: Path, default=None):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def write_json(path: Path, payload) -> None:
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def load_config(path: Path = CONFIG) -> dict:
    config = read_json(path, {})
    return config if isinstance(config, dict) else {}


def message_state(config: dict) -> dict:
    messages = config.get("messages") or []
    if not isinstance(messages, list):
        return {"mode": "unknown", "texts": [], "images": [], "raw": messages}
    images = []
    if len(messages) == 1 and isinstance(messages[0], dict):
        first = messages[0]
        if first.get("type") == "text":
            return {"mode": "single", "texts": [str(first.get("value", ""))], "images": []}
        if first.get("type") == "image":
            value = str(first.get("value", first.get("path", "")))
            return {"mode": "single", "texts": [], "images": [value] if value else []}
        if first.get("type") == "random":
            choices = first.get("choices") or []
            texts = [str(item.get("value", "")) for item in choices if isinstance(item, dict) and item.get("type") == "text"]
            images = [str(item.get("value", item.get("path", ""))) for item in choices if isinstance(item, dict) and item.get("type") == "image"]
            images = [value for value in images if value]
            return {"mode": "random" if len(texts) + len(images) > 1 else "single", "texts": texts, "images": images}
    texts = [str(item.get("value", "")) for item in messages if isinstance(item, dict) and item.get("type") == "text"]
    images = [str(item.get("value", item.get("path", ""))) for item in messages if isinstance(item, dict) and item.get("type") == "image"]
    return {"mode": "sequence", "texts": texts, "images": [value for value in images if value]}


def image_suffix_from_bytes(data: bytes, filename: str) -> str | None:
    suffix = Path(filename).suffix.lower()
    if suffix not in IMAGE_SUFFIXES:
        return None
    valid = (
        suffix == ".png" and data.startswith(b"\x89PNG\r\n\x1a\n")
        or suffix in {".jpg", ".jpeg"} and data.startswith(b"\xff\xd8\xff")
        or suffix == ".gif" and data.startswith((b"GIF87a", b"GIF89a"))
        or suffix == ".webp" and len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    )
    if not valid:
        return None
    return ".jpg" if suffix == ".jpeg" else suffix


def plist_schedule() -> dict:
    if not PLIST.exists():
        return {"present": False, "hour": 8, "minute": 0}
    text = PLIST.read_text(encoding="utf-8")
    hour = re.search(r"<key>Hour</key>\s*<integer>(\d+)</integer>", text)
    minute = re.search(r"<key>Minute</key>\s*<integer>(\d+)</integer>", text)
    return {
        "present": True,
        "hour": int(hour.group(1)) if hour else 8,
        "minute": int(minute.group(1)) if minute else 0,
    }


def run_command(mode: str, *args: str) -> list[str]:
    if DESKTOP_MODE:
        return [*BACKEND_PREFIX, mode, *args]
    if mode == "login":
        return [str(PYTHON), str(ROOT / "scripts" / "login_mac.py"), *args]
    return [str(PYTHON), "run.py", *args]


def scheduled_run_command(hour: int, minute: int) -> list[str]:
    # 定时任务既按点触发，也每 30 分钟轻量检查一次，用于开机/唤醒后的当天补跑。
    # 补跑门禁在 app.main.run() 里：当天已完成、未到设定时间、或当天尝试已达上限
    # 都会立刻退出，不会启动浏览器。
    catch_up_args = ["--catch-up", "--not-before", f"{hour:02d}:{minute:02d}"]
    appimage = os.environ.get("DOUYIN_SCHEDULE_LAUNCHER")
    if sys.platform.startswith("linux") and appimage:
        return [appimage, "--scheduled-run", *catch_up_args]
    return run_command("run", *catch_up_args)


def plist_body(hour: int, minute: int) -> str:
    arguments = scheduled_run_command(hour, minute)
    program_arguments = "\n".join(f"    <string>{xml_escape(argument)}</string>" for argument in arguments)
    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    browser_environment = (
        f"    <key>PLAYWRIGHT_BROWSERS_PATH</key>\n    <string>{xml_escape(browser_path)}</string>\n"
        if browser_path else ""
    )
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>
  <string>{LABEL}</string>

  <key>ProgramArguments</key>
  <array>
{program_arguments}
  </array>

  <key>WorkingDirectory</key>
  <string>{xml_escape(str(DATA_ROOT))}</string>

  <key>StartCalendarInterval</key>
  <dict>
    <key>Hour</key><integer>{hour}</integer>
    <key>Minute</key><integer>{minute}</integer>
  </dict>

  <key>StartInterval</key>
  <integer>1800</integer>

  <key>RunAtLoad</key>
  <true/>

  <key>EnvironmentVariables</key>
  <dict>
    <key>PATH</key>
    <string>/usr/local/bin:/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <key>DOUYIN_DATA_DIR</key>
    <string>{xml_escape(str(DATA_ROOT))}</string>
{browser_environment}    <key>LANG</key>
    <string>zh_CN.UTF-8</string>
  </dict>

  <key>StandardOutPath</key>
  <string>{xml_escape(str(ARTIFACTS / 'launchd.out.log'))}</string>
  <key>StandardErrorPath</key>
  <string>{xml_escape(str(ARTIFACTS / 'launchd.err.log'))}</string>

  <key>ProcessType</key>
  <string>Background</string>
</dict>
</plist>
"""


def next_run_text(hour: int, minute: int) -> str:
    now = datetime.now()
    target = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if target <= now:
        target += timedelta(days=1)
    days = (target.date() - now.date()).days
    prefix = "今天" if days == 0 else "明天" if days == 1 else target.strftime("%m 月 %d 日")
    return f"{prefix} {hour:02d}:{minute:02d}"


def launchd(command: list[str]) -> tuple[int, str]:
    try:
        result = subprocess.run(command, capture_output=True, text=True, timeout=20)
        return result.returncode, (result.stderr or result.stdout).strip()
    except (OSError, subprocess.TimeoutExpired) as exc:
        return 1, str(exc)


def current_schedule() -> dict:
    if sys.platform == "darwin":
        return plist_schedule()
    saved = read_json(SCHEDULE_FILE, {})
    if isinstance(saved, dict):
        return {
            "present": bool(saved.get("present", False)),
            "hour": int(saved.get("hour", 8)),
            "minute": int(saved.get("minute", 0)),
        }
    return {"present": False, "hour": 8, "minute": 0}


def _save_schedule(hour: int, minute: int) -> None:
    write_json(SCHEDULE_FILE, {"present": True, "hour": hour, "minute": minute})


def _apply_windows_schedule(hour: int, minute: int) -> None:
    wrapper = DATA_ROOT / "run-scheduled.cmd"
    data_dir = str(DATA_ROOT).replace('"', '""')
    browser_dir = os.environ.get("PLAYWRIGHT_BROWSERS_PATH", "").replace('"', '""')
    command = subprocess.list2cmdline(scheduled_run_command(hour, minute))
    content = (
        "@echo off\r\n"
        "chcp 65001 >nul\r\n"
        f'cd /d "{data_dir}"\r\n'
        f'set "DOUYIN_DATA_DIR={data_dir}"\r\n'
        f'set "PLAYWRIGHT_BROWSERS_PATH={browser_dir}"\r\n'
        f"{command}\r\n"
    )
    wrapper.write_text(content, encoding="utf-8")
    at = f"{hour:02d}:{minute:02d}"
    # StartWhenAvailable 负责睡眠/关机导致的错过后补跑；程序里的补跑门禁再兜一层。
    script = (
        "$ErrorActionPreference='Stop';"
        f"$action=New-ScheduledTaskAction -Execute '{wrapper}';"
        f"$trigger=New-ScheduledTaskTrigger -Daily -At '{at}';"
        "$settings=New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries "
        "-DontStopIfGoingOnBatteries -ExecutionTimeLimit (New-TimeSpan -Hours 2);"
        "Register-ScheduledTask -TaskName 'DouyinAutoFire' -Action $action -Trigger $trigger "
        "-Settings $settings -Force | Out-Null"
    )
    result = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
        capture_output=True,
        text=True,
        timeout=60,
    )
    if not result.returncode:
        return
    # 旧系统或受限策略：退回按天触发，补跑只能靠下次开机后的整点任务。
    fallback = subprocess.run(
        ["schtasks", "/Create", "/F", "/SC", "DAILY", "/ST", at, "/TN", "DouyinAutoFire", "/TR", f'"{wrapper}"'],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if fallback.returncode:
        detail = (result.stderr or result.stdout or fallback.stderr or fallback.stdout).strip()
        raise RuntimeError(detail or "Windows 任务计划程序创建失败")


def _apply_linux_schedule(hour: int, minute: int) -> None:
    crontab = shutil.which("crontab")
    if not crontab:
        raise RuntimeError("没有找到 crontab；此 Linux 发行版暂时无法安装每日定时任务")
    listed = subprocess.run([crontab, "-l"], capture_output=True, text=True, timeout=15)
    existing = listed.stdout.splitlines() if listed.returncode == 0 else []
    retained = [line for line in existing if "# douyin-fire-local-schedule" not in line]
    environment = f"DOUYIN_DATA_DIR={shlex.quote(str(DATA_ROOT))} HEADLESS=true"
    browser_path = os.environ.get("PLAYWRIGHT_BROWSERS_PATH")
    if os.environ.get("DOUYIN_SCHEDULE_LAUNCHER"):
        browser_path = None  # AppImage chooses its fresh mount path at startup.
    if browser_path:
        environment += f" PLAYWRIGHT_BROWSERS_PATH={shlex.quote(browser_path)}"
    command = " ".join(shlex.quote(part) for part in scheduled_run_command(hour, minute))
    # 每 30 分钟轻量检查一次：到点执行，错过（睡眠/关机）则在恢复后的下一个整半点补跑。
    retained.append(f"*/30 * * * * {environment} {command} >> {shlex.quote(str(ARTIFACTS / 'scheduled.log'))} 2>&1 # douyin-fire-local-schedule")
    installed = subprocess.run([crontab, "-"], input="\n".join(retained) + "\n", capture_output=True, text=True, timeout=15)
    if installed.returncode:
        raise RuntimeError((installed.stderr or installed.stdout).strip() or "写入 crontab 失败")


def apply_schedule(hour: int, minute: int) -> None:
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    if sys.platform == "darwin":
        PLIST.parent.mkdir(parents=True, exist_ok=True)
        PLIST.write_text(plist_body(hour, minute), encoding="utf-8")
        uid = os.getuid()
        launchd(["launchctl", "bootout", f"gui/{uid}/{LABEL}"])
        code, message = launchd(["launchctl", "bootstrap", f"gui/{uid}", str(PLIST)])
        if code:
            raise RuntimeError(message or "macOS launchd 定时任务启动失败")
    elif os.name == "nt":
        _apply_windows_schedule(hour, minute)
    elif sys.platform.startswith("linux"):
        _apply_linux_schedule(hour, minute)
    else:
        raise RuntimeError(f"暂不支持在 {sys.platform} 设置系统定时任务")
    _save_schedule(hour, minute)


def last_run(paths: AccountPaths | None = None) -> dict | None:
    result_path = paths.artifacts_dir / "result.json" if paths else RESULT
    payload = read_json(result_path)
    if not isinstance(payload, dict):
        return None
    results = payload.get("results") or []
    ok = [item for item in results if isinstance(item, dict) and item.get("status") == "success"]
    failed = [item for item in results if isinstance(item, dict) and item.get("status") == "failed"]
    unknown = [item for item in results if isinstance(item, dict) and item.get("status") == "unknown"]
    finished = payload.get("finished_at") or ""
    ok_aliases = [str(item.get("target") or "") for item in ok if isinstance(item, dict)]
    return {
        "finishedAt": finished,
        "dryRun": bool(payload.get("dry_run")),
        "ok": len(ok),
        "failed": len(failed),
        "unknown": len(unknown),
        "sent": sum(int(item.get("sent") or 0) for item in ok if isinstance(item, dict)),
        "date": finished[:10],
        "okAliases": ok_aliases,
    }


def run_history(limit: int = 20, paths: AccountPaths | None = None) -> list[dict]:
    records: list[dict] = []
    history_path = paths.artifacts_dir / "run-history.jsonl" if paths else RUN_HISTORY
    try:
        lines = history_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        lines = []

    for line in lines:
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(record, dict) and isinstance(record.get("results"), list):
            records.append(record)

    latest = read_json(paths.artifacts_dir / "result.json" if paths else RESULT)
    if isinstance(latest, dict) and isinstance(latest.get("results"), list):
        latest_time = latest.get("finished_at")
        if latest_time and all(item.get("finished_at") != latest_time for item in records):
            records.append(latest)

    output: list[dict] = []
    for record in reversed(records[-limit:]):
        results = [item for item in record["results"] if isinstance(item, dict)]
        output.append({
            "taskId": str(record.get("task_id") or ""),
            "finishedAt": str(record.get("finished_at") or ""),
            "dryRun": bool(record.get("dry_run")),
            "results": [{
                "target": str(item.get("target") or "未知好友"),
                "status": str(item.get("status") or "unknown"),
                "sent": int(item.get("sent") or 0),
                "error": str(item.get("error") or ""),
            } for item in results],
        })
    return output


def stamp_targets(run: dict | None, friends: list[str] | None = None) -> list[int]:
    """今天成功投递的好友在 config 里的下标（只在「今天 + 真发」时成立）。

    按 result.json 里的「好友NN」别名逐个映射，而不是「前 N 个」：中间某位好友
    失败时，只有该位不盖戳，其余照常。
    """
    if not run or run.get("dryRun") or not run.get("date"):
        return []
    if run["date"] != datetime.now().strftime("%Y-%m-%d"):
        return []
    friends = friends if friends is not None else (load_config().get("friends") or [])
    indices: list[int] = []
    for alias in run.get("okAliases") or []:
        match = re.fullmatch(r"好友(\d+)", alias.strip())
        if not match:
            continue
        index = int(match.group(1)) - 1
        if 0 <= index < len(friends) and index not in indices:
            indices.append(index)
    return sorted(indices)


def credential_state(path: Path | None = STATE_FILE) -> dict:
    if path is None or not path.exists():
        return {"present": False, "updatedAt": None}
    stat = path.stat()
    return {"present": True, "updatedAt": datetime.fromtimestamp(stat.st_mtime).astimezone().isoformat(timespec="seconds")}


def normalize_imported_cookie_state(payload: object) -> dict:
    """Convert common browser cookie exports into a Playwright storage state."""
    origins: list = []
    if isinstance(payload, list):
        cookies = payload
    elif isinstance(payload, dict) and isinstance(payload.get("cookies"), list):
        cookies = payload["cookies"]
        raw_origins = payload.get("origins", [])
        if isinstance(raw_origins, list):
            for origin in raw_origins:
                if not isinstance(origin, dict) or not isinstance(origin.get("origin"), str):
                    continue
                host = (urlparse(origin["origin"]).hostname or "").lower()
                if host == "douyin.com" or host.endswith(".douyin.com"):
                    origins.append(origin)
    else:
        raise ValueError("文件格式不正确，请选择 Cookie 数组或包含 cookies 数组的 JSON 文件")

    normalized: list[dict] = []
    has_session = False
    session_names = {"sessionid", "sessionid_ss", "sid_guard"}
    for index, cookie in enumerate(cookies):
        if not isinstance(cookie, dict):
            raise ValueError(f"第 {index + 1} 条 Cookie 格式不正确")
        name, value, domain = cookie.get("name"), cookie.get("value"), cookie.get("domain")
        if not isinstance(name, str) or not name or not isinstance(value, str):
            raise ValueError(f"第 {index + 1} 条 Cookie 缺少有效的名称或内容")
        if not isinstance(domain, str) or not domain.strip():
            raise ValueError(f"第 {index + 1} 条 Cookie 缺少域名，请导出包含域名的 JSON")
        domain = domain.strip().lower()
        if not (domain.lstrip(".") == "douyin.com" or domain.lstrip(".").endswith(".douyin.com")):
            continue

        expires = cookie.get("expires", cookie.get("expirationDate", -1))
        if cookie.get("session") is True or isinstance(expires, bool) or not isinstance(expires, (int, float)):
            expires = -1
        same_site = str(cookie.get("sameSite", "Lax")).lower()
        same_site = {"strict": "Strict", "lax": "Lax", "none": "None", "no_restriction": "None"}.get(same_site, "Lax")
        normalized.append({
            "name": name,
            "value": value,
            "domain": domain,
            "path": cookie.get("path") if isinstance(cookie.get("path"), str) else "/",
            "expires": expires,
            "httpOnly": bool(cookie.get("httpOnly", False)),
            "secure": bool(cookie.get("secure", False)),
            "sameSite": same_site,
        })
        has_session = has_session or name.lower() in session_names

    if not normalized:
        raise ValueError("文件中没有抖音域名的 Cookie")
    if not has_session:
        raise ValueError("没有找到抖音登录 Cookie（sessionid、sessionid_ss 或 sid_guard），请重新导出")
    return {"cookies": normalized, "origins": origins}


def build_state(account_id: str | None = None) -> dict:
    account = account_for(account_id) if account_id else None
    paths = account_paths(account, DATA_ROOT) if account else None
    task_path = paths.task_config if paths else CONFIG
    artifacts_dir = paths.artifacts_dir if paths else ARTIFACTS
    config = load_config(task_path)
    task = None
    try:
        task = load_task(Settings(
            task_config_path=task_path,
            storage_state=None,
            cookie=None,
            headless=False,
            browser_path=None,
            artifacts_dir=artifacts_dir,
            trace=False,
        ))
    except ConfigError:
        # The panel must remain usable while a new account has no friends or messages yet.
        if config.get("messages") or config.get("targets"):
            raise

    if task is None:
        raw_friends = config.get("friends") or []
        friends = [str(name) for name in raw_friends] if isinstance(raw_friends, list) else []
        friend_statuses = ["pending"] * len(friends)
        pending_targets = list(friends)
        unknown_targets = []
        all_targets_complete = False
    else:
        history = History(artifacts_dir / "history.json")
        run_date = history.run_date(task.timezone)
        history.merge_legacy_run_history(
            artifacts_dir / "run-history.jsonl",
            task.task_id,
            run_date,
            task.timezone,
            task.targets,
            config_path=task_path,
        )
        friends = [target.name for target in task.targets]
        friend_statuses = []
        pending_targets = []
        unknown_targets = []
        for target in task.targets:
            keys = [
                history.key(task.task_id, run_date, target.name, message_id(index, message))
                for index, message in enumerate(target.messages)
            ]
            delivery_status = history.target_status(keys)
            status = {"success": "sent", "failed": "failed", "unknown": "unknown", "pending": "pending"}[delivery_status]
            friend_statuses.append(status)
            if status in {"pending", "failed"}:
                pending_targets.append(target.name)
            elif status == "unknown":
                unknown_targets.append(target.name)
        all_targets_complete = bool(friends) and not pending_targets and not unknown_targets
    schedule = current_schedule()
    run = last_run(paths)
    return {
        "accountId": account_id,
        "accountName": account.display_name if account else None,
        "accountEnabled": account.enabled if account else True,
        "friends": friends,
        "friendStatuses": friend_statuses,
        "pendingTargets": pending_targets,
        "unknownTargets": unknown_targets,
        "allTargetsComplete": all_targets_complete,
        "sendQueueReady": task is not None,
        "message": message_state(config),
        "schedule": {**schedule, "next": next_run_text(schedule["hour"], schedule["minute"])},
        "credential": credential_state(paths.storage_state if paths else STATE_FILE),
        "lastRun": run,
        "runHistory": run_history(paths=paths),
        "stamped": [index for index, status in enumerate(friend_statuses) if status == "sent"],
        "running": RUNNER.snapshot(account_id),
        "now": datetime.now().astimezone().isoformat(timespec="seconds"),
    }


def account_records() -> list[Account] | None:
    return load_account_records(REGISTRY)


def account_for(account_id: str | None, *, allow_legacy: bool = True) -> Account:
    if account_id is None:
        raise ConfigError("缺少账号 ID")
    account_id = validate_account_id(account_id)
    records = account_records()
    if records is None:
        if allow_legacy and account_id == "main":
            return Account("main", True, Path(".env"), "主账号")
        raise ConfigError("没有 config/accounts.json，请先创建账号")
    account = next((item for item in records if item.id == account_id), None)
    if account is None:
        raise ConfigError(f"不存在账号 {account_id}")
    return account


def account_paths_for(account_id: str | None) -> AccountPaths:
    account = account_for(account_id)
    return account_paths(account, DATA_ROOT)


def _write_private(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(path)


def write_registry(records: list[dict]) -> None:
    REGISTRY.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = REGISTRY.with_suffix(".json.tmp")
    temporary.write_text(json.dumps({"accounts": records}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    os.chmod(temporary, 0o600)
    temporary.replace(REGISTRY)


def ensure_registry_for_add() -> list[Account]:
    records = account_records()
    if records is not None:
        return records
    main_env = DATA_ROOT / ".env.account-main"
    suffix = 2
    while main_env.exists():
        main_env = DATA_ROOT / f".env.account-main-migrated-{suffix}"
        suffix += 1
    _write_private(main_env, "TASK_CONFIG=config.json\nDOUYIN_STORAGE_STATE=storage-state.json\nARTIFACTS_DIR=artifacts\n")
    write_registry([{"id": "main", "name": "主账号", "enabled": True, "env_file": main_env.relative_to(DATA_ROOT).as_posix()}])
    return account_records() or []


def account_overview() -> dict:
    records = account_records()
    legacy = records is None
    if records is None:
        records = [Account("main", True, Path(".env"), "主账号")]
    items = []
    for account in records:
        try:
            paths = account_paths(account, DATA_ROOT)
            config = load_config(paths.task_config)
            run = last_run(paths)
            credential = credential_state(paths.storage_state)
            items.append({
                "id": account.id,
                "name": account.display_name,
                "enabled": account.enabled,
                "friendCount": len(config.get("friends") or config.get("targets") or []),
                "lastRun": run,
                "credential": credential,
            })
        except Exception as exc:
            items.append({"id": account.id, "name": account.display_name, "enabled": account.enabled, "friendCount": 0, "error": str(exc)})
    return {"accounts": items, "legacy": legacy, "running": RUNNER.snapshot()}


# ---------------------------------------------------------------- 子进程运行器


class Runner:
    def __init__(self) -> None:
        self.process: subprocess.Popen | None = None
        self.action: str | None = None
        self.started: str | None = None
        self.account_id: str | None = None
        self.lock_path: Path = ARTIFACTS / "run.lock"
        self.buffer: list[str] = []
        self.subscribers: set[queue.Queue] = set()
        self.guard = threading.Lock()

    def snapshot(self, account_id: str | None = None) -> dict | None:
        if not self.process or self.process.poll() is not None:
            return None
        if account_id is not None and self.account_id not in {None, account_id}:
            return None
        return {"action": self.action, "startedAt": self.started, "accountId": self.account_id}

    def subscribe(self) -> queue.Queue:
        channel: queue.Queue = queue.Queue()
        self.subscribers.add(channel)
        return channel

    def unsubscribe(self, channel: queue.Queue) -> None:
        self.subscribers.discard(channel)

    def emit(self, event: dict) -> None:
        for channel in list(self.subscribers):
            channel.put(event)

    def active(self) -> bool:
        return bool(self.process and self.process.poll() is None)

    def start(
        self,
        action: str,
        account_id: str | None = None,
        extra_args: list[str] | None = None,
    ) -> tuple[bool, str]:
        with self.guard:
            if self.active():
                return False, "上一条任务还没结束"
            if not Path(BACKEND_PREFIX[0]).exists():
                return False, "找不到自动化运行环境，请重新安装应用或检查项目虚拟环境"

            if action == "send-all":
                command = run_command("run", *(extra_args or []))
                run_account_id = None
            else:
                try:
                    account = account_for(account_id)
                    if not account.enabled:
                        return False, f"账号 {account_id} 已停用"
                    account_id = account.id
                    paths = account_paths(account, DATA_ROOT)
                except ConfigError as exc:
                    return False, str(exc)
                legacy_mode = account_records() is None
                if action == "login":
                    command = run_command("login", *([] if legacy_mode else ["--account", account_id]))
                else:
                    run_args = [] if legacy_mode else ["--account", account_id]
                    if action == "dry-run":
                        run_args.append("--dry-run")
                    command = run_command("run", *run_args)
                run_account_id = account_id

            environment = os.environ.copy()
            environment["PYTHONUNBUFFERED"] = "1"
            environment["DOUYIN_DATA_DIR"] = str(DATA_ROOT)
            self.buffer = []
            try:
                self.process = subprocess.Popen(
                    command,
                    cwd=str(DATA_ROOT),
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                    bufsize=1,
                    env=environment,
                )
            except OSError as exc:
                return False, str(exc)

            self.action = action
            self.account_id = run_account_id
            self.lock_path = paths.artifacts_dir / "run.lock" if action != "send-all" else ARTIFACTS / "run.lock"
            self.started = datetime.now().astimezone().isoformat(timespec="seconds")
            self.emit({"type": "started", "action": action, "startedAt": self.started, "accountId": self.account_id})
            threading.Thread(target=self._pump, args=(self.process, action, self.account_id), daemon=True).start()
            return True, ""

    def stop(self) -> None:
        """SIGINT 让 run.py 走完 finally（关浏览器、释放 run.lock），8 秒后仍未退出则升级。"""
        process = self.process
        if not process or process.poll() is not None:
            return
        try:
            process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            if os.name == "nt":
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                return
            raise
        threading.Thread(target=self._escalate, args=(process,), daemon=True).start()

    @staticmethod
    def _escalate(process: subprocess.Popen) -> None:
        if os.name == "nt":
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
            return
        final_signal = getattr(signal, "SIGKILL", signal.SIGTERM)
        for signal_number, wait in ((signal.SIGTERM, 8.0), (final_signal, 5.0)):
            try:
                process.wait(timeout=wait)
                return
            except subprocess.TimeoutExpired:
                try:
                    process.send_signal(signal_number)
                except OSError:
                    return

    def _pump(self, process: subprocess.Popen, action: str, account_id: str | None) -> None:
        previous_alias: str | None = None
        failures: set[str] = set()
        summary = None
        account_summaries: list[dict] = []
        active_account = account_id
        last_account = account_id
        error = None
        assert process.stdout is not None
        for raw in process.stdout:
            line = raw.rstrip("\n")
            self.buffer.append(line)
            if account_id is None:
                tagged = re.search(r"\[([A-Za-z0-9_-]{1,32})\]", line)
                if tagged:
                    active_account = tagged.group(1)
                    if active_account != last_account:
                        if previous_alias and previous_alias not in failures:
                            self.emit({"type": "stamp", "alias": previous_alias, "ok": True, "accountId": last_account})
                        previous_alias = None
                        failures.clear()
                        last_account = active_account
                    if action == "send-all":
                        try:
                            self.lock_path = account_paths(account_for(active_account), DATA_ROOT).artifacts_dir / "run.lock"
                        except ConfigError:
                            pass
            self.emit({"type": "line", "text": line, "accountId": active_account})

            match = TARGET_RE.search(line)
            if match:
                alias = match.group(1)
                if previous_alias and previous_alias not in failures:
                    self.emit({"type": "stamp", "alias": previous_alias, "ok": True, "accountId": active_account})
                previous_alias = alias
                self.emit({"type": "target", "alias": alias, "accountId": active_account})
                continue
            fail = TARGET_FAIL_RE.search(line)
            if fail:
                failures.add(fail.group(1))
                self.emit({"type": "stamp", "alias": fail.group(1), "ok": False, "accountId": active_account})
                continue
            found = SUMMARY_RE.search(line)
            if found:
                summary = {
                    "ok": int(found.group(1)),
                    "failed": int(found.group(2)),
                    "unknown": int(found.group(3) or 0),
                }
                account_summaries.append({"accountId": active_account, **summary})
                continue
            bad = ERROR_RE.search(line)
            if bad and not error:
                error = bad.group(1)

        code = process.wait()
        _release_lock(process.pid, self.lock_path)
        if previous_alias and previous_alias not in failures and code == 0:
            self.emit({"type": "stamp", "alias": previous_alias, "ok": True, "accountId": active_account})
        final_summary = summary
        if action == "send-all" and account_summaries:
            final_summary = {
                "ok": sum(item["ok"] for item in account_summaries),
                "failed": sum(item["failed"] for item in account_summaries),
                "unknown": sum(item.get("unknown", 0) for item in account_summaries),
            }
        self.emit(
            {
                "type": "done",
                "action": action,
                "code": code,
                "summary": final_summary,
                "error": error,
                "accountId": account_id,
                "accounts": account_summaries,
            }
        )
        self.process = None
        self.action = None
        self.started = None
        self.account_id = None
        self.emit({"type": "state", "state": build_state(account_id) if account_id else account_overview(), "accountId": account_id})


def _release_lock(pid: int, lock: Path = ARTIFACTS / "run.lock") -> None:
    """只清理属于这个子进程的 run.lock：正常退出会自己删，被强杀时由这里兜底。"""
    try:
        owner = lock.read_text(encoding="utf-8").strip()
    except OSError:
        return
    if owner == str(pid):
        try:
            lock.unlink()
        except OSError:
            pass


RUNNER = Runner()


# ---------------------------------------------------------------- HTTP


class Handler(BaseHTTPRequestHandler):
    server_version = "DouyinUI/1.0"

    def log_message(self, *args) -> None:  # 静默：控制台只留真正的错误
        pass

    # -- helpers --------------------------------------------------
    def send_json(self, payload, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def read_body(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if not length:
            return {}
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def fail(self, message: str, status: int = 400) -> None:
        self.send_json({"error": message}, status)

    # -- routes ---------------------------------------------------
    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)
        if route in {"/", "/index.html"}:
            page = (Path(__file__).parent / "static" / "index.html").read_text(encoding="utf-8")
            body = page.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/state":
            account_id = query.get("accountId", [None])[0]
            if account_id is None:
                try:
                    self.send_json(account_overview())
                except (ValueError, OSError) as exc:
                    self.fail(f"账号配置读取失败：{exc}", 500)
            else:
                try:
                    self.send_json(build_state(account_id))
                except (ValueError, OSError) as exc:
                    self.fail(f"账号配置读取失败：{exc}", 400)
            return
        if route.startswith("/api/images/"):
            filename = unquote(route.removeprefix("/api/images/"))
            if not filename:
                return self.fail("图片不存在", 404)
            image_path = (DATA_ROOT / filename).resolve()
            try:
                image_path.relative_to((DATA_ROOT / "images").resolve())
                body = image_path.read_bytes()
            except (ValueError, OSError):
                return self.fail("图片不存在", 404)
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(image_path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "private, max-age=3600")
            self.end_headers()
            self.wfile.write(body)
            return
        if route == "/api/stream":
            account_id = query.get("accountId", [None])[0]
            try:
                if account_id:
                    account_for(account_id)
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            self.stream(account_id)
            return
        self.fail("没有这个地址", 404)

    def stream(self, account_id: str | None = None) -> None:
        try:
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Connection", "keep-alive")
            self.end_headers()
        except (BrokenPipeError, ConnectionResetError, OSError):
            return

        channel = RUNNER.subscribe()
        try:
            self._event({"type": "state", "state": build_state(account_id) if account_id else account_overview(), "accountId": account_id})
            if RUNNER.buffer and (not account_id or RUNNER.account_id == account_id):
                self._event({"type": "backlog", "lines": RUNNER.buffer[-200:]})
            while True:
                try:
                    event = channel.get(timeout=15)
                except queue.Empty:
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                event_account = event.get("accountId")
                if account_id and event_account not in {None, account_id}:
                    continue
                self._event(event)
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            RUNNER.unsubscribe(channel)

    def _event(self, payload: dict) -> None:
        data = f"data: {json.dumps(payload, ensure_ascii=False)}\n\n".encode("utf-8")
        self.wfile.write(data)
        self.wfile.flush()

    def origin_ok(self) -> bool:
        """只接受本页面发起的写操作，挡住其他网站对 127.0.0.1 的跨站请求。"""
        origin = self.headers.get("Origin")
        if origin is None:
            return True  # curl / 命令行
        return origin in {f"http://{HOST}:{PORT}", f"http://localhost:{PORT}"}

    def do_POST(self) -> None:
        route = urlparse(self.path).path
        if not self.origin_ok():
            return self.fail("来源不被允许", 403)
        try:
            request_length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self.fail("请求长度无效")
        if request_length > MAX_POST_BODY_BYTES:
            return self.fail("上传内容超过 12 MB 限制", 413)
        body = self.read_body()

        def scoped_account(require_enabled: bool = False) -> tuple[Account, AccountPaths]:
            account = account_for(body.get("accountId"))
            if require_enabled and not account.enabled:
                raise ConfigError(f"账号 {account.id} 已停用")
            return account, account_paths(account, DATA_ROOT)

        if route == "/api/accounts":
            name = body.get("name")
            if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40:
                return self.fail("账号名称需为 1–40 个字符")
            try:
                records = ensure_registry_for_add()
                slug = re.sub(r"[^A-Za-z0-9_-]+", "-", name.strip()).strip("-_").lower()[:24]
                base_id = slug or f"account{len(records) + 1}"
                account_id = base_id
                suffix = 2
                existing_ids = {item.id for item in records}
                def account_files_exist(candidate: str) -> bool:
                    return any(path.exists() for path in (
                        DATA_ROOT / "config" / "tasks" / f"{candidate}.json",
                        DATA_ROOT / f".env.account-{candidate}",
                        DATA_ROOT / "storage_state" / f"{candidate}.json",
                        DATA_ROOT / "artifacts" / candidate,
                    ))
                while account_id in existing_ids or account_files_exist(account_id):
                    account_id = f"{base_id[:26]}-{suffix}"
                    suffix += 1
                validate_account_id(account_id)
                task_path = DATA_ROOT / "config" / "tasks" / f"{account_id}.json"
                env_path = DATA_ROOT / f".env.account-{account_id}"
                storage_path = DATA_ROOT / "storage_state" / f"{account_id}.json"
                artifacts_path = DATA_ROOT / "artifacts" / account_id
                task_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                storage_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                artifacts_path.mkdir(parents=True, exist_ok=True, mode=0o700)
                _write_private(task_path, json.dumps({"task_id": f"daily-streak-{account_id}", "timezone": "Asia/Shanghai", "friends": [], "messages": []}, ensure_ascii=False, indent=2) + "\n")
                _write_private(env_path, f"TASK_CONFIG=config/tasks/{account_id}.json\nDOUYIN_STORAGE_STATE=storage_state/{account_id}.json\nARTIFACTS_DIR=artifacts/{account_id}\n")
                entries = [{"id": item.id, "name": item.display_name, "enabled": item.enabled, "env_file": item.env_file.as_posix()} for item in records]
                entries.append({"id": account_id, "name": name.strip(), "enabled": False, "env_file": env_path.relative_to(DATA_ROOT).as_posix()})
                write_registry(entries)
                self.send_json(account_overview())
            except (ConfigError, OSError) as exc:
                self.fail(f"创建账号失败：{exc}", 400)
            return

        if route == "/api/accounts/update":
            account_id = body.get("accountId")
            try:
                records = account_records()
                if records is None:
                    return self.fail("旧单账号模式无需更新账号信息", 400)
                account_id = validate_account_id(account_id)
                updated = []
                found = False
                for item in records:
                    if item.id != account_id:
                        updated.append({"id": item.id, "name": item.display_name, "enabled": item.enabled, "env_file": item.env_file.as_posix()})
                        continue
                    found = True
                    name = body.get("name", item.display_name)
                    enabled = body.get("enabled", item.enabled)
                    if not isinstance(name, str) or not 1 <= len(name.strip()) <= 40 or not isinstance(enabled, bool):
                        return self.fail("账号名称或启用状态格式不正确")
                    if enabled:
                        paths = account_paths(item, DATA_ROOT)
                        config = load_config(paths.task_config)
                        if not (config.get("friends") or config.get("targets")) or not config.get("messages"):
                            return self.fail("先为账号添加至少一位好友和一条消息，再启用账号")
                    updated.append({"id": item.id, "name": name.strip(), "enabled": enabled, "env_file": item.env_file.as_posix()})
                if not found:
                    return self.fail(f"不存在账号 {account_id}", 404)
                write_registry(updated)
                self.send_json(account_overview())
            except (ConfigError, OSError) as exc:
                self.fail(f"更新账号失败：{exc}", 400)
            return

        if route == "/api/accounts/remove":
            try:
                records = account_records()
                if records is None:
                    return self.fail("旧单账号模式不能移除账号", 400)
                account_id = validate_account_id(body.get("accountId"))
                if not any(item.id == account_id for item in records):
                    return self.fail(f"不存在账号 {account_id}", 404)
                write_registry([{"id": item.id, "name": item.display_name, "enabled": item.enabled, "env_file": item.env_file.as_posix()} for item in records if item.id != account_id])
                self.send_json(account_overview())
            except (ConfigError, OSError) as exc:
                self.fail(f"移除账号失败：{exc}", 400)
            return

        if route == "/api/image":
            filename = body.get("name")
            encoded = body.get("data")
            if not isinstance(filename, str) or not isinstance(encoded, str):
                return self.fail("图片数据格式不正确")
            try:
                image_data = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                return self.fail("图片数据无法解码")
            if not image_data or len(image_data) > MAX_IMAGE_BYTES:
                return self.fail("图片需小于或等于 8 MB")
            suffix = image_suffix_from_bytes(image_data, filename)
            if suffix is None:
                return self.fail("请选择格式正确的 PNG、JPG、GIF 或 WebP 图片")
            try:
                account, _ = scoped_account()
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            image_dir = DATA_ROOT / "images" / account.id
            image_dir.mkdir(parents=True, exist_ok=True)
            saved_name = f"{uuid.uuid4().hex}{suffix}"
            image_path = image_dir / saved_name
            temporary = image_path.with_suffix(image_path.suffix + ".tmp")
            temporary.write_bytes(image_data)
            temporary.replace(image_path)
            return self.send_json({"path": f"images/{account.id}/{saved_name}", "name": filename, "size": len(image_data)})

        if route == "/api/friends":
            friends = body.get("friends")
            if not isinstance(friends, list):
                return self.fail("friends 必须是数组")
            cleaned: list[str] = []
            for item in friends:
                if not isinstance(item, str):
                    return self.fail("好友昵称必须是文字")
                name = item.strip()
                if not name or len(name) > 32:
                    return self.fail("好友昵称需为 1–32 个字符")
                if name not in cleaned:
                    cleaned.append(name)
            if not cleaned:
                return self.fail("至少要保留一位好友")
            if len(cleaned) > 50:
                return self.fail("最多 50 位好友")
            try:
                _, paths = scoped_account()
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            config = load_config(paths.task_config)
            config["friends"] = cleaned
            write_json(paths.task_config, config)
            return self.send_json(build_state(body.get("accountId")))

        if route == "/api/message":
            texts = body.get("texts")
            if not isinstance(texts, list):
                return self.fail("texts 必须是数组")
            cleaned = [str(item).strip() for item in texts if str(item).strip()]
            if any(len(item) > 500 for item in cleaned):
                return self.fail("单条消息不要超过 500 字")
            images = body.get("images")
            if images is None:
                try:
                    _, paths = scoped_account()
                except ConfigError as exc:
                    return self.fail(str(exc), 400)
                images = message_state(load_config(paths.task_config)).get("images", [])
            if not isinstance(images, list) or len(images) > 10:
                return self.fail("图片列表格式不正确")
            clean_images: list[str] = []
            try:
                account, paths = scoped_account()
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            image_root = (DATA_ROOT / "images").resolve()
            for item in images:
                if not isinstance(item, str):
                    return self.fail("图片路径格式不正确")
                candidate = (DATA_ROOT / item).resolve()
                try:
                    candidate.relative_to(image_root)
                except ValueError:
                    return self.fail("只能选择项目 images 目录中的图片")
                if candidate.suffix.lower() not in IMAGE_SUFFIXES or not candidate.is_file():
                    return self.fail("图片文件不存在或格式不支持")
                clean_images.append(candidate.relative_to(DATA_ROOT).as_posix())
            if not cleaned and not clean_images:
                return self.fail("至少添加一句文字或一张图片")

            choices = [{"type": "text", "value": item} for item in cleaned]
            choices.extend({"type": "image", "value": item} for item in clean_images)
            config = load_config(paths.task_config)
            if len(choices) == 1:
                config["messages"] = choices
            else:
                config["messages"] = [{"type": "random", "choices": choices}]
            write_json(paths.task_config, config)
            return self.send_json(build_state(account.id))

        if route == "/api/schedule":
            if body.get("accountId"):
                try:
                    account_for(body.get("accountId"))
                except ConfigError as exc:
                    return self.fail(str(exc), 400)
            try:
                hour = int(body.get("hour"))
                minute = int(body.get("minute"))
            except (TypeError, ValueError):
                return self.fail("时间格式不对")
            if not (0 <= hour <= 23 and 0 <= minute <= 59):
                return self.fail("时间超出范围")
            try:
                apply_schedule(hour, minute)
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                return self.fail(f"定时任务设置失败：{exc}", 500)
            return self.send_json(build_state(body.get("accountId")) if body.get("accountId") else account_overview())

        if route == "/api/action":
            action = body.get("action")
            account_id = body.get("accountId")
            extra_args: list[str] = []
            if action == "send-all":
                if account_id is not None:
                    return self.fail("发送全部账号不接受 accountId")
                if body.get("catchUp"):
                    not_before = body.get("notBefore")
                    extra_args.append("--catch-up")
                    if not_before:
                        if not isinstance(not_before, str) or not re.fullmatch(r"[0-2]?\d:[0-5]\d", not_before):
                            return self.fail("notBefore 需要 HH:MM 格式")
                        extra_args += ["--not-before", not_before]
            elif action not in {"send", "dry-run", "login"}:
                return self.fail("未知动作")
            elif account_id is None:
                return self.fail("缺少当前账号 ID")
            try:
                if action != "send-all":
                    selected = account_for(account_id)
                    if not selected.enabled:
                        return self.fail(f"账号 {account_id} 已停用", 409)
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            started, reason = RUNNER.start(action, account_id, extra_args)
            if not started:
                return self.fail(reason, 409)
            return self.send_json({"started": action})

        if route == "/api/action/stop":
            RUNNER.stop()
            return self.send_json({"stopped": True})

        if route == "/api/credential/import":
            try:
                _, paths = scoped_account()
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            if paths.storage_state is None:
                return self.fail("当前账号没有配置登录凭证保存位置", 400)
            try:
                storage_state = normalize_imported_cookie_state(body.get("credential"))
            except ValueError as exc:
                return self.fail(str(exc), 400)
            paths.storage_state.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            write_json(paths.storage_state, storage_state)
            return self.send_json(build_state(body.get("accountId")))

        if route == "/api/credential/clear":
            try:
                _, paths = scoped_account()
            except ConfigError as exc:
                return self.fail(str(exc), 400)
            if paths.storage_state and paths.storage_state.exists():
                paths.storage_state.unlink()
            return self.send_json(build_state(body.get("accountId")))

        self.fail("没有这个地址", 404)


def main() -> int:
    global PORT
    # 把 SIGINT 恢复成 Python 的默认处理：脚本里用 `&` 起的进程会继承 SIG_IGN，
    # 那样「停下」发出的 SIGINT 会被子进程忽略（子进程执行 exec 后只在 SIG_DFL 时才重置）。
    try:
        signal.signal(signal.SIGINT, signal.default_int_handler)
    except (ValueError, OSError):
        pass
    if not CONFIG.exists() and not REGISTRY.exists() and DESKTOP_MODE:
        _write_private(DATA_ROOT / ".env", "TASK_CONFIG=config.json\nDOUYIN_STORAGE_STATE=storage-state.json\nARTIFACTS_DIR=artifacts\n")
        write_json(CONFIG, {"task_id": "daily-streak", "timezone": "Asia/Shanghai", "friends": [], "messages": []})
    bundled_stickers = ROOT / "config" / "stickers.json"
    if DESKTOP_MODE and not (DATA_ROOT / "stickers.json").exists() and bundled_stickers.is_file():
        shutil.copy2(bundled_stickers, DATA_ROOT / "stickers.json")
    if not CONFIG.exists() and not REGISTRY.exists():
        print(f"找不到 {CONFIG}，请在项目根目录运行", file=sys.stderr)
        return 2
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    PORT = server.server_port
    server.daemon_threads = True
    print(f"抖音自动续火花 · 控制面板  http://{HOST}:{PORT}")
    print(f"LISTENING http://{HOST}:{PORT}", flush=True)
    print("按 Ctrl+C 停止")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止")
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
