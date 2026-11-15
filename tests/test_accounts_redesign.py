"""Approved account desktop UI with temporary accounts and intercepted login only."""
import asyncio
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect
from app.main import _append_run_history
from test_contacts_api import page,server,http_request


async def capture(page,name):
    directory=os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if directory: await page.screenshot(path=str(Path(directory)/name),animations='disabled',full_page=True)


def seed_accounts(server,http_request):
    for account_id in ['second','test','new']:
        assert http_request('/api/accounts',{'name':account_id})[0]==200
    names=['日常账号','备用账号','测试账号','新账号']
    for i,(account_id,count) in enumerate(zip(['main','second','test','new'],[10,6,4,0])):
        paths=server.account_paths_for(account_id)
        server.write_json(paths.task_config,{'task_id':f'isolated-{account_id}','friends':[f'隔离好友{x+1:02d}' for x in range(count)],'messages':[{'type':'text','value':f'隔离文字{x+1}'} for x in range([5,3,2,0][i])]})
        if i<3:
            server.write_json(paths.storage_state,{'cookies':[{'name':'sessionid','value':'isolated-not-real','domain':'.douyin.com','path':'/'}],'origins':[]})
            run={'task_id':f'isolated-{account_id}','finished_at':f'2026-11-{13 if i==2 else 14}T07:0{4-i}:00+08:00','dry_run':False,'results':[{'target':f'隔离好友{x+1:02d}','status':'success','sent':1} for x in range(count)]}
            _append_run_history(paths.artifacts_dir,run)
            server.write_json(paths.artifacts_dir/'result.json',run)
        assert http_request('/api/accounts/update',{'accountId':account_id,'name':names[i],'enabled':i<2})[0]==200


async def install_accounts(page,server,http_request,variant='normal'):
    seed_accounts(server,http_request)
    posts,gets,errors=[],[],[]
    controls={'fail':variant=='load_error','fail_post':variant=='post_error','running':{'action':'send','accountId':'second'} if variant=='running' else None,'empty':variant=='empty','long':variant=='long','state_fail':variant=='state_error','health':{}}
    page.on('pageerror',lambda error:errors.append(str(error)))
    if variant=='config_error': server.account_paths_for('test').task_config.write_text('{broken')
    async def route(r):
        req=r.request;parsed=urlsplit(req.url)
        if parsed.path=='/api/stream':return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            if req.method=='GET':gets.append(req.url)
            else:
                posts.append((parsed.path,req.post_data_json))
                assert parsed.path in ['/api/accounts','/api/accounts/update','/api/accounts/remove','/api/action'], 'Never send or schedule from account UI fixtures'
                if controls['fail_post']:return await r.fulfill(status=409,json={'error':'synthetic-cookie-secret must not be echoed'})
                if parsed.path=='/api/action':
                    assert req.post_data_json['action']=='login'
                    controls['running']={'action':'login','accountId':req.post_data_json['accountId']}
                    return await r.fulfill(json={'started':'login'})
            if parsed.path=='/api/state' and (controls['fail'] and not parsed.query or controls['state_fail'] and parsed.query):
                return await r.fulfill(status=503,json={'error':'isolated account failure'})
            status,data=http_request(parsed.path+('?' + parsed.query if parsed.query else ''),req.post_data_json if req.method=='POST' else None)
            if isinstance(data.get('accounts'),list):
                if controls['empty']:data['accounts']=[]
                data['running']=controls['running']
                for account in data['accounts']:
                    if not account.get('error'):
                        account['credential']['health']=controls['health'].get(account['id']) or ('valid' if account['id'] in ['main','second'] else 'unchecked' if account['credential']['present'] else 'missing')
                    if controls['long']:account['name']='长账户名称用于检查桌面菜单与底部布局不会发生重叠' if account['id']=='main' else account['name']
                if variant=='reference':
                    first,last=data['accounts'][0],data['accounts'][-1]
                    first.update(name='主账号',friendCount=10,messageCount=1,lastRun={'finishedAt':'2026-11-15T12:49:00+08:00'})
                    last.update(name='1')
                    data['accounts']=[first,last]
            if parsed.path=='/api/state' and parsed.query and status==200:
                data.update(accountName='主账号' if variant=='reference' else '日常账号' if 'accountId=main' in parsed.query else data.get('accountName'),now='2026-11-15T13:20:00+08:00' if variant=='reference' else '2026-11-14T08:20:00+08:00')
                data['schedule'].update(present=variant!='no_schedule',hour=7,minute=0,next='明天 07:00')
            return await r.fulfill(status=status,json=data)
        return await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://accounts.test/**',route)
    await page.goto('http://accounts.test/#/accounts/main/accounts')
    await expect(page.locator('#accountsTotal')).to_have_text('—' if variant=='load_error' else '0' if variant=='empty' else '2' if variant=='reference' else '4')
    if variant!='load_error': await expect(page.locator('#accountsGrid')).not_to_contain_text('正在读取账户')
    return posts,gets,errors,controls


@pytest.mark.parametrize('width,height',[(1280,800),(1440,900),(1625,968),(1920,1080),(1152,720)])
async def test_account_reference_composition(page,server,http_request,width,height):
    await page.set_viewport_size({'width':width,'height':height})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    await expect(page.locator('.account-card')).to_have_count(4)
    await expect(page.locator('#accountsEnabled')).to_have_text('2')
    await expect(page.locator('.account-card').first.locator('.account-card__login-status')).to_have_text('本次检查可用')
    await expect(page.locator('.account-card').first.locator('.toggle')).to_contain_text('每天 07:00')
    boxes=await page.locator('.account-card').evaluate_all('(nodes)=>nodes.map(n=>{let r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})')
    assert boxes[0]['y']==boxes[1]['y'] and boxes[2]['y']==boxes[3]['y']
    assert boxes[0]['right']<boxes[1]['x'] and boxes[0]['bottom']<boxes[2]['y']
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.locator('#pageTitle').click()
    await capture(page,f'accounts-reference-{width}.png')
    assert not posts and not errors


async def open_menu(page,account_id):
    summary=page.locator(f'.account-card[data-account-id="{account_id}"] summary')
    await summary.click()
    return page.locator(f'.account-card[data-account-id="{account_id}"] .account-card__manage')


async def test_main_menu_rename_only_updates_name_and_preserves_files(page,server,http_request):
    await page.set_viewport_size({'width':1625,'height':968})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    config=server.CONFIG.read_bytes();credential=server.account_paths_for('main').storage_state.read_bytes()
    menu=await open_menu(page,'main')
    await expect(menu.locator('.account-remove')).to_have_count(0)
    await expect(menu.locator('.account-login')).to_be_visible()
    await capture(page,'accounts-main-menu.png')
    await menu.locator('.account-rename').click()
    await expect(page.locator('#accountName')).to_have_value('日常账号')
    await page.locator('#accountName').fill('新的日常名称')
    await capture(page,'accounts-rename-dialog.png')
    await page.locator('#submitAccount').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#accountDialog')).not_to_be_visible()
    await expect(page.locator('.account-card[data-account-id="main"] h3')).to_have_text('新的日常名称')
    assert posts==[('/api/accounts/update',{'accountId':'main','name':'新的日常名称'})]
    assert server.CONFIG.read_bytes()==config and server.account_paths_for('main').storage_state.read_bytes()==credential
    assert not errors


async def test_new_unlogged_account_keeps_manage_and_enter_scope(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    card=page.locator('.account-card[data-account-id="new"]')
    await expect(card.locator('.toggle')).to_be_disabled()
    await expect(card.locator('.account-card__foot .account-login')).to_be_visible()
    menu=await open_menu(page,'new')
    await expect(menu.locator('a')).to_have_attribute('href','#/accounts/new/home')
    await menu.locator('a').click()
    await expect(page).to_have_url('http://accounts.test/#/accounts/new/home')
    await expect(page.locator('#headerAccount')).to_have_value('new')
    assert not posts and not errors


async def test_toggle_and_add_real_roundtrip_defaults_disabled(page,server,http_request):
    await page.set_viewport_size({"width":1625,"height":968})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    await page.locator('.account-card[data-account-id="test"] .toggle').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('.account-card[data-account-id="test"] .toggle')).to_have_attribute('aria-checked','true')
    assert posts==[('/api/accounts/update',{'accountId':'test','enabled':True})]
    await page.locator('#addAccountBtn').click()
    await page.locator('#submitAccount').click()
    await expect(page.locator('#accountDialogHint')).to_contain_text('请先填写')
    await page.locator('#accountName').fill('隔离新增账号')
    await capture(page,'accounts-add-dialog.png')
    await page.locator('#submitAccount').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#accountsTotal')).to_have_text('5')
    await expect(page.locator('#accountDialog')).not_to_be_visible()
    new=[x for x in http_request('/api/state')[1]['accounts'] if x['name']=='隔离新增账号'][0]
    assert not new['enabled'] and new['friendCount']==0 and not new['credential']['present']
    assert len(posts)==2 and posts[1]==('/api/accounts',{'name':'隔离新增账号'})
    assert not errors


async def test_remove_confirmation_preserves_files_and_main_protection(page,server,http_request):
    await page.set_viewport_size({'width':1625,'height':968})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    paths=server.account_paths_for('second');config=paths.task_config.read_bytes();credential=paths.storage_state.read_bytes()
    menu=await open_menu(page,'second');await menu.locator('.account-remove').click()
    await expect(page.locator('#removeAccountName')).to_have_text('账户：备用账号')
    await capture(page,'accounts-remove-confirmation.png')
    await page.locator('#cancelRemoveAccount').click()
    assert not posts
    await (await open_menu(page,'second')).locator('.account-remove').click()
    await page.locator('#confirmRemoveAccount').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#accountsTotal')).to_have_text('3')
    assert posts==[('/api/accounts/remove',{'accountId':'second'})]
    assert paths.task_config.read_bytes()==config and paths.storage_state.read_bytes()==credential
    assert http_request('/api/accounts/remove',{'accountId':'main'})[0]==400
    assert not errors


@pytest.mark.parametrize('variant',['load_error','empty','config_error','running','long','state_error','no_schedule'])
async def test_account_error_empty_long_and_running_states(page,server,http_request,variant):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request,variant)
    if variant=='load_error':
        await expect(page.locator('#accountsFeedback')).to_contain_text('读取失败')
        await capture(page,'accounts-state-load_error.png')
        controls['fail']=False;await page.locator('#retryAccountsLoad').click()
        await expect(page.locator('#accountsTotal')).to_have_text('4')
    elif variant=='empty':await expect(page.locator('.accounts-empty')).to_contain_text('还没有账户')
    elif variant=='config_error':
        card=page.locator('.account-card[data-account-id="test"]')
        await expect(card.locator('.account-card__login-status')).to_have_text('状态未读取')
        await expect(card.locator('.account-card__stats')).to_contain_text('—')
        await expect(card.locator('.toggle')).to_be_disabled()
    elif variant=='running':
        await expect(page.locator('.account-card[data-account-id="second"] .badge--running')).to_have_text('运行中')
        for toggle in await page.locator('.toggle').all():await expect(toggle).to_be_disabled()
        menu=await open_menu(page,'main');await expect(menu.locator('.account-login')).to_be_disabled()
        await page.keyboard.press('Escape')
    elif variant=='state_error':
        await expect(page.locator('.account-card')).to_have_count(4)
        await expect(page.locator('.account-card').first.locator('.toggle')).to_contain_text('时间未载入')
    elif variant=='no_schedule':await expect(page.locator('.account-card').first.locator('.toggle')).to_contain_text('未设置时间')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    if variant!='load_error':await capture(page,f'accounts-state-{variant}.png')
    assert not posts and not errors


def test_legacy_main_rename_migrates_without_changing_config_or_credentials(server,http_request):
    original=server.CONFIG.read_bytes();server.write_json(server.STATE_FILE,{'cookies':[]});credential=server.STATE_FILE.read_bytes()
    status,data=http_request('/api/accounts/update',{'accountId':'main','name':'日常账号'})
    assert status==200 and data['accounts'][0]['name']=='日常账号'
    assert not data['legacy'] and server.CONFIG.read_bytes()==original and server.STATE_FILE.read_bytes()==credential
    assert data['accounts'][0]['enabled']


async def test_account_menu_keyboard_and_unchanged_refresh_preserve_focus(page,server,http_request):
    await page.clock.install()
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    summary=page.locator('.account-card[data-account-id="second"] summary')
    await summary.focus();await page.keyboard.press('ArrowDown')
    await expect(page.locator('.account-card[data-account-id="second"] .account-card__manage a')).to_be_focused()
    await page.keyboard.press('End')
    await expect(page.locator('.account-card[data-account-id="second"] .account-remove')).to_be_focused()
    await page.clock.run_for(6100)
    await expect(page.locator('#refreshAccounts')).to_be_enabled()
    await expect(summary).to_have_attribute('aria-expanded','true')
    await expect(page.locator('.account-card[data-account-id="second"] .account-remove')).to_be_focused()
    await page.keyboard.press('Escape')
    await expect(summary).to_have_attribute('aria-expanded','false')
    await expect(summary).to_be_focused()
    await open_menu(page,'second');await page.locator('#pageTitle').click()
    await expect(summary).to_have_attribute('aria-expanded','false')
    assert not posts and not errors


async def test_account_save_failure_retains_name_and_recovers(page,server,http_request):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'post_error')
    await (await open_menu(page,'second')).locator('.account-rename').click()
    await page.locator('#accountName').fill('失败后保留的名称')
    await page.locator('#submitAccount').click()
    await expect(page.locator('#accountDialogHint')).to_have_text('名称未保存，请稍后重试。')
    await expect(page.locator('#accountName')).to_have_value('失败后保留的名称')
    await expect(page.locator('#submitAccount')).to_be_enabled()
    await expect(page.locator('body')).not_to_contain_text('synthetic-cookie-secret')
    await capture(page,'accounts-rename-error.png')
    controls['fail_post']=False
    await page.locator('#submitAccount').click()
    await expect(page.locator('#accountDialog')).not_to_be_visible()
    await expect(page.locator('.account-card[data-account-id="second"] h3')).to_have_text('失败后保留的名称')
    assert len(posts)==2 and not errors


async def test_refresh_failure_retains_stale_cards_but_blocks_mutations(page,server,http_request):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    controls['fail']=True;await page.locator('#refreshAccounts').click()
    await expect(page.locator('#accountsFeedback')).to_contain_text('上次读取')
    await expect(page.locator('.account-card')).to_have_count(4)
    await expect(page.locator('.account-card').first.locator('.toggle')).to_be_disabled()
    await capture(page,'accounts-stale-error.png')
    controls['fail']=False;await page.locator('#retryAccountsLoad').click()
    await expect(page.locator('#accountsFeedback')).not_to_be_visible()
    await expect(page.locator('.account-card').first.locator('.toggle')).to_be_enabled()
    assert not posts and not errors


async def test_inflight_account_save_blocks_close_navigation_and_duplicates(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    gate=asyncio.Event();started=asyncio.Event()
    async def delayed(r):started.set();await gate.wait();await r.fallback()
    await page.route('**/api/accounts/update',delayed)
    await (await open_menu(page,'second')).locator('.account-rename').click()
    await page.locator('#accountName').fill('新备用名称')
    await page.locator('#submitAccount').evaluate('e=>{e.click();e.click()}')
    await asyncio.wait_for(started.wait(),10)
    await expect(page.locator('#accountName')).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(page.locator('#accountDialog')).to_be_visible()
    await page.evaluate("location.hash='#/accounts/new/home'")
    await expect(page).to_have_url('http://accounts.test/#/accounts/main/accounts')
    gate.set()
    await expect(page.locator('#accountDialog')).not_to_be_visible()
    assert posts==[('/api/accounts/update',{'accountId':'second','name':'新备用名称'})] and not errors


async def test_remove_current_account_routes_to_main_after_confirm(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    await page.evaluate("location.hash='#/accounts/second/accounts'")
    await expect(page.locator('.account-card[data-account-id="second"]')).to_have_attribute('data-current','true')
    await (await open_menu(page,'second')).locator('.account-remove').click()
    await page.locator('#confirmRemoveAccount').click()
    await expect(page).to_have_url('http://accounts.test/#/accounts/main/accounts')
    await expect(page.locator('#headerAccount')).to_have_value('main')
    await expect(page.locator('#accountsTotal')).to_have_text('3')
    assert posts==[('/api/accounts/remove',{'accountId':'second'})] and not errors


async def test_login_duplicate_guard_targets_card_account(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    await page.locator('.account-card[data-account-id="new"] .account-login').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('.account-card[data-account-id="new"] .badge--running')).to_have_text('等待扫码')
    await expect(page.locator('.account-card[data-account-id="second"] .toggle')).to_be_disabled()
    assert posts==[('/api/action',{'action':'login','accountId':'new'})] and not errors


def test_rename_does_not_reenable_or_require_repairing_invalid_config(server,http_request):
    seed_accounts(server,http_request)
    paths=server.account_paths_for('second');paths.task_config.write_text('{broken')
    status,data=http_request('/api/accounts/update',{'accountId':'second','name':'异常账户新名称'})
    assert status==200
    account=next(x for x in data['accounts'] if x['id']=='second')
    assert account['enabled'] and account['error'] and account['name']=='异常账户新名称'
    assert paths.task_config.read_text()=='{broken'


def test_invalid_legacy_update_does_not_create_registry(server,http_request):
    assert not server.REGISTRY.exists()
    for payload in [{'accountId':'main','name':''},{'accountId':'other','name':'无效'},{'accountId':'main','enabled':'yes'}]:
        assert http_request('/api/accounts/update',payload)[0]==400
        assert not server.REGISTRY.exists()


@pytest.mark.parametrize('health,label',[('expired','需要重新登录'),('verification_required','需要安全验证'),('check_failed','检查未完成')])
async def test_account_health_outcomes_are_distinct(page,server,http_request,health,label):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    controls['health']['test']=health
    await page.locator('#refreshAccounts').click()
    await expect(page.locator('.account-card[data-account-id="test"] .account-card__login-status')).to_have_text(label)
    await capture(page,f'accounts-health-{health}.png')
    assert not posts and not errors


@pytest.mark.parametrize('operation',['toggle','remove','login','add'])
async def test_account_action_failures_keep_data_and_allow_retry(page,server,http_request,operation):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'post_error')
    before=server.REGISTRY.read_bytes()
    if operation=='toggle':
        await page.locator('.account-card[data-account-id="test"] .toggle').click()
        await expect(page.locator('#accountsFeedback')).to_contain_text('状态未保存')
        await expect(page.locator('.account-card[data-account-id="test"] .toggle')).to_have_attribute('aria-checked','false')
    elif operation=='remove':
        await (await open_menu(page,'second')).locator('.account-remove').click()
        await page.locator('#confirmRemoveAccount').click()
        await expect(page.locator('#removeAccountError')).to_contain_text('未移除')
        await expect(page.locator('#confirmRemoveAccount')).to_be_enabled()
        await capture(page,'accounts-remove-error.png')
    elif operation=='login':
        await page.locator('.account-card[data-account-id="new"] .account-login').click()
        await expect(page.locator('#accountsFeedback')).to_contain_text('扫码未能开始')
        await expect(page.locator('.account-card[data-account-id="new"] .account-login')).to_be_enabled()
    else:
        await page.locator('#addAccountBtn').click()
        await page.locator('#accountName').fill('添加失败后保留')
        await page.locator('#submitAccount').click()
        await expect(page.locator('#accountDialogHint')).to_have_text('账户未添加，请稍后重试。')
        await expect(page.locator('#accountName')).to_have_value('添加失败后保留')
    assert server.REGISTRY.read_bytes()==before and len(posts)==1 and not errors
    await expect(page.locator('body')).not_to_contain_text('synthetic-cookie-secret')


async def test_loading_has_unknown_counts_until_overview_arrives(page,server,http_request):
    await page.set_viewport_size({'width':1440,'height':1050})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    gate=asyncio.Event();started=asyncio.Event()
    async def delayed(r):started.set();await gate.wait();await r.fallback()
    await page.route('**/api/state',delayed)
    await page.reload(wait_until='domcontentloaded')
    await asyncio.wait_for(started.wait(),10)
    await expect(page.locator('#accountsTotal')).to_have_text('—')
    await expect(page.locator('.accounts-empty')).to_contain_text('正在读取账户')
    await capture(page,'accounts-loading.png')
    gate.set();await expect(page.locator('#accountsTotal')).to_have_text('4')
    assert not posts and not errors


async def test_global_batch_locks_actions_without_claiming_individual_execution(page,server,http_request):
    await page.set_viewport_size({'width':1625,'height':1100})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    controls['running']={'action':'send-all','accountId':None}
    await page.locator('#refreshAccounts').click()
    await expect(page.locator('#accountsFeedback')).to_contain_text('批量任务正在运行')
    await expect(page.locator('.badge--running')).to_have_count(0)
    await expect(page.locator('#accountsEnabled')).to_have_text('2')
    for toggle in await page.locator('.toggle').all():await expect(toggle).to_be_disabled()
    await expect(page.locator('.account-card[data-account-id="new"] .account-login')).to_be_disabled()
    await capture(page,'accounts-batch-running.png')
    assert not posts and not errors


@pytest.mark.parametrize('button',['#retryAccountsLoad','#refreshAccounts'])
async def test_scoped_state_failure_recovers_without_leaving_accounts(page,server,http_request,button):
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'state_error')
    await expect(page.locator('#accountsFeedback')).to_contain_text('运行时间未载入')
    await expect(page.locator('#sidebarRunStatus')).to_have_text('账户状态未载入')
    controls['state_fail']=False
    await page.locator(button).click()
    await expect(page.locator('.account-card').first.locator('.toggle')).to_contain_text('每天 07:00')
    await expect(page.locator('#accountsFeedback')).not_to_be_visible()
    await expect(page.locator('#sidebarRunStatus')).not_to_have_text('账户状态未载入')
    assert not posts and not errors


async def test_scoped_refresh_response_cannot_overwrite_new_account(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    gate=asyncio.Event();started=asyncio.Event();done=asyncio.Event()
    async def delayed(r):
        started.set();await gate.wait();await r.fallback();done.set()
    await page.route('**/api/state?accountId=main',delayed)
    await page.locator('#refreshAccounts').click()
    await asyncio.wait_for(started.wait(),10)
    await page.evaluate("location.hash='#/accounts/new/home'")
    await expect(page.locator('#headerAccount')).to_have_value('new')
    gate.set();await asyncio.wait_for(done.wait(),10)
    await page.evaluate('()=>new Promise(r=>requestAnimationFrame(()=>requestAnimationFrame(r)))')
    await expect(page.locator('#headerAccount')).to_have_value('new')
    await expect(page).to_have_url('http://accounts.test/#/accounts/new/home')
    assert not posts and not errors


async def test_enabled_broken_account_can_disable_but_cannot_reenable(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    path=server.account_paths_for('second').task_config;path.write_text('{broken')
    await page.locator('#refreshAccounts').click()
    toggle=page.locator('.account-card[data-account-id="second"] .toggle')
    await expect(toggle).to_be_enabled()
    await expect(toggle).to_have_attribute('aria-checked','true')
    await toggle.click()
    await expect(toggle).to_have_attribute('aria-checked','false')
    await expect(toggle).to_be_disabled()
    assert posts==[('/api/accounts/update',{'accountId':'second','enabled':False})]
    assert path.read_text()=='{broken' and not errors


async def test_broken_secondary_remove_keeps_files_and_main_never_has_remove(page,server,http_request):
    await page.set_viewport_size({'width':1625,'height':1100})
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'config_error')
    paths=server.account_paths_for('test');config=paths.task_config.read_bytes();credential=paths.storage_state.read_bytes()
    menu=await open_menu(page,'test')
    await expect(menu.locator('.account-remove')).to_be_enabled()
    await expect(menu.locator('.account-login')).to_be_visible()
    await capture(page,'accounts-broken-config-menu.png')
    await menu.locator('.account-remove').click()
    await page.locator('#confirmRemoveAccount').click()
    await expect(page.locator('#accountsTotal')).to_have_text('3')
    await expect((await open_menu(page,'main')).locator('.account-remove')).to_have_count(0)
    assert paths.task_config.read_bytes()==config and paths.storage_state.read_bytes()==credential
    assert posts==[('/api/accounts/remove',{'accountId':'test'})] and not errors


async def test_broken_account_scan_uses_target_and_still_respects_global_lock(page,server,http_request):
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'config_error')
    controls['running']={'action':'send','accountId':'second'}
    await page.locator('#refreshAccounts').click()
    menu=await open_menu(page,'test')
    await expect(menu.locator('.account-login')).to_be_disabled()
    await expect(menu.locator('.account-remove')).to_be_disabled()
    await page.keyboard.press('Escape')
    controls['running']=None;await page.locator('#refreshAccounts').click()
    menu=await open_menu(page,'test')
    await expect(menu.locator('.account-login')).to_be_enabled()
    await menu.locator('.account-login').click()
    await expect(page.locator('.account-card[data-account-id="test"] .badge--running')).to_have_text('等待扫码')
    assert posts==[('/api/action',{'action':'login','accountId':'test'})] and not errors


async def test_latest_two_account_reference(page,server,http_request):
    await page.set_viewport_size({'width':1672,'height':941})
    posts,gets,errors,controls=await install_accounts(page,server,http_request,'reference')
    await expect(page.locator('.account-card')).to_have_count(2)
    await expect(page.locator('#accountsEnabled')).to_have_text('1')
    await expect(page.locator('.account-card[data-current="true"] .account-card__name h3')).to_have_text('主账号')
    await expect(page.locator('.account-card').last.locator('.account-card__login-status')).to_have_text('未登录')
    await expect(page.locator('.account-card').last.locator('.toggle')).to_be_disabled()
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await capture(page,'accounts-latest-reference-1672.png')
    assert not posts and not errors


async def test_latest_account_small_window(page,server,http_request):
    await page.set_viewport_size({'width':390,'height':844})
    posts,gets,errors,controls=await install_accounts(page,server,http_request)
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await open_menu(page,'main')
    await expect(page.locator('.account-card[data-account-id="main"] .account-rename')).to_be_visible()
    await capture(page,'accounts-latest-small-390.png')
    assert not posts and not errors
