"""Default message UI: isolated storage, real upload validation, no sending actions."""
import os
import struct
import zlib
from pathlib import Path
from urllib.parse import urlsplit, unquote

import pytest
from playwright.async_api import expect
from test_contacts_api import page, server, http_request


def sample_png(pink=False):
    # Geometric test-only media, never copied into production assets.
    width = height = 180
    pixels = bytearray()
    for y in range(height):
        pixels.append(0)
        for x in range(width):
            rgb = (255,237,228) if pink else (255,245,223)
            dx,dy = x-90,y-90
            if dx*dx+dy*dy < 46*46:
                rgb = (255,158,140) if pink else (255,198,78)
            if (x-75)**2+(y-84)**2 < 3*3 or (x-105)**2+(y-84)**2 < 3*3:
                rgb = (100,61,24)
            if 79<x<101 and 96<y<106 and (x-90)**2+(y-94)**2<12*12:
                rgb = (100,61,24)
            pixels.extend(rgb)
    def chunk(kind,data):
        return struct.pack('!I',len(data))+kind+data+struct.pack('!I',zlib.crc32(kind+data)&0xffffffff)
    return b'\x89PNG\r\n\x1a\n'+chunk(b'IHDR',struct.pack('!2I5B',width,height,8,2,0,0,0))+chunk(b'IDAT',zlib.compress(bytes(pixels)))+chunk(b'IEND',b'')


async def capture(page,name):
    directory=os.environ.get('DOUYIN_UI_CAPTURE_DIR')
    if directory:
        await page.screenshot(path=str(Path(directory)/name),animations='disabled',full_page=True)


async def install_messages(page,server,http_request,variant='normal'):
    image_dir=server.DATA_ROOT/'images'/'main'; image_dir.mkdir(parents=True)
    (image_dir/'早安.png').write_bytes(sample_png())
    (image_dir/'开心.png').write_bytes(sample_png(True))
    texts=['今天也要开心','新的一天，继续保持火花～','记得吃早饭']
    paths=['images/main/早安.png','images/main/开心.png']
    config=server.load_config()
    choices=[{'type':'text','value':text} for text in texts]+[{'type':'image','value':path} for path in paths]
    config['messages']=[{'type':'random','choices':choices}]
    if variant=='reference':
        config['messages']=[{'type':'text','value':'续火花'}]
    if variant=='empty':
        config['friends']=[];config['messages']=[]
    if variant=='sequence':
        config['messages']=[{'type':'text','value':text} for text in texts]
    if variant=='unsupported':
        config['messages']=[{'type':'image','value':paths[0]},{'type':'text','value':'旧顺序中的文字'}]
    server.write_json(server.CONFIG,config)
    posts,gets,errors=[],[],[]
    page.on('pageerror',lambda error:errors.append(str(error)))
    fail_once={'count':1 if variant=='save_failure' else 0}
    async def route(r):
        req=r.request;parsed=urlsplit(req.url)
        if parsed.path=='/api/stream':
            return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/images/'):
            path=(server.DATA_ROOT/unquote(parsed.path.removeprefix('/api/images/'))).resolve()
            assert path.is_relative_to(server.DATA_ROOT.resolve())
            if not path.exists(): return await r.fulfill(status=404,body='missing')
            return await r.fulfill(content_type='image/png',body=path.read_bytes())
        if parsed.path.startswith('/api/'):
            if req.method!='GET':
                posts.append((parsed.path,req.post_data_json))
                assert parsed.path in ['/api/image','/api/message'], 'Message fixtures must never send or alter scheduling'
                if parsed.path=='/api/message' and fail_once['count']:
                    fail_once['count']-=1
                    return await r.fulfill(status=409,json={'error':'隔离测试：当前任务占用，请重试'})
            gets.append(req.url)
            if variant=='load_error' and parsed.path=='/api/state' and parsed.query:
                return await r.fulfill(status=503,json={'error':'隔离测试：消息暂不可用'})
            status,data=http_request(parsed.path+('?' + parsed.query if parsed.query else ''),req.post_data_json if req.method=='POST' else None)
            if parsed.path=='/api/state' and not parsed.query:
                data={'accounts':[{'id':'main','name':'日常账号'},{'id':'second','name':'备用账号'}]}
            elif parsed.path=='/api/state' and status==200:
                data.update(accountName='主账号' if variant=='reference' else '备用账号' if 'accountId=second' in parsed.query else '日常账号',now='2026-11-14T08:20:00+08:00')
                data['schedule'].update(present=True,next='明天 07:00')
                data['credential']={'present':True,'health':'unchecked'}
                if variant=='running': data['running']={'action':'send','accountId':'main'}
            return await r.fulfill(status=status,json=data)
        return await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://messages.test/**',route)
    await page.goto('http://messages.test/#/accounts/main/messages')
    await expect(page.locator('#messageSavedBadge')).to_have_text('读取失败' if variant=='load_error' else '高级消息已保留' if variant=='unsupported' else '未配置消息' if variant=='empty' else '当前消息已保存')
    return posts,gets,errors


@pytest.mark.parametrize('width,height',[(1280,800),(1440,900),(1644,957),(1920,1080),(1152,720)])
async def test_message_reference_composition_and_image_preview(page,server,http_request,width,height):
    await page.set_viewport_size({'width':width,'height':height})
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#messageBox').fill('今天也要开心呀\n新的一天，继续保持火花～\n记得吃早饭')
    await expect(page.locator('#messageTextCount')).to_have_text('3 条')
    await expect(page.locator('#messageImageCount')).to_have_text('2 / 10 张')
    await expect(page.locator('#messageSavedBadge')).to_have_text('未保存修改')
    await expect(page.locator('#messageRuleSummary')).to_have_text('每次从文字与图片候选中随机发送一项。')
    boxes=await page.locator('.message-text-card,.message-images-card,.message-rules-card').evaluate_all('(nodes)=>nodes.map(n=>{let r=n.getBoundingClientRect();return {x:r.x,y:r.y,right:r.right,bottom:r.bottom}})')
    assert boxes[0]['y']==boxes[1]['y'] and boxes[0]['right']<boxes[1]['x']
    assert boxes[2]['y']>boxes[0]['bottom']
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.locator('#messageImagesTitle').click()
    await capture(page,f'messages-editor-{width}.png')
    await page.locator('.message-image-open').first.click()
    await expect(page.locator('#messageImagePreview')).to_be_visible()
    await expect(page.locator('#messagePreviewImage')).to_have_attribute('src', '/api/images/images/main/%E6%97%A9%E5%AE%89.png')
    await page.keyboard.press('Escape')
    await expect(page.locator('#messageImagePreview')).not_to_be_visible()
    assert not posts and not errors


async def test_save_roundtrip_is_account_scoped_and_double_click_guarded(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#messageBox').fill(' 新的候选一 \n\n新的候选二\n')
    await page.locator('#saveMessageInline').evaluate('e=>{e.click();e.click()}')
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    assert len(posts)==1
    assert posts[0][1]['accountId']=='main'
    assert posts[0][1]['texts']==['新的候选一','新的候选二']
    assert posts[0][1]['strategy']=='random'
    rows=server.load_config()['messages']
    assert rows[0]['type']=='random' and len(rows[0]['choices'])==4
    assert not errors


async def test_upload_remove_and_failed_save_keeps_draft_without_reupload(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request,'save_failure')
    await page.locator('#messageBox').fill('失败后保留文字草稿')
    await page.locator('#imageInput').set_input_files({'name':'新图片.png','mimeType':'image/png','buffer':sample_png(True)})
    await expect(page.locator('#messageImageCount')).to_have_text('3 / 10 张')
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageFeedback')).to_contain_text('当前任务占用')
    await expect(page.locator('#messageBox')).to_have_value('失败后保留文字草稿')
    await expect(page.locator('.message-image-open')).to_have_count(3)
    assert [path for path,_ in posts]==['/api/image','/api/message']
    await capture(page,'messages-save-failure-1280.png')
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    assert [path for path,_ in posts]==['/api/image','/api/message','/api/message']
    assert posts[0][1]['accountId']=='main'
    assert len(server.load_config()['messages'][0]['choices'])==4
    await expect(page.locator('figcaption').last).to_have_text('新图片.png')
    assert not errors


async def test_unsaved_page_and_account_switch_keep_or_discard(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#messageBox').fill('未保存的账户草稿')
    await page.locator('[data-page-link="home"]').first.click()
    await expect(page.locator('#messageDiscardDialog')).to_be_visible()
    await page.keyboard.press('Escape')
    await expect(page).to_have_url('http://messages.test/#/accounts/main/messages')
    await expect(page.locator('#messageBox')).to_have_value('未保存的账户草稿')
    await page.locator('#headerAccount').focus()
    await expect(page.locator('#headerAccount option')).to_have_count(2)
    await page.locator('#headerAccount').select_option('second')
    await page.locator('#keepMessageEditing').click()
    await expect(page.locator('#headerAccount')).to_have_value('main')
    await expect(page.locator('#messageBox')).to_have_value('未保存的账户草稿')
    await page.locator('[data-page-link="home"]').first.click()
    await page.locator('#discardMessageChanges').click()
    await expect(page).to_have_url('http://messages.test/#/accounts/main/home')
    await page.locator('[data-page-link="messages"]').first.click()
    await expect(page.locator('#messageBox')).to_have_value('今天也要开心\n新的一天，继续保持火花～\n记得吃早饭')
    assert not posts and not errors


@pytest.mark.parametrize('variant',['empty','running','unsupported','load_error','sequence'])
async def test_message_secondary_states_truthful_and_recoverable(page,server,http_request,variant):
    await page.set_viewport_size({'width':1440,'height':900})
    posts,gets,errors=await install_messages(page,server,http_request,variant)
    if variant=='empty':
        await expect(page.locator('#messageTextCount')).to_have_text('0 条')
        await expect(page.locator('#messageImageCount')).to_have_text('0 / 10 张')
        await page.locator('#saveMessageInline').click()
        await expect(page.locator('#messageFeedback')).to_contain_text('至少添加一句')
    elif variant in ['running','unsupported']:
        await expect(page.locator('#messageBox')).to_be_disabled()
        await expect(page.locator('#imageInput')).to_be_disabled()
        await expect(page.locator('#saveMessageInline')).to_be_disabled()
        await expect(page.locator('#messageFeedback')).to_contain_text('任务运行中' if variant=='running' else '原设置已保留')
    elif variant=='sequence':
        await expect(page.locator('#messageRuleSummary')).to_contain_text('依次发送全部')
        await page.locator('#messageBox').fill('保留原顺序一\n保留原顺序二')
        await page.locator('#saveMessageInline').click()
        await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
        assert posts[0][1]['strategy']=='sequence'
        assert server.load_config()['messages']==[{'type':'text','value':'保留原顺序一'},{'type':'text','value':'保留原顺序二'}]
    else:
        await expect(page.locator('#retryMessagesLoad')).to_be_visible()
        await expect(page.locator('#messageBox')).to_have_value('')
        await expect(page.locator('#messageTextCount')).to_have_text('— 条')
        await expect(page.locator('#messageAccountFooter')).not_to_contain_text('日常账号')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await capture(page,f'messages-{variant}-1440.png')
    assert not errors


async def test_limits_and_drag_drop_are_local_until_save(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#imageInput').set_input_files({'name':'不支持.txt','mimeType':'text/plain','buffer':b'123'})
    await expect(page.locator('#messageUploadError')).to_contain_text('格式不支持')
    await page.locator('#imageInput').set_input_files({'name':'超限.png','mimeType':'image/png','buffer':b'x'*(8*1024*1024+1)})
    await expect(page.locator('#messageUploadError')).to_contain_text('8 MB')
    await expect(page.locator('#messageImageCount')).to_have_text('2 / 10 张')
    await page.locator('#imageInput').set_input_files([{'name':f'候选{i}.png','mimeType':'image/png','buffer':sample_png()} for i in range(9)])
    await expect(page.locator('#messageImageCount')).to_have_text('10 / 10 张')
    await expect(page.locator('#messageUploadError')).to_contain_text('最多添加 10 张')
    await page.locator('.image-remove').last.click()
    await expect(page.locator('#messageImageCount')).to_have_text('9 / 10 张')
    await page.locator('.upload-drop').evaluate("e=>{const d=new DataTransfer();d.items.add(new File([new Uint8Array([137,80,78,71])],'拖入.png',{type:'image/png'}));e.dispatchEvent(new DragEvent('drop',{bubbles:true,cancelable:true,dataTransfer:d}))}")
    await expect(page.locator('#messageImageCount')).to_have_text('10 / 10 张')
    await expect(page.locator('#messageUploadError')).not_to_be_visible()
    assert not posts and not errors


@pytest.mark.parametrize('length,count',[(501,1),(2,51)])
async def test_text_validation_retains_draft(page,server,http_request,length,count):
    posts,gets,errors=await install_messages(page,server,http_request)
    value='\n'.join(['字'*length]*count)
    await page.locator('#messageBox').fill(value)
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageFeedback')).to_contain_text('最多 50 条')
    await expect(page.locator('#messageBox')).to_have_value(value)
    assert not posts and not errors


def test_default_metadata_protects_legacy_order_and_types(server):
    assert server.default_message_state({'messages':[{'type':'douyin_sticker','value':'旧表情'}]})['editable'] is False
    assert server.default_message_state({'messages':[{'type':'text','value':'多行\n原消息'}]})['editable'] is False
    assert server.default_message_state({'messages':[{'type':'image','value':'x.png'},{'type':'text','value':'后文字'}]})['editable'] is False
    data=server.default_message_state({'messages':[{'type':'rotation','choices':[{'type':'text','value':'一'},{'type':'text','value':'二'}]}]})
    assert data['editable'] is True and data['editorStrategy']=='rotation'


async def test_exact_8mb_boundary_is_allowed_but_empty_file_rejected(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#imageInput').set_input_files({'name':'空图片.png','mimeType':'image/png','buffer':b''})
    await expect(page.locator('#messageUploadError')).to_contain_text('不能为空')
    data=sample_png()
    data+=b'\0'*(8*1024*1024-len(data))
    await page.locator('#imageInput').set_input_files({'name':'边界8MB.png','mimeType':'image/png','buffer':data})
    await expect(page.locator('#messageImageCount')).to_have_text('3 / 10 张')
    await expect(page.locator('#messageUploadError')).not_to_be_visible()
    assert not posts and not errors


async def test_live_state_does_not_erase_text_or_pending_image_and_locks_running(page,server,http_request):
    await page.add_init_script("window.messageStreams=[];window.EventSource=class{constructor(url){this.url=url;window.messageStreams.push(this)}close(){}}")
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('#messageBox').fill('保留实时刷新前的草稿')
    await page.locator('#imageInput').set_input_files({'name':'待保存.png','mimeType':'image/png','buffer':sample_png()})
    state=server.build_state('main');state['running']={'action':'send','accountId':'main'}
    await page.evaluate("data=>window.messageStreams.at(-1).onmessage({data:JSON.stringify({type:'state',accountId:'main',state:data})})",state)
    await expect(page.locator('#messageBox')).to_have_value('保留实时刷新前的草稿')
    await expect(page.locator('#messageImageCount')).to_have_text('3 / 10 张')
    await expect(page.locator('#saveMessageInline')).to_be_disabled()
    state['running']=None
    await page.evaluate("data=>window.messageStreams.at(-1).onmessage({data:JSON.stringify({type:'state',accountId:'main',state:data})})",state)
    await expect(page.locator('#saveMessageInline')).to_be_enabled()
    await expect(page.locator('#messageBox')).to_have_value('保留实时刷新前的草稿')
    assert not posts and not errors


async def test_discard_account_switch_clears_images_and_saves_only_new_account(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    status,_=http_request('/api/accounts',{'name':'second'})
    assert status==200
    before=server.CONFIG.read_bytes()
    await page.locator('#messageBox').fill('主账户未保存的文字')
    await page.locator('#imageInput').set_input_files({'name':'主账户草稿.png','mimeType':'image/png','buffer':sample_png()})
    await page.locator('#headerAccount').focus()
    await expect(page.locator('#headerAccount option')).to_have_count(2)
    await page.locator('#headerAccount').select_option('second')
    await page.locator('#discardMessageChanges').click()
    await expect(page).to_have_url('http://messages.test/#/accounts/second/messages')
    await expect(page.locator('#messageBox')).to_have_value('')
    await expect(page.locator('#messageImageCount')).to_have_text('0 / 10 张')
    await page.locator('#messageBox').fill('备用账户新消息')
    await page.locator('#imageInput').set_input_files({'name':'备用图.png','mimeType':'image/png','buffer':sample_png(True)})
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    assert all(body['accountId']=='second' for path,body in posts)
    assert server.CONFIG.read_bytes()==before
    assert 'images/second/' in posts[-1][1]['images'][0]
    assert not errors


async def test_latest_message_reference_preview(page,server,http_request):
    await page.set_viewport_size({'width':1670,'height':941})
    posts,gets,errors=await install_messages(page,server,http_request,'reference')
    await expect(page.locator('#messageBox')).to_have_value('续火花')
    await expect(page.locator('#messageCharCount')).to_have_text('3 / 500 字')
    await expect(page.locator('.message-image-placeholder')).to_have_count(4)
    await expect(page.locator('[data-message-preference="random"]')).to_have_attribute('aria-checked','true')
    await capture(page,'messages-latest-reference-1670.png')
    assert not posts and not errors


async def test_message_preference_roundtrip_guard_and_keyboard(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request)
    await page.locator('[data-message-preference="text_first"]').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('未保存修改')
    await page.locator('[data-page-link="home"]').first.click()
    await expect(page.locator('#messageDiscardDialog')).to_be_visible()
    await page.locator('#keepMessageEditing').click()
    await expect(page.locator('[data-message-preference="text_first"]')).to_have_attribute('aria-checked','true')
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    assert posts[-1][1]['preference']=='text_first'
    config=server.load_config()
    assert config['message_preference']=='text_first'
    assert len(config['messages'][0]['choices'])==5
    task,_=server.review_context(server.account_paths_for('main'))
    assert {m.type for m in task.targets[0].messages[0].choices}=={'text'}
    await page.reload()
    await expect(page.locator('[data-message-preference="text_first"]')).to_have_attribute('aria-checked','true')
    await page.locator('[data-message-preference="text_first"]').focus()
    await page.keyboard.press('ArrowRight')
    await expect(page.locator('[data-message-preference="image_first"]')).to_have_attribute('aria-checked','true')
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    task,_=server.review_context(server.account_paths_for('main'))
    assert {m.type for m in task.targets[0].messages[0].choices}=={'image'}
    assert not errors


@pytest.mark.parametrize('preference,types,expected',[('text_first',['image'],'image'),('image_first',['text'],'text'),('text_first',['text','image'],'text'),('image_first',['text','image'],'image')])
def test_preference_fallback_and_friend_override(preference,types,expected):
    from app.rules import effective_rows
    config={'friends':['甲','乙'],'message_preference':preference,'messages':[{'type':'random','choices':[{'type':kind,'value':'内容'} for kind in types]}],
            'friend_rules':{'乙':{'mode':'custom','messages':[{'type':'text','value':'专属'}]}}}
    rows=effective_rows(config)
    assert rows[0]['messages']==[{'type':expected,'value':'内容'}]
    assert rows[1]['messages']==[{'type':'text','value':'专属'}]
    from datetime import datetime
    config['daily_messages']={'date':datetime.now().strftime('%Y-%m-%d'),'targets':{'甲':[{'type':'text','value':'已冻结'}]}}
    assert effective_rows(config)[0]['messages']==[{'type':'text','value':'已冻结'}]


def test_message_preference_invalid_is_atomic(server,http_request):
    before=server.CONFIG.read_bytes()
    for value in [None,[],{},'wrong']:
        status,_=http_request('/api/message',{'accountId':'main','texts':['测试'],'images':[],'preference':value})
        assert status==400
        assert server.CONFIG.read_bytes()==before


async def test_preference_failure_and_stream_update_keep_draft(page,server,http_request):
    posts,gets,errors=await install_messages(page,server,http_request,'save_failure')
    await page.locator('#messageBox').fill('文字草稿')
    await page.locator('[data-message-preference="image_first"]').click()
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageFeedback')).to_contain_text('当前任务占用')
    await expect(page.locator('[data-message-preference="image_first"]')).to_have_attribute('aria-checked','true')
    await expect(page.locator('#messageBox')).to_have_value('文字草稿')
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    assert server.load_config()['message_preference']=='image_first'
    assert not errors


async def test_reference_small_window_remains_usable(page,server,http_request):
    await page.set_viewport_size({'width':390,'height':844})
    posts,gets,errors=await install_messages(page,server,http_request,'reference')
    assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
    await page.locator('[data-message-preference="text_first"]').click()
    await page.locator('#saveMessageInline').click()
    await expect(page.locator('#messageSavedBadge')).to_have_text('当前消息已保存')
    await capture(page,'messages-latest-small-390.png')
    assert not errors
