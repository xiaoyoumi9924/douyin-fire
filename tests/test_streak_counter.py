from datetime import datetime
import json
from app.models import TargetResult
from app.streaks import record_success,load_progress,snapshot,rename_progress,SOURCE


def stamp(day):return datetime.fromisoformat(f'2026-11-{day:02d}T08:00:00+08:00')


def seed(tmp_path):
    cache={'syncedAt':stamp(14).isoformat(),'contacts':[{'name':'好友','selectable':True,'streakDays':457,'streakSource':SOURCE}]}
    (tmp_path/'chat-list.json').write_text(json.dumps(cache))
    return cache


def test_success_increments_once_per_day_and_survives_restart(tmp_path):
    cache=seed(tmp_path)
    result=[TargetResult('好友','success',sent=1)]
    record_success(tmp_path,result,False,stamp(14))
    assert snapshot(cache,['好友'],stamp(14),progress=load_progress(tmp_path))['好友']['days']==457
    record_success(tmp_path,result,False,stamp(15))
    record_success(tmp_path,result,False,stamp(15))
    assert snapshot(cache,['好友'],stamp(15),progress=load_progress(tmp_path))['好友']['days']==458
    for status in ['failed','unknown','duplicate','skipped']:
        record_success(tmp_path,[TargetResult('好友',status,sent=1)],False,stamp(16))
    record_success(tmp_path,result,True,stamp(16))
    record_success(tmp_path,[TargetResult('好友','success',sent=0)],False,stamp(16))
    assert snapshot(cache,['好友'],stamp(16),progress=load_progress(tmp_path))['好友']['days']==458
    record_success(tmp_path,result,False,stamp(17))
    assert snapshot({},['好友'],stamp(17),progress=load_progress(tmp_path))['好友']['days']==459
    assert (tmp_path/'streak-progress.json').stat().st_mode & 0o777 == 0o600


def test_resync_new_baseline_and_rename_preserve_counter(tmp_path):
    cache=seed(tmp_path)
    record_success(tmp_path,[TargetResult('好友','success',sent=1)],False,stamp(15))
    cache['syncedAt']=stamp(15).isoformat();cache['contacts'][0]['streakDays']=500
    (tmp_path/'chat-list.json').write_text(json.dumps(cache))
    record_success(tmp_path,[TargetResult('好友','success',sent=1)],False,stamp(15))
    assert snapshot(cache,['好友'],stamp(15),progress=load_progress(tmp_path))['好友']['days']==500
    rename_progress(tmp_path,'好友','新备注',stamp(15))
    assert snapshot({},['新备注'],stamp(15),progress=load_progress(tmp_path))['新备注']['days']==500
    record_success(tmp_path,[TargetResult('新备注','success',sent=1)],False,stamp(16))
    assert snapshot({},['新备注'],stamp(16),progress=load_progress(tmp_path))['新备注']['days']==501


def test_no_baseline_stays_unknown_and_corrupt_journal_is_not_overwritten(tmp_path):
    result=[TargetResult('好友','success',sent=1)]
    record_success(tmp_path,result,False,stamp(15))
    assert snapshot({},['好友'],stamp(15),progress=load_progress(tmp_path))['好友']['status']=='unknown'
    file=tmp_path/'streak-progress.json';file.write_text('{broken')
    import pytest
    with pytest.raises(ValueError):record_success(tmp_path,result,False,stamp(16))
    assert file.read_text()=='{broken'
