#!/usr/bin/env python3
"""桌面版扫码登录助手：自动检测登录成功并保存 storage-state.json。

上游 scripts/login.py 需要回到终端按 Enter，这里改成轮询 Cookie，
扫码完成即自动保存并关闭浏览器，方便在后台/无人值守时使用。

用法（在项目根目录）：
    .venv/bin/python scripts/login_mac.py
"""

from __future__ import annotations

import asyncio
import argparse
import os
import sys
import time
from pathlib import Path

CODE_ROOT = Path(__file__).resolve().parents[1]
DATA_ROOT = Path(os.environ.get("DOUYIN_DATA_DIR", CODE_ROOT)).expanduser().resolve()
if str(CODE_ROOT) not in sys.path:
    sys.path.insert(0, str(CODE_ROOT))

from playwright.async_api import async_playwright

from app.accounts import account_paths, load_account_records
from app.config import ConfigError
from app.selectors import DOUYIN_CHAT_URL, SEARCH_INPUTS

DOUYIN_URL = "https://www.douyin.com/"
SESSION_COOKIE_NAMES = ("sessionid", "sessionid_ss", "sid_guard")
LOGIN_TIMEOUT_SECONDS = 600


async def _open_login(page) -> None:
    login = page.get_by_text("登录", exact=True)
    if await login.count():
        try:
            await login.first.click(timeout=10_000)
        except Exception:
            pass

    qr_login = page.get_by_text("扫码登录", exact=True)
    if await qr_login.count():
        try:
            await qr_login.first.click(timeout=5_000)
        except Exception:
            pass


async def _session_cookies(context) -> list[str]:
    try:
        cookies = await context.cookies()
    except Exception:
        return []
    found = []
    for cookie in cookies:
        name = str(cookie.get("name", ""))
        if name in SESSION_COOKIE_NAMES and cookie.get("value"):
            found.append(name)
    return found


async def _wait_for_login(context) -> list[str]:
    deadline = time.monotonic() + LOGIN_TIMEOUT_SECONDS
    last_report = 0.0
    while time.monotonic() < deadline:
        found = await _session_cookies(context)
        if found:
            return found
        waited = time.monotonic() - (deadline - LOGIN_TIMEOUT_SECONDS)
        if waited - last_report >= 15:
            last_report = waited
            print(f"... 等待扫码登录中（已等待 {int(waited)} 秒）", flush=True)
        await asyncio.sleep(1.5)
    return []


async def _search_box_ready(page) -> bool:
    for selector in SEARCH_INPUTS:
        try:
            await page.locator(selector).first.wait_for(state="visible", timeout=5_000)
            return True
        except Exception:
            continue
    return False


async def login(target: Path | None = None) -> int:
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            headless=False,
            args=["--start-maximized"],
        )
        context = await browser.new_context(locale="zh-CN", viewport={"width": 1440, "height": 1000})
        page = await context.new_page()

        print("正在打开抖音首页……", flush=True)
        await page.goto(DOUYIN_URL, wait_until="domcontentloaded", timeout=60_000)
        await _open_login(page)
        print("=" * 60, flush=True)
        print("请在弹出的浏览器窗口里用抖音 App 扫码登录", flush=True)
        print("登录成功后会自动保存并关闭浏览器，无需在终端操作", flush=True)
        print("=" * 60, flush=True)

        found = await _wait_for_login(context)
        if not found:
            print("错误: 等待超时，没有检测到登录 Cookie，请重新运行本脚本", flush=True)
            await browser.close()
            return 1

        print(f"检测到登录凭证: {', '.join(found)}，正在校验私信页……", flush=True)
        try:
            await page.goto(DOUYIN_CHAT_URL, wait_until="domcontentloaded", timeout=60_000)
            if await _search_box_ready(page):
                print("私信页面校验通过（已找到好友搜索框）", flush=True)
            else:
                print("警告: 私信页搜索框暂未检测到，仍会保存登录状态；若 Dry Run 失败请重新扫码", flush=True)
        except Exception as exc:  # noqa: BLE001
            print(f"警告: 私信页校验失败（{exc}），仍会保存登录状态", flush=True)

        target = target or DATA_ROOT / "storage-state.json"
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        temporary = target.with_suffix(".json.tmp")
        previous_umask = os.umask(0o077)
        try:
            await context.storage_state(path=str(temporary))
        finally:
            os.umask(previous_umask)
        os.chmod(temporary, 0o600)
        temporary.replace(target)
        print(f"登录状态已保存到: {target}", flush=True)
        await browser.close()
        return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="抖音扫码登录并保存本地 Storage State")
    parser.add_argument("--account", help="保存到多账号配置中的指定账号")
    args = parser.parse_args()
    target = None
    if args.account:
        try:
            records = load_account_records(DATA_ROOT / "config" / "accounts.json")
            if records is None:
                raise ConfigError("没有 config/accounts.json，无法选择账号")
            account = next((item for item in records if item.id == args.account), None)
            if account is None:
                raise ConfigError(f"不存在账号 {args.account}")
            if not account.enabled:
                raise ConfigError(f"账号 {args.account} 已停用")
            paths = account_paths(account, DATA_ROOT)
            if paths.storage_state is None:
                raise ConfigError("该账号未配置 DOUYIN_STORAGE_STATE 保存位置")
            target = paths.storage_state
        except ConfigError as exc:
            print(f"错误: {exc}")
            return 2
    return asyncio.run(login(target))


if __name__ == "__main__":
    raise SystemExit(main())
