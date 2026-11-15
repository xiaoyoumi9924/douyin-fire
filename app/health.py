"""Local credential checks and non-sending task diagnostics."""
import asyncio
from dataclasses import replace
from datetime import datetime
import hashlib
from pathlib import Path

from app.browser import AuthenticationError, RiskControlError, open_douyin, open_private_messages
from app.history import run_lock
from app.contacts import write_cache

CACHE_NAME = "login-health.json"


def credential_revision(state_path, env_path):
    digest = hashlib.sha256()
    for path in (state_path, env_path):
        if path:
            path = Path(path)
            digest.update(str(path.resolve()).encode())
            try:
                digest.update(path.read_bytes())
            except FileNotFoundError:
                digest.update(b"missing")
    return digest.hexdigest()


async def check_login(settings, check_id, revision):
    payload = {"checkId": check_id, "revision": revision, "checkedAt": datetime.now().astimezone().isoformat()}
    try:
        if not settings.storage_state and not settings.cookie:
            payload.update(status="missing", detail="请先扫码登录或导入凭证")
        else:
            with run_lock(settings.artifacts_dir / "run.lock"):
                async with asyncio.timeout(90):
                    async with open_douyin(replace(settings, headless=True, trace=False)) as session:
                        await open_private_messages(session.page)
            payload.update(status="valid", detail="本次检查可以打开抖音私信页")
    except AuthenticationError:
        payload.update(status="expired", detail="抖音显示需要重新登录，请扫码更新凭证")
    except RiskControlError:
        payload.update(status="verification_required", detail="抖音要求安全验证，请在抖音中完成验证")
    except Exception:
        payload.update(status="check_failed", detail="本次检查未完成，请检查网络后重试；不能据此认定登录已失效")
    payload["checkedAt"] = datetime.now().astimezone().isoformat()
    write_cache(settings.artifacts_dir / CACHE_NAME, payload)
    print(f"登录检查完成：{payload['status']}", flush=True)
    return 0 if payload["status"] == "valid" else 1


def report(state, scheduler):
    issues = []
    def add(title, detail, action):
        issues.append({"title": title, "detail": detail, "action": action})
    credential = state.get("credential", {})
    if not state.get("accountEnabled", True):
        add("账户已停用", "自动任务会跳过这个账户", "accounts")
    if not credential.get("present"):
        add("尚未配置登录凭证", "先扫码登录或导入 Cookie，再进行登录检查", "login")
    elif credential.get("health") in {"expired", "verification_required"}:
        add("登录需要处理", credential.get("detail", "请重新登录"), "login")
    elif credential.get("health") != "valid":
        add("登录状态尚未确认", credential.get("detail") or "点击检查登录，实际打开私信页验证；凭证文件存在不代表登录有效", "check")
    if not state.get("friends"):
        add("还没有续火好友", "前往好友管理，同步聊天列表或手动添加", "friends")
    elif state.get("friendStatuses") and all(value == "paused" for value in state["friendStatuses"]):
        add("所有好友都已暂停", "在好友的消息规则中恢复自动发送", "friends")
    if not state.get("sendQueueReady"):
        add("发送配置尚未就绪", "检查好友、默认消息及专属消息配置", "messages")
    if state.get("unknownTargets"):
        add("有发送结果待核对", f"{len(state['unknownTargets'])} 位好友仍被保护性跳过；请先核对抖音聊天记录", "review")
    if "failed" in state.get("friendStatuses", []):
        add("今天有明确失败", "查看运行详情；程序只会对允许重试的消息补跑", "history")
    if scheduler["status"] != "ok":
        add("自动任务需要检查", scheduler["detail"], "tasks")
    pending = len(state.get("pendingTargets", []))
    attempts = state.get("catchUp", {})
    now = datetime.fromisoformat(state["now"]) if state.get("now") else datetime.now().astimezone()
    if pending and attempts.get("date") == now.date().isoformat() and isinstance(attempts.get("attempts"), int) and attempts["attempts"] >= 3:
        add("今日自动尝试已达上限", "今天最多自动尝试 3 次；请先查看失败原因，处理后可手动运行", "history")
    if not issues and pending:
        schedule = state.get("schedule", {})
        before = (now.hour, now.minute) < (schedule.get("hour", 0), schedule.get("minute", 0))
        add("尚未到设定时间" if before else "等待下一次补跑检查", f"还有 {pending} 位好友待发送。" + (f"下次执行：{schedule.get('next', '未设置')}。" if before else "已过设定时间，将按系统任务的补跑机制检查。") + "电脑需要开机并可联网", "tasks")
    return {"issues": issues, "scheduler": scheduler, "ready": not issues,
            "summary": task_summary(state, scheduler, issues),
            "checkedAt": datetime.now().astimezone().isoformat()}


def task_summary(state, scheduler, issues):
    """Describe observed delivery state; registration never proves execution."""
    statuses = state.get("friendStatuses", [])
    pending = len(state.get("pendingTargets", []))
    running = state.get("running")
    if running:
        sending = running.get("action") in {"send", "send-all", "review-retry"}
        return {"status": "running", "title": "正在发送" if sending else "正在检查", "detail": "请等待当前任务结束，结果会自动更新。", "action": "history"}
    if state.get("unknownTargets") or "unknown" in statuses:
        return {"status": "attention", "title": "有发送结果待核对", "detail": "请先核对抖音聊天记录，未确认的消息不会自动重发。", "action": "review"}
    if "failed" in statuses:
        return {"status": "attention", "title": "今天有发送失败", "detail": "查看失败原因，处理后再决定是否补跑。", "action": "history"}
    if state.get("allTargetsComplete") and state.get("friends"):
        return {"status": "complete", "title": "今日发送已完成", "detail": "当前启用好友的今日消息均已确认发送。", "action": "history"}
    blockers = [issue for issue in issues if issue["title"] not in {"尚未到设定时间", "等待下一次补跑检查"}]
    if blockers:
        return {"status": "attention", "title": blockers[0]["title"], "detail": blockers[0]["detail"], "action": blockers[0]["action"]}
    if pending:
        schedule = state.get("schedule", {})
        now = datetime.fromisoformat(state["now"]) if state.get("now") else datetime.now().astimezone()
        before = (now.hour, now.minute) < (schedule.get("hour", 0), schedule.get("minute", 0))
        return {"status": "waiting" if before else "pending", "title": "等待设定时间" if before else "今天还有消息未完成",
                "detail": f"还有 {pending} 位好友待发送。" + (f"下次执行：{schedule.get('next', '未设置')}。" if before else "定时已注册不代表已经执行，请查看最近记录；电脑需要开机并联网。"), "action": "tasks"}
    return {"status": "idle", "title": "暂无可确认的今日发送结果", "detail": "查看好友进度和运行记录，确认当前任务状态。", "action": "history"}
