"""Read scoped chat titles and visible streak badges; never open or send."""
from __future__ import annotations

import argparse
import asyncio
from contextlib import ExitStack
from dataclasses import replace
from datetime import datetime
import json
import os
from pathlib import Path
import secrets

from playwright.async_api import Page

from app.accounts import account_paths, load_account_records
from app.browser import AuthenticationError, RiskControlError, open_douyin, open_private_messages, verify_login
from app.config import ConfigError, load_settings
from app.history import run_lock
from app.selectors import CONVERSATION_ITEMS
from app.streaks import SOURCE as STREAK_SOURCE, valid_days

CACHE_NAME = "chat-list.json"
MAX_CONTACTS = 500
MAX_PASSES = 80

# Only scoped title nodes are read. Row text contains private message previews
# and must not be used as a fallback for missing names.
READ_LIST = r"""(selectors) => {
  const visible = e => !!(e.getClientRects().length && getComputedStyle(e).visibility !== 'hidden');
  const titleSelector = '[data-e2e="conversation-title"], [class*="conversationConversationItemtitle"], [class*="ConversationItemtitle"], [class*="ConversationItemTitle"], [class*="conversation-item-title"]';
  let rows = [];
  for (const selector of selectors) {
    rows = [...document.querySelectorAll(selector)].filter(e => visible(e) && e.querySelector(titleSelector));
    if (rows.length) break;
  }
  // Class-prefix selectors can also match child nodes. Keep actual outer rows.
  rows = rows.filter(e => !rows.some(other => other !== e && other.contains(e)));
  const contacts = rows.map(row => {
    // Prefix selectors also match titleWrapper, which contains streak days and
    // timestamps. Resolve the innermost title node, never its wrapper text.
    const title = [...row.querySelectorAll(titleSelector)].find(e => !e.querySelector(titleSelector));
    const name = (title.textContent || '').trim();
    const identity = row.getAttribute('data-conversation-id') || row.getAttribute('data-conversation-short-id') || row.getAttribute('data-id') || '';
    // Read only the known title-adjacent streak badge, never row text,
    // timestamps, message previews, or arbitrary numeric labels.
    const tags = [...row.querySelectorAll('[class*="ConversationItemTagNextToTitle"] [class*="commonStreaknormalText"]')].filter(visible);
    const label = tags.length === 1 ? (tags[0].textContent || '').trim() : '';
    const days = /^[1-9]\d{0,4}(?:\s*天)?$/.test(label) ? Number(label.replace(/\s*天$/, '')) : null;
    return {name, identity, streakDays: Number.isInteger(days) && days <= 36500 ? days : null};
  });
  let scroller = rows[0]?.parentElement;
  while (scroller && (scroller.scrollHeight <= scroller.clientHeight + 2 || !/(auto|scroll)/.test(getComputedStyle(scroller).overflowY))) scroller = scroller.parentElement;
  if (!scroller || scroller === document.body || scroller === document.documentElement) return {contacts, atEnd:true, canScroll:false};
  const atEnd = scroller.scrollTop + scroller.clientHeight >= scroller.scrollHeight - 2;
  if (!atEnd) scroller.scrollTop += Math.max(1, Math.floor(scroller.clientHeight * .75));
  return {contacts, atEnd, canScroll:true};
}"""


async def collect_contacts(page: Page, *, max_passes: int = MAX_PASSES) -> dict:
    entries: dict[tuple[str, str], dict] = {}
    ambiguous: set[str] = set()
    previous = None
    stable_end = 0
    complete = False
    for _ in range(max_passes):
        snapshot = await page.evaluate(READ_LIST, list(CONVERSATION_ITEMS))
        rows = snapshot.get("contacts", [])
        per_frame: dict[str, int] = {}
        for row in rows:
            name = str(row.get("name") or "").strip()
            if not name:
                continue
            identity = str(row.get("identity") or "")[:256]
            per_frame[name] = per_frame.get(name, 0) + 1
            key = ("id", identity) if identity else ("name", name)
            if key not in entries and len(entries) >= MAX_CONTACTS:
                return finish_contacts(entries.values(), ambiguous, False)
            old = entries.get(key)
            if old and old["name"] != name:
                ambiguous.update((old["name"], name))
            entries[key] = {"name": name, "identity": identity, "streakDays": row.get("streakDays")}
        ambiguous.update(name for name, count in per_frame.items() if count > 1)
        fingerprint = tuple((row.get("name"), row.get("identity")) for row in rows)
        if snapshot.get("atEnd") and fingerprint == previous:
            stable_end += 1
        else:
            stable_end = 0
        if stable_end >= 2:
            complete = bool(snapshot.get("canScroll"))
            break
        previous = fingerprint
        await page.wait_for_timeout(500)
    return finish_contacts(entries.values(), ambiguous, complete)


def finish_contacts(entries, ambiguous: set[str], complete: bool) -> dict:
    rows = list(entries)
    counts: dict[str, int] = {}
    for row in rows:
        counts[row["name"]] = counts.get(row["name"], 0) + 1
    contacts = []
    for name in dict.fromkeys(row["name"] for row in rows):
        reason = "同名会话，请手动核对" if name in ambiguous or counts[name] > 1 else ""
        if len(name) > 32 or any(ord(char) < 32 for char in name):
            reason = "名称格式暂不支持，请手动添加"
        contact = {"name": name, "selectable": not reason, "reason": reason}
        match = next(row for row in rows if row["name"] == name)
        if not reason and valid_days(match.get("streakDays")):
            contact.update(streakDays=match["streakDays"], streakSource=STREAK_SOURCE)
        contacts.append(contact)
    return {"contacts": contacts, "complete": complete, "scope": "chat-list"}


def merge_contacts(config: dict, cache: dict, selected: object, scan_id: object) -> tuple[dict, int]:
    if not isinstance(scan_id, str) or scan_id != cache.get("scanId") or cache.get("error"):
        raise ConfigError("聊天列表已更新或不可用，请重新同步")
    if not isinstance(selected, list) or not selected or any(not isinstance(name, str) for name in selected):
        raise ConfigError("请至少勾选一位好友")
    allowed = {row["name"] for row in cache.get("contacts", []) if row.get("selectable")}
    if any(name not in allowed for name in selected):
        raise ConfigError("所选好友不在可添加的聊天列表中，请重新同步")
    targets = config.get("targets")
    existing = [row["name"] for row in targets] if isinstance(targets, list) else list(config.get("friends") or [])
    added = [name for name in dict.fromkeys(selected) if name not in existing]
    if len(existing) + len(added) > 50:
        raise ConfigError("最多保留 50 位好友，请减少勾选数量")
    updated = dict(config)
    if isinstance(targets, list):
        defaults = config.get("messages")
        if added and not defaults:
            raise ConfigError("当前使用好友专属消息，请先设置默认消息后再同步添加")
        updated["targets"] = targets + [{"name": name, "messages": defaults} for name in added]
        updated["friend_rules"] = {**config.get("friend_rules", {}), **{name: {"mode": "inherit", "enabled": True} for name in added}}
    else:
        updated["friends"] = existing + added
    return updated, len(added)


def write_cache(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    descriptor = os.open(temporary, os.O_CREAT | os.O_TRUNC | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "w", encoding="utf-8") as output:
        json.dump(payload, output, ensure_ascii=False)
    os.chmod(temporary, 0o600)
    temporary.replace(path)


async def sync(settings, scan_id: str) -> int:
    cache_path = settings.artifacts_dir / CACHE_NAME
    payload = {"scanId": scan_id, "syncedAt": datetime.now().astimezone().isoformat(), "contacts": [], "complete": False}
    try:
        if not settings.storage_state and not settings.cookie:
            raise ConfigError("请先在「登录与记录」扫码登录或导入凭证")
        with run_lock(settings.artifacts_dir / "run.lock"):
            async with asyncio.timeout(120):
                async with open_douyin(replace(settings, headless=True, trace=False)) as session:
                    await open_private_messages(session.page)
                    await verify_login(session.page)
                    payload.update(await collect_contacts(session.page))
                    await verify_login(session.page)
        payload["syncedAt"] = datetime.now().astimezone().isoformat()
        write_cache(cache_path, payload)
        try:
            from app.streaks import remember_baselines
            remember_baselines(settings.artifacts_dir, payload)
        except (OSError, ValueError):
            pass  # The observed cache remains usable; never erase a damaged tracker.
        print(f"聊天列表同步完成，共读取 {len(payload['contacts'])} 个名称", flush=True)
        return 0
    except (AuthenticationError, RiskControlError, ConfigError) as exc:
        payload["error"] = str(exc) if isinstance(exc, (AuthenticationError, RiskControlError)) else "请检查当前账号的登录凭证和配置后重试"
    except Exception:
        payload["error"] = "聊天列表读取失败，请确认网络和登录状态后重试"
    # No exception repr, names, message previews, screenshots or trace in logs.
    payload["contacts"] = []
    write_cache(cache_path, payload)
    print(f"错误: {payload['error']}", flush=True)
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(description="只读取当前账号的聊天列表")
    parser.add_argument("--account")
    parser.add_argument("--scan-id", default=None)
    parser.add_argument("--health-check", action="store_true")
    args = parser.parse_args()
    root = Path(os.environ.get("DOUYIN_DATA_DIR", Path.cwd())).resolve()
    os.chdir(root)
    try:
        with ExitStack() as stack:
            env_file = None
            state_path = root / "storage-state.json"
            credential_env = root / ".env"
            if args.account:
                from app.account_runner import account_env, _LEGACY_ENV_KEYS
                records = load_account_records(root / "config/accounts.json") or []
                account = next((item for item in records if item.id == args.account), None)
                if account is None:
                    raise ConfigError("账号不存在")
                paths = account_paths(account, root)
                env_file = account.env_file
                state_path = paths.storage_state
                credential_env = account.env_file
                for key in _LEGACY_ENV_KEYS:
                    os.environ.pop(key, None)
                stack.enter_context(account_env(account.env_file, defaults={
                    "DOUYIN_ACCOUNT_ID": account.id,
                    "TASK_CONFIG": str(paths.task_config),
                    "ARTIFACTS_DIR": str(paths.artifacts_dir),
                }))
            settings = load_settings(env_file)
            if args.health_check:
                from app.health import check_login, credential_revision
                if not args.account:
                    from app.accounts import Account
                    state_path = account_paths(Account("main", True, Path(".env")), root).storage_state
                return asyncio.run(check_login(settings, args.scan_id or secrets.token_urlsafe(16), credential_revision(state_path, credential_env)))
            return asyncio.run(sync(settings, args.scan_id or secrets.token_urlsafe(16)))
    except KeyboardInterrupt:
        return 130
    except Exception:
        print("错误: 无法读取账号配置，请检查配置后重试", flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
