"""Friend workspace fixtures use a temporary backend and reject every send action."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect
from test_contacts_api import page, server, http_request


async def install_friends(page, server, http_request, variant='normal'):
    config = server.load_config()
    config['friends'] = [f'好友 {i:02}' for i in range(1, 9)]
    config['friend_rules'] = {
        '好友 02': {'mode': 'custom', 'enabled': True, 'messages': [{'type': 'rotation', 'choices': [{'type': 'text', 'value': '今天也要开心呀'}, {'type': 'text', 'value': '记得吃早饭'}]}], 'holiday_enabled': True},
        '好友 05': {'mode': 'custom', 'enabled': True, 'messages': [{'type': 'text', 'value': '早上好'}]},
    }
    if variant == 'streak_sorted':
        config['friends'] += [f'好友 {i:02}' for i in range(9,15)]
    if variant == 'long':
        config['friends'][0] = '这是一个用于验证桌面布局的三十二个字符以内的很长好友昵称'
        config['friends'] += [f'测试好友 {i:02}' for i in range(9, 45)]
    if variant == 'empty':
        config['friends'] = []
        config['friend_rules'] = {}
    server.write_json(server.CONFIG, config)
    posts = []
    errors = []
    page.on('pageerror', lambda error: errors.append(str(error)))
    async def route(r):
        req = r.request
        parsed = urlsplit(req.url)
        if parsed.path == '/api/stream':
            return await r.fulfill(content_type='text/event-stream', body='\n')
        if parsed.path.startswith('/api/'):
            if req.method == 'POST':
                posts.append((parsed.path, req.post_data_json))
                assert parsed.path in ['/api/friends/rule', '/api/friends', '/api/image'], 'Fixture cannot start any browser or send task'
                if variant == 'failure' and parsed.path == '/api/friends/rule':
                    return await r.fulfill(status=409, json={'error': '任务正在运行，暂时不能保存'})
            status, data = http_request(parsed.path + ('?' + parsed.query if parsed.query else ''), req.post_data_json if req.method == 'POST' else None)
            if status == 200 and (parsed.path == '/api/state' and parsed.query or variant == 'streak_sorted' and parsed.path == '/api/friends/rule'):
                data['friendStreaks'] = {data['friends'][0]: {'status': 'local', 'days': 37, 'observedAt': '2026-11-14T07:00:00+08:00'}} if data['friends'] else {}
                data['accountName'] = '日常账号' if variant != 'long' else '这是一个很长很长的隔离测试账户名称'
                data['credential'] = {'present': True, 'health': 'unchecked'}
                data['now'] = '2026-11-14T08:20:00+08:00'
                data['friendStatuses'] = (['sent'] * 6 + ['pending', 'unknown'] + ['pending'] * 36)[:len(data['friends'])]
                data['stamped'] = list(range(min(6, len(data['friends']))))
                data['schedule'].update(present=True, next='明天 07:00')
                data['runHistory'] = [{'finishedAt': '2026-11-14T07:04:00+08:00', 'dryRun': False, 'durationSeconds': 28, 'results': [{'target': f'好友{i:02}', 'status': 'success'} for i in range(1, 7)]}]
                if variant == 'streak_sorted':
                    values=[30,300,300,None,200,40,80,None,50,0,2000,100,1000,None]
                    data['friendStreaks']={name:{'status':'observed' if days is not None else 'unknown','days':days} for name,days in zip(data['friends'],values)}
                    data['friendStreaks']['好友 08']={'status':'stale','days':5000}
                for rule in data['friendRules'].values():
                    rule['deferred'] = True
                if variant == 'running':
                    data['running'] = {'action': 'send', 'accountId': 'main'}
                if variant == 'unsupported':
                    data['friendRules']['好友 02'].update(editable=False)
                if variant == 'states':
                    data['friendStatuses'][:2] = ['failed', 'paused']
            return await r.fulfill(status=status, json=data)
        return await r.fulfill(content_type='text/html', body=(server.ROOT / 'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed', '1')")
    await page.route('http://friends.test/**', route)
    await page.goto('http://friends.test/#/accounts/main/friends')
    await expect(page.locator('#albumGrid tr')).to_have_count(min(10, len(config['friends'])) or 1)
    return posts, errors


def capture_path(name):
    directory = os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    return str(Path(directory) / name) if directory else None


@pytest.mark.parametrize('width,height', [(1280, 800), (1440, 900), (1625, 968), (1920, 1080)])
async def test_friends_matches_adjacent_editor_composition(page, server, http_request, width, height):
    await page.set_viewport_size({'width': width, 'height': height})
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('#albumGrid tr').nth(1).get_by_role('button', name='设置 好友 02 的消息规则', exact=True).click()
    await expect(page.locator('#friendRuleTitle')).to_have_text('好友 02')
    await expect(page.locator('#friendRuleAccount')).to_have_text('当前账户：日常账号')
    await expect(page.locator('#friendRuleTexts')).to_have_value('今天也要开心呀\n记得吃早饭')
    await expect(page.locator('#albumGrid tr').nth(1)).to_have_attribute('data-selected', 'true')
    await expect(page.locator('#friendRuleStrategy')).to_have_value('rotation')
    assert not await page.locator('#friendRuleDialog').evaluate('e => e.matches(":modal")')
    boxes = await page.locator('#friends, #friendRuleDialog').evaluate_all('(nodes)=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right}})')
    assert boxes[0]['y'] == boxes[1]['y']
    assert boxes[0]['right'] < boxes[1]['x']
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if path := capture_path(f'friends-editor-{width}.png'):
        await page.screenshot(path=path, animations='disabled', full_page=True)
    await page.keyboard.press('Escape')
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    await expect(page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link')).to_be_focused()
    assert not posts and not errors


async def test_unsaved_guard_for_friend_route_and_account_switch(page, server, http_request):
    await page.set_viewport_size({'width': 1440, 'height': 900})
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link').click()
    await page.locator('#friendRuleTexts').fill('尚未保存的新内容')
    await page.locator('#albumGrid tr').nth(2).locator('.friend-rule-link').click()
    await expect(page.locator('#ruleDiscardDialog')).to_be_visible()
    await page.locator('#keepRuleEditing').click()
    await expect(page.locator('#friendRuleTitle')).to_have_text('好友 02')
    await expect(page.locator('#friendRuleTexts')).to_have_value('尚未保存的新内容')
    await page.locator('[data-page-link="tasks"]').first.click()
    await expect(page.locator('#ruleDiscardDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page).to_have_url('http://friends.test/#/accounts/main/friends')
    await expect(page.locator('#friendRuleTexts')).to_have_value('尚未保存的新内容')
    await page.evaluate("location.hash = '#/accounts/second/friends'")
    await expect(page.locator('#ruleDiscardDialog')).to_be_visible()
    await page.locator('#keepRuleEditing').click()
    await expect(page).to_have_url('http://friends.test/#/accounts/main/friends')
    await page.locator('#albumGrid tr').nth(2).locator('.friend-rule-link').click()
    await page.locator('#discardRuleChanges').click()
    await expect(page.locator('#friendRuleTitle')).to_have_text('好友 03')
    await page.locator('[data-rule-source="custom"]').click()
    await page.locator('#friendRuleTexts').fill('新规则')
    await page.locator('#closeFriendRule').click()
    await page.locator('#discardRuleChanges').click()
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    assert not posts and not errors


async def test_save_rule_uses_real_isolated_backend_and_prevents_double_submit(page, server, http_request):
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link').click()
    await page.locator('#friendRuleTexts').fill('新的第一条\n新的第二条')
    await page.locator('#friendHolidayEnabled').uncheck()
    await page.locator('#saveFriendRule').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    assert len(posts) == 1
    assert posts[0][1]['accountId'] == 'main' and posts[0][1]['name'] == '好友 02'
    config = server.load_config()
    assert config['friend_rules']['好友 02']['messages'] == [{'type': 'rotation', 'choices': [{'type': 'text', 'value': '新的第一条'}, {'type': 'text', 'value': '新的第二条'}]}]
    assert config['friend_rules']['好友 02']['holiday_enabled'] is False
    assert config['friend_rules']['好友 05']['messages'] == [{'type': 'text', 'value': '早上好'}]
    assert not errors


@pytest.mark.parametrize('variant', ['failure', 'running', 'unsupported', 'long', 'empty', 'states'])
async def test_friends_secondary_states(page, server, http_request, variant):
    await page.set_viewport_size({'width': 1440, 'height': 900})
    posts, errors = await install_friends(page, server, http_request, variant)
    if variant == 'empty':
        await expect(page.locator('#albumGrid')).to_contain_text('还没有好友')
        await expect(page.locator('#friendsListMeta')).to_have_text('共 0 位好友')
    elif variant == 'states':
        await expect(page.locator('#friendMoreFilter')).not_to_be_visible()
        # The removed dropdown no longer has a visible entry; retain data-filter coverage.
        await page.locator('#friendStatusFilter').evaluate("e=>{e.value='failed';e.dispatchEvent(new Event('change',{bubbles:true}))}")
        await expect(page.locator('#albumGrid tr')).to_have_count(1)
        await expect(page.locator('#albumGrid')).to_contain_text('失败')
        await page.locator('#friendStatusFilter').evaluate("e=>{e.value='paused';e.dispatchEvent(new Event('change',{bubbles:true}))}")
        await expect(page.locator('#albumGrid')).to_contain_text('已暂停')
    else:
        await page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link').click()
        if variant == 'running':
            await expect(page.locator('#saveFriendRule')).to_be_disabled()
            await expect(page.locator('#friendRuleTexts')).to_be_disabled()
            await expect(page.locator('#friendRuleLocked')).to_be_visible()
        elif variant == 'unsupported':
            await expect(page.locator('#friendRuleTexts')).to_be_disabled()
            await expect(page.locator('#friendRuleStatus')).to_contain_text('保留原设置')
        elif variant == 'failure':
            await page.locator('#friendRuleTexts').fill('出错后保留此草稿')
            await page.locator('#saveFriendRule').click()
            await expect(page.locator('#friendRuleStatus')).to_contain_text('任务正在运行')
            await expect(page.locator('#friendRuleTexts')).to_have_value('出错后保留此草稿')
            await expect(page.locator('#saveFriendRule')).to_be_enabled()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if path := capture_path(f'friends-{variant}-1440.png'):
        await page.screenshot(path=path, animations='disabled', full_page=True)
    assert not errors
    assert all(path != '/api/action' for path, _ in posts)


async def test_menu_rename_delete_and_search_remain_operational(page, server, http_request):
    posts, errors = await install_friends(page, server, http_request)
    row = page.locator('#albumGrid tr').first
    await row.locator('.friend-more').click()
    await expect(row.locator('.friend-row-menu')).to_be_visible()
    if path := capture_path('friends-menu-1280.png'):
        await page.screenshot(path=path, animations='disabled', full_page=True)
    await row.get_by_role('button', name='修改 好友 01 的昵称', exact=True).click()
    title = row.locator('[contenteditable=true]')
    await title.fill('改名后的好友')
    await title.press('Enter')
    await expect(page.locator('#albumGrid')).to_contain_text('改名后的好友')
    await page.locator('#friendSearch').fill('改名后的好友')
    await expect(page.locator('#albumGrid tr')).to_have_count(1)
    await row.locator('.friend-more').click()
    await row.get_by_role('button', name='删除好友 改名后的好友', exact=True).click()
    assert '改名后的好友' in server.load_config()['friends']
    await row.get_by_role('button', name='删除好友 改名后的好友', exact=True).click()
    await expect(page.locator('#albumGrid')).to_contain_text('没有符合条件')
    assert '改名后的好友' not in server.load_config()['friends']
    assert not errors


def test_empty_workspace_keeps_defaults_and_last_friend_protection(server, http_request):
    assert http_request('/api/friends', {'accountId': 'main', 'friends': []})[0] == 400
    config = server.load_config()
    config['friends'] = []
    server.write_json(server.CONFIG, config)
    status, data = http_request('/api/state?accountId=main')
    assert status == 200
    assert data['friends'] == [] and data['sendQueueReady'] is False
    assert data['message']['texts'] == ['默认消息']
    assert http_request('/api/state?accountId=main')[0] == 200


async def test_picture_limits_removal_and_pause_save_without_sending(page, server, http_request):
    import base64
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link').click()
    png = base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jG1sAAAAASUVORK5CYII=')
    await page.locator('#friendRuleFiles').set_input_files({'name': '隔离图片.png', 'mimeType': 'image/png', 'buffer': png})
    await expect(page.locator('#friendRuleGallery img')).to_have_count(1)
    await expect(page.locator('#friendRuleImageCount')).to_have_text('1 / 10 张')
    await page.locator('#friendRuleGallery button').click()
    await expect(page.locator('#friendRuleImageCount')).to_have_text('0 / 10 张')
    await page.locator('#friendRuleFiles').set_input_files([{'name': f'{i}.png', 'mimeType': 'image/png', 'buffer': png} for i in range(11)])
    await expect(page.locator('#friendRuleStatus')).to_contain_text('最多 10 张')
    await expect(page.locator('#friendRuleGallery img')).to_have_count(0)
    await page.locator('#friendRuleFiles').set_input_files({'name': '超限.png', 'mimeType': 'image/png', 'buffer': b'x' * (8 * 1024 * 1024 + 1)})
    await expect(page.locator('#friendRuleGallery img')).to_have_count(0)
    await page.locator('#friendRuleFiles').set_input_files({'name': '不支持.txt', 'mimeType': 'text/plain', 'buffer': b'hello'})
    await expect(page.locator('#friendRuleGallery img')).to_have_count(0)
    await page.locator('#friendRuleFiles').set_input_files({'name': '隔离图片.png', 'mimeType': 'image/png', 'buffer': png})
    await page.locator('#friendRuleEnabled').uncheck()
    await page.locator('#saveFriendRule').click()
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    assert [path for path, _ in posts] == ['/api/image', '/api/friends/rule']
    rule = server.load_config()['friend_rules']['好友 02']
    assert rule['enabled'] is False
    assert rule['messages'][0]['choices'][-1]['type'] == 'image'
    assert not errors


async def test_desktop_zoom_equivalent_and_open_menu_fit(page, server, http_request):
    # 1440px at browser 125% zoom gives approximately 1152 CSS pixels.
    await page.set_viewport_size({'width': 1152, 'height': 720})
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('#albumGrid tr').nth(1).locator('.friend-rule-link').click()
    await page.locator('#albumGrid tr').nth(7).locator('.friend-more').click()
    menu = page.locator('#albumGrid tr').nth(7).locator('.friend-row-menu')
    box = await menu.bounding_box()
    assert 0 <= box['x'] and box['x'] + box['width'] <= 1152
    assert 0 <= box['y'] and box['y'] + box['height'] <= 720
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    if path := capture_path('friends-zoom-equivalent-1152.png'):
        await page.screenshot(path=path, full_page=True, animations='disabled')
    assert not posts and not errors


async def test_load_failure_clears_old_rows_and_retry_recovers(page, server, http_request):
    posts, errors = await install_friends(page, server, http_request)
    async def unavailable(route):
        await route.fulfill(status=503, json={'error': '隔离测试：暂时无法读取'})
    url = 'http://friends.test/api/state?accountId=main'
    await page.route(url, unavailable)
    await page.reload()
    await expect(page.locator('#friendsLoadError')).to_be_visible()
    await expect(page.locator('#albumGrid tr')).to_have_count(0)
    await expect(page.locator('#toggleAddFriend')).to_be_disabled()
    if path := capture_path('friends-load-error-1280.png'):
        await page.screenshot(path=path, full_page=True, animations='disabled')
    await page.unroute(url, unavailable)
    await page.locator('#retryFriendsLoad').click()
    await expect(page.locator('#albumGrid tr')).to_have_count(8)
    await expect(page.locator('#friendsLoadError')).not_to_be_visible()
    assert not posts and not errors


async def test_pagination_and_name_spark(page, server, http_request):
    posts, errors = await install_friends(page, server, http_request, 'long')
    await expect(page.locator('#friendsPageInfo')).to_have_text('1 / 5')
    await expect(page.locator('#friendsPrevPage')).to_be_disabled()
    await expect(page.locator('.friend-copy .friend-spark').first).to_contain_text('37 天')
    await expect(page.locator('.friend-copy .friend-spark svg').first).to_be_visible()
    await page.locator('#friendsNextPage').click()
    await expect(page.locator('#friendsPageInfo')).to_have_text('2 / 5')
    await expect(page.locator('#albumGrid tr').first).to_have_attribute('data-friend-name', '测试好友 11')
    for _ in range(3):
        await page.locator('#friendsNextPage').click()
    await expect(page.locator('#albumGrid tr')).to_have_count(4)
    await expect(page.locator('#friendsNextPage')).to_be_disabled()
    await page.locator('#friendSearch').fill('好友 02')
    await expect(page.locator('#friendsPageInfo')).to_have_text('1 / 1')
    await expect(page.locator('#albumGrid tr')).to_have_count(1)
    assert not posts and not errors


async def test_edit_holiday_greeting_save_reopen_and_guard(page, server, http_request):
    posts, errors = await install_friends(page, server, http_request)
    await page.locator('.friend-rule-link').first.click()
    await expect(page.locator('#ruleReferenceToggle')).to_have_count(0)
    details = page.locator('#friendHolidayDetails')
    await details.locator('summary').click()
    greeting = page.get_by_role('textbox', name='冬至祝福语', exact=True)
    await greeting.fill('冬至安康，记得吃饺子')
    await page.locator('#closeFriendRule').click()
    await expect(page.locator('#ruleDiscardDialog')).to_be_visible()
    await page.locator('#keepRuleEditing').click()
    await page.locator('#saveFriendRule').click()
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    assert server.load_config()['friend_rules']['好友 01']['holiday_greetings']['2026-12-22'] == '冬至安康，记得吃饺子'
    await page.locator('.friend-rule-link').first.click()
    await details.locator('summary').click()
    await expect(greeting).to_have_value('冬至安康，记得吃饺子')
    boxes = await page.locator('#friendHolidayDetails, #friendTodayDetails').evaluate_all('(nodes)=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,bottom:r.bottom}})')
    assert boxes[1]['y'] >= boxes[0]['bottom'] and boxes[1]['x'] == boxes[0]['x']
    if path := capture_path('friends-holiday-edited.png'):
        await page.screenshot(path=path, animations='disabled', full_page=True)
    assert not errors


async def test_streak_sort_before_paging_preserves_status_and_rule_target(page,server,http_request):
    await page.set_viewport_size({'width':1672,'height':941})
    posts,errors=await install_friends(page,server,http_request,'streak_sorted')
    original=list(server.load_config()['friends'])
    rows=page.locator('#albumGrid tr')
    expected=['好友 11','好友 13','好友 02','好友 03','好友 05','好友 12','好友 07','好友 09','好友 06','好友 01']
    assert await rows.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.friendName)')==expected
    await expect(rows.first.locator('.status')).to_have_text('待发送')
    await expect(rows.nth(2).locator('.status')).to_have_text('已发送')
    await expect(rows.nth(2).locator('.friend-time')).to_contain_text('07:04')
    if path:=capture_path('friends-streak-sorted-1672.png'):
        await page.screenshot(path=path,full_page=True,animations='disabled')
    await page.locator('#friendsNextPage').click()
    assert await rows.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.friendName)')==['好友 10','好友 04','好友 08','好友 14']
    await page.locator('#friendsPrevPage').click()
    await rows.first.locator('.friend-rule-link').click()
    await expect(page.locator('#friendRuleTitle')).to_have_text('好友 11')
    await page.locator('#friendRuleEnabled').uncheck()
    await page.locator('#saveFriendRule').click()
    await expect(page.locator('#friendRuleDialog')).not_to_be_visible()
    config=server.load_config()
    assert config['friends']==original and config['friend_rules']['好友 11']['enabled'] is False
    await page.locator('#friendSearch').fill('好友 0')
    assert await rows.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.friendName)')==['好友 02','好友 03','好友 05','好友 07','好友 09','好友 06','好友 01','好友 04','好友 08']
    assert not errors


async def test_sorted_rename_updates_original_friend_index(page,server,http_request):
    posts,errors=await install_friends(page,server,http_request,'streak_sorted')
    row=page.locator('#albumGrid tr').first
    await row.locator('.friend-more').click()
    await row.get_by_role('button',name='修改 好友 11 的昵称',exact=True).click()
    title=row.locator('[contenteditable=true]')
    await title.fill('火花最高的好友');await title.press('Enter')
    await expect(page.locator('#albumGrid')).to_contain_text('火花最高的好友')
    names=server.load_config()['friends']
    assert names[10]=='火花最高的好友' and names[0]=='好友 01' and '好友 11' not in names
    assert not errors
