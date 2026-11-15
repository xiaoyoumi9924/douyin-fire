from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.config import ConfigError
from app.history import History, message_id
from app.models import Message, Settings, Target, TaskConfig
from app.review import find_review, review_state
import app.main as runner


@pytest.fixture
def context(tmp_path):
    settings = Settings(tmp_path / "config.json", None, "[]", True, None, tmp_path / "artifacts", False)
    settings.task_config_path.write_text("{}")
    settings.artifacts_dir.mkdir()
    task = TaskConfig("test", "Asia/Shanghai", (
        Target("好友甲", (Message("text", "消息一"), Message("text", "消息二"))),
        Target("好友乙", (Message("text", "其他好友消息"),)),
    ), {}, 0, 0, True)
    history = History(settings.artifacts_dir / "history.json")
    key = history.key(task.task_id, history.run_date(task.timezone), task.targets[0].name, message_id(0, task.targets[0].messages[0]))
    history.reserve(key)
    return settings, task, history, key


def test_manual_sent_blocks_duplicate_and_keeps_audit(context):
    settings, task, history, key = context
    token = history.review_token(key)
    history.review(key, token, "sent", target="好友甲", message_number=1)
    loaded = History(history.path)
    assert loaded.status(key) == "success"
    assert loaded.contains(key)
    assert loaded.entries[key]["reviews"][0]["action"] == "sent"
    assert review_state(task, loaded)["items"] == []
    with pytest.raises(ValueError):
        loaded.review(key, token, "not_sent", target="好友甲", message_number=1)


def test_manual_not_sent_stays_blocked_and_invalidates_old_token(context):
    settings, task, history, key = context
    old_token = history.review_token(key)
    history.review(key, old_token, "not_sent", target="好友甲", message_number=1)
    loaded = History(history.path)
    assert loaded.status(key) == "unknown" and loaded.contains(key)
    assert loaded.target_status([key]) == "unknown"
    item = review_state(task, loaded)["items"][0]
    assert item["decision"] == "not_sent" and item["id"] != old_token
    with pytest.raises(ValueError):
        find_review(task, loaded, old_token)


def test_changed_config_and_next_day_cannot_reuse_token(context, monkeypatch):
    settings, task, history, key = context
    token = history.review_token(key)
    changed = TaskConfig("test", "Asia/Shanghai", (Target("好友甲", (Message("text", "修改后的消息"),)),), {}, 0, 0, True)
    with pytest.raises(ValueError):
        find_review(changed, history, token)
    assert review_state(changed, history)["unmapped"] == 1
    monkeypatch.setattr(history, "run_date", lambda zone: "2099-01-01")
    with pytest.raises(ValueError):
        find_review(task, history, token)


def test_review_token_is_bound_to_account_history_path(context, tmp_path):
    settings, task, history, key = context
    second = History(tmp_path / "second/history.json")
    second.entries = dict(history.entries)
    assert second.review_token(key) != history.review_token(key)


async def test_scoped_retry_sends_exactly_one_message_and_never_replays(context, monkeypatch):
    settings, task, history, key = context
    history.review(key, history.review_token(key), "not_sent", target="好友甲", message_number=1)
    token = history.review_token(key)
    opened = []
    page = MagicMock()
    @asynccontextmanager
    async def browser(value):
        opened.append(value)
        yield SimpleNamespace(page=page, context=MagicMock())
    monkeypatch.setattr(runner, "load_settings", lambda env=None: settings)
    monkeypatch.setattr(runner, "load_task", lambda value: task)
    monkeypatch.setattr(runner, "open_douyin", browser)
    monkeypatch.setattr(runner, "open_private_messages", AsyncMock())
    monkeypatch.setattr(runner, "verify_login", AsyncMock())
    monkeypatch.setattr(runner, "_open_target_with_retry", AsyncMock())
    monkeypatch.setattr(runner, "_configure_logging", lambda *args: None)
    monkeypatch.setattr(runner, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(runner, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(runner, "send_macos_notification", AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(runner, "send_message", send)
    assert await runner.run(review_message=token) == 0
    assert send.await_count == 1
    assert send.await_args.args[2] == task.targets[0].messages[0]
    runner._open_target_with_retry.assert_awaited_once()
    assert runner._open_target_with_retry.await_args.args[1] == "好友甲"
    loaded = History(history.path)
    assert loaded.status(key) == "success"
    assert [event["action"] for event in loaded.entries[key]["reviews"]] == ["not_sent", "retry"]
    # A message-scoped run cannot migrate success onto unattempted messages.
    loaded.merge_legacy_run_history(settings.artifacts_dir / "run-history.jsonl", task.task_id, loaded.run_date(task.timezone), task.timezone, task.targets, config_path=settings.task_config_path)
    assert len(loaded.entries) == 1
    with pytest.raises(ConfigError):
        await runner.run(review_message=token)
    assert len(opened) == 1


async def test_retry_without_manual_not_sent_confirmation_never_opens_browser(context, monkeypatch):
    settings, task, history, key = context
    monkeypatch.setattr(runner, "load_settings", lambda env=None: settings)
    monkeypatch.setattr(runner, "load_task", lambda value: task)
    monkeypatch.setattr(runner, "_configure_logging", lambda *args: None)
    browser = MagicMock()
    monkeypatch.setattr(runner, "open_douyin", browser)
    with pytest.raises(ConfigError, match="先人工确认"):
        await runner.run(review_message=history.review_token(key))
    browser.assert_not_called()


async def test_normal_run_does_not_send_a_manually_confirmed_unsent_message(context, monkeypatch):
    from dataclasses import replace
    settings, task, history, key = context
    task = replace(task, targets=(task.targets[0],))
    history.review(key, history.review_token(key), "not_sent", target="好友甲", message_number=1)
    monkeypatch.setattr(runner, "load_settings", lambda env=None: settings)
    monkeypatch.setattr(runner, "load_task", lambda value: task)
    monkeypatch.setattr(runner, "_configure_logging", lambda *args: None)
    monkeypatch.setattr(runner, "_notify_dingtalk", AsyncMock())
    monkeypatch.setattr(runner, "_notify_webhook", AsyncMock())
    monkeypatch.setattr(runner, "send_macos_notification", AsyncMock())
    browser = MagicMock()
    monkeypatch.setattr(runner, "open_douyin", browser)
    assert await runner.run() == 1
    browser.assert_not_called()
    assert History(history.path).status(key) == "unknown"


async def test_uncertain_retry_requires_a_new_manual_decision(context, monkeypatch):
    from app.sender import SendOutcomeUnknownError
    settings, task, history, key = context
    history.review(key, history.review_token(key), "not_sent", target="好友甲", message_number=1)
    token = history.review_token(key)
    monkeypatch.setattr(runner, "verify_login", AsyncMock())
    monkeypatch.setattr(runner, "send_message", AsyncMock(side_effect=SendOutcomeUnknownError("结果不明")))
    with pytest.raises(SendOutcomeUnknownError):
        await runner._send_message_with_retry(MagicMock(), MagicMock(), task.targets[0].messages[0], {}, history, key, "好友01", 1)
    loaded = History(history.path)
    assert loaded.status(key) == "unknown"
    assert loaded.entries[key].get("review_decision") is None
    assert loaded.review_token(key) != token
