from datetime import datetime, timedelta
import json
import pytest
from playwright.async_api import expect
from app.streaks import SOURCE, snapshot
from app.contacts import collect_contacts
from test_contacts_api import server, http_request, page


def cache(days=457,stamp='2026-11-14T09:00:00+08:00'):
    return {'syncedAt':stamp,'contacts':[{'name':'原有好友','selectable':True,'streakDays':days,'streakSource':SOURCE}]}


def test_observed_stale_and_unknown_never_infer_streaks():
    now=datetime.fromisoformat('2026-11-14T10:00:00+08:00')
    result=snapshot(cache(),['原有好友','不存在'],now)
    assert result['原有好友']['status']=='observed' and result['原有好友']['days']==457
    assert result['不存在']['status']=='unknown' and result['不存在']['days'] is None
    assert snapshot(cache(),['原有好友'],now+timedelta(days=1))['原有好友']['status']=='local'
    assert snapshot(cache(),['原有好友'],now+timedelta(days=1))['原有好友']['days']==457
    for days in [True,0,-1,999999,'457',None]:
        assert snapshot(cache(days),['原有好友'],now)['原有好友']['status']=='unknown'
    for stamp in ['bad','2026-11-14T09:00:00','2026-11-15T10:00:00+08:00']:
        assert snapshot(cache(stamp=stamp),['原有好友'],now)['原有好友']['status']=='unknown'


def test_ambiguous_error_and_untrusted_source_are_unknown():
    now=datetime.fromisoformat('2026-11-14T10:00:00+08:00')
    data=cache()
    data['contacts']*=2
    assert snapshot(data,['原有好友'],now)['原有好友']['status']=='unknown'
    data=cache();data['contacts'][0]['selectable']=False
    assert snapshot(data,['原有好友'],now)['原有好友']['status']=='unknown'
    data=cache();data['error']='同步失败'
    assert snapshot(data,['原有好友'],now)['原有好友']['status']=='unknown'
    data=cache();data['contacts'][0]['streakSource']='guessed'
    assert snapshot(data,['原有好友'],now)['原有好友']['status']=='unknown'


@pytest.mark.parametrize('badge,expected', [('457',457),('30天',30),('0',None),('12万',None),('12条',None),('999999',None)])
async def test_only_scoped_visible_badges_are_read(page,badge,expected):
    await page.set_content(f'<div data-e2e="conversation-item"><span data-e2e="conversation-title">好友</span><div class="ConversationItemTagNextToTitlewrapper"><span class="commonStreaknormalText">{badge}</span></div><p>PRIVATE MESSAGE 999 天</p><span>60分钟前</span></div>')
    rows=(await collect_contacts(page))['contacts']
    assert rows[0].get('streakDays')==expected
    assert 'PRIVATE' not in json.dumps(rows)


async def test_hidden_ambiguous_and_unscoped_numbers_are_not_badges(page):
    await page.set_content('<div data-e2e="conversation-item"><span data-e2e="conversation-title">好友</span><span class="commonStreaknormalText">999</span><div class="ConversationItemTagNextToTitlewrapper"><span class="commonStreaknormalText" hidden>457</span></div></div>')
    assert 'streakDays' not in (await collect_contacts(page))['contacts'][0]
    await page.set_content('<div data-e2e="conversation-item"><span data-e2e="conversation-title">同名</span><div class="ConversationItemTagNextToTitlewrapper"><span class="commonStreaknormalText">457</span><span class="commonStreaknormalText">458</span></div></div>')
    assert 'streakDays' not in (await collect_contacts(page))['contacts'][0]


def test_account_state_uses_only_own_cache(server,http_request):
    assert http_request('/api/state?accountId=main')[1]['friendStreaks']['原有好友']['status']=='unknown'
    data=cache(stamp=datetime.now().astimezone().isoformat())
    server.write_json(server.ARTIFACTS/'chat-list.json',data)
    result=http_request('/api/state?accountId=main')[1]
    assert result['friendStreaks']['原有好友']['days']==457
    assert result['friendStreaks']['原有好友']['status']=='observed'
    data['syncedAt']=(datetime.now().astimezone()-timedelta(days=1)).isoformat()
    server.write_json(server.ARTIFACTS/'chat-list.json',data)
    assert http_request('/api/state?accountId=main')[1]['friendStreaks']['原有好友']['status']=='local'


@pytest.mark.parametrize('width',[1320,390])
async def test_friend_streak_display_unknown_and_stale(page,server,http_request,width):
    from urllib.parse import urlsplit
    config=server.load_config();config['friends']=['原有好友','旧数据','未读取'];server.write_json(server.CONFIG,config)
    data=server.build_state('main')
    observed=datetime.now().astimezone()
    data['friendStreaks']={'原有好友':{'status':'observed','days':457,'observedAt':observed.isoformat(),'expiresAt':(observed+timedelta(hours=1)).isoformat()},'旧数据':{'status':'stale','days':None,'observedAt':(observed-timedelta(days=1)).isoformat()},'未读取':{'status':'unknown','days':None}}
    async def route(r):
        path=urlsplit(r.request.url).path
        if path=='/api/stream':return await r.fulfill(content_type='text/event-stream',body='\n')
        if path=='/api/state':return await r.fulfill(json=data)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.set_viewport_size({'width':width,'height':900})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://streaks.test/**',route)
    await page.goto('http://streaks.test/#/accounts/main/friends')
    await expect(page.locator('.friend-spark').nth(0)).to_have_text('457 天')
    await expect(page.locator('.friend-spark').nth(1)).to_have_text('待更新')
    await expect(page.locator('.friend-spark').nth(2)).to_have_text('未知')
    await page.locator('#albumGrid tr').first.locator('.friend-more').click()
    await page.locator('.friend-spark').first.click()
    await expect(page.locator('.friend-spark-details > p').first).to_contain_text('抖音页面读取')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.screenshot(path=f'/tmp/streak-display-{width}.png',full_page=True,animations='disabled')
