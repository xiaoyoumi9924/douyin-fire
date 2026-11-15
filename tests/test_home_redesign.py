"""Desktop homepage uses account data; visual fixtures cannot start real tasks."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect
from test_contacts_api import page, server


async def install_home(page, server, variant='complete'):
    config = server.load_config()
    config['friends'] = [f'好友 {i:02}' for i in range(1, 11)]
    server.write_json(server.CONFIG, config)
    state = server.build_state('main')
    state.update(accountName='日常账号', now='2026-11-14T08:20:00+08:00',
                 credential={'present': True, 'health': 'unchecked'},
                 friendStatuses=['sent'] * 10, stamped=list(range(10)),
                 friendRules={'好友 02': {'mode': 'custom'}, '好友 05': {'mode': 'custom'}})
    state['schedule'].update(present=True, next='明天 07:00')
    run = {'finishedAt': '2026-11-14T07:04:00+08:00', 'dryRun': False,
           'durationSeconds': 28, 'results': [{'target': f'好友{i:02}', 'status': 'success'} for i in range(1, 11)]}
    state['lastRun'] = run
    state['runHistory'] = [run]
    if variant == 'empty':
        state.update(friends=[], friendStatuses=[], stamped=[], runHistory=[], lastRun=None)
    elif variant == 'unknown':
        state.update(friendStatuses=['unknown'] + ['sent'] * 9, stamped=list(range(1, 10)))
    elif variant == 'running':
        state['running'] = {'action': 'send', 'accountId': 'main'}
    elif variant == 'login':
        state['credential'] = {'present': False, 'health': 'missing'}
    elif variant == 'paused':
        state.update(friendStatuses=['paused'] * 10, stamped=[])
    elif variant == 'failed':
        state.update(friendStatuses=['failed'] + ['sent'] * 9, stamped=list(range(1, 10)))
    if variant == 'reference':
        state.update(accountName='主账号', now='2026-11-15T13:00:00+08:00',
            friends=['李嘉兴','小法师','刘倍诗','刘凤','史慧慧','王若彤','王雅娇','陈思彤','刘刘刘刘刘晰文','羽毛球'], friendRules={})
        run.update(finishedAt='2026-11-15T12:49:00+08:00', durationSeconds=202,
            results=[{'target':f'好友{i:02}', 'status':'success'} for i in range(1,10)])
    if variant in ['reference','streak_sorted']:
        values=[459,169,74,348,334,246,312,264,610,419]
        state['friendStreaks']={name:{'status':'local','days':days,'observedAt':'2026-11-15T07:00:00+08:00'} for name,days in zip(state['friends'],values)}
    if variant=='streak_sorted':
        state['friendStatuses'][8]='unknown'
        state['stamped'].remove(8)
    posts = []
    async def route(request_route):
        request = request_route.request
        path = urlsplit(request.url).path
        if path == '/api/stream':
            return await request_route.fulfill(content_type='text/event-stream', body='\n')
        if path.startswith('/api/'):
            if request.method != 'GET':
                posts.append(path)
                return await request_route.fulfill(status=403, json={'error': 'Visual review cannot start tasks'})
            if path == '/api/state':
                return await request_route.fulfill(json=state if urlsplit(request.url).query else {'accounts': [{'id': 'main', 'name': '主账号' if variant=='reference' else '日常账号'}, {'id': 'second', 'name': '隔离账号'}]})
            return await request_route.fulfill(json={})
        return await request_route.fulfill(content_type='text/html', body=(server.ROOT / 'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed', '1')")
    await page.route('http://home.test/**', route)
    return posts


@pytest.mark.parametrize('width,height', [(1280, 800), (1440, 900), (1764, 894), (1920, 1080)])
async def test_home_desktop_composition_and_actions(page, server, width, height):
    await page.set_viewport_size({'width': width, 'height': height})
    posts = await install_home(page, server)
    await page.goto('http://home.test/#/accounts/main/home')
    await expect(page.locator('#runBadgeTitle')).to_have_text('今日消息已全部发送')
    await expect(page.locator('#homeFriendList>li')).to_have_count(6)
    await expect(page.locator('#homeFriendList>li').nth(1)).to_contain_text('专属消息')
    await expect(page.locator('#headerAccount')).to_have_value('main')
    await expect(page.locator('#headerSchedule')).to_have_text('自动任务已启用')
    boxes = await page.locator('.hero-card, .friends-card, .recent-card').evaluate_all('(nodes)=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})')
    assert boxes[0]['y'] == boxes[1]['y']
    assert boxes[0]['right'] < boxes[1]['x']
    assert boxes[2]['y'] > boxes[0]['bottom']
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    capture = os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if capture:
        await page.screenshot(path=str(Path(capture) / f'home-complete-{width}.png'), animations='disabled', full_page=True)
    await page.locator('.home-friend-more').first.click()
    await expect(page.locator('#friendRuleDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await page.locator('#homeRecentDetail').click()
    await expect(page.locator('#runDetailDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await page.locator('#sendBtn').click()
    await expect(page.locator('#sendPreviewDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    assert not posts


@pytest.mark.parametrize('variant,title', [('empty', '还没有好友'), ('unknown', '有好友待核对'), ('running', '正在发送'), ('login', '需要登录'), ('paused', '好友已全部暂停'), ('failed', '任务有失败')])
async def test_home_truthful_states(page, server, variant, title):
    await page.set_viewport_size({'width': 1440, 'height': 900})
    posts = await install_home(page, server, variant)
    await page.goto('http://home.test/#/accounts/main/home')
    await expect(page.locator('#runBadgeTitle')).to_have_text(title)
    if variant == 'running':
        await expect(page.locator('#stopBtn')).to_be_visible()
        await expect(page.locator('#dryRunBtn')).to_be_disabled()
    if variant == 'login':
        await expect(page.locator('#sendBtn')).to_be_disabled()
    if variant == 'empty':
        await expect(page.locator('#homeRecentDetail')).to_be_disabled()
    capture = os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if capture:
        await page.screenshot(path=str(Path(capture) / f'home-{variant}-1440.png'), animations='disabled', full_page=True)
    assert not posts


async def test_home_account_picker_uses_ids_and_reloads_scoped_data(page, server):
    posts = await install_home(page, server)
    await page.goto('http://home.test/#/accounts/main/home')
    await expect(page.locator('#headerAccount')).to_have_value('main')
    await page.locator('#headerAccount').focus()
    await expect(page.locator('#headerAccount option')).to_have_count(2)
    async with page.expect_request(lambda req: '/api/state?accountId=second' in req.url):
        await page.locator('#headerAccount').select_option('second')
    await expect(page).to_have_url('http://home.test/#/accounts/second/home')
    await expect(page.locator('.nav-link[data-page-link="friends"]')).to_have_attribute('href', '#/accounts/second/friends')
    await expect(page.locator('#headerAccount')).to_have_value('second')
    assert not posts


async def test_latest_home_reference_preview(page,server):
    await page.set_viewport_size({'width':1672,'height':941})
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    posts=await install_home(page,server,'reference')
    await page.goto('http://home.test/#/accounts/main/home')
    await expect(page.locator('#runBadgeTitle')).to_have_text('今日消息已全部发送')
    await expect(page.locator('#runBadgeDetail')).to_contain_text('10 位好友')
    await expect(page.locator('#todayCount')).to_have_text('10 / 10')
    await expect(page.locator('#homeFriendList>li')).to_have_count(6)
    await expect(page.locator('#homeFriendMeta')).to_have_text('共 10 位，当前显示 6 位')
    await expect(page.locator('#homeRecentSummary')).to_contain_text('成功 9')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    capture=os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if capture: await page.screenshot(path=str(Path(capture)/'home-latest-reference-1672.png'),animations='disabled',full_page=True)
    assert not posts and not errors


async def test_home_latest_small_window(page,server):
    await page.set_viewport_size({'width':390,'height':844})
    posts=await install_home(page,server)
    await page.goto('http://home.test/#/accounts/main/home')
    await expect(page.locator('#todayCount')).to_have_text('10 / 10')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await page.locator('#sendBtn').click()
    await expect(page.locator('#sendPreviewDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    assert not posts


async def test_home_streak_order_status_and_rule_target(page,server):
    await page.set_viewport_size({'width':1672,'height':941})
    posts=await install_home(page,server,'streak_sorted')
    await page.goto('http://home.test/#/accounts/main/home')
    rows=page.locator('#homeFriendList>li')
    await expect(rows).to_have_count(6)
    assert await rows.evaluate_all('(nodes)=>nodes.map(n=>n.dataset.friendName)')==['好友 09','好友 01','好友 10','好友 04','好友 05','好友 07']
    await expect(rows.first.locator('.home-friend-spark')).to_have_text('610 天')
    await expect(rows.first.locator('.status')).to_have_text('待核对')
    await expect(rows.nth(2).locator('.home-friend-time')).to_have_text('今天 07:04')
    await rows.first.locator('.home-friend-more').click()
    await expect(page.locator('#friendRuleTitle')).to_have_text('好友 09')
    assert not posts
