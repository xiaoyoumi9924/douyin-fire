"""Manual decisions for uncertain deliveries; no browser actions here."""
from pathlib import Path

from app.history import History, message_id


def message_preview(message) -> dict:
    if message.type == "text":
        return {"type": "text", "text": message.content}
    if message.type == "image":
        return {"type": "image", "text": f"图片：{Path(message.path).name}"}
    if message.type in {"random", "rotation"}:
        return {"type": "random", "text": "按日期轮换的候选，请以聊天记录为准" if message.type == "rotation" else "随机候选，原先实际选择未记录", "choices": [message_preview(item) for item in message.choices[:20]]}
    return {"type": "sticker", "text": f"抖音表情：{message.sticker}"}


def current_messages(task, history: History):
    today = history.run_date(task.timezone)
    for target_index, target in enumerate(task.targets):
        for index, message in enumerate(target.messages):
            key = history.key(task.task_id, today, target.name, message_id(index, message))
            yield key, target_index, index, target, message


def review_state(task, history: History) -> dict:
    items = []
    mapped = set()
    for key, _, index, target, message in current_messages(task, history):
        mapped.add(key)
        if history.status(key) != "unknown":
            continue
        entry = history.entries.get(key)
        entry = entry if isinstance(entry, dict) else {}
        selected = entry.get("selected_choice")
        if message.type in {"random", "rotation"} and type(selected) is int and 0 <= selected < len(message.choices):
            message = message.choices[selected]
        elif message.type == "rotation":
            from app.rules import rotation_choice
            message = rotation_choice(message, history.run_date(task.timezone), target.name)
        items.append({
            "id": history.review_token(key), "target": target.name, "messageNumber": index + 1,
            "recordedAt": entry.get("started_at") or entry.get("finished_at"),
            "decision": entry.get("review_decision"), "message": message_preview(message),
            "legacy": bool(entry.get("migrated_from_legacy")),
            "paused": not target.enabled,
        })
    audit = []
    for key, entry in history.entries.items():
        if not isinstance(entry, dict):
            continue
        context = {}
        for event in entry.get("reviews", []):
            if "target" in event:
                context = {"target": event["target"], "messageNumber": event["messageNumber"]}
            audit.append({**context, **event, "currentStatus": history.status(key)})
    audit.sort(key=lambda row: row.get("at", ""), reverse=True)
    prefix = f"{task.task_id}:{history.run_date(task.timezone)}:"
    unmapped = sum(key.startswith(prefix) and key not in mapped and history.status(key) == "unknown" for key in history.entries)
    return {"date": history.run_date(task.timezone), "items": items, "audit": audit[:30], "unmapped": unmapped}


def find_review(task, history: History, token: object):
    if not isinstance(token, str):
        raise ValueError("缺少核对记录，请刷新后重试")
    for key, target_index, index, target, message in current_messages(task, history):
        if history.status(key) == "unknown" and history.review_token(key) == token:
            return key, target_index, index, target, message
    raise ValueError("记录已变化、配置已更新或已跨天，请刷新后重新核对")
