from copy import deepcopy
import json
import pytest
from playwright.async_api import expect
from app.groups import apply_batch,groups_for
from app.config import ConfigError
from app.history import History,message_id,run_lock
from test_contacts_api import server,http_request,page


def test_group_changes_do_not_mutate_source_or_custom_messages():
    config={'friends':['甲','乙'],'messages':[{'type':'text','value':'默认'}],'friend_rules':{'甲':{'mode':'custom','enabled':True,'messages':[{'type':'text','value':'专属'}],'holiday_enabled':False}}}
    before=deepcopy(config)
    grouped=apply_batch(config,['甲'],'group','朋友')
    assert config==before and grouped['friend_groups']=={'甲':'朋友'}
    paused=apply_batch(grouped,['甲','乙'],'pause')
    assert paused['friend_rules']['甲']['messages']==before['friend_rules']['甲']['messages']
    assert paused['friend_rules']['甲']['holiday_enabled'] is False
    assert all(paused['friend_rules'][name]['enabled'] is False for name in ['甲','乙'])
    assert apply_batch(paused,['甲'],'resume')['friend_rules']['甲']['enabled']
    assert groups_for(apply_batch(grouped,['甲'],'group',''))=={}
    for selected,operation,group in [([], 'pause',None),(['不存在'],'pause',None),(['甲'],'send',None),(['甲'],'group','a\nb'),(['甲'],'group','字'*21)]:
        with pytest.raises(ConfigError):apply_batch(config,selected,operation,group)
    large={'friends':[str(i) for i in range(13)],'friend_groups':{str(i):f'组{i}' for i in range(12)}}
    with pytest.raises(ConfigError):apply_batch(large,['12'],'group','组12')


def test_batch_api_preserves_today_deduplication_rules_and_rename(server,http_request):
    config=server.load_config();config['friends'].append('其他好友');server.write_json(server.CONFIG,config)
    task,history=server.review_context(server.account_paths_for('main'))
    key=history.key(task.task_id,history.run_date(task.timezone),task.targets[0].name,message_id(0,task.targets[0].messages[0]))
    history.mark_success(key)
    payload={'accountId':'main','names':['原有好友'],'operation':'group','group':'朋友'}
    status,state=http_request('/api/friends/batch',payload)
    assert status==200 and state['friendGroups']=={'原有好友':'朋友'} and state['friendStatuses'][0]=='sent'
    assert http_request('/api/friends/batch',{**payload,'operation':'pause'})[1]['friendStatuses'][0]=='paused'
    assert http_request('/api/friends/batch',{**payload,'operation':'resume'})[1]['friendStatuses'][0]=='sent'
    assert History(server.ARTIFACTS/'history.json').status(key)=='success'
    result=http_request('/api/friends',{'accountId':'main','friends':['新备注','其他好友'],'renamedFrom':'原有好友','renamedTo':'新备注'})
    assert result[0]==200 and result[1]['friendGroups']=={'新备注':'朋友'} and result[1]['friendStatuses'][0]=='sent'
    assert http_request('/api/friends',{'accountId':'main','friends':['其他好友']})[1]['friendGroups']=={}


def test_invalid_batch_is_atomic_and_requires_account_and_idle(server,http_request):
    path=server.CONFIG;before=path.read_bytes()
    for payload in [{'names':['原有好友'],'operation':'pause'}, {'accountId':'missing','names':['原有好友'],'operation':'pause'}, {'accountId':'main','names':['已删除'],'operation':'pause'}]:
        assert http_request('/api/friends/batch',payload)[0]==400
        assert path.read_bytes()==before
    with run_lock(server.ARTIFACTS/'run.lock'):
        assert http_request('/api/friends/batch',{'accountId':'main','names':['原有好友'],'operation':'pause'})[0]==409
    assert path.read_bytes()==before


@pytest.mark.parametrize('width',[1320,390])
async def test_batch_group_filter_pause_resume_ui(page,server,http_request,width):
    from urllib.parse import urlsplit
    config=server.load_config();config['friends'].append('其他好友');server.write_json(server.CONFIG,config)
    posts=[]
    async def route(r):
        parsed=urlsplit(r.request.url)
        if parsed.path=='/api/stream':return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            body=r.request.post_data_json if r.request.method=='POST' else None
            if body:posts.append(parsed.path)
            status,data=http_request(parsed.path+('?' + parsed.query if parsed.query else ''),body)
            return await r.fulfill(status=status,json=data)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.set_viewport_size({'width':width,'height':900})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    page.on('dialog',lambda dialog:dialog.accept())
    await page.route('http://groups.test/**',route)
    await page.goto('http://groups.test/#/accounts/main/friends')
    await expect(page.locator('#friendBatchToggle, #friendBatchPanel, .friend-identity-help')).to_have_count(0)
    await expect(page.locator('#albumGrid tr')).to_have_count(2)
    assert posts == []
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
