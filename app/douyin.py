from __future__ import annotations

import asyncio
import re

from playwright.async_api import Locator, Page

from app.selectors import CHAT_PANEL_MARKERS, MESSAGE_INPUTS, SEARCH_INPUTS


class PageOperationError(RuntimeError):
    pass


class RecipientIdentityError(PageOperationError):
    """No send is permitted until the intended conversation is unambiguous."""


class RecipientChangedError(RecipientIdentityError):
    """Stop the run when an already-confirmed conversation changes."""


RETRY_DELAY_MS = 3_000


class DouyinChat:
    def __init__(
        self,
        page: Page,
        timeout_ms: int = 15_000,
        confirm_timeout_ms: int = 15_000,
    ) -> None:
        self.page = page
        self.timeout_ms = timeout_ms
        self.confirm_timeout_ms = confirm_timeout_ms
        self._recipient_name: str | None = None

    async def open_target(self, name: str, retries: int = 1) -> None:
        self._recipient_name = None
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                await self._open_target_once(name)
                return
            except RecipientIdentityError:
                raise
            except Exception as exc:
                last_error = exc
                if attempt < retries:
                    await self.page.wait_for_timeout(RETRY_DELAY_MS)
        if last_error is not None:
            raise last_error
        raise PageOperationError("打开聊天失败")

    async def _open_target_once(self, name: str) -> None:
        search = await first_visible(self.page, SEARCH_INPUTS, self.timeout_ms)
        await search.click()
        await search.fill("")
        await search.fill(name)
        await self.page.wait_for_timeout(1_500)

        result = await self._search_result(name)
        if result is None:
            raise PageOperationError("搜索不到目标好友")
        await result.click(force=True)
        await self._confirm_opened(name)
        self._recipient_name = name

    async def _search_result(self, name: str) -> Locator | None:
        # Search mode renders a separate SearchPanel. Its "发消息" action is the
        # correct control; clicking the hidden conversation cache does not mount
        # the composer.
        #
        # Identity must be resolved from the per-result name node, never from the
        # collection container: `[class*="SearchPanelitem"]` also matches an outer
        # `SearchPanelitems` wrapper, whose descendants would then contain the
        # target name while `.first` returns another row's button. Scope to result
        # rows and require the matched name node and its button to be visible here.
        search_items = self.page.locator('[class*="SearchPanelitembox"], [class*="SearchPanelitem-box"], [class*="SearchPanelitem_box"]')
        name_selectors = (
            '[class*="SearchPanelitemtitle"]',
            '[class*="SearchPanelitemTitle"]',
            '[class*="SearchPanelitem_title"]',
            '[class*="SearchPanelitem-title"]',
            '[class*="SearchPanelitemname"]',
            '[class*="SearchPanelitemName"]',
            '[class*="SearchPanelitem_name"]',
            '[class*="SearchPanelitem-name"]',
        )

        # Two-phase priority: an exact friend name always wins over a group whose
        # display name happens to start with it. Pass 1 scans every search row for
        # an exact name; only if none is found does pass 2 accept a group member
        # count suffix like "4161(7)" for target "4161". This ordering guarantees
        # "test" never returns "test(7)" or "test1".
        for matcher in (_visible_exact_text_locator, _visible_group_text_locator):
            matches = []
            for index in range(await search_items.count()):
                item = search_items.nth(index)
                if await matcher(item, name_selectors, name) is not None:
                    matches.append(item)
            match = await _unique_recipient(matches)
            if match is not None:
                button = match.locator('[class*="SearchPanelitemchat_btn"]').first
                if await button.count() and await button.is_visible():
                    return button
                # A matching search row without a usable action is not permission
                # to click an unrelated cached conversation behind the search.
                raise RecipientIdentityError("目标搜索结果尚不可操作，请人工确认后再试")

        # Search fallbacks require a visible title in a visible conversation row.
        # Inspect every selector variant before accepting a unique recipient.
        row_selectors = (
            '[data-e2e="conversation-item"]',
            '[class*="conversationConversationItem"]',
            '[class*="conversation-item"]',
            '[class*="ConversationItem"]',
        )
        title_selectors = (
            '[class*="conversationConversationItemtitle"]',
            '[class*="ConversationItemtitle"]',
            '[class*="ConversationItemTitle"]',
            '[class*="conversation-item-title"]',
            '[class*="conversation-item-Title"]',
        )
        for matcher in (_visible_exact_text_locator, _visible_group_text_locator):
            matches = []
            for selector in row_selectors:
                rows = self.page.locator(selector)
                for index in range(await rows.count()):
                    row = rows.nth(index)
                    if await row.is_visible() and await matcher(row, title_selectors, name) is not None:
                        matches.append(row)
            match = await _unique_recipient(matches)
            if match is not None:
                return match
        # Hidden title nodes may be a stale conversation cache. They cannot
        # establish recipient identity even if their parent remains visible.
        return None

    async def assert_recipient(self) -> None:
        if self._recipient_name is None:
            raise RecipientChangedError("尚未确认目标会话，已停止发送")
        if await self._chat_open_error(self._recipient_name) is not None:
            raise RecipientChangedError("当前会话或输入框已变化，已停止发送，请人工确认目标好友")

    async def message_input(self) -> Locator:
        return await first_visible(self.page, MESSAGE_INPUTS, self.timeout_ms)

    async def _confirm_opened(self, name: str, timeout_ms: int | None = None) -> None:
        timeout = timeout_ms if timeout_ms is not None else self.confirm_timeout_ms
        deadline = asyncio.get_running_loop().time() + timeout / 1000
        while True:
            last_error = await self._chat_open_error(name)
            if last_error is None:
                return
            if asyncio.get_running_loop().time() >= deadline:
                raise last_error
            await self.page.wait_for_timeout(500)

    async def _chat_open_error(self, name: str) -> PageOperationError | None:
        # A successful dry run must prove both that the requested conversation is
        # open and that its composer is ready. A title can render before the
        # message editor mounts, so title-only confirmation can produce a false
        # positive that fails immediately during a real send.
        title_selectors = (
            '[class*="RightPanelHeadertitle"]',
            '[class*="RightPanelHeaderTitle"]',
            '[class*="RightPanelHeader_title"]',
            '[class*="RightPanelHeader-title"]',
            '[class*="chatHeadertitle"]',
            '[class*="ChatHeaderTitle"]',
            '[class*="chatHeader_title"]',
            '[class*="ChatHeader-title"]',
            '[class*="name"]',
            '[class*="Name"]',
            '[class*="nickname"]',
            '[class*="Nickname"]',
        )
        title_confirmed = False
        for selector in CHAT_PANEL_MARKERS[:3]:
            headers = self.page.locator(selector)
            for index in range(await headers.count()):
                header = headers.nth(index)
                try:
                    if not await header.is_visible():
                        continue
                except Exception:
                    continue
                if await _header_recipient_matches(header, title_selectors, name):
                    title_confirmed = True
                    break
            if title_confirmed:
                break

        composer_visible = await self._composer_visible()
        if title_confirmed and composer_visible:
            return None
        return PageOperationError(
            "点击搜索结果后未能确认消息可发送 "
            f"（聊天标题: {'已确认' if title_confirmed else '未确认'}，"
            f"消息输入框: {'可用' if composer_visible else '未就绪'}）"
        )

    async def _composer_visible(self) -> bool:
        for selector in MESSAGE_INPUTS:
            locator = self.page.locator(selector).first
            try:
                if await locator.count() and await locator.is_visible():
                    return True
            except Exception:
                continue
        return False


async def _unique_recipient(matches: list[Locator]) -> Locator | None:
    unique: list[Locator] = []
    for candidate in matches:
        duplicate = False
        for previous in list(unique):
            handle = await previous.element_handle()
            try:
                relation = await candidate.evaluate("(node, other) => node === other ? 'same' : node.contains(other) ? 'outer' : other.contains(node) ? 'inner' : 'separate'", handle)
            finally:
                if handle is not None:
                    await handle.dispose()
            if relation in {"same", "outer"}:
                duplicate = True
                break
            if relation == "inner":
                unique.remove(previous)
        if not duplicate:
            unique.append(candidate)
    if len(unique) > 1:
        raise RecipientIdentityError("发现多个同名会话，已停止发送，请在抖音设置不同备注后重新同步")
    return unique[0] if unique else None


async def _visible_exact_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _text_equals(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _visible_group_text_locator(
    container: Locator, selectors: tuple[str, ...], expected: str
) -> Locator | None:
    # Second-phase match for group chats: accepts a trailing "(N)"/"（N）"
    # member count. Only reached after the exact pass found nothing, so a bare
    # "test" never reaches here when an exact "test" row exists.
    for selector in selectors:
        nodes = container.locator(selector)
        for index in range(await nodes.count()):
            node = nodes.nth(index)
            if await _group_name_matches(node, expected):
                try:
                    if await node.is_visible():
                        return node
                except Exception:
                    continue
    return None


async def _header_recipient_matches(header: Locator, selectors: tuple[str, ...], name: str) -> bool:
    # A known title must win over an unrelated visible nickname elsewhere in
    # the header. Generic name fallbacks apply only when primary titles are absent.
    for group in (selectors[:8], selectors[8:]):
        visible_names = set()
        for selector in group:
            nodes = header.locator(selector)
            for index in range(await nodes.count()):
                node = nodes.nth(index)
                if await node.is_visible():
                    # Prefix selectors also match title-bar SVG icons. SVG has
                    # no innerText; its <title> is an icon label, not a recipient.
                    # Keep reading rendered HTML text so hidden names stay out.
                    try:
                        text = await node.evaluate(
                            "node => node instanceof HTMLElement ? node.innerText : null",
                            timeout=500,
                        )
                    except Exception:
                        # A changing header cannot establish recipient identity.
                        return False
                    value = (text or "").strip()
                    if value:
                        visible_names.add(value)
        if visible_names:
            return all(_group_count_suffix_matches(value, name) for value in visible_names)
    return False


async def _text_equals(locator: Locator, expected: str) -> bool:
    try:
        return (await locator.inner_text(timeout=500)).strip() == expected
    except Exception:
        return False


# Matches a group chat display name: the configured target name optionally
# followed by exactly one pair of brackets containing a pure member count,
# e.g. "4161" / "4161(7)" / "4161（123）". This is an INDEPENDENT helper kept
# separate from _text_equals so the friend exact-match semantics stay strict:
# it must never let "test" match "test1" (no trailing brackets to legitimize a
# longer name). re.fullmatch anchors both ends; re.escape makes the name literal.
_GROUP_COUNT_SUFFIX_RE_TEMPLATE = r"{name}\s*[\(（]\s*\d+\s*[\)）]"


def _group_count_suffix_matches(actual: str, expected: str) -> bool:
    actual = actual.strip()
    expected = expected.strip()
    if actual == expected:
        return True
    pattern = _GROUP_COUNT_SUFFIX_RE_TEMPLATE.format(name=re.escape(expected))
    return re.fullmatch(pattern, actual) is not None


async def _group_name_matches(locator: Locator, expected: str) -> bool:
    try:
        return _group_count_suffix_matches(
            await locator.inner_text(timeout=500), expected
        )
    except Exception:
        return False


async def first_visible(page: Page, selectors: tuple[str, ...], timeout_ms: int = 15_000) -> Locator:
    per_selector = max(500, timeout_ms // max(1, len(selectors)))
    for selector in selectors:
        locator = page.locator(selector).first
        try:
            await locator.wait_for(state="visible", timeout=per_selector)
            return locator
        except Exception:
            continue
    raise PageOperationError(f"找不到页面元素，已尝试: {', '.join(selectors)}")
