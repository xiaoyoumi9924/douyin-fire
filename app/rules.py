"""Friend overrides, inherited defaults, and same-day message snapshots."""
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from zoneinfo import ZoneInfo
import json
import hashlib
from datetime import date

from app.config import ConfigError
from app.history import message_id
from app.holidays import greeting_for, calendar_from, HOLIDAYS


def names(config):
    targets = config.get("targets")
    if isinstance(targets, list) and any(not isinstance(row, dict) or not isinstance(row.get("name"), str) for row in targets):
        raise ConfigError("targets 中的好友格式不正确")
    return [row["name"] for row in targets] if isinstance(targets, list) else list(config.get("friends") or [])


def rule_for(config, name):
    rules = config.get("friend_rules", {})
    if not isinstance(rules, dict):
        raise ConfigError("好友消息规则格式不正确")
    explicit = rules.get(name)
    if explicit is not None:
        if not isinstance(explicit, dict):
            raise ConfigError("好友消息规则格式不正确")
        return deepcopy(explicit)
    legacy = next((row for row in (config.get("targets") or []) if row.get("name") == name), None)
    if legacy:
        return {"mode": "custom", "enabled": legacy.get("enabled", True), "messages": deepcopy(legacy.get("messages", []))}
    return {"mode": "inherit", "enabled": True}


def preferred_messages(config):
    preference = config.get("message_preference", "random")
    if not isinstance(preference, str) or preference not in {"random", "text_first", "image_first"}:
        raise ConfigError("默认发送规则格式不正确")
    messages = config.get("messages", [])
    if preference == "random":
        return messages
    choices = messages[0].get("choices", []) if len(messages) == 1 and messages[0].get("type") == "random" else messages
    if any(row.get("type") not in {"text", "image"} for row in choices):
        raise ConfigError("优先发送规则仅支持文字和图片候选")
    selected = [row for row in choices if row.get("type") == ("text" if preference == "text_first" else "image")]
    selected = selected or choices
    return [{"type": "random", "choices": selected}] if len(selected) > 1 else selected


def effective_rows(config):
    today = datetime.now(ZoneInfo(config.get("timezone", "Asia/Shanghai"))).date().isoformat()
    frozen = config.get("daily_messages", {})
    if not isinstance(frozen, dict):
        raise ConfigError("当日消息快照格式不正确")
    frozen = frozen.get("targets", {}) if frozen.get("date") == today else {}
    rows = []
    for name in names(config):
        rule = rule_for(config, name)
        validate_holidays(rule)
        if rule.get("mode") not in {"inherit", "custom"} or not isinstance(rule.get("enabled", True), bool):
            raise ConfigError("好友消息规则格式不正确")
        messages = rule.get("messages", []) if rule["mode"] == "custom" else preferred_messages(config)
        special = greeting_for(today) if rule.get("holiday_enabled", True) else None
        if special:
            messages = [{"type": "text", "value": rule.get("holiday_greetings", {}).get(today, special["text"])}]
        rows.append({"name": name, "messages": frozen.get(name, messages), "enabled": rule.get("enabled", True)})
    return rows


def validate_holidays(rule):
    # Legacy birthday/custom-date fields are ignored, including invalid dates.
    if not isinstance(rule.get("holiday_enabled", True), bool):
        raise ConfigError("节日祝福开关格式不正确")
    greetings = rule.get("holiday_greetings", {})
    dates = {row[0] for row in HOLIDAYS}
    if not isinstance(greetings, dict) or any(
        when not in dates or not isinstance(text, str) or not text.strip() or len(text) > 500
        for when, text in greetings.items()
    ):
        raise ConfigError("节日祝福须为内置节日的 1–500 字非空文字")


def upcoming_dates(rule, today):
    validate_holidays(rule)
    return [{**row, "text": rule.get("holiday_greetings", {}).get(row["date"], row["text"])}
            for row in calendar_from(today)] if rule.get("holiday_enabled", True) else []


def rotation_choice(message, run_date, target):
    if message.type != "rotation":
        return message
    offset = int.from_bytes(hashlib.sha256(target.encode("utf-8")).digest()[:8], "big")
    return message.choices[(date.fromisoformat(run_date).toordinal() + offset) % len(message.choices)]


def preserve_today(old_task, history, updated):
    """After an attempt, message edits apply tomorrow; pausing is immediate."""
    result = deepcopy(updated)
    today = history.run_date(old_task.timezone)
    frozen = result.get("daily_messages", {})
    snapshots = dict(frozen.get("targets", {})) if frozen.get("date") == today else {}
    new_names = set(names(updated))
    for target in old_task.targets:
        if target.name not in new_names:
            continue
        keys = [history.key(old_task.task_id, today, target.name, message_id(index, message)) for index, message in enumerate(target.messages)]
        if any(history.status(key) is not None for key in keys):
            snapshots[target.name] = [json.loads(json.dumps(asdict(message), default=str)) for message in target.messages]
    snapshots = {name: messages for name, messages in snapshots.items() if name in new_names}
    if snapshots:
        result["daily_messages"] = {"date": today, "targets": snapshots}
    else:
        result.pop("daily_messages", None)
    return result
