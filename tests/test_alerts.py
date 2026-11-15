import json
from pathlib import Path
import pytest
from playwright.async_api import expect
from app import alerts
from test_contacts_api import server, http_request, page


def config(**kwargs):
    return {'enabled':True,'channel':'webhook','events':list(alerts.KINDS),'delayMinutes':60,'url':'https://example.com/hook?token=private-token','secret':'private-secret',**kwargs}


def state(**kwargs):
    return {'accountId':'main','accountEnabled':True,'running':None,'friendStatuses':['failed','unknown'], 'pendingTargets':['私密姓名'], 'credential':{'health':'expired'}, 'schedule':{'present':True,'hour':7,'minute':0}, 'now':'2026-11-14T09:00:00+08:00','sendQueueReady':True,**kwargs}


def test_alert_settings_private_validation_and_channel_changes(tmp_path):
    result=alerts.save_settings(tmp_path,config())
    assert result['urlConfigured'] and result['secretConfigured']
    assert 'private' not in json.dumps(result)
    assert (tmp_path/'notification-settings.json').stat().st_mode & 0o777 == 0o600
    alerts.save_settings(tmp_path,config(url='',secret=''))
    assert alerts.settings(tmp_path)['url'].endswith('private-token')
    for url in ['http://example.com/hook','https://user:pass@example.com/hook']:
        with pytest.raises(ValueError):alerts.save_settings(tmp_path,config(url=url))
    with pytest.raises(ValueError):alerts.save_settings(tmp_path,config(channel='dingtalk',url=''))
    alerts.save_settings(tmp_path,config(clearSecret=True,secret=''))
    assert not alerts.settings(tmp_path)['secret']


def test_background_alerts_deduplicate_retry_and_recover(tmp_path,monkeypatch):
    alerts.save_settings(tmp_path,config())
    calls=[]
    monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:calls.append(args))
    current=state()
    alerts.poll(tmp_path,current)
    alerts.poll(tmp_path,current)
    assert len(calls)==1 and set(calls[0][2])==set(alerts.KINDS)
    assert '私密姓名' not in json.dumps(calls,ensure_ascii=False)
    alerts.poll(tmp_path,state(running={'action':'send'}))
    alerts.poll(tmp_path,current)
    assert len(calls)==1
    alerts.poll(tmp_path,state(friendStatuses=['sent'],pendingTargets=[],credential={'health':'valid'}))
    alerts.poll(tmp_path,current)
    assert len(calls)==2
    current['now']='2026-11-15T09:00:00+08:00'
    def fail(*args,**kwargs):raise RuntimeError('private-token private-secret')
    monkeypatch.setattr(alerts,'deliver',fail)
    alerts.poll(tmp_path,current)
    status=alerts.public_settings(tmp_path)['delivery']
    assert status['status']=='failed' and 'private' not in json.dumps(status)
    monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:calls.append(args))
    alerts.poll(tmp_path,current)
    assert len(calls)==2
    current['now']='2026-11-15T09:16:00+08:00'
    alerts.poll(tmp_path,current)
    assert len(calls)==3


def test_opt_in_and_evidence_boundaries(tmp_path,monkeypatch):
    calls=[]
    monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:calls.append(args))
    alerts.poll(tmp_path,state())
    assert len(calls)==1
    alerts.save_settings(tmp_path,config())
    (tmp_path/'run.lock').write_text('running')
    alerts.poll(tmp_path,state())
    assert len(calls)==1
    cfg=config()
    assert not alerts.anomalies(state(accountEnabled=False),cfg)
    assert not alerts.anomalies(state(running={'action':'send'}),cfg)
    assert 'login' not in alerts.anomalies(state(credential={'health':'check_failed'}),cfg)
    assert 'overdue' not in alerts.anomalies(state(now='2026-11-14T07:59:00+08:00'),cfg)
    assert 'overdue' not in alerts.anomalies(state(schedule={'present':False}),cfg)


def test_notifications_api_is_account_scoped_and_tests_are_explicit(server,http_request,monkeypatch):
    calls=[]
    monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:calls.append((args,kwargs)))
    assert http_request('/api/notifications')[0]==400
    assert http_request('/api/notifications?accountId=missing')[0]==400
    assert http_request('/api/notifications?accountId=main')[1]['enabled'] is True
    assert http_request('/api/notifications',{'accountId':'main',**config()})[0]==200
    assert not calls
    response=http_request('/api/notifications?accountId=main')[1]
    assert 'private' not in json.dumps(response)
    assert http_request('/api/notifications/test',{'accountId':'main'})[1]['status']=='submitted'
    assert len(calls)==1 and calls[0][1]['test'] is True
    assert not (server.ARTIFACTS/'run-history.jsonl').exists()


@pytest.mark.parametrize('width',[1320,390])
async def test_notification_settings_ui(page,server,http_request,monkeypatch,width):
    from urllib.parse import urlsplit
    calls=[]
    monkeypatch.setattr(alerts,'deliver',lambda *args,**kwargs:calls.append(args))
    async def route(r):
        path=urlsplit(r.request.url)
        if path.path=='/api/stream':return await r.fulfill(content_type='text/event-stream',body='\n')
        if path.path.startswith('/api/'):
            status,body=http_request(path.path+('?' + path.query if path.query else ''),r.request.post_data_json if r.request.method=='POST' else None)
            return await r.fulfill(status=status,json=body)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.set_viewport_size({'width':width,'height':900})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://notifications.test/**',route)
    await page.goto('http://notifications.test/#/accounts/main/tasks')
    await expect(page.locator('#notificationPanel')).to_have_count(0)
    await expect(page.locator('#notificationTest')).to_have_count(0)
    assert not calls
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
