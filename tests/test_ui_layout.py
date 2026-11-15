"""Check navigation and layout against isolated local data at three widths."""
import os
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from playwright.async_api import expect

from test_contacts_api import page, server, http_request


@pytest.mark.parametrize("width,height", [(1320, 900), (1024, 900), (390, 844)])
async def test_workspaces_fit_and_removed_panels_stay_removed(page, server, http_request, width, height):
    config = server.load_config()
    config["friends"] = ["小林", "这是一个比较长的好友备注，用于确认操作按钮不会挤在一起"]
    server.write_json(server.CONFIG, config)
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def route(request_route):
        request = request_route.request
        parsed = urlsplit(request.url)
        if parsed.path == "/api/stream":
            return await request_route.fulfill(content_type="text/event-stream", body="\n")
        if parsed.path.startswith("/api/"):
            assert request.method == "GET", "Layout review must not change data or start tasks"
            status, body = http_request(parsed.path + ("?" + parsed.query if parsed.query else ""))
            return await request_route.fulfill(status=status, json=body)
        await request_route.fulfill(content_type="text/html", body=(server.ROOT / "ui/static/index.html").read_text())

    await page.set_viewport_size({"width": width, "height": height})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed', '1')")
    await page.route("http://layout.test/**", route)
    capture = os.environ.get("DOUYIN_UI_CAPTURE_DIR")
    for workspace, title in [("home", "首页"), ("friends", "好友管理"), ("tasks", "自动任务"), ("messages", "消息管理"), ("system", "登录与记录"), ("accounts", "多账户管理")]:
        await page.goto(f"http://layout.test/#/accounts/main/{workspace}")
        await expect(page.locator("#pageTitle")).to_have_text(title)
        if workspace == "home":
            await expect(page.locator(".desktop-statusbar")).to_be_visible()
        else:
            await expect(page.locator(".desktop-statusbar")).not_to_be_visible()
        if workspace in ["tasks", "messages", "system"]:
            await expect(page.locator(f".{workspace}-footer")).not_to_be_visible()
        if workspace in ["friends", "messages", "system"]:
            await expect(page.locator(f".{workspace}-page-description")).to_have_count(0)
        await expect(page.locator(".schedule-environment,.accounts-guidance")).to_have_count(0)
        for removed in ["#friendMoreFilter", "#friendFilterMenu", "#taskSendBtn"]:
            await expect(page.locator(removed)).not_to_be_visible()
        await expect(page.locator(".tasks-page-description,.schedule-frequency,#messageTextHint")).to_have_count(0)
        await expect(page.locator("#checkLoginBtn")).to_be_attached()
        await expect(page.locator("#healthIssues, .accounts-note")).to_have_count(0)
        if workspace == "friends":
            await expect(page.locator("#albumGrid")).to_contain_text("小林")
        assert await page.evaluate("document.documentElement.scrollWidth <= innerWidth"), workspace
        if capture:
            await page.screenshot(path=str(Path(capture) / f"polish-{workspace}-{width}.png"), animations="disabled", full_page=True)
    assert errors == []


@pytest.mark.parametrize("outcome,label", [("valid", "本次检查可用"), ("expired", "需要重新登录"), ("check_failed", "检查未完成")])
async def test_login_check_updates_status_without_removed_panel(page, server, outcome, label):
    state = server.build_state("main")
    state["credential"] = {"present": True, "health": "unchecked"}
    actions = []
    errors = []
    page.on("pageerror", lambda error: errors.append(str(error)))

    async def route(request_route):
        parsed = urlsplit(request_route.request.url)
        if parsed.path == "/api/stream":
            return await request_route.fulfill(content_type="text/event-stream", body="\n")
        if parsed.path == "/api/action":
            actions.append(request_route.request.post_data_json)
            return await request_route.fulfill(json={"started": "health-check"})
        if parsed.path == "/api/health":
            return await request_route.fulfill(json={"credential": {"present": True, "health": outcome, "detail": "本次登录检查的说明"}, "running": None})
        if parsed.path == "/api/state":
            return await request_route.fulfill(json=state)
        await request_route.fulfill(content_type="text/html", body=(server.ROOT / "ui/static/index.html").read_text())

    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed', '1')")
    await page.route("http://login.test/**", route)
    await page.goto("http://login.test/#/accounts/main/system")
    await page.locator("#checkLoginBtn").click()
    await expect(page.locator("#credBadge")).to_have_text(label)
    if outcome == "valid":
        await expect(page.locator("#credStateText")).not_to_contain_text("本次登录检查的说明")
        await expect(page.locator("#credStateText")).to_have_attribute("title", "本次登录检查的说明")
    else:
        await expect(page.locator("#credStateText")).to_contain_text("本次登录检查的说明")
    assert actions == [{"accountId": "main", "action": "health-check"}]
    assert errors == []


@pytest.mark.parametrize('width,height', [(1920,1080),(1672,941),(1440,900),(1280,800),(1152,720),(1024,900),(390,844)])
async def test_navigation_geometry_is_stable_across_workspace_switches(page,server,http_request,width,height):
    async def route(r):
        parsed=urlsplit(r.request.url)
        if parsed.path=='/api/stream':
            return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            assert r.request.method=='GET'
            status,data=http_request(parsed.path+('?' + parsed.query if parsed.query else ''))
            return await r.fulfill(status=status,json=data)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.set_viewport_size({'width':width,'height':height})
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.route('http://navigation.test/**',route)
    await page.goto('http://navigation.test/#/accounts/main/home')
    baseline=None
    errors=[];page.on('pageerror',lambda error:errors.append(str(error)))
    for workspace,title in [('home','首页'),('friends','好友管理'),('tasks','自动任务'),('messages','消息管理'),('system','登录与记录'),('accounts','多账户管理'),('home','首页')]:
        await page.locator(f'.main-nav [data-page-link="{workspace}"]').click()
        await expect(page.locator('#pageTitle')).to_have_text(title)
        metrics=await page.evaluate('''() => {
            const selectors=['.sidebar','.sidebar .brand','.sidebar .brand-flame','.sidebar .brand-text strong','.main-nav','.main-nav .nav-link','.main-nav .nav-link .i','.account-picker','.account-picker select'];
            return selectors.flatMap(selector=>[...document.querySelectorAll(selector)].map(node=>{
                const r=node.getBoundingClientRect(),s=getComputedStyle(node);
                const positional=innerWidth>900 || !node.closest('.nav-link');
                return {selector,x:positional?Math.round(r.x*100)/100:null,y:Math.round(r.y*100)/100,width:Math.round(r.width*100)/100,height:Math.round(r.height*100)/100,font:s.fontSize,weight:s.fontWeight,padding:s.padding,gap:s.gap};
            }));
        }''')
        if baseline is None: baseline=metrics
        else: assert metrics==baseline, (workspace,width,metrics,baseline)
        assert await page.evaluate('document.documentElement.scrollWidth<=innerWidth')
        capture=os.environ.get('DOUYIN_UI_CAPTURE_DIR')
        if capture and width in [1672,1024]:
            await page.screenshot(path=str(Path(capture)/f'navigation-{workspace}-{width}.png'),full_page=True,animations='disabled')
    assert not errors
