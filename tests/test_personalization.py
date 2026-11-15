from datetime import datetime, timedelta
import json

import pytest

from app.models import Message
from unittest.mock import AsyncMock, MagicMock
from app.history import History
from app.sender import SendRejectedError, SendOutcomeUnknownError
from app.config import ConfigError
import app.main as runner
from app.rules import effective_rows, rotation_choice
import app.rules as rules
from test_contacts_api import server, http_request


def test_daily_rotation_is_stable_within_day_and_changes_next_day():
    message = Message("rotation", choices=(Message("text", "一"), Message("text", "二"), Message("text", "三")))
    first = rotation_choice(message, "2026-11-13", "好友甲")
    assert rotation_choice(message, "2026-11-13", "好友甲") == first
    assert rotation_choice(message, "2026-11-14", "好友甲") != first


def test_default_holiday_replaces_daily_messages_and_ignores_birthday(monkeypatch):
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2027, 9, 15, tzinfo=zone)
    monkeypatch.setattr(rules, "datetime", Clock)
    config = {"friends": ["甲"], "messages": [{"type": "text", "value": "日常"}], "friend_rules": {"甲": {"mode": "inherit", "birthday": {"date": "09-15", "text": "生日快乐"}}}}
    assert effective_rows(config)[0]["messages"] == [{"type": "text", "value": "中秋节快乐"}]
    config["friend_rules"]["甲"]["holiday_enabled"] = False
    assert effective_rows(config)[0]["messages"][0]["value"] == "日常"


def test_holiday_save_defaults_and_removed_birthday(server, http_request):
    body = {"accountId": "main", "name": "原有好友", "mode": "custom", "enabled": True, "texts": ["一", "二"], "images": [], "strategy": "rotation", "birthday": {"date": "02-29", "text": "生日快乐"}}
    status, state = http_request("/api/friends/rule", body)
    assert status == 200
    assert state["friendRules"]["原有好友"]["holidayEnabled"] is True
    assert "birthday" not in server.load_config()["friend_rules"]["原有好友"]
    assert "birthday" not in state["friendRules"]["原有好友"]
    body["holidayEnabled"] = False
    assert http_request("/api/friends/rule", body)[1]["friendRules"]["原有好友"]["holidayEnabled"] is False
    before = server.CONFIG.read_bytes()
    body["holidayEnabled"] = "invalid"
    assert http_request("/api/friends/rule", body)[0] == 400
    assert server.CONFIG.read_bytes() == before


def test_year_specific_lunar_holidays_do_not_repeat_in_wrong_year(monkeypatch):
    from app.holidays import greeting_for
    assert greeting_for("2027-09-15")["text"] == "中秋节快乐"
    assert greeting_for("2028-09-15") is None
    assert greeting_for("2027-02-05")["name"] == "除夕"
    assert greeting_for("2027-02-06")["name"] == "春节"
    assert greeting_for("2027-04-05") is None


def test_upcoming_calendar_has_verified_dates_only():
    from app.rules import upcoming_dates
    from datetime import date
    result = upcoming_dates({}, date(2026, 11, 13))
    assert len(result) == 11
    assert result[0]["date"] == "2026-12-22"
    assert result[-1]["date"] == "2027-10-08"
    assert upcoming_dates({}, date(2028, 1, 1)) == []


def test_removed_birthday_never_triggers_and_same_day_snapshot_wins(monkeypatch):
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2027, 9, 15, tzinfo=zone)
    monkeypatch.setattr(rules, "datetime", Clock)
    config = {"friends": ["甲"], "messages": [{"type": "text", "value": "日常"}],
              "friend_rules": {"甲": {"mode": "inherit", "birthday": {"date": "invalid", "text": "旧生日"}}},
              "daily_messages": {"date": "2027-09-15", "targets": {"甲": [{"type": "text", "value": "已尝试内容"}]}}}
    assert effective_rows(config)[0]["messages"][0]["value"] == "已尝试内容"
    config.pop("daily_messages")
    config["friend_rules"]["甲"]["holiday_enabled"] = False
    assert effective_rows(config)[0]["messages"][0]["value"] == "日常"


async def test_festival_run_sends_only_greeting_and_second_run_sends_nothing(server, monkeypatch):
    from contextlib import asynccontextmanager
    from types import SimpleNamespace
    from app.models import Settings

    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2027, 9, 15, tzinfo=zone)
    monkeypatch.setattr(rules, "datetime", Clock)
    monkeypatch.setattr(History, "run_date", lambda self, zone: "2027-09-15")
    config = server.load_config()
    config.update(friends=["默认好友", "专属好友", "暂停好友"], send_interval_seconds={"min": 0, "max": 0}, friend_rules={
        "专属好友": {"mode": "custom", "messages": [{"type": "random", "choices": [{"type": "text", "value": "旧专属消息"}]}]},
        "暂停好友": {"mode": "inherit", "enabled": False},
    })
    server.write_json(server.CONFIG, config)
    settings = Settings(server.CONFIG, None, "[]", True, None, server.ARTIFACTS, False)
    @asynccontextmanager
    async def browser(value):
        yield SimpleNamespace(page=MagicMock(), context=MagicMock())
    monkeypatch.setattr(runner, "load_settings", lambda *_: settings)
    monkeypatch.setattr(runner, "open_douyin", browser)
    for name in ("open_private_messages", "verify_login", "_open_target_with_retry", "send_macos_notification", "_notify_webhook", "_notify_dingtalk"):
        monkeypatch.setattr(runner, name, AsyncMock())
    send = AsyncMock()
    monkeypatch.setattr(runner, "send_message", send)
    assert await runner.run() == 0
    assert [call.args[2].content for call in send.await_args_list] == ["中秋节快乐", "中秋节快乐"]
    assert await runner.run() == 0
    assert send.await_count == 2


async def test_random_failed_run_and_manual_retry_keep_original_candidate(tmp_path, monkeypatch):
    message = Message("random", choices=(Message("text", "第一句"), Message("text", "第二句")))
    history = History(tmp_path / "history.json")
    monkeypatch.setattr(runner, "verify_login", AsyncMock())
    monkeypatch.setattr(runner.asyncio, "sleep", AsyncMock())
    selection = MagicMock(return_value=1)
    monkeypatch.setattr(runner.random, "randrange", selection)
    send = AsyncMock(side_effect=SendRejectedError("明确失败"))
    monkeypatch.setattr(runner, "send_message", send)
    with pytest.raises(SendRejectedError):
        await runner._send_message_with_retry(None, None, message, {}, history, "key", "好友01", 1)
    loaded = History(history.path)
    assert loaded.entries["key"]["selected_choice"] == 1
    send.side_effect = SendOutcomeUnknownError("待核对")
    with pytest.raises(SendOutcomeUnknownError):
        await runner._send_message_with_retry(None, None, message, {}, loaded, "key", "好友01", 1)
    loaded = History(history.path)
    loaded.review("key", loaded.review_token("key"), "not_sent", target="好友", message_number=1)
    send.side_effect = None
    await runner._send_message_with_retry(None, None, message, {}, loaded, "key", "好友01", 1)
    assert selection.call_count == 1
    assert send.await_count >= 3
    assert all(call.args[2].content == "第二句" for call in send.await_args_list)
    assert History(history.path).entries["key"]["selected_choice"] == 1


def test_review_shows_selected_random_and_current_rotation(server):
    from app.review import review_state, current_messages
    config = server.load_config()
    config["messages"] = [{"type": "random", "choices": [{"type": "text", "value": "甲"}, {"type": "text", "value": "乙"}]}]
    server.write_json(server.CONFIG, config)
    task, history = server.review_context(server.account_paths_for("main"))
    key, *_ = next(current_messages(task, history))
    history.reserve(key, selected_choice=1)
    assert review_state(task, history)["items"][0]["message"]["text"] == "乙"
    assert server.dispatch_message_state(task, task.targets[0], history, history.run_date(task.timezone))["texts"] == ["乙"]


def test_send_preview_shows_only_todays_rotation(server, http_request):
    body = {"accountId": "main", "name": "原有好友", "mode": "custom", "enabled": True, "texts": ["一", "二", "三"], "images": [], "strategy": "rotation"}
    status, state = http_request("/api/friends/rule", body)
    assert status == 200
    assert len(state["friendRules"]["原有好友"]["message"]["texts"]) == 3
    assert len(state["dispatchPreview"][0]["message"]["texts"]) == 1


def test_custom_holiday_is_scoped_and_snapshot_still_wins(monkeypatch):
    class Clock:
        @staticmethod
        def now(zone):
            return datetime(2027, 9, 15, tzinfo=zone)
    monkeypatch.setattr(rules, 'datetime', Clock)
    config = {'friends': ['甲', '乙'], 'messages': [{'type': 'text', 'value': '日常'}],
              'friend_rules': {'甲': {'mode': 'inherit', 'holiday_greetings': {'2027-09-15': '月圆人团圆'}}}}
    assert [row['messages'][0]['value'] for row in effective_rows(config)] == ['月圆人团圆', '中秋节快乐']
    config['daily_messages'] = {'date': '2027-09-15', 'targets': {'甲': [{'type': 'text', 'value': '已冻结'}]}}
    assert effective_rows(config)[0]['messages'][0]['value'] == '已冻结'


def test_custom_holiday_save_preserve_and_validate(server, http_request):
    body = {'accountId': 'main', 'name': '原有好友', 'mode': 'inherit', 'enabled': True,
            'holidayGreetings': {'2027-09-15': '月圆人团圆'}}
    status, state = http_request('/api/friends/rule', body)
    assert status == 200
    assert state['friendRules']['原有好友']['holidayGreetings']['2027-09-15'] == '月圆人团圆'
    assert next(row['text'] for row in state['friendRules']['原有好友']['upcoming'] if row['date'] == '2027-09-15') == '月圆人团圆'
    del body['holidayGreetings']
    assert http_request('/api/friends/rule', body)[0] == 200
    assert server.load_config()['friend_rules']['原有好友']['holiday_greetings']['2027-09-15'] == '月圆人团圆'
    before = server.CONFIG.read_bytes()
    for value in [None, [], {'2027-09-15': ''}, {'2027-09-15': ' '}, {'2027-09-15': '字' * 501}, {'2028-09-15': '日期错误'}]:
        body['holidayGreetings'] = value
        assert http_request('/api/friends/rule', body)[0] == 400
        assert server.CONFIG.read_bytes() == before
