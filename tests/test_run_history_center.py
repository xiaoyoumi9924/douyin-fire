"""Historical runs must survive writing and remain searchable per account."""
import json
from urllib.parse import urlencode

from app.main import _append_run_history
from test_contacts_api import server, http_request, page
from playwright.async_api import expect


def seed(server, count=35):
    for i in range(count):
        _append_run_history(server.ARTIFACTS, {"task_id": "task", "finished_at": f"2026-10-{i % 28 + 1:02d}T08:{i:02d}:00+08:00", "dry_run": i % 2 == 0,
            "results": [{"target": "好友01", "status": "failed" if i % 3 == 0 else "success", "sent": 0 if i % 3 == 0 else 1}]})


def test_history_survives_append_and_filters(server, http_request):
    seed(server)
    assert len((server.ARTIFACTS / "run-history.jsonl").read_text().splitlines()) == 35
    status, result = http_request('/api/history?accountId=main')
    assert status == 200 and result['total'] == 35 and len(result['items']) == 10 and result['pages'] == 4
    last = http_request('/api/history?accountId=main&page=4')[1]
    assert len(last['items']) == 5
    filtered = http_request('/api/history?accountId=main&mode=send&status=failed&start=2026-10-01&end=2026-10-10&export=1')[1]
    assert filtered['items']
    assert all(not row['dryRun'] and row['results'][0]['status'] == 'failed' and row['finishedAt'][:10] <= '2026-10-10' for row in filtered['items'])
    assert http_request('/api/history?accountId=missing')[0] == 400
    assert http_request('/api/history?accountId=main&start=2026-02-30')[0] == 400
    assert http_request('/api/history?accountId=main&page=-1')[0] == 400
    assert http_request('/api/history?accountId=main&start=2026-11-01&end=2026-10-01')[0] == 400


async def test_history_paging_and_filter_ui(page, server, http_request):
    seed(server)
    async def route(r):
        from urllib.parse import urlsplit
        parsed = urlsplit(r.request.url)
        if parsed.path == '/api/stream': return await r.fulfill(content_type='text/event-stream',body='\n')
        if parsed.path.startswith('/api/'):
            status, body = http_request(parsed.path + ('?' + parsed.query if parsed.query else ''))
            return await r.fulfill(status=status,json=body)
        await r.fulfill(content_type='text/html',body=(server.ROOT/'ui/static/index.html').read_text())
    await page.route('http://history.test/**',route)
    await page.add_init_script("sessionStorage.setItem('douyin-fire:login-welcome-dismissed','1')")
    await page.goto('http://history.test/#/accounts/main/system')
    await expect(page.locator('#historyContent')).to_be_visible()
    await expect(page.locator('#historyPageText')).to_contain_text('1 / 4')
    await page.locator('#historyNext').click()
    await expect(page.locator('#historyPageText')).to_contain_text('2 / 4')
    await page.locator('#historyFilterToggle').click()
    await expect(page.locator('#historyQueryPanel')).to_be_visible()
    await page.locator('#historyStatus').select_option('failed')
    await page.locator('#historySearch').click()
    await expect(page.locator('#historyPageSize')).to_contain_text('共 12 条')
    await page.locator('#historyReset').click()
    await expect(page.locator('#historyPageSize')).to_contain_text('共 35 条')
    await page.locator('.history-open').first.click()
    await expect(page.locator('#runDetailDialog')).to_be_visible()


def test_restore_preview_confirm_and_account_boundary(server, http_request):
    seed(server)
    exported = http_request('/api/history?accountId=main&export=1')[1]
    extra = {**exported['items'][0], 'finishedAt': '2026-11-14T08:00:00+08:00'}
    exported['items'].append(extra)
    path = server.ARTIFACTS / 'run-history.jsonl'
    before = path.read_bytes()
    body = {'accountId': 'main', 'backup': exported}
    status, preview = http_request('/api/history/restore', body)
    assert status == 200 and preview['added'] == 1 and preview['duplicates'] == 35
    assert path.read_bytes() == before
    status, result = http_request('/api/history/restore', {**body, 'confirm': True})
    assert status == 200 and result['total'] == 36
    backups = list((server.ARTIFACTS / 'history-backups').glob('*.jsonl'))
    assert len(backups) == 1 and backups[0].read_bytes() == before
    assert http_request('/api/history/restore', {**body, 'confirm': True})[1]['added'] == 0
    exported['accountId'] = 'different'
    assert http_request('/api/history/restore', body)[0] == 400
    assert len(path.read_text().splitlines()) == 36


def test_restore_does_not_change_delivery_history_or_drop_corrupt_lines(server, http_request):
    seed(server, 1)
    exported = http_request('/api/history?accountId=main&export=1')[1]
    exported['items'][0]['finishedAt'] = '2026-11-14T08:00:00+08:00'
    path = server.ARTIFACTS / 'run-history.jsonl'
    path.write_text(path.read_text() + '{broken\n')
    before = path.read_bytes()
    status, _ = http_request('/api/history/restore', {'accountId':'main','backup':exported,'confirm':True})
    assert status == 400 and path.read_bytes() == before
    assert not (server.ARTIFACTS / 'history.json').exists()
