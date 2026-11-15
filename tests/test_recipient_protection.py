from unittest.mock import AsyncMock
import pytest
from app.douyin import DouyinChat, RecipientIdentityError, RecipientChangedError
from app.errors import classify_error, should_stop_all_tasks, ErrorCategory
from app.models import Message
from app import sender
from test_contacts_api import page


def search_row(name):
    return f'<div class="SearchPanelitembox"><span class="SearchPanelitemname">{name}</span><button class="SearchPanelitemchat_btn" onclick="window.clicks++">发消息</button></div>'


@pytest.mark.parametrize('names,target', [(['秘密名字','秘密名字'],'秘密名字'),(['群聊(3)','群聊(5)'],'群聊')])
async def test_duplicate_search_names_never_select_first(page,names,target):
    await page.set_content('<script>window.clicks=0</script>'+''.join(search_row(n) for n in names))
    with pytest.raises(RecipientIdentityError) as error:
        await DouyinChat(page)._search_result(target)
    assert target not in str(error.value)
    assert await page.evaluate('window.clicks') == 0
    assert classify_error(error.value)==ErrorCategory.PERMANENT


async def test_search_collection_wrapper_cannot_hide_duplicate_rows(page):
    await page.set_content('<div class="SearchPanelitemboxOuter">'+search_row('同名')+search_row('同名')+'</div>')
    with pytest.raises(RecipientIdentityError):
        await DouyinChat(page)._search_result('同名')


async def test_selector_variants_same_row_are_not_a_duplicate(page):
    await page.set_content('<div data-e2e="conversation-item" class="conversation-item"><div class="conversationConversationItem"><span class="conversationConversationItemtitle">唯一好友</span></div></div>')
    result=await DouyinChat(page)._search_result('唯一好友')
    assert result is not None


async def test_duplicate_conversations_across_selector_variants_are_blocked(page):
    await page.set_content('<div data-e2e="conversation-item"><span class="conversationConversationItemtitle">同名</span></div><div class="conversation-item"><span class="conversationConversationItemtitle">同名</span></div>')
    with pytest.raises(RecipientIdentityError):await DouyinChat(page)._search_result('同名')


async def test_hidden_title_cache_cannot_select_a_recipient(page):
    await page.set_content('<div class="conversationConversationItem"><span class="conversationConversationItemtitle" style="display:none">好友</span></div>')
    assert await DouyinChat(page)._search_result('好友') is None


async def test_ambiguous_open_has_no_retry(page):
    chat=DouyinChat(page)
    chat._open_target_once=AsyncMock(side_effect=RecipientIdentityError('发现同名'))
    with pytest.raises(RecipientIdentityError):await chat.open_target('好友',retries=3)
    assert chat._open_target_once.await_count==1


async def test_switched_conversation_is_blocked_before_text_publish(page,monkeypatch):
    chat=DouyinChat(page)
    chat._recipient_name='好友'
    chat._chat_open_error=AsyncMock(return_value=None)
    await page.set_content('<div contenteditable="true" class="messageEditorinputArea"></div><button class="e2e-send-msg-bt" onclick="window.sent++">发送</button><script>window.sent=0</script>')
    async def changed(_):
        chat._chat_open_error.return_value=RecipientChangedError('会话已变化')
        return ('anchor','')
    monkeypatch.setattr(sender,'_mark_latest_outgoing_message',changed)
    with pytest.raises(RecipientChangedError) as error:
        await sender.send_message(page,chat,Message(type='text',content='测试内容'),{})
    assert should_stop_all_tasks(error.value)
    assert await page.evaluate('window.sent')==0
    assert sender._recipient_guard.get() is None


async def test_guard_isolation_and_unconfirmed_image_cannot_upload(page,monkeypatch):
    await page.set_content('<input type="file"><script>window.uploads=0;document.querySelector("input").onchange=()=>window.uploads++</script>')
    chat=DouyinChat(page)
    with pytest.raises(RecipientChangedError):
        await sender.send_message(page,chat,Message(type='image',path=__import__('pathlib').Path('never-read.png')), {})
    assert await page.evaluate('window.uploads')==0
    assert sender._recipient_guard.get() is None


async def test_sticker_context_change_preserves_unknown_after_first_action(page,monkeypatch):
    calls=0
    async def guard():
        nonlocal calls
        calls+=1
        if calls>1: raise RecipientChangedError('变化')
    item=AsyncMock()
    item.get_attribute.return_value=None
    monkeypatch.setattr(sender,'_sticker_resource_key',AsyncMock(return_value=''))
    monkeypatch.setattr(sender,'_confirm_sticker_sent',AsyncMock(side_effect=sender.SendOutcomeUnknownError('未确认')))
    monkeypatch.setattr(sender,'_publish_ready',AsyncMock(return_value=True))
    monkeypatch.setattr(sender,'_click_retry_on_latest_failed_message',AsyncMock(return_value=False))
    monkeypatch.setattr(sender,'_mark_latest_outgoing_message',AsyncMock(return_value=('new','')))
    token=sender._recipient_guard.set(guard)
    try:
        with pytest.raises(sender.SendOutcomeUnknownError) as error:
            await sender._click_and_confirm_sticker(page,item,('old',''),'表情')
        assert error.value.stop_all
        assert item.click.await_count==1
    finally:sender._recipient_guard.reset(token)


async def test_explicit_header_title_wins_over_auxiliary_name(page):
    from app.selectors import CHAT_PANEL_MARKERS
    # Use a real recognized header class, and retain a secondary name node.
    header='RightPanelHeader'
    await page.set_content('<div class="'+header+'"><span class="RightPanelHeadertitle">另一位好友</span><span class="nickname">目标好友</span></div><div class="messageEditorinputArea" contenteditable="true"></div>')
    chat=DouyinChat(page)
    assert await chat._chat_open_error('目标好友') is not None


@pytest.mark.parametrize('actual,confirmed', [('目标好友', True), ('另一位好友', False)])
async def test_title_bar_svg_does_not_break_recipient_confirmation(page, actual, confirmed):
    # Reproduce the live header: all four elements match the title prefix.
    await page.set_content(f'''
        <div class="RightPanelHeader">
          <div class="RightPanelHeadertitleContainer">
            <div class="RightPanelHeadertitle">{actual}</div>
            <div class="RightPanelHeadertitleBarContainer">
              <svg class="RightPanelHeadertitleBarMoreIcon" width="24" height="24">
                <title>更多</title><path d="M0 0h24v24H0z" />
              </svg>
            </div>
          </div>
        </div>
        <div contenteditable="true"></div>
    ''')
    chat = DouyinChat(page)
    assert (await chat._chat_open_error('目标好友') is None) is confirmed
    if confirmed:
        await chat._confirm_opened('目标好友', timeout_ms=0)
    else:
        chat._recipient_name = '目标好友'
        with pytest.raises(RecipientChangedError):
            await chat.assert_recipient()
