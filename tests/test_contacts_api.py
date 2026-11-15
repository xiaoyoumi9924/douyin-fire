import http.client
import importlib
import json
import os
from pathlib import Path
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import MagicMock

import pytest

from app.contacts import CACHE_NAME


@pytest.fixture
async def page():
    from playwright.async_api import async_playwright
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


@pytest.fixture
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("DOUYIN_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("DOUYIN_DESKTOP_MODE", "1")
    import ui.server
    module = importlib.reload(ui.server)
    (tmp_path / "config.json").write_text(json.dumps({"friends": ["原有好友"], "messages": [{"type": "text", "value": "默认消息"}]}))
    (tmp_path / "artifacts").mkdir()
    return module


@pytest.fixture
def http_request(server):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.daemon_threads = True
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    def send(path, body=None):
        client = http.client.HTTPConnection(*httpd.server_address, timeout=3)
        client.request("GET" if body is None else "POST", path, body=None if body is None else json.dumps(body), headers={"Content-Type": "application/json"})
        response = client.getresponse()
        result = response.status, json.loads(response.read())
        client.close()
        return result
    yield send
    httpd.shutdown()
    httpd.server_close()
    thread.join()


def save_cache(server, scan_id="scan-1"):
    server.write_json(server.ARTIFACTS / CACHE_NAME, {"scanId": scan_id, "contacts": [{"name": "小林", "selectable": True}], "complete": False})


def test_preview_does_not_write_friends_and_import_only_adds_selected(server, http_request):
    save_cache(server)
    before = server.CONFIG.read_bytes()
    status, preview = http_request("/api/friends/sync?accountId=main")
    assert status == 200 and preview["result"]["contacts"][0]["name"] == "小林"
    assert server.CONFIG.read_bytes() == before
    status, imported = http_request("/api/friends/import", {"accountId": "main", "scanId": "scan-1", "names": ["小林"]})
    assert status == 200 and imported["added"] == 1
    assert imported["state"]["friends"] == ["原有好友", "小林"]
    # Repeating an import does not duplicate or rewrite the task config.
    before = server.CONFIG.read_bytes()
    assert http_request("/api/friends/import", {"accountId": "main", "scanId": "scan-1", "names": ["小林"]})[1]["added"] == 0
    assert server.CONFIG.read_bytes() == before


def test_different_accounts_cannot_import_each_others_scan(server, http_request, tmp_path):
    save_cache(server)
    (tmp_path / "config").mkdir()
    (tmp_path / "config/accounts.json").write_text(json.dumps({"accounts": [
        {"id": "main", "env_file": ".env"}, {"id": "second", "enabled": False, "env_file": ".env.second"},
    ]}))
    (tmp_path / ".env.second").write_text("TASK_CONFIG=config/second.json\nARTIFACTS_DIR=artifacts/second\n")
    (tmp_path / "config/second.json").write_text('{"friends": [], "messages": []}')
    assert http_request("/api/friends/sync?accountId=second")[1]["result"] is None
    status, _ = http_request("/api/friends/import", {"accountId": "second", "scanId": "scan-1", "names": ["小林"]})
    assert status == 400
    assert json.loads((tmp_path / "config/second.json").read_text())["friends"] == []


def test_sync_action_is_scoped_and_cannot_send(server, http_request, monkeypatch):
    calls = []
    monkeypatch.setattr(server.RUNNER, "start", lambda *args: (calls.append(args) or (True, "")))
    status, result = http_request("/api/action", {"accountId": "main", "action": "sync-friends"})
    assert status == 200
    assert calls == [("sync-friends", "main", ["--scan-id", result["scanId"]])]
    assert http_request("/api/action", {"action": "sync-friends"})[0] == 400
    assert http_request("/api/action", {"accountId": "missing", "action": "sync-friends"})[0] == 400


def test_runner_builds_sync_command_and_clears_stale_cache(server, monkeypatch):
    save_cache(server)
    monkeypatch.setattr(server.threading.Thread, "start", lambda self: None)
    process = MagicMock()
    monkeypatch.setattr(server.subprocess, "Popen", lambda command, **kwargs: process)
    commands = []
    monkeypatch.setattr(server, "run_command", lambda *args: (commands.append(args) or ["sync-command"]))
    assert server.RUNNER.start("sync-friends", "main", ["--scan-id", "fresh"]) == (True, "")
    assert commands == [("sync-friends", "--scan-id", "fresh")]
    assert not (server.ARTIFACTS / CACHE_NAME).exists()


@pytest.mark.parametrize("width,height", [(1320, 900), (390, 844)])
async def test_ui_selection_search_ambiguous_rows_and_import(page, server, width, height):
    from playwright.async_api import expect
    state = server.build_state("main")
    state["credential"]["present"] = True
    posted = []
    contacts = [
        {"name": "原有好友", "selectable": True, "reason": ""},
        {"name": "小林", "selectable": True, "reason": ""},
        {"name": "小周", "selectable": True, "reason": ""},
        {"name": "同名", "selectable": False, "reason": "同名会话，请手动核对"},
    ]
    async def route(request_route):
        url = request_route.request.url
        if "/api/stream" in url:
            return await request_route.fulfill(status=200, content_type="text/event-stream", body="\n")
        if "/api/action" in url:
            posted.append(request_route.request.post_data_json)
            payload = {"started": "sync-friends", "scanId": "ui-scan"}
        elif "/api/friends/sync" in url:
            payload = {"running": False, "result": {"scanId": "ui-scan", "contacts": contacts, "complete": False}}
        elif "/api/friends/import" in url:
            posted.append(request_route.request.post_data_json)
            state["friends"].append("小林")
            state["friendStatuses"].append("pending")
            payload = {"state": state, "added": 1}
        elif "/api/state" in url:
            payload = state
        else:
            return await request_route.fulfill(content_type="text/html", body=(Path(server.ROOT) / "ui/static/index.html").read_text())
        await request_route.fulfill(json=payload)
    await page.set_viewport_size({"width": width, "height": height})
    await page.route("http://sync.test/**", route)
    await page.goto("http://sync.test/#/accounts/main/friends")
    await page.get_by_role("button", name="从聊天列表添加", exact=True).click()
    await expect(page.get_by_role("checkbox", name="小林", exact=True)).to_be_enabled()
    await expect(page.get_by_role("checkbox", name="同名", exact=True)).to_be_disabled()
    await expect(page.get_by_role("checkbox", name="原有好友", exact=True)).to_be_checked()
    await page.get_by_role("checkbox", name="小林", exact=True).check()
    assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth")
    assert await page.locator("#syncFriendsDialog").evaluate("e => e.scrollWidth <= e.clientWidth")
    capture_dir = os.environ.get("DOUYIN_UI_CAPTURE_DIR")
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(capture_dir) / f"chat-sync-{width}.png"))
    await page.locator("#syncFriendsSearch").fill("小周")
    await expect(page.get_by_role("checkbox", name="小林", exact=True)).to_have_count(0)
    await expect(page.locator("#importSyncFriends")).to_have_text("添加 1 位好友")
    await page.locator("#importSyncFriends").click()
    await expect(page.locator("#syncFriendsDialog")).not_to_be_visible()
    assert posted == [{"action": "sync-friends", "accountId": "main"}, {"accountId": "main", "scanId": "ui-scan", "names": ["小林"]}]


def test_credential_clear_invalidates_scan_and_busy_tasks_block_import(server, http_request, monkeypatch):
    save_cache(server)
    monkeypatch.setattr(server.RUNNER, "active", lambda: True)
    assert http_request("/api/friends/import", {"accountId": "main", "scanId": "scan-1", "names": ["小林"]})[0] == 409
    assert http_request("/api/credential/clear", {"accountId": "main"})[0] == 409
    monkeypatch.setattr(server.RUNNER, "active", lambda: False)
    assert http_request("/api/credential/clear", {"accountId": "main"})[0] == 200
    assert not (server.ARTIFACTS / CACHE_NAME).exists()


@pytest.mark.parametrize("mode", ["login", "empty", "error"])
async def test_ui_login_empty_and_failed_sync_are_recoverable(page, server, mode):
    from playwright.async_api import expect
    state = server.build_state("main")
    state["credential"]["present"] = mode != "login"
    actions = []
    async def route(request_route):
        url = request_route.request.url
        if "/api/stream" in url:
            return await request_route.fulfill(content_type="text/event-stream", body="\n")
        if "/api/state" in url:
            return await request_route.fulfill(json=state)
        if "/api/action" in url:
            actions.append(request_route.request.post_data_json)
            return await request_route.fulfill(json={"started": "sync-friends", "scanId": "test"})
        if "/api/friends/sync" in url:
            result = {"scanId": "test", "contacts": [], "complete": False}
            if mode == "error":
                result["error"] = "抖音登录状态已失效"
            return await request_route.fulfill(json={"running": False, "result": result})
        await request_route.fulfill(content_type="text/html", body=(server.ROOT / "ui/static/index.html").read_text())
    await page.route("http://sync.test/**", route)
    await page.goto("http://sync.test/#/accounts/main/friends")
    await page.locator("#welcomeDialog").evaluate("e => e.close()")
    await page.locator("#syncFriendsBtn").click()
    message = {"login": "请先登录当前抖音账户", "empty": "已读取 0 个会话", "error": "抖音登录状态已失效"}[mode]
    await expect(page.locator("#syncFriendsStatus")).to_contain_text(message)
    await expect(page.locator("#importSyncFriends")).to_be_disabled()
    await expect(page.locator("#retrySyncFriends")).to_be_enabled()
    if mode == "login":
        assert actions == []
    else:
        assert actions == [{"action": "sync-friends", "accountId": "main"}]
