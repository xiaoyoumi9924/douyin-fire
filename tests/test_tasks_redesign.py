"""Task page uses isolated state; schedule writes are mocked, real OS tasks cannot change."""
import os
from copy import deepcopy
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect
from test_contacts_api import page, server


async def install_tasks(page, server, variant='normal'):
    config = server.load_config()
    config['friends'] = [f'好友{i:02}' for i in range(1, 11)]
    server.write_json(server.CONFIG, config)
    state = server.build_state('main')
    state.update(accountName='日常账号', now='2026-11-14T08:20:00+08:00', credential={'present':True,'health':'unchecked'}, friendStatuses=['sent']*10, stamped=list(range(10)))
    state['schedule'] = {'present':True,'hour':7,'minute':0,'next':'明天 07:00','savedAt':'2026-11-14T06:00:00+08:00','commandMatches':True}
    state['runHistory'] = [{'finishedAt':f'2026-11-{day:02}T07:04:00+08:00', 'dryRun':False, 'durationSeconds':duration, 'results':[{'target':f'好友{i:02}', 'status':'success'} for i in range(1,11)]} for day,duration in zip(range(14,8,-1),[28,31,29,30,30,29])]
    state['lastRun'] = state['runHistory'][0]
    if variant == 'reference':
        state.update(accountName='主账号', now='2026-11-15T13:00:00+08:00')
        state['schedule']['savedAt'] = '2026-11-15T06:00:00+08:00'
        state['runHistory'] = [
            {'finishedAt': f'2026-11-15T{time}:00+08:00', 'dryRun': False, 'durationSeconds': duration,
             'results': [{'target': f'好友{i:02}', 'status': status} for i in range(1, 10)]}
            for time, duration, status in [('12:49',202,'success'),('12:18',111,'failed'),('08:06',132,'failed'),('07:34',126,'failed')]]
        state['runHistory'] += [{'finishedAt':f'2026-11-{day:02}T07:04:00+08:00', 'dryRun':False, 'durationSeconds':30,
                                'results':[{'target':f'好友{i:02}', 'status':'success'} for i in range(1,11)]} for day in range(14,8,-1)]
        state['lastRun'] = state['runHistory'][0]
    if variant == 'empty':
        state.update(runHistory=[], lastRun=None, friendStatuses=['pending']*10, stamped=[])
    if variant == 'missing':
        state['schedule']['present'] = False
    if variant == 'paused':
        state['accountEnabled'] = False
    if variant == 'running':
        state['running'] = {'action':'send','accountId':'main'}
    if variant == 'repair':
        state['schedule']['commandMatches'] = False
    if variant == 'unstable':
        state['schedule']['unstableLocation'] = True
    if variant == 'uncertain':
        state['friendStatuses'][0] = 'unknown'
        state['runHistory'][0]['results'][0]['status'] = 'unknown'
        state['stamped'] = list(range(1,10))
    if variant == 'mixed':
        state['runHistory'].insert(0, {**deepcopy(state['runHistory'][0]), 'messageScoped':True, 'results':[{'target':'好友01','status':'success'}]})
        state['runHistory'].insert(0, {**deepcopy(state['runHistory'][0]), 'dryRun':True, 'messageScoped':False})
        state['runHistory'][3]['results'][0]['status'] = 'duplicate'
    posts, gets, errors = [], [], []
    page.on('pageerror', lambda error: errors.append(str(error)))
    async def route(r):
        req = r.request; parsed = urlsplit(req.url)
        if parsed.path == '/api/stream':
            return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            if req.method != 'GET':
                posts.append((parsed.path,req.post_data_json))
                assert parsed.path in ['/api/schedule', '/api/accounts/update'], 'UI fixtures must never send or change real OS schedules'
                if variant == 'failure':
                    return await r.fulfill(status=500,json={'error':'隔离测试：系统计划登记失败'})
                body = req.post_data_json
                if parsed.path == '/api/accounts/update':
                    if variant == 'toggle_failure':
                        return await r.fulfill(status=500, json={'error': '暂时无法更新账户'})
                    state['accountEnabled'] = body['enabled']
                    return await r.fulfill(json={'accounts': []})
                state['schedule'].update(hour=body['hour'],minute=body['minute'],next=f"明天 {body['hour']:02}:{body['minute']:02}",present=True)
                return await r.fulfill(json=deepcopy(state))
            gets.append(req.url)
            if parsed.path == '/api/state':
                if not parsed.query:
                    return await r.fulfill(json={'accounts':[{'id':'main','name':'日常账号'},{'id':'second','name':'备用账号'}]})
                if variant == 'load_error':
                    return await r.fulfill(status=503,json={'error':'隔离测试：暂时无法读取'})
                data = deepcopy(state)
                if 'accountId=second' in parsed.query:
                    data.update(accountId='second',accountName='备用账号')
                return await r.fulfill(json=data)
            return await r.fulfill(json={})
        return await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://tasks.test/**',route)
    await page.goto('http://tasks.test/#/accounts/main/tasks')
    if variant == 'load_error':
        await expect(page.locator('#scheduleEnabled')).to_have_text('读取失败')
    else:
        await expect(page.locator('#taskWeekStrip>li')).to_have_count(7)
    return state, posts, gets, errors


async def capture(page, name):
    directory = os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if directory:
        await page.screenshot(path=str(Path(directory)/name),animations='disabled',full_page=True)


@pytest.mark.parametrize('width,height',[(1280,800),(1440,900),(1625,968),(1920,1080),(1152,720)])
async def test_tasks_reference_composition_and_real_week(page,server,width,height):
    await page.set_viewport_size({'width':width,'height':height})
    state,posts,gets,errors = await install_tasks(page,server)
    await expect(page.locator('#scheduleHour')).to_have_value('07')
    await expect(page.locator('#scheduleMinute')).to_have_value('00')
    await expect(page.locator('#scheduleEnabled')).to_have_text('当前设置已保存')
    await expect(page.locator('#scheduleNextCard')).to_have_text('明天 07:00')
    await expect(page.locator('#taskWeekRange')).to_have_text('11月9日 — 11月15日')
    await expect(page.locator('#weekDelivered')).to_have_text('60条')
    await expect(page.locator('#weekComplete')).to_have_text('6/ 7 天')
    await expect(page.locator('.week-day.is-today .week-day__label')).to_contain_text('周六')
    await expect(page.locator('.week-day').last).to_contain_text('待运行')
    await expect(page.locator('#taskRecentList tr')).to_have_count(4)
    await expect(page.locator('#taskAccountFooter')).to_have_text('当前账户：日常账号')
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    boxes = await page.locator('.tasks-page .schedule-card,.tasks-page .week-card,.tasks-page .task-recent-card').evaluate_all('(nodes)=>nodes.map(n=>{const r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})')
    if width > 1200:
        assert boxes[1]['y'] == boxes[2]['y'] and boxes[1]['right'] < boxes[2]['x']
    else:
        assert boxes[2]['y'] >= boxes[1]['bottom']
    await capture(page,f'tasks-complete-{width}.png')
    await page.locator('#taskRecentList .task-run-detail').first.click()
    await expect(page.locator('#runDetailDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page.locator('#taskSendBtn')).not_to_be_visible()
    await page.locator('[data-page-link="home"]').first.click()
    await page.locator('#sendBtn').click()
    await expect(page.locator('#sendPreviewDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    assert not posts and not errors


async def test_schedule_draft_does_not_replace_real_next_and_save_is_scoped(page,server):
    state,posts,gets,errors = await install_tasks(page,server)
    await page.locator('#scheduleHour').select_option('09')
    await page.locator('#scheduleMinute').select_option('35')
    await expect(page.locator('#scheduleEnabled')).to_have_text('时间已修改，尚未保存')
    await expect(page.locator('#scheduleNextCard')).to_have_text('明天 07:00')
    await capture(page,'tasks-unsaved-1280.png')
    await page.locator('#saveSchedule').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#scheduleEnabled')).to_have_text('当前设置已保存')
    await expect(page.locator('#scheduleNextCard')).to_have_text('明天 09:35')
    assert posts == [('/api/schedule',{'accountId':'main','hour':9,'minute':35})]
    assert not errors


async def test_task_unsaved_navigation_account_and_escape_guards(page,server):
    state,posts,gets,errors = await install_tasks(page,server)
    await page.locator('#scheduleHour').select_option('09')
    await page.locator('[data-page-link="home"]').first.click()
    await expect(page.locator('#scheduleDiscardDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page).to_have_url('http://tasks.test/#/accounts/main/tasks')
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    await page.locator('#headerAccount').focus()
    await expect(page.locator('#headerAccount option')).to_have_count(2)
    await page.locator('#headerAccount').select_option('second')
    await page.locator('#keepScheduleEditing').click()
    await expect(page.locator('#headerAccount')).to_have_value('main')
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    await page.locator('#headerAccount').select_option('second')
    await page.locator('#discardScheduleChanges').click()
    await expect(page).to_have_url('http://tasks.test/#/accounts/second/tasks')
    await expect(page.locator('#scheduleHour')).to_have_value('07')
    await expect(page.locator('#taskAccountFooter')).to_contain_text('备用账号')
    assert not posts and not errors


@pytest.mark.parametrize('variant',['empty','missing','paused','running','repair','unstable','uncertain','mixed','failure','load_error'])
async def test_tasks_truthful_secondary_states(page,server,variant):
    await page.set_viewport_size({'width':1440,'height':900})
    state,posts,gets,errors = await install_tasks(page,server,variant)
    if variant == 'empty':
        await expect(page.locator('#taskRecentList')).to_contain_text('还没有正式运行记录')
        await expect(page.locator('#weekComplete')).to_have_text('0/ 7 天')
    elif variant == 'missing':
        await expect(page.locator('#scheduleNextCard')).to_have_text('未启用')
        await expect(page.locator('#scheduleEnabled')).to_have_text('尚未配置自动任务')
    elif variant == 'paused':
        await expect(page.locator('#scheduleNextCard')).to_have_text('账户已停用')
        await expect(page.locator('#taskSendBtn')).to_be_disabled()
    elif variant == 'running':
        await expect(page.locator('#scheduleToggle')).to_be_disabled()
        await expect(page.locator('#saveSchedule')).to_be_disabled()
        await expect(page.locator('#scheduleHour')).to_be_disabled()
        await expect(page.locator('#taskSendBtn')).to_be_disabled()
    elif variant == 'repair':
        await expect(page.locator('#scheduleEnabled')).to_have_text('计划任务需要修复')
        await expect(page.locator('#scheduleNote')).to_contain_text('旧位置')
    elif variant == 'unstable':
        await expect(page.locator('#scheduleNote')).to_contain_text('临时位置')
    elif variant == 'uncertain':
        await expect(page.locator('#weekFailed')).to_have_text('0位')
        await expect(page.locator('#weekUnknown')).to_have_text('另有 1 位待核对')
        await expect(page.locator('.week-day.is-today')).to_have_attribute('data-state','unknown')
    elif variant == 'mixed':
        await expect(page.locator('#taskRecentList tr')).to_have_count(4)
        await expect(page.locator('#taskRecentList tr').nth(1)).not_to_contain_text('全部送达')
    elif variant == 'failure':
        await page.locator('#scheduleHour').select_option('09')
        await page.locator('#saveSchedule').click()
        await expect(page.locator('#scheduleEnabled')).to_have_text('保存失败，请重试')
        await expect(page.locator('#scheduleHour')).to_have_value('09')
        await expect(page.locator('#scheduleNextCard')).to_have_text('明天 07:00')
        await expect(page.locator('#scheduleNote')).to_contain_text('登记失败')
        await expect(page.locator('#saveSchedule')).to_be_enabled()
    else:
        await expect(page.locator('#retryTasksLoad')).to_be_visible()
        await expect(page.locator('#scheduleToggle')).to_be_disabled()
        await expect(page.locator('#scheduleActiveBadge')).to_have_text('读取失败')
        await expect(page.locator('#saveSchedule')).to_be_disabled()
        await expect(page.locator('#taskSendBtn')).to_be_disabled()
        await expect(page.locator('#weekDelivered')).to_have_text('—')
        await expect(page.locator('#weekComplete')).to_have_text('—')
        await expect(page.locator('#weekFailed')).to_have_text('—')
        await expect(page.locator('#taskWeekRange')).to_have_text('本周数据未载入')
        await expect(page.locator('#taskRecentList')).to_contain_text('运行记录读取失败')
        await expect(page.locator('#scheduleHint')).not_to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth <= innerWidth')
    await capture(page,f'tasks-{variant}-1440.png')
    assert not errors


async def test_live_state_refresh_preserves_time_draft_and_lock_state(page,server):
    await page.add_init_script("window.taskStreams=[];window.EventSource=class{constructor(url){this.url=url;window.taskStreams.push(this)}close(){}}")
    state,posts,gets,errors = await install_tasks(page,server)
    await page.locator('#scheduleHour').select_option('09')
    await page.locator('#scheduleMinute').select_option('35')
    state['running'] = {'action':'send','accountId':'main'}
    await page.evaluate("payload=>window.taskStreams.at(-1).onmessage({data:JSON.stringify({type:'state',accountId:'main',state:payload})})",state)
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    await expect(page.locator('#scheduleMinute')).to_have_value('35')
    await expect(page.locator('#scheduleHour')).to_be_disabled()
    await expect(page.locator('#scheduleNextCard')).to_have_text('明天 07:00')
    state['running'] = None
    await page.evaluate("payload=>window.taskStreams.at(-1).onmessage({data:JSON.stringify({type:'state',accountId:'main',state:payload})})",state)
    await expect(page.locator('#saveSchedule')).to_be_enabled()
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    await expect(page.locator('#scheduleEnabled')).to_have_text('时间已修改，尚未保存')
    assert not posts and not errors


async def test_task_read_retry_recovers_with_no_schedule_write(page,server):
    state,posts,gets,errors = await install_tasks(page,server,'load_error')
    async def recovered(route):
        await route.fulfill(json=state)
    await page.route('http://tasks.test/api/state?accountId=main',recovered)
    await page.locator('#retryTasksLoad').click()
    await expect(page.locator('#scheduleEnabled')).to_have_text('当前设置已保存')
    await expect(page.locator('#taskRecentList tr')).to_have_count(4)
    await expect(page.locator('#retryTasksLoad')).not_to_be_visible()
    assert not posts and not errors


async def test_read_failure_after_success_does_not_show_prior_account_totals(page,server):
    state,posts,gets,errors = await install_tasks(page,server,'uncertain')
    await expect(page.locator('#weekUnknown')).to_have_text('另有 1 位待核对')
    async def unavailable(route):
        await route.fulfill(status=503,json={'error':'隔离测试：切换后无法读取'})
    await page.route('http://tasks.test/api/state?accountId=second',unavailable)
    await page.evaluate("location.hash='#/accounts/second/tasks'")
    await expect(page.locator('#scheduleEnabled')).to_have_text('读取失败')
    for selector in ['#weekDelivered','#weekComplete','#weekFailed']:
        await expect(page.locator(selector)).to_have_text('—')
    await expect(page.locator('#weekUnknown')).not_to_be_visible()
    await expect(page.locator('#taskRecentList')).to_contain_text('运行记录读取失败')
    await expect(page.locator('#taskSendBtn')).to_be_disabled()
    await expect(page.locator('#scheduleHint')).not_to_be_visible()
    await expect(page.locator('#taskAccountFooter')).not_to_contain_text('日常账号')
    await capture(page,'tasks-account-read-failure-1280.png')
    assert not posts and not errors


async def test_task_attached_reference_preview(page, server):
    await page.set_viewport_size({'width': 1672, 'height': 941})
    state, posts, gets, errors = await install_tasks(page, server, 'reference')
    await expect(page.locator('#weekDelivered')).to_have_text('70条')
    await expect(page.locator('#scheduleActiveBadge')).to_have_text('已开启')
    await expect(page.locator('#scheduleNextDate')).to_have_text('11月16日 · 周一')
    await expect(page.locator('#taskRecentList tr').nth(1)).to_contain_text('0 成功 · 9 失败')
    await capture(page, 'tasks-attached-reference-1672.png')
    assert not posts and not errors


async def test_task_toggle_preserves_draft_and_is_scoped(page, server):
    state, posts, gets, errors = await install_tasks(page, server)
    await page.locator('#scheduleHour').select_option('09')
    await page.locator('#scheduleToggle').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#scheduleToggle')).to_have_attribute('aria-checked', 'false')
    await expect(page.locator('#scheduleActiveBadge')).to_have_text('未开启')
    await expect(page.locator('#scheduleNextCard')).to_have_text('账户已停用')
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    assert posts == [('/api/accounts/update', {'accountId':'main', 'enabled':False})]
    await page.locator('#scheduleToggle').click()
    await expect(page.locator('#scheduleToggle')).to_have_attribute('aria-checked', 'true')
    await expect(page.locator('#scheduleHour')).to_have_value('09')
    assert posts[-1] == ('/api/accounts/update', {'accountId':'main', 'enabled':True})
    assert not errors


async def test_task_toggle_failure_keeps_real_enabled_state(page, server):
    state, posts, gets, errors = await install_tasks(page, server, 'toggle_failure')
    await page.locator('#scheduleToggle').click()
    await expect(page.locator('#scheduleNote')).to_contain_text('暂时无法更新账户')
    await expect(page.locator('#scheduleToggle')).to_have_attribute('aria-checked', 'true')
    await expect(page.locator('#scheduleToggle')).to_be_enabled()
    assert not errors


async def test_task_toggle_installs_missing_schedule(page, server):
    state, posts, gets, errors = await install_tasks(page, server, 'missing')
    await page.locator('#scheduleToggle').click()
    await expect(page.locator('#scheduleToggle')).to_have_attribute('aria-checked', 'true')
    assert posts == [('/api/schedule', {'accountId':'main', 'hour':7, 'minute':0})]
    assert not errors
