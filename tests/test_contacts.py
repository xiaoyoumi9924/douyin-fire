from contextlib import asynccontextmanager
import json
import os
from unittest.mock import AsyncMock

import pytest
from playwright.async_api import async_playwright

from app import contacts
from app.config import ConfigError
from app.history import run_lock
from app.models import Settings


@pytest.fixture
def settings(tmp_path):
    return Settings(tmp_path / "config.json", "state.json", None, False, None, tmp_path / "artifacts", True)


@pytest.fixture
async def page():
    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch()
        page = await browser.new_page()
        yield page
        await browser.close()


async def test_dom_reads_only_scoped_titles_without_messages(page):
    await page.set_content('''
      <div data-e2e="conversation-item" data-conversation-id="1">
        <span data-e2e="conversation-title">  小林 </span><p>PRIVATE MESSAGE NOT A NAME</p>
      </div>
      <div data-e2e="conversation-item"><p>UNSUPPORTED PREVIEW</p></div>
      <div data-e2e="conversation-item" style="display:none"><span data-e2e="conversation-title">Hidden</span></div>
    ''')
    result = await contacts.collect_contacts(page)
    assert result["contacts"] == [{"name": "小林", "selectable": True, "reason": ""}]
    assert "PRIVATE" not in json.dumps(result)
    assert result["complete"] is False


async def test_virtual_list_collects_recycled_rows_and_blocks_same_names(page):
    await page.set_content('''
      <div id="scroll" style="height:100px;overflow-y:auto;position:relative">
        <div style="height:400px"></div><div id="rows" style="position:absolute;top:0"></div>
      </div>
      <script>
      const box = document.querySelector('#scroll');
      function draw() {
        const names = box.scrollTop < 100 ? [['1','小林'],['2','同名']] : [['3','小周'],['4','同名']];
        const rows = document.querySelector('#rows'); rows.style.top = box.scrollTop + 'px';
        rows.innerHTML = names.map(([id,name])=>`<div data-e2e="conversation-item" data-conversation-id="${id}"><span data-e2e="conversation-title">${name}</span></div>`).join('');
      }
      box.addEventListener('scroll', draw); draw();
      </script>
    ''')
    result = await contacts.collect_contacts(page)
    assert [row["name"] for row in result["contacts"]] == ["小林", "同名", "小周"]
    assert result["contacts"][1]["selectable"] is False
    assert result["complete"] is True


async def test_same_name_without_ids_is_not_selectable(page):
    await page.set_content(''.join('<div class="conversationConversationItem"><span class="conversationConversationItemtitle">同名</span></div>' for _ in range(2)))
    result = await contacts.collect_contacts(page)
    assert len(result["contacts"]) == 1
    assert result["contacts"][0]["selectable"] is False


async def test_actual_title_wrapper_excludes_streak_days_and_chat_time(page):
    await page.set_content('''
      <div class="conversationConversationItemwrapper">
        <div class="conversationConversationItemtitleWrapper">
          <div class="conversationConversationItemtitle">小林</div>
          <div class="ConversationItemTagNextToTitlewrapper">
            <div class="commonStreaknormalText">457</div>
            <div class="ConversationItemTagNextToTitletimeStr">40分钟前</div>
          </div>
        </div><p>PRIVATE MESSAGE PREVIEW</p>
      </div>
    ''')
    result = await contacts.collect_contacts(page)
    assert result["contacts"] == [{"name": "小林", "selectable": True, "reason": "", "streakDays": 457, "streakSource": "chat-list-dom-v1"}]


async def test_collection_stops_at_pass_limit_and_marks_partial():
    page = AsyncMock()
    page.evaluate.return_value = {"contacts": [{"name": "小林", "identity": "1"}], "atEnd": False}
    result = await contacts.collect_contacts(page, max_passes=3)
    assert page.evaluate.await_count == 3
    assert result["complete"] is False


def cache(names=("小林", "小周")):
    return {"scanId": "scan-1", "contacts": [{"name": name, "selectable": True} for name in names]}


def test_import_merges_without_reordering_or_removing_existing():
    config = {"friends": ["原有好友", "小林"], "messages": [{"type": "text", "value": "hello"}], "timezone": "Asia/Shanghai"}
    updated, added = contacts.merge_contacts(config, cache(), ["小林", "小周", "小周"], "scan-1")
    assert updated["friends"] == ["原有好友", "小林", "小周"]
    assert added == 1
    assert config["friends"] == ["原有好友", "小林"]
    assert updated["messages"] == config["messages"]


@pytest.mark.parametrize("selected,scan_id", [(["小林"], "old-scan"), (["伪造"], "scan-1"), ([], "scan-1"), ([1], "scan-1")])
def test_import_rejects_stale_or_forged_selection(selected, scan_id):
    with pytest.raises(ConfigError):
        contacts.merge_contacts({}, cache(), selected, scan_id)


def test_ambiguous_and_over_limit_imports_are_rejected():
    data = cache()
    data["contacts"][0]["selectable"] = False
    with pytest.raises(ConfigError):
        contacts.merge_contacts({}, data, ["小林"], "scan-1")
    with pytest.raises(ConfigError, match="50"):
        contacts.merge_contacts({"friends": [f"好友{i}" for i in range(50)]}, cache(), ["小林"], "scan-1")


def test_import_preserves_target_messages_and_requires_defaults():
    config = {"targets": [{"name": "原有好友", "messages": [{"type": "text", "value": "专属"}]}]}
    with pytest.raises(ConfigError, match="默认消息"):
        contacts.merge_contacts(config, cache(), ["小林"], "scan-1")
    config["messages"] = [{"type": "text", "value": "默认"}]
    updated, added = contacts.merge_contacts(config, cache(), ["小林"], "scan-1")
    assert added == 1
    assert updated["targets"][0] == config["targets"][0]
    assert updated["targets"][1]["messages"] == config["messages"]


async def test_sync_has_no_trace_or_send_and_writes_private_cache(monkeypatch, settings, capsys):
    seen = []
    @asynccontextmanager
    async def browser(value):
        seen.append(value)
        yield type("Session", (), {"page": object()})()
    monkeypatch.setattr(contacts, "open_douyin", browser)
    monkeypatch.setattr(contacts, "open_private_messages", AsyncMock())
    monkeypatch.setattr(contacts, "verify_login", AsyncMock())
    monkeypatch.setattr(contacts, "collect_contacts", AsyncMock(return_value={"contacts": cache()["contacts"], "complete": False}))
    assert await contacts.sync(settings, "new-scan") == 0
    result_path = settings.artifacts_dir / contacts.CACHE_NAME
    assert json.loads(result_path.read_text())["scanId"] == "new-scan"
    assert seen[0].trace is False and seen[0].headless is True
    assert "小林" not in capsys.readouterr().out
    assert not (settings.artifacts_dir / "run.lock").exists()
    if os.name != "nt":
        assert result_path.stat().st_mode & 0o777 == 0o600


async def test_login_failure_and_browser_errors_do_not_expose_page_content(monkeypatch, settings, capsys):
    @asynccontextmanager
    async def browser(value):
        raise RuntimeError("PRIVATE PAGE CONTENT")
        yield
    monkeypatch.setattr(contacts, "open_douyin", browser)
    assert await contacts.sync(settings, "scan-2") == 1
    result = json.loads((settings.artifacts_dir / contacts.CACHE_NAME).read_text())
    assert result["contacts"] == [] and result["error"]
    assert "PRIVATE" not in json.dumps(result) + capsys.readouterr().out


async def test_sync_does_not_open_browser_when_account_is_locked(monkeypatch, settings):
    open_browser = AsyncMock()
    monkeypatch.setattr(contacts, "open_douyin", open_browser)
    with run_lock(settings.artifacts_dir / "run.lock"):
        assert await contacts.sync(settings, "scan-3") == 1
        open_browser.assert_not_called()
        assert (settings.artifacts_dir / "run.lock").exists()


def test_sync_cli_uses_selected_account_credentials_not_main(monkeypatch, tmp_path):
    monkeypatch.setattr(os, "environ", {"DOUYIN_DATA_DIR": str(tmp_path), "DOUYIN_ENV_FILE": str(tmp_path / ".env")})
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".env").write_text('DOUYIN_COOKIE=[{"name":"main-secret"}]\n')
    (tmp_path / ".env.second").write_text('DOUYIN_STORAGE_STATE=second-state.json\n')
    (tmp_path / "config").mkdir()
    (tmp_path / "config/accounts.json").write_text(json.dumps({"accounts": [{"id": "second", "enabled": False, "env_file": ".env.second"}]}))
    monkeypatch.setattr("sys.argv", ["sync_friends.py", "--account", "second", "--scan-id", "test"])
    observed = []
    async def sync(value, scan_id):
        observed.append(value)
        assert scan_id == "test"
        return 0
    monkeypatch.setattr(contacts, "sync", sync)
    assert contacts.main() == 0
    assert observed[0].cookie is None
    assert observed[0].storage_state == "second-state.json"
    assert observed[0].artifacts_dir == tmp_path / "artifacts/second"
