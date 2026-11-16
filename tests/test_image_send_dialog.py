"""Regression from the 2026-11-16 trace: file upload opens a modal."""
import asyncio
from unittest.mock import AsyncMock
import base64

import pytest
from playwright.async_api import async_playwright, TimeoutError as PlaywrightTimeoutError

from app import sender
from app.douyin import RecipientChangedError


@pytest.fixture
async def image_page(tmp_path):
    async with async_playwright() as pw:
        browser = await pw.chromium.launch()
        page = await browser.new_page()
        await page.route('https://image.test/image.png', lambda route: route.fulfill(content_type='image/png', body=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+aQioAAAAASUVORK5CYII=')))
        async def receipt(route):
            await asyncio.sleep(.3)
            await route.fulfill(json={'status_code':0}, headers={'Access-Control-Allow-Origin':'*'})
        await page.route('https://imapi.douyin.com/v1/message/send', receipt)
        await page.set_content('''
          <input type="file" accept="image/*">
          <button class="messageMsgInputpublishBtn" onclick="window.composerClicks++">发送</button>
          <div class="messageMessageListlist"></div>
          <div id="modal" style="display:none;position:fixed;inset:0;background:#eee;z-index:9">
            <div role="dialog"><div class="MsgInputSendFileModalbox">
              <div>发送给 测试好友</div>
              <button class="MsgInputSendFileModalbtnCancle">取消</button>
              <button class="MsgInputSendFileModalbtnSure">发送（1）</button>
            </div></div>
          </div>
          <script>
            window.composerClicks=0; window.imageClicks=0; window.cancels=0;
            document.querySelector('input').onchange=()=>{
              setTimeout(()=>document.querySelector('#modal').style.display='block',100);
            };
            document.querySelector('.MsgInputSendFileModalbtnCancle').onclick=()=>{
              window.cancels++; document.querySelector('#modal').style.display='none';
            };
            document.querySelector('.MsgInputSendFileModalbtnSure').onclick=()=>{
              window.imageClicks++; document.querySelector('#modal').style.display='none';
              fetch('https://imapi.douyin.com/v1/message/send',{method:'POST',body:'image'});
              document.querySelector('.messageMessageListlist').innerHTML=`
                <div data-index="0"><div class="messageMessageBoxmessageBox">
                  <div class="messageMessageBoxcontentBox messageMessageBoxisFromMe">
                    <div data-e2e="msg-item-content"><img class="MessageItemImageImage" src="https://image.test/image.png" alt="uploaded image"></div>
                  </div>
                </div></div>`;
            };
          </script>
        ''')
        image = tmp_path / 'sample.png'
        image.write_bytes(b'\x89PNG\r\n\x1a\n')
        yield page, str(image)
        await browser.close()


@pytest.mark.asyncio
async def test_image_upload_confirms_modal_once_and_checks_terminal_state(image_page, monkeypatch):
    page, image = image_page
    guard = AsyncMock()
    token = sender._recipient_guard.set(guard)
    try:
        await sender.send_image(page, image)
        assert await page.evaluate('window.imageClicks') == 1
        assert await page.evaluate('window.composerClicks') == 0
        assert await page.evaluate('window.cancels') == 0
        assert guard.await_count == 2  # Before upload and before modal confirmation.
    finally:
        sender._recipient_guard.reset(token)


@pytest.mark.asyncio
async def test_recipient_change_cancels_upload_without_sending(image_page):
    page, image = image_page
    guard = AsyncMock(side_effect=[None, RecipientChangedError('会话已变化')])
    token = sender._recipient_guard.set(guard)
    try:
        with pytest.raises(RecipientChangedError):
            await sender.send_image(page, image)
        assert await page.evaluate('window.imageClicks') == 0
        assert await page.evaluate('window.composerClicks') == 0
        assert await page.evaluate('window.cancels') == 1
        assert not await page.locator('#modal').is_visible()
    finally:
        sender._recipient_guard.reset(token)


@pytest.mark.asyncio
async def test_failed_confirmation_cleans_modal_and_preserves_unknown(image_page, monkeypatch):
    page, image = image_page

    async def uncertain(_page):
        raise sender.SendOutcomeUnknownError('结果不明')

    monkeypatch.setattr(sender, '_submit_image_and_wait', uncertain)
    with pytest.raises(sender.SendOutcomeUnknownError, match='结果不明'):
        await sender.send_image(page, image)
    assert await page.evaluate('window.cancels') == 1
    assert not await page.locator('#modal').is_visible()


@pytest.mark.asyncio
async def test_cleanup_failure_stops_later_recipients(image_page, monkeypatch):
    page, image = image_page
    monkeypatch.setattr(sender, '_submit_image_and_wait', AsyncMock(side_effect=sender.SendOutcomeUnknownError('结果不明')))
    monkeypatch.setattr(sender, '_cancel_image_dialog', AsyncMock(side_effect=RuntimeError('blocked')))
    with pytest.raises(sender.SendOutcomeUnknownError) as error:
        await sender.send_image(page, image)
    assert error.value.stop_all


@pytest.mark.asyncio
async def test_legacy_composer_fallback_verifies_recipient(image_page, monkeypatch):
    page, _ = image_page
    # Skip the five-second detection timeout while exercising the real fallback.
    from playwright.async_api import Locator
    monkeypatch.setattr(Locator, 'wait_for', AsyncMock(side_effect=PlaywrightTimeoutError('no modal')))
    guard = AsyncMock()
    token = sender._recipient_guard.set(guard)
    try:
        await sender._trigger_image_send(page)
        assert await page.evaluate('window.composerClicks') == 1
        assert await page.evaluate('window.imageClicks') == 0
        guard.assert_awaited_once()
    finally:
        sender._recipient_guard.reset(token)


@pytest.mark.asyncio
async def test_old_text_remount_and_avatar_are_not_image_delivery(image_page, monkeypatch):
    page, image = image_page
    await page.evaluate('''() => {
      document.querySelector('.MsgInputSendFileModalbtnSure').onclick=()=>{
        document.querySelector('#modal').style.display='none';
        document.querySelector('.messageMessageListlist').innerHTML=`
          <div data-index="0"><div class="messageMessageBoxmessageBox">
            <div class="messageMessageBoxcontentBox messageMessageBoxisFromMe">
              <img src="https://image.test/image.png" alt="avatar">
              <div data-e2e="msg-item-content">yesterday's text</div>
            </div>
          </div></div>`;
      };
    }''')
    monkeypatch.setattr(sender, '_submit_image_and_wait', sender._trigger_image_send)
    original_wait = page.wait_for_function
    async def short_wait(expression, **kwargs):
        kwargs['timeout'] = 300
        return await original_wait(expression, **kwargs)
    monkeypatch.setattr(page, 'wait_for_function', short_wait)
    with pytest.raises(sender.SendOutcomeUnknownError):
        await sender.send_image(page, image)


@pytest.mark.asyncio
@pytest.mark.parametrize('body,content_type', [
    (b'{"status_code":1}', 'application/json'),
    (b'\x18\x01\x22\x05error', 'application/x-protobuf'),
])
async def test_http_200_with_rejected_receipt_is_not_success(image_page, body, content_type):
    page, image = image_page
    await page.route('https://imapi.douyin.com/v1/message/send', lambda route: route.fulfill(body=body, content_type=content_type, headers={'Access-Control-Allow-Origin':'*'}))
    with pytest.raises(sender.SendOutcomeUnknownError, match='未确认接受'):
        await sender.send_image(page, image)


@pytest.mark.asyncio
async def test_new_incoming_message_does_not_hide_image_receipt(image_page):
    page, image = image_page
    await page.route('https://imapi.douyin.com/v1/message/send', lambda route: route.fulfill(body=b'\x18\x00\x22\x02OK', content_type='application/x-protobuf', headers={'Access-Control-Allow-Origin':'*'}))
    await page.evaluate('''() => {
      const button=document.querySelector('.MsgInputSendFileModalbtnSure');
      const send=button.onclick;
      button.onclick=()=>{
        send();
        const list=document.querySelector('.messageMessageListlist');
        list.firstElementChild.dataset.index='1';
        list.insertAdjacentHTML('afterbegin','<div data-index="0"><div class="messageMessageBoxmessageBox"><div class="messageMessageBoxcontentBox">received reply</div></div></div>');
      };
    }''')
    await sender.send_image(page, image)
    assert await page.evaluate('window.imageClicks') == 1
