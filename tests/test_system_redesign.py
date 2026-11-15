"""Login and history use isolated local storage; action requests are intercepted."""
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect
from app.main import _append_run_history
from test_contacts_api import page, server, http_request


async def capture(page, name):
    directory = os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if directory:
        await page.screenshot(path=str(Path(directory)/name), animations='disabled', full_page=True)


async def install_system(page, server, http_request, variant='normal', health='valid'):
    times=['14T07:04','13T07:03','12T07:04','11T07:03','10T18:20','10T07:03','09T18:10','09T07:04','08T07:03','07T07:04','06T07:03','05T07:04']
    if variant=='reference':
        from datetime import datetime,timedelta
        top=['2026-11-15T12:49','2026-11-15T12:42','2026-11-15T12:18','2026-11-15T08:06','2026-11-15T07:34','2026-11-15T07:02','2026-11-14T07:04','2026-11-13T07:04','2026-11-12T07:03','2026-11-11T07:03']
        top += [(datetime(2026,11,10,7,3)-timedelta(days=i)).isoformat(timespec='minutes') for i in range(32)]
        for i,stamp in enumerate(top):
            dry=i==1 or i>=35
            count=9 if i in [0,2,3,4] else 10
            _append_run_history(server.ARTIFACTS,{'task_id':'isolated-reference','finished_at':stamp+':00+08:00','dry_run':dry,
                'results':[{'target':f'隔离好友{j+1:02d}','status':'failed' if i in [2,3,4] or i==5 and j>0 else 'success','sent':0 if dry else 1} for j in range(count)]})
    elif variant != 'empty':
        for i,time in enumerate(times):
            _append_run_history(server.ARTIFACTS, {'task_id':'isolated-test','finished_at':f'2026-11-{time}:00+08:00','dry_run':i in [4,6],
                'results':[{'target':f'隔离好友{j+1:02d}','status':'unknown' if i==3 and j>=8 else 'success','sent':0 if i in [4,6] else 1} for j in range(10)]})
    posts,gets,errors=[],[],[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    controls={'fail_history':variant=='history_error','fail_state':variant=='state_error','fail_import':variant=='import_error','fail_clear':variant=='clear_error','health':health,'present':health!='missing'}
    async def route(r):
        req=r.request; parsed=urlsplit(req.url)
        if parsed.path=='/api/stream': return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            if req.method=='GET': gets.append(req.url)
            else:
                posts.append((parsed.path,req.post_data_json))
                assert parsed.path in ['/api/action','/api/credential/import','/api/credential/clear'], 'No sends or scheduling in UI tests'
                if parsed.path=='/api/action':
                    assert req.post_data_json['action'] in ['health-check','login']
                    return await r.fulfill(json={'started':req.post_data_json['action']})
            if parsed.path=='/api/health':
                return await r.fulfill(json={'credential':{'present':True,'health':'valid','checkedAt':'2026-11-14T08:20:00+08:00'},'running':None})
            if parsed.path=='/api/history' and controls['fail_history']:
                return await r.fulfill(status=503,json={'error':'隔离测试读取失败'})
            if parsed.path=='/api/state' and parsed.query and controls['fail_state']:
                return await r.fulfill(status=503,json={'error':'隔离测试账户不可用'})
            if parsed.path=='/api/credential/import' and controls['fail_import']:
                return await r.fulfill(status=400,json={'error':'Do not echo synthetic-cookie-secret'})
            if parsed.path=='/api/credential/clear' and controls['fail_clear']:
                return await r.fulfill(status=409,json={'error':'Do not echo synthetic-cookie-secret'})
            status,data=http_request(parsed.path+('?' + parsed.query if parsed.query else ''),req.post_data_json if req.method=='POST' else None)
            if parsed.path=='/api/credential/import' and status==200:
                controls.update(health='unchecked',present=True)
            if parsed.path=='/api/credential/clear' and status==200:
                controls.update(health='missing',present=False)
            if parsed.path=='/api/state' and not parsed.query:
                data={'accounts':[{'id':'main','name':'日常账号'},{'id':'second','name':'备用账号'}]}
            elif parsed.path in ['/api/state','/api/credential/import','/api/credential/clear'] and status==200:
                data.update(accountName='备用账号' if 'accountId=second' in parsed.query or (req.method=='POST' and req.post_data_json.get('accountId')=='second') else '主账号' if variant=='reference' else '日常账号',now='2026-11-15T13:20:00+08:00' if variant=='reference' else '2026-11-14T08:20:00+08:00')
                data['schedule'].update(present=True,next='明天 07:00')
                data['credential']={'present':controls['present'],'health':controls['health'],'checkedAt':('2026-11-15T13:10:00+08:00' if variant=='reference' else '2026-11-14T08:20:00+08:00') if controls['health']=='valid' else None}
                if variant=='running': data['running']={'action':'send','accountId':'main'}
            return await r.fulfill(status=status,json=data)
        return await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://system.test/**',route)
    await page.goto('http://system.test/#/accounts/main/system')
    await expect(page.locator('#historyMeta')).to_have_text('读取失败' if variant=='history_error' else '共 0 条' if variant=='empty' else '共 42 条' if variant=='reference' else '共 12 条')
    if variant!='state_error': await expect(page.locator('#credentialCardTitle')).to_have_text('主账号' if variant=='reference' else '日常账号')
    return posts,gets,errors,controls


@pytest.mark.parametrize('width,height',[(1280,800),(1440,900),(1552,1013),(1920,1080),(1152,720)])
async def test_system_reference_composition(page,server,http_request,width,height):
    await page.set_viewport_size({'width':width,'height':height})
    posts,gets,errors,controls=await install_system(page,server,http_request)
    await expect(page.locator('#historyContent')).to_be_visible()
    await expect(page.locator('#historyList tr')).to_have_count(10)
    await expect(page.locator('#credBadge')).to_have_text('本次检查可用')
    await page.locator('#historyFilterToggle').click()
    await page.locator('#historyStart').fill('2026-11-09')
    await page.locator('#historyEnd').fill('2026-11-14')
    await expect(page.locator('#historyDraftHint')).to_be_visible()
    await expect(page.locator('#historyPageText')).to_have_text('第 1 / 2 页')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    rects=await page.locator('.credential-card,.history-card').evaluate_all('(nodes)=>nodes.map(n=>{let r=n.getBoundingClientRect();return {x:r.x,y:r.y,bottom:r.bottom,right:r.right}})')
    assert rects[0]['bottom']<rects[1]['y']
    if width==1552: assert abs(rects[0]['y']-156)<2 and abs(rects[1]['y']-276)<2
    await expect(page.locator('.system-footer')).not_to_be_visible()
    await page.locator('#pageTitle').click()
    await capture(page,f'system-reference-{width}.png')
    assert not posts and not errors


async def test_history_filters_paging_pending_and_validation(page,server,http_request):
    await page.set_viewport_size({'width':1552,'height':1013})
    posts,gets,errors,controls=await install_system(page,server,http_request)
    await expect(page.locator('#historyPrev')).to_be_disabled()
    await page.locator('#historyNext').click()
    await expect(page.locator('#historyList tr')).to_have_count(2)
    await expect(page.locator('#historyPageText')).to_have_text('第 2 / 2 页')
    await page.locator('#historyFilters [data-history-filter="dry"]').click()
    await expect(page.locator('#historyPageSize')).to_contain_text('共 2 条')
    await expect(page.locator('#historyPageText')).to_have_text('第 1 / 1 页')
    await expect(page.locator('#historyList')).to_contain_text('检查完成')
    await page.locator('#historyFilterToggle').click()
    await page.locator('#historyStart').fill('2026-11-14')
    await page.locator('#historyEnd').fill('2026-11-09')
    before=len([x for x in gets if '/api/history' in x])
    await page.locator('#historySearch').click()
    await expect(page.locator('#historyQueryError')).to_contain_text('开始日期不能晚于结束日期')
    assert before==len([x for x in gets if '/api/history' in x])
    await page.locator('#historyStart').fill('2026-11-09')
    await page.locator('#historyEnd').fill('2026-11-14')
    await page.locator('#historySearch').click()
    await expect(page.locator('#historyDraftHint')).not_to_be_visible()
    await expect(page.locator('#historyFilterLabel')).to_have_text('筛选 · 1')
    await page.locator('#historyReset').click()
    await expect(page.locator('#historyPageSize')).to_contain_text('共 12 条')
    await page.locator('#historyStatus').select_option('unknown')
    await page.locator('#historySearch').click()
    await expect(page.locator('#historyList tr')).to_have_count(1)
    await expect(page.locator('#historyList')).to_contain_text('待核对 2')
    await page.locator('.history-open').click()
    await expect(page.locator('#runDetailDialog')).to_be_visible()
    await expect(page.locator('#reviewFromRun')).to_be_visible()
    await capture(page,'system-unknown-detail.png')
    await page.keyboard.press('Escape')
    await page.locator('#historySummary').click()
    await expect(page.locator('#historyContent')).not_to_be_visible()
    await page.locator('#historySummary').click()
    await expect(page.locator('#historyContent')).to_be_visible()
    assert not posts and not errors


@pytest.mark.parametrize('health,label',[('missing','未配置凭证'),('unchecked','待验证'),('expired','需要重新登录'),('verification_required','需要安全验证'),('check_failed','检查未完成')])
async def test_credential_truthful_states(page,server,http_request,health,label):
    posts,gets,errors,controls=await install_system(page,server,http_request,health=health)
    await expect(page.locator('#credBadge')).to_have_text(label)
    await expect(page.locator('#credStateText')).to_have_text('最近检查：尚未检查')
    if health=='missing': await expect(page.locator('#clearCred')).to_be_disabled()
    await capture(page,f'system-health-{health}.png')
    assert not posts and not errors


async def test_import_clear_roundtrip_and_dialog_privacy(page,server,http_request):
    await page.set_viewport_size({'width':1552,'height':1013})
    posts,gets,errors,controls=await install_system(page,server,http_request)
    await page.locator('#credentialImport').click()
    await expect(page.locator('#credentialDialogAccount')).to_have_text('当前账户：日常账号')
    await page.locator('#submitCredential').click()
    await expect(page.locator('#credentialHint')).to_contain_text('请先粘贴')
    await page.locator('#credentialText').fill('synthetic-cookie-secret')
    await page.locator('#submitCredential').click()
    await expect(page.locator('#credentialHint')).to_contain_text('不是有效的 JSON')
    assert not posts
    await capture(page,'system-import-validation.png')
    await page.keyboard.press('Escape')
    await expect(page.locator('#credentialText')).to_have_value('')
    await page.locator('#credentialImport').click()
    cookie=[{'name':'sessionid','value':'synthetic-cookie-secret','domain':'.douyin.com','path':'/'}]
    await page.locator('#credentialFile').set_input_files({'name':'isolated-cookies.json','mimeType':'application/json','buffer':json.dumps(cookie).encode()})
    await expect(page.locator('#credentialHint')).to_contain_text('已读取')
    await page.locator('#submitCredential').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#credentialDialog')).not_to_be_visible()
    await expect(page.locator('#credBadge')).to_have_text('待验证')
    assert len(posts)==1 and posts[0][1]['accountId']=='main'
    assert (server.DATA_ROOT/'storage-state.json').exists()
    await expect(page.locator('body')).not_to_contain_text('synthetic-cookie-secret')
    await page.locator('#clearCred').click()
    await expect(page.locator('#clearCredentialAccount')).to_contain_text('日常账号')
    await capture(page,'system-clear-confirmation.png')
    await page.locator('#cancelClearCredential').click()
    assert len(posts)==1 and (server.DATA_ROOT/'storage-state.json').exists()
    await page.locator('#clearCred').click()
    await page.locator('#confirmClearCredential').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#credBadge')).to_have_text('未配置凭证')
    assert len(posts)==2 and posts[1][1]=={'accountId':'main'}
    assert not (server.DATA_ROOT/'storage-state.json').exists() and server.load_config()['friends']==['原有好友']
    await expect(page.locator('#historyPageSize')).to_contain_text('共 12 条')
    assert not errors


@pytest.mark.parametrize('variant', ['history_error','state_error','empty','import_error','clear_error','running'])
async def test_system_error_empty_and_busy_states(page,server,http_request,variant):
    posts,gets,errors,controls=await install_system(page,server,http_request,variant)
    if variant=='history_error':
        await expect(page.locator('#historyPageSize')).to_have_text('记录未载入')
        await expect(page.locator('#historyFilters b').first).to_have_text('—')
        await capture(page,'system-history-error.png')
        controls['fail_history']=False
        await page.locator('#historyRetry').click()
        await expect(page.locator('#historyPageSize')).to_contain_text('共 12 条')
    elif variant=='state_error':
        await expect(page.locator('#credentialFeedback')).to_be_visible()
        await expect(page.locator('#checkLoginBtn')).to_be_disabled()
        await expect(page.locator('#historyList tr')).to_have_count(10)
        controls['fail_state']=False
        await page.locator('#retrySystemLoad').click()
        await expect(page.locator('#credBadge')).to_have_text('本次检查可用')
    elif variant=='empty':
        await expect(page.locator('#historyList')).to_contain_text('没有符合条件')
        await expect(page.locator('#historyNext')).to_be_disabled()
        await capture(page,'system-empty.png')
    elif variant=='import_error':
        await page.locator('#credentialImport').click()
        await page.locator('#credentialText').fill('[]')
        await page.locator('#submitCredential').click()
        await expect(page.locator('#credentialHint')).to_contain_text('导入未完成')
        await expect(page.locator('#credentialText')).to_have_value('[]')
        await expect(page.locator('#submitCredential')).to_be_enabled()
    elif variant=='clear_error':
        await page.locator('#clearCred').click()
        await page.locator('#confirmClearCredential').click()
        await expect(page.locator('#clearCredentialError')).to_be_visible()
        await expect(page.locator('#confirmClearCredential')).to_be_enabled()
        await expect(page.locator('#clearCredentialDialog')).to_be_visible()
    else:
        for selector in ['#checkLoginBtn','#loginBtn','#credentialImport','#clearCred']:
            await expect(page.locator(selector)).to_be_disabled()
    await expect(page.locator('body')).not_to_contain_text('synthetic-cookie-secret')
    assert not errors


async def test_account_switch_resets_filters_and_never_leaks_credentials(page,server,http_request):
    assert http_request('/api/accounts',{'name':'second'})[0]==200
    posts,gets,errors,controls=await install_system(page,server,http_request)
    await page.locator('#historyFilterToggle').click()
    await page.locator('#historyStart').fill('2026-11-09')
    await page.locator('#historySearch').click()
    await expect(page.locator('#historyPageSize')).to_contain_text('共 8 条')
    await page.locator('#credentialImport').click()
    await page.locator('#credentialText').fill('synthetic-cookie-secret')
    await page.evaluate("location.hash='#/accounts/second/system'")
    await expect(page.locator('#credentialCardTitle')).to_have_text('备用账号')
    await expect(page.locator('#credentialDialog')).not_to_be_visible()
    await expect(page.locator('#credentialText')).to_have_value('')
    await expect(page.locator('#historyStart')).to_have_value('')
    await expect(page.locator('#historyPageSize')).to_contain_text('共 0 条')
    await page.locator('#credentialImport').click()
    await page.locator('#credentialText').fill(json.dumps([{'name':'sessionid','value':'synthetic-cookie-secret','domain':'.douyin.com','path':'/'}]))
    await page.locator('#submitCredential').click()
    await expect(page.locator('#credBadge')).to_have_text('待验证')
    assert posts[0][1]['accountId']=='second'
    assert (server.DATA_ROOT/'storage_state/second.json').exists()
    assert not (server.DATA_ROOT/'storage-state.json').exists()
    await page.locator('#clearCred').click()
    await expect(page.locator('#clearCredentialAccount')).to_have_text('当前账户：备用账号')
    await page.locator('#confirmClearCredential').click()
    await expect(page.locator('#credBadge')).to_have_text('未配置凭证')
    assert posts[1][1]=={'accountId':'second'}
    await page.locator('#headerAccount').focus()
    await page.locator('#headerAccount').select_option('main')
    await expect(page.locator('#historyPageSize')).to_contain_text('共 12 条')
    assert not errors


async def test_inflight_import_blocks_navigation_and_escape(page,server,http_request):
    import asyncio
    posts,gets,errors,controls=await install_system(page,server,http_request)
    started=asyncio.Event(); release=asyncio.Event()
    async def delayed(r):
        started.set(); await release.wait(); await r.fallback()
    await page.route('**/api/credential/import',delayed)
    await page.locator('#credentialImport').click()
    await page.locator('#credentialText').fill(json.dumps([{'name':'sessionid','value':'synthetic-cookie-secret','domain':'.douyin.com','path':'/'}]))
    await page.locator('#submitCredential').evaluate('e=>{e.click();e.click()}')
    await asyncio.wait_for(started.wait(),10)
    await expect(page.locator('#submitCredential')).to_be_disabled()
    await page.keyboard.press('Escape')
    await expect(page.locator('#credentialDialog')).to_be_visible()
    await page.evaluate("location.hash='#/accounts/second/system'")
    await expect(page).to_have_url('http://system.test/#/accounts/main/system')
    release.set()
    await expect(page.locator('#credentialDialog')).not_to_be_visible()
    assert len(posts)==1 and posts[0][1]['accountId']=='main'
    assert not errors


async def test_health_check_rejects_response_after_account_roundtrip(page,server,http_request):
    import asyncio
    assert http_request('/api/accounts',{'name':'second'})[0]==200
    posts,gets,errors,controls=await install_system(page,server,http_request,health='unchecked')
    started=asyncio.Event(); release=asyncio.Event(); done=asyncio.Event()
    async def delayed(r):
        started.set(); await release.wait()
        await r.fulfill(json={'credential':{'present':True,'health':'expired'},'running':None}); done.set()
    await page.route('**/api/health?*',delayed)
    await page.locator('#checkLoginBtn').evaluate('e=>{e.click();e.click()}')
    await asyncio.wait_for(started.wait(),10)
    await expect(page.locator('#credBadge')).to_have_text('正在检查登录')
    await page.locator('#headerAccount').focus()
    await page.locator('#headerAccount').select_option('second')
    await expect(page.locator('#credentialCardTitle')).to_have_text('备用账号')
    await page.locator('#headerAccount').focus()
    await page.locator('#headerAccount').select_option('main')
    await expect(page.locator('#credentialCardTitle')).to_have_text('日常账号')
    release.set(); await asyncio.wait_for(done.wait(),10)
    await page.evaluate('()=>new Promise(resolve=>requestAnimationFrame(()=>requestAnimationFrame(resolve)))')
    await expect(page.locator('#credBadge')).to_have_text('待验证')
    assert posts==[('/api/action',{'accountId':'main','action':'health-check'})] and not errors


async def test_oversized_file_and_scan_login_are_guarded(page,server,http_request):
    posts,gets,errors,controls=await install_system(page,server,http_request)
    await page.locator('#credentialImport').click()
    await page.locator('#credentialFile').set_input_files({'name':'oversized.json','mimeType':'application/json','buffer':b' '*(2*1024*1024+1)})
    await expect(page.locator('#credentialHint')).to_contain_text('不能超过 2 MB')
    assert not posts
    await page.keyboard.press('Escape')
    await page.locator('#loginBtn').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#loginBtn')).to_be_enabled()
    assert posts==[('/api/action',{'accountId':'main','action':'login'})] and not errors


@pytest.mark.parametrize('next_action',[None,'send'])
async def test_health_poll_recovers_missed_done_without_unlocking_active_task(page,server,http_request,next_action):
    import asyncio
    await page.add_init_script('''window.EventSource=class { constructor(){window.isolatedEventSource=this} close(){} };''')
    posts,gets,errors,controls=await install_system(page,server,http_request,health='unchecked')
    started=asyncio.Event();release=asyncio.Event();done=asyncio.Event()
    async def delayed(r):
        started.set();await release.wait()
        await r.fulfill(json={'credential':{'present':True,'health':'valid','checkedAt':'2026-11-14T08:20:00+08:00'},'running':{'action':next_action} if next_action else None})
        done.set()
    await page.route('**/api/health?*',delayed)
    await page.locator('#checkLoginBtn').click()
    await asyncio.wait_for(started.wait(),10)
    # Deliver started but deliberately omit done, as on a disconnected event stream.
    await page.evaluate("isolatedEventSource.onmessage({data:JSON.stringify({type:'started',accountId:'main',action:'health-check'})})")
    await expect(page.locator('#credBadge')).to_have_text('正在检查登录')
    release.set();await asyncio.wait_for(done.wait(),10)
    await expect(page.locator('#credBadge')).to_have_text('本次检查可用')
    if next_action:
        await expect(page.locator('#checkLoginBtn')).to_be_disabled()
        await expect(page.locator('#credentialImport')).to_be_disabled()
    else:
        await expect(page.locator('#checkLoginBtn')).to_be_enabled()
        await expect(page.locator('#credentialImport')).to_be_enabled()
    assert posts==[('/api/action',{'accountId':'main','action':'health-check'})] and not errors


async def test_latest_system_reference_preview(page,server,http_request):
    await page.set_viewport_size({'width':1672,'height':941})
    posts,gets,errors,controls=await install_system(page,server,http_request,'reference')
    await expect(page.locator('#historySendTotal')).to_have_text('34')
    await expect(page.locator('#historyDryTotal')).to_have_text('8')
    await expect(page.locator('#historyAllTotal')).to_have_text('42')
    await expect(page.locator('#historyList tr')).to_have_count(10)
    await expect(page.locator('#historyPageText')).to_have_text('第 1 / 5 页')
    await expect(page.locator('#historyQueryPanel')).not_to_be_visible()
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await capture(page,'system-latest-reference-1672.png')
    await page.locator('#historyNext').click()
    await expect(page.locator('#historyPageText')).to_have_text('第 2 / 5 页')
    await expect(page.locator('#historyAllTotal')).to_have_text('42')
    await page.locator('#historyFilters [data-history-filter="dry"]').click()
    await expect(page.locator('#historyList tr')).to_have_count(8)
    await expect(page.locator('#historyAllTotal')).to_have_text('42')
    await expect(page.locator('#historyDryTotal')).to_have_text('8')
    assert not posts and not errors


async def test_summary_unknown_on_error_and_recovers(page,server,http_request):
    posts,gets,errors,controls=await install_system(page,server,http_request,'history_error')
    for id in ['historySendTotal','historyDryTotal','historyAllTotal']:
        await expect(page.locator('#'+id)).to_have_text('—')
    controls['fail_history']=False
    await page.locator('#historyRetry').click()
    await expect(page.locator('#historyAllTotal')).to_have_text('12')
    await expect(page.locator('#historySendTotal')).to_have_text('10')
    await expect(page.locator('#historyDryTotal')).to_have_text('2')
    assert not posts and not errors


async def test_system_reference_small_window(page,server,http_request):
    await page.set_viewport_size({'width':390,'height':844})
    posts,gets,errors,controls=await install_system(page,server,http_request)
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.locator('#historyNext').click()
    await expect(page.locator('#historyList tr')).to_have_count(2)
    await capture(page,'system-latest-small-390.png')
    assert not errors
