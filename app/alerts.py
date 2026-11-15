"""Opt-in, per-account anomaly notifications; never reads chat content."""
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import subprocess
import sys
from urllib.parse import urlsplit

from app.history import run_lock

KINDS = {'login': '登录需要处理', 'failed': '今天有发送失败', 'unknown': '有发送结果待核对', 'overdue': '今天的发送尚未完成'}
DEFAULT = {'enabled': True, 'channel': 'local', 'events': list(KINDS), 'delayMinutes': 60, 'url': '', 'secret': ''}


def read(path, default):
    try:
        return json.loads(path.read_text())
    except FileNotFoundError:
        return default


def write(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        json.dump(data, stream, ensure_ascii=False)
    temporary.replace(path)


def settings(folder):
    return {**DEFAULT, **read(folder/'notification-settings.json', {}), 'enabled': True}


def public_settings(folder):
    config = settings(folder)
    status = read(folder/'notification-status.json', {})
    return {k: config[k] for k in ('enabled','channel','events','delayMinutes')} | {'urlConfigured':bool(config['url']), 'secretConfigured':bool(config['secret']), 'localAvailable':sys.platform=='darwin', 'delivery':status}


def save_settings(folder, data):
    old = settings(folder)
    if type(data.get('enabled')) is not bool or data.get('channel') not in {'local','dingtalk','webhook'}:
        raise ValueError('请选择有效的通知方式')
    events = data.get('events')
    delay = data.get('delayMinutes')
    if not isinstance(events,list) or any(event not in KINDS for event in events) or type(delay) is not int or delay not in {30,60,120}:
        raise ValueError('提醒条件或等待时间不正确')
    url, secret = data.get('url',''), data.get('secret','')
    if not isinstance(url,str) or not isinstance(secret,str) or len(url)>2048 or len(secret)>512:
        raise ValueError('通知地址或签名密钥格式不正确')
    url,secret = url.strip() or (old['url'] if data['channel']==old['channel'] else ''), secret.strip() or (old['secret'] if data['channel']==old['channel'] else '')
    if data.get('clearSecret') is True: secret = ''
    if url:
        parsed=urlsplit(url)
        if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password or parsed.fragment:
            raise ValueError('通知地址需要使用有效的 HTTPS 地址')
        if data['channel']=='dingtalk' and (parsed.hostname!='oapi.dingtalk.com' or parsed.path!='/robot/send'):
            raise ValueError('请填写钉钉自定义机器人的通知地址')
    if data['enabled']:
        if not events: raise ValueError('请至少选择一种提醒条件')
        if data['channel']=='local' and sys.platform!='darwin': raise ValueError('当前系统暂不支持本机通知，请选择其他方式')
        if data['channel']!='local' and not url: raise ValueError('请先填写通知地址')
    write(folder/'notification-settings.json', {'enabled':True,'channel':data['channel'],'events':list(dict.fromkeys(events)),'delayMinutes':delay,'url':url,'secret':secret})
    return public_settings(folder)


def anomalies(state, config):
    if not state.get('accountEnabled',True) or state.get('running'): return {}
    output={}
    health=state.get('credential',{}).get('health')
    if health in {'expired','verification_required'}: output['login']=KINDS['login']
    statuses=state.get('friendStatuses',[])
    failed,unknown=statuses.count('failed'),statuses.count('unknown')
    if failed: output['failed']=f'今天有 {failed} 位好友发送失败，请查看运行记录。'
    if unknown: output['unknown']=f'有 {unknown} 位好友的发送结果待核对，请先人工确认。'
    schedule=state.get('schedule',{})
    if schedule.get('present') and state.get('pendingTargets') and state.get('sendQueueReady') and not state.get('allTargetsComplete'):
        now=datetime.fromisoformat(state['now'])
        due=now.replace(hour=schedule['hour'],minute=schedule['minute'],second=0,microsecond=0)+timedelta(minutes=config['delayMinutes'])
        if now>=due:output['overdue']=f'已超过设定时间 {config["delayMinutes"]} 分钟，仍有 {len(state["pendingTargets"])} 位好友未完成。请检查电脑、网络和运行记录。'
    return {k:v for k,v in output.items() if k in config['events']}


def deliver(config, account_id, events, *, test=False):
    from app.notifier import _post_json, _post_json_webhook, _signed_webhook_url
    title='抖音自动续火花 · '+('测试通知' if test else '异常提醒')
    text='这是一条测试通知，不会发送抖音消息。' if test else '\n'.join(events.values())
    text=f'账户 {account_id}\n{text}'
    if config['channel']=='local':
        if sys.platform!='darwin':raise ValueError('当前系统暂不支持本机通知')
        script='on run argv\n display notification (item 1 of argv) with title (item 2 of argv)\nend run'
        subprocess.run(['/usr/bin/osascript','-e',script,text,title],check=True,capture_output=True,timeout=8)
    elif config['channel']=='dingtalk':
        if not config['url']:raise ValueError('尚未配置通知地址')
        url=_signed_webhook_url(config['url'],config['secret']) if config['secret'] else config['url']
        _post_json(url,{'msgtype':'text','text':{'content':title+'\n'+text},'at':{'isAtAll':False}})
    else:
        if not config['url']:raise ValueError('尚未配置通知地址')
        _post_json_webhook(config['url'],{'title':title,'accountId':account_id,'test':test,'events':events,'text':text},{'Content-Type':'application/json; charset=utf-8'})


def attempt(folder, config, account_id, events, *, test=False):
    at=datetime.now().astimezone().isoformat()
    try:
        deliver(config,account_id,events,test=test)
        result={'at':at,'status':'submitted','detail':'已提交到通知通道；请在接收端确认。','test':test}
    except Exception:
        # Vendor errors and request URLs can contain tokens. Never return them.
        result={'at':at,'status':'failed','detail':'通知提交失败，请检查地址、密钥、网络或系统通知权限。','test':test}
    write(folder/'notification-status.json',result)
    return result


def poll(folder, state):
    config=settings(folder)
    if not config['enabled'] or (folder/'run.lock').exists():return
    with run_lock(folder/'notification.lock'):
        now=datetime.fromisoformat(state['now'])
        journal=read(folder/'notification-journal.json',{})
        current=anomalies(state,config)
        # Running is not recovery; retain receipts until the task has finished.
        if state.get('running'):return
        receipts={key:value for key,value in journal.items() if key in current}
        due={}
        for kind,text in current.items():
            previous=receipts.get(kind,{})
            if previous.get('day')==now.date().isoformat() and previous.get('submitted'):continue
            if previous.get('at') and now-datetime.fromisoformat(previous['at'])<timedelta(minutes=15):continue
            due[kind]=text
        if due:
            # Reserve first: a crash during submission must not cause a tight resend loop.
            for kind in due:receipts[kind]={'at':now.isoformat(),'day':now.date().isoformat(),'submitted':False}
            write(folder/'notification-journal.json',receipts)
            result=attempt(folder,config,state['accountId'],due)
            for kind in due:receipts[kind]['submitted']=result['status']=='submitted'
        write(folder/'notification-journal.json',receipts)
