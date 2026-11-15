"""Task registration and past runs must not masquerade as today's delivery."""
from copy import deepcopy
import pytest
from playwright.async_api import expect
from app.health import report
from test_contacts_api import server, http_request, page


def ready_state():
    return {'accountId':'main','accountEnabled':True,'friends':['好友'], 'friendStatuses':['pending'], 'pendingTargets':['好友'], 'unknownTargets':[],
            'allTargetsComplete':False, 'credential':{'present':True,'health':'valid'}, 'sendQueueReady':True,
            'schedule':{'present':True,'hour':8,'minute':5,'next':'明天 08:05'},'now':'2026-11-14T07:00:00+08:00','running':None}


def test_registered_does_not_mean_today_completed():
    state = ready_state()
    scheduler = {'status':'ok','detail':'已注册'}
    assert report(state,scheduler)['summary']['status'] == 'waiting'
    state['now'] = '2026-11-14T10:00:00+08:00'
    state['lastRun'] = {'finishedAt':'2026-11-13T08:05:00+08:00'}
    assert report(state,scheduler)['summary']['status'] == 'pending'
    state.update(friendStatuses=['sent'],pendingTargets=[],allTargetsComplete=True)
    assert report(state,scheduler)['summary']['status'] == 'complete'
    state['credential']['health'] = 'expired'
    result = report(state,scheduler)
    assert result['summary']['status'] == 'complete'  # Today's delivery remains true.
    assert any(row['action']=='login' for row in result['issues'])  # Future runs still need fixing.


@pytest.mark.parametrize('change,title', [
    ({'unknownTargets':['好友'],'friendStatuses':['unknown']},'有发送结果待核对'),
    ({'friendStatuses':['failed']},'今天有发送失败'),
    ({'running':{'action':'send'}},'正在发送'),
    ({'running':{'action':'health-check'}},'正在检查'),
    ({'credential':{'present':True,'health':'check_failed','detail':'网络检查未完成'}},'登录状态尚未确认'),
    ({'catchUp':{'date':'2026-11-14','attempts':3}},'今日自动尝试已达上限'),
    ({'friendStatuses':['paused'],'pendingTargets':[]},'所有好友都已暂停'),
])
def test_summary_states(change,title):
    state = {**ready_state(),**change}
    assert report(state,{'status':'ok','detail':'已注册'})['summary']['title'] == title


@pytest.mark.parametrize('width', [1320,390])
async def test_health_ui_read_only_refresh_and_recovery(page,server,http_request,width):
    from urllib.parse import urlsplit
    state = server.build_state('main')
    state.update(ready_state())
    health = report(state,{'status':'ok','detail':'已注册'})
    health.update(credential=state['credential'],running=None)
    requests=[]
    async def route(r):
        path=urlsplit(r.request.url).path
        if path=='/api/stream':return await r.fulfill(content_type='text/event-stream',body='\n')
        if path=='/api/state':return await r.fulfill(json=state)
        if path=='/api/health':
            requests.append(r.request.method)
            if len(requests)==1:return await r.fulfill(status=503,json={'error':'临时不可用'})
            return await r.fulfill(json=health)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.set_viewport_size({'width':width,'height':900})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://task.test/**',route)
    await page.goto('http://task.test/#/accounts/main/home')
    await expect(page.locator('#taskHealth')).to_have_count(0)
    await expect(page.locator('#todayCount')).to_be_visible()
    await expect(page.locator('#homeFriendList')).to_be_visible()
    assert requests == []
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
