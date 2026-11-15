from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
from unittest.mock import AsyncMock, MagicMock

import pytest
from playwright.async_api import expect

from app.browser import AuthenticationError, RiskControlError
from app.config import parse_task
from app.health import check_login, credential_revision, CACHE_NAME
from app.history import History, message_id
from app.models import Settings
import app.health as health_module
import app.rules as rules_module
from test_contacts_api import server, http_request, page


def payload(name="原有好友", **kwargs):
    return {"accountId": "main", "name": name, "mode": "custom", "enabled": True, "texts": ["专属问候"], "images": [], "strategy": "single", **kwargs}


def mark_attempt(server, status="unknown"):
    task, history = server.review_context(server.account_paths_for("main"))
    target = task.targets[0]
    key = history.key(task.task_id, history.run_date(task.timezone), target.name, message_id(0, target.messages[0]))
    if status == "success":
        history.mark_success(key)
    else:
        history.reserve(key)
    return key


def test_friend_rule_is_scoped_and_does_not_change_defaults(server, http_request):
    config = server.load_config()
    config["friends"].append("其他好友")
    server.write_json(server.CONFIG, config)
    status, state = http_request("/api/friends/rule", payload())
    assert status == 200
    assert state["friendRules"]["原有好友"]["mode"] == "custom"
    task, _ = server.review_context(server.account_paths_for("main"))
    assert [target.messages[0].content for target in task.targets] == ["专属问候", "默认消息"]
    assert server.load_config()["messages"] == config["messages"]
    assert http_request("/api/friends/rule", payload(name="不存在"))[0] == 400


def test_edits_after_attempt_take_effect_next_day_without_resetting_history(server, http_request, monkeypatch):
    key = mark_attempt(server)
    status, state = http_request("/api/friends/rule", payload())
    assert status == 200 and state["friendRules"]["原有好友"]["deferred"]
    assert state["dispatchPreview"][0]["message"]["texts"] == ["默认消息"]
    assert History(server.ARTIFACTS / "history.json").status(key) == "unknown"
    now = datetime.now
    class Tomorrow:
        @staticmethod
        def now(zone):
            return now(zone) + timedelta(days=1)
    monkeypatch.setattr(rules_module, "datetime", Tomorrow)
    task, _ = server.review_context(server.account_paths_for("main"))
    assert task.targets[0].messages[0].content == "专属问候"


def test_default_edits_only_freeze_friends_with_prior_attempts(server, http_request):
    config = server.load_config()
    config["friends"].append("新好友")
    server.write_json(server.CONFIG, config)
    mark_attempt(server, "success")
    status, state = http_request("/api/message", {"accountId": "main", "texts": ["新默认"], "images": []})
    assert status == 200
    assert [row["message"]["texts"] for row in state["dispatchPreview"]] == [["默认消息"], ["新默认"]]
    assert state["friendStatuses"] == ["sent", "pending"]


def test_edit_preserves_trusted_legacy_success_before_config_mtime_changes(server, http_request):
    now = datetime.now().astimezone()
    os.utime(server.CONFIG, (now.timestamp() - 600, now.timestamp() - 600))
    server.write_json(server.ARTIFACTS / "result.json", {})
    (server.ARTIFACTS / "run-history.jsonl").write_text(json.dumps({"task_id": "daily-streak", "dry_run": False, "finished_at": (now - timedelta(minutes=5)).isoformat(), "results": [{"target": "好友01", "status": "success", "sent": 1}]}) + "\n")
    status, state = http_request("/api/friends/rule", payload())
    assert status == 200 and state["friendStatuses"] == ["sent"]
    assert state["dispatchPreview"][0]["message"]["texts"] == ["默认消息"]


def test_pause_and_rename_preserve_rules_alias_order_and_delivery_history(server, http_request):
    assert http_request("/api/friends/rule", payload(enabled=False))[0] == 200
    state = server.build_state("main")
    assert state["friendStatuses"] == ["paused"] and state["pendingTargets"] == []
    assert http_request("/api/friends/rule", payload(enabled=True))[0] == 200
    mark_attempt(server, "success")
    status, state = http_request("/api/friends", {"accountId": "main", "friends": ["新备注"], "renamedFrom": "原有好友", "renamedTo": "新备注"})
    assert status == 200 and state["friendStatuses"] == ["sent"]
    assert state["friendRules"]["新备注"]["mode"] == "custom"
    assert "原有好友" not in state["friendRules"]


def test_rule_validation_is_atomic_and_uses_uploaded_images_only(server, http_request, tmp_path):
    before = server.CONFIG.read_bytes()
    assert http_request("/api/friends/rule", payload(images=["../outside.png"]))[0] == 400
    assert http_request("/api/friends/rule", payload(strategy="single", texts=["一", "二"]))[0] == 400
    assert server.CONFIG.read_bytes() == before
    images = tmp_path / "images/main"
    images.mkdir(parents=True)
    (images / "test.png").write_bytes(b"image")
    other = tmp_path / "images/second"
    other.mkdir()
    (other / "test.png").write_bytes(b"image")
    assert http_request("/api/friends/rule", payload(images=["images/second/test.png"]))[0] == 400
    status, state = http_request("/api/friends/rule", payload(texts=["一", "二"], images=["images/main/test.png"], strategy="sequence"))
    assert status == 200
    task, _ = server.review_context(server.account_paths_for("main"))
    assert [message.type for message in task.targets[0].messages] == ["text", "text", "image"]


@pytest.mark.parametrize("exception,status", [(None, "valid"), (AuthenticationError("private"), "expired"), (RiskControlError("private"), "verification_required"), (RuntimeError("private"), "check_failed")])
async def test_health_check_classifies_outcomes_without_trace_or_sending(server, monkeypatch, exception, status, capsys):
    settings = Settings(server.CONFIG, "state.json", None, False, None, server.ARTIFACTS, True)
    opened = []
    @asynccontextmanager
    async def browser(value):
        opened.append(value)
        yield MagicMock()
    monkeypatch.setattr(health_module, "open_douyin", browser)
    monkeypatch.setattr(health_module, "open_private_messages", AsyncMock(side_effect=exception))
    code = await check_login(settings, "check", "revision")
    result = server.read_json(server.ARTIFACTS / CACHE_NAME)
    assert result["status"] == status
    assert code == (0 if status == "valid" else 1)
    assert opened[0].trace is False and opened[0].headless is True
    assert "private" not in json.dumps(result) + capsys.readouterr().out
    assert not (server.ARTIFACTS / "run.lock").exists()


def test_health_cache_does_not_treat_file_presence_or_changed_credentials_as_valid(server):
    state = server.STATE_FILE
    state.write_text("{}")
    assert server.credential_state()["health"] == "unchecked"
    env = server.DATA_ROOT / ".env"
    revision = credential_revision(state, env)
    server.write_json(server.ARTIFACTS / CACHE_NAME, {"status": "valid", "revision": revision, "checkedAt": datetime.now().astimezone().isoformat()})
    assert server.credential_state()["health"] == "valid"
    state.write_text('{"changed": true}')
    assert server.credential_state()["health"] == "unchecked"


def test_linux_scheduler_detects_missing_exact_trigger(server, monkeypatch):
    monkeypatch.setattr(server.sys, "platform", "linux")
    monkeypatch.setattr(server, "current_schedule", lambda: {"present": True, "hour": 8, "minute": 5})
    monkeypatch.setattr(server.shutil, "which", lambda _: "/usr/bin/crontab")
    monkeypatch.setattr(server, "scheduled_run_command", lambda *_: ["/run", "--catch-up", "--not-before", "08:05"])
    row = f"DOUYIN_DATA_DIR={server.DATA_ROOT} /run --catch-up --not-before 08:05 # douyin-fire-local-schedule"
    output = [f"*/30 * * * * {row}"]
    monkeypatch.setattr(server.subprocess, "run", lambda *_, **__: subprocess.CompletedProcess([], 0, stdout="\n".join(output)))
    assert server.scheduler_health()["status"] == "mismatch"
    output.append(f"5 8 * * * {row}")
    assert server.scheduler_health()["status"] == "ok"


def test_health_endpoint_explains_blockers_without_mutating_task(server, http_request, monkeypatch):
    monkeypatch.setattr(server, "scheduler_health", lambda: {"status": "missing", "detail": "尚未保存时间"})
    before = server.CONFIG.read_bytes()
    status, result = http_request("/api/health?accountId=main")
    assert status == 200
    titles = [issue["title"] for issue in result["issues"]]
    assert "尚未配置登录凭证" in titles and "自动任务需要检查" in titles
    assert server.CONFIG.read_bytes() == before


def test_rule_endpoint_requires_account_and_serializes_with_scheduled_tasks(server, http_request):
    from app.history import run_lock
    body = payload()
    body.pop("accountId")
    assert http_request("/api/friends/rule", body)[0] == 400
    before = server.CONFIG.read_bytes()
    with run_lock(server.ARTIFACTS / "run.lock"):
        assert http_request("/api/friends/rule", payload())[0] == 409
        assert http_request("/api/message", {"accountId": "main", "texts": ["新默认"], "images": []})[0] == 409
    assert server.CONFIG.read_bytes() == before


async def test_all_paused_friends_do_not_open_browser(server, monkeypatch):
    import app.main as runner
    config = server.load_config()
    config["friend_rules"] = {"原有好友": {"mode": "inherit", "enabled": False}}
    server.write_json(server.CONFIG, config)
    settings = Settings(server.CONFIG, None, None, True, None, server.ARTIFACTS, False)
    monkeypatch.setattr(runner, "load_settings", lambda *_: settings)
    browser = MagicMock()
    monkeypatch.setattr(runner, "open_douyin", browser)
    assert await runner.run() == 0
    browser.assert_not_called()


async def test_sender_uses_each_friends_messages_and_skips_paused_friend(server, monkeypatch):
    from types import SimpleNamespace
    import app.main as runner
    config = server.load_config()
    config.update(friends=["甲", "乙", "丙"], send_interval_seconds={"min": 0, "max": 0}, friend_rules={
        "甲": {"mode": "custom", "enabled": True, "messages": [{"type": "rotation", "choices": [{"type": "text", "value": "甲的专属一"}, {"type": "text", "value": "甲的专属二"}]}]},
        "丙": {"mode": "inherit", "enabled": False},
    })
    server.write_json(server.CONFIG, config)
    settings = Settings(server.CONFIG, None, "[]", True, None, server.ARTIFACTS, False)
    @asynccontextmanager
    async def browser(value):
        yield SimpleNamespace(page=MagicMock(), context=MagicMock())
    monkeypatch.setattr(runner, "load_settings", lambda *_: settings)
    monkeypatch.setattr(runner, "open_douyin", browser)
    monkeypatch.setattr(runner, "open_private_messages", AsyncMock())
    monkeypatch.setattr(runner, "verify_login", AsyncMock())
    open_target = AsyncMock()
    monkeypatch.setattr(runner, "_open_target_with_retry", open_target)
    monkeypatch.setattr(runner, "send_macos_notification", AsyncMock())
    monkeypatch.setattr(runner, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(runner, "_notify_dingtalk", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(runner, "send_message", send)
    assert await runner.run() == 0
    task = parse_task(config, settings)
    expected = rules_module.rotation_choice(task.targets[0].messages[0], History(server.ARTIFACTS / "history.json").run_date(task.timezone), "甲")
    assert [call.args[2].content for call in send.await_args_list] == [expected.content, "默认消息"]
    assert [call.args[1] for call in open_target.await_args_list] == ["甲", "乙"]


def test_health_check_action_is_read_only_and_works_for_disabled_account(server, http_request, monkeypatch):
    calls = []
    monkeypatch.setattr(server.RUNNER, "start", lambda *args: (calls.append(args) or (True, "")))
    status, result = http_request("/api/action", {"action": "health-check", "accountId": "main"})
    assert status == 200 and calls == [("health-check", "main", ["--scan-id", result["scanId"]])]


def test_task_health_explains_attempt_limit_instead_of_promising_another_retry():
    state = {"accountEnabled": True, "credential": {"present": True, "health": "valid"}, "friends": ["甲"], "friendStatuses": ["pending"], "pendingTargets": ["甲"], "sendQueueReady": True,
             "now": "2026-11-13T23:00:00+08:00", "catchUp": {"date": "2026-11-13", "attempts": 3}}
    result = health_module.report(state, {"status": "ok", "detail": "已注册"})
    assert [issue["title"] for issue in result["issues"]] == ["今日自动尝试已达上限"]


@pytest.mark.parametrize("width,height", [(1320, 900), (390, 844)])
async def test_rule_editor_and_health_ui(page, server, http_request, monkeypatch, width, height):
    monkeypatch.setattr(server, "scheduler_health", lambda: {"status": "missing", "detail": "尚未保存每日运行时间"})
    async def route(request_route):
        from urllib.parse import urlsplit
        request = request_route.request
        parsed = urlsplit(request.url)
        if "/api/stream" in parsed.path:
            return await request_route.fulfill(content_type="text/event-stream", body="\n")
        if parsed.path.startswith("/api/"):
            status, body = http_request(parsed.path + ("?" + parsed.query if parsed.query else ""), request.post_data_json if request.method == "POST" else None)
            return await request_route.fulfill(status=status, json=body)
        await request_route.fulfill(content_type="text/html", body=(server.ROOT / "ui/static/index.html").read_text())
    await page.set_viewport_size({"width": width, "height": height})
    await page.route("http://rules.test/**", route)
    await page.goto("http://rules.test/#/accounts/main/friends")
    await page.locator("#welcomeDialog").evaluate("e => e.close()")
    await expect(page.locator(".nav-heading")).to_have_count(0)
    await page.get_by_role("button", name="设置 原有好友 的消息规则", exact=True).click()
    if width <= 680:
        size = await page.locator("#friendRuleDialog").evaluate("e => ({width: e.offsetWidth, viewport: innerWidth})")
        assert abs(size["width"] - size["viewport"]) < 1, size
    capture_dir = os.environ.get("DOUYIN_UI_CAPTURE_DIR")
    await expect(page.locator("#reviewResultsBtn")).to_have_count(0)
    if capture_dir:
        await page.screenshot(path=str(Path(capture_dir) / f"friend-settings-simple-{width}.png"), animations="disabled")
    await page.locator("#friendRuleMode").select_option("custom")
    await page.locator("#friendRuleTexts").fill("早上好，记得吃早饭\n今天也要开心")
    await page.locator("#friendRuleStrategy").select_option("rotation")
    await expect(page.locator("#friendHolidayEnabled")).to_be_checked()
    await expect(page.locator("#friendBirthdayDate")).to_have_count(0)
    await page.locator("#friendHolidayDetails summary").click()
    await expect(page.get_by_role("textbox", name="中秋节祝福语", exact=True)).to_have_value("中秋节快乐")
    assert await page.locator("#friendRuleDialog").evaluate("e => e.scrollWidth <= e.clientWidth")
    capture_dir = os.environ.get("DOUYIN_UI_CAPTURE_DIR")
    if capture_dir:
        await page.screenshot(path=str(Path(capture_dir) / f"friend-rule-{width}.png"), animations="disabled")
    await page.locator("#saveFriendRule").click()
    await expect(page.locator("#friendRuleDialog")).not_to_be_visible()
    await expect(page.locator("#albumGrid")).to_contain_text("专属消息")
    rule = server.load_config()["friend_rules"]["原有好友"]
    assert rule["messages"][0]["type"] == "rotation"
    assert rule["holiday_enabled"] is True
    assert "birthday" not in rule and "holidays" not in rule
    await page.goto("http://rules.test/#/accounts/main/system")
    await expect(page.locator(".health-card")).to_have_count(0)
    await expect(page.locator(".accounts-note")).to_have_count(0)
    await expect(page.locator("#checkLoginBtn")).to_be_visible()
    await expect(page.locator("#credBadge")).not_to_have_text("登录有效")
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    if capture_dir:
        await page.screenshot(path=str(Path(capture_dir) / f"task-health-{width}.png"), full_page=True, animations="disabled")
