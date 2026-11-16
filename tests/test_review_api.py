import json
from pathlib import Path
import os

import pytest
from playwright.async_api import expect

from app.history import run_lock
from test_contacts_api import page, server, http_request


def seed(server):
    task, history = server.review_context(server.account_paths_for("main"))
    from app.review import current_messages
    key, *_ = next(current_messages(task, history))
    history.reserve(key)
    return history.review_token(key)


def test_resolution_does_not_send_and_rejects_duplicate(server, http_request, monkeypatch):
    token = seed(server)
    monkeypatch.setattr(server.RUNNER, "start", lambda *args: pytest.fail("Resolving must not start a task"))
    assert http_request("/api/review?accountId=main")[1]["items"][0]["id"] == token
    status, response = http_request("/api/review/resolve", {"accountId": "main", "id": token, "decision": "sent"})
    assert status == 200 and response["state"]["friendStatuses"] == ["sent"]
    assert response["review"]["items"] == []
    assert response["review"]["audit"][0]["action"] == "sent"
    assert http_request("/api/review/resolve", {"accountId": "main", "id": token, "decision": "not_sent"})[0] == 409


def test_not_sent_remains_held_and_retry_is_separate_and_scoped(server, http_request, monkeypatch):
    token = seed(server)
    assert http_request("/api/review/retry", {"accountId": "main", "id": token})[0] == 409
    status, response = http_request("/api/review/resolve", {"accountId": "main", "id": token, "decision": "not_sent"})
    assert status == 200
    assert response["state"]["pendingTargets"] == [] and response["state"]["unknownTargets"] == ["原有好友"]
    fresh = response["review"]["items"][0]["id"]
    calls = []
    monkeypatch.setattr(server.RUNNER, "start", lambda *args: (calls.append(args) or (True, "")))
    assert http_request("/api/review/retry", {"accountId": "main", "id": fresh})[0] == 200
    assert calls == [("review-retry", "main", ["--review-message", fresh])]


def test_review_rejects_active_process_and_cross_account_tokens(server, http_request, monkeypatch, tmp_path):
    token = seed(server)
    with run_lock(server.ARTIFACTS / "run.lock"):
        assert http_request("/api/review/resolve", {"accountId": "main", "id": token, "decision": "sent"})[0] == 409
    (tmp_path / "config").mkdir()
    (tmp_path / "config/accounts.json").write_text(json.dumps({"accounts": [{"id": "main", "env_file": ".env"}, {"id": "second", "env_file": ".env.second"}]}))
    (tmp_path / ".env.second").write_text("TASK_CONFIG=config/second.json\nARTIFACTS_DIR=artifacts/second\n")
    (tmp_path / "config/second.json").write_text(server.CONFIG.read_text())
    assert http_request("/api/review/resolve", {"accountId": "second", "id": token, "decision": "sent"})[0] == 409
    assert http_request("/api/review/resolve", {"id": token, "decision": "sent"})[0] == 400


@pytest.mark.parametrize("width,height", [(1320, 900), (390, 844)])
@pytest.mark.parametrize("outcome", ["success", "unknown"])
async def test_ui_not_sent_retries_once_and_reports_outcome(page, server, width, height, outcome):
    token = seed(server)
    state = server.build_state("main")
    data = server.review_state(*server.review_context(server.account_paths_for("main")))
    posted = []
    async def route(request_route):
        nonlocal data, state
        url = request_route.request.url
        if "/api/stream" in url:
            return await request_route.fulfill(content_type="text/event-stream", body="\n")
        if "/api/state" in url:
            return await request_route.fulfill(json=state)
        if "/api/review/resolve" in url:
            body = request_route.request.post_data_json
            posted.append(body)
            data["items"][0]["id"] = "new-token"
            data["items"][0]["decision"] = "not_sent"
            return await request_route.fulfill(json={"review": data, "state": state})
        if "/api/review/retry" in url:
            posted.append(request_route.request.post_data_json)
            if outcome == "success":
                data["items"] = []
            else:
                data["items"][0]["decision"] = None
                data["items"][0]["id"] = "after-retry"
            data["audit"] = [{"action": "retry", "target": "原有好友", "messageNumber": 1, "outcome": outcome}]
            return await request_route.fulfill(json={"started": "review-retry"})
        if "/api/review" in url:
            return await request_route.fulfill(json=data)
        await request_route.fulfill(content_type="text/html", body=(server.ROOT / "ui/static/index.html").read_text())
    await page.set_viewport_size({"width": width, "height": height})
    await page.route("http://review.test/**", route)
    await page.goto("http://review.test/#/accounts/main/friends")
    await page.locator("#welcomeDialog").evaluate("e => e.close()")
    await page.get_by_role("button", name="核对结果", exact=True).click()
    await expect(page.get_by_text("人工处理记录", exact=True)).to_have_count(0)
    await page.get_by_role("button", name="未发送，补发", exact=True).click()
    assert posted[0]["decision"] == "not_sent"
    assert await page.locator("#reviewDialog").evaluate("e => e.scrollWidth <= e.clientWidth")
    capture_dir = os.environ.get("DOUYIN_UI_CAPTURE_DIR")
    if capture_dir:
        Path(capture_dir).mkdir(parents=True, exist_ok=True)
        await page.screenshot(path=str(Path(capture_dir) / f"review-{width}.png"))
    await expect(page.locator("#reviewDialog")).to_be_visible()
    await expect(page.locator("#reviewStatus")).to_contain_text("已成功补发" if outcome == "success" else "尚未确认送达")
    assert len(posted) == 2
    assert posted[-1] == {"accountId": "main", "id": "new-token"}
