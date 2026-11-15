"""Validate and merge run-result backups without changing delivery deduplication."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import uuid


def records_from_backup(payload, account_id):
    if not isinstance(payload, dict) or payload.get('version') != 1 or payload.get('accountId') != account_id:
        raise ValueError('备份格式不支持，或备份不属于当前账户')
    items = payload.get('items')
    if not isinstance(items, list) or len(items) > 5000:
        raise ValueError('每次最多恢复 5000 条运行记录')
    records = []
    for item in items:
        if not isinstance(item, dict) or not isinstance(item.get('results'), list) or len(item['results']) > 500:
            raise ValueError('运行记录格式不完整')
        try:
            stamp = datetime.fromisoformat(item['finishedAt'])
            if stamp.tzinfo is None:
                raise ValueError('记录时间缺少时区')
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('记录时间无效') from exc
        results = []
        for result in item['results']:
            if not isinstance(result, dict) or result.get('status') not in {'success', 'failed', 'unknown', 'skipped', 'duplicate'}:
                raise ValueError('记录结果无效')
            sent = result.get('sent', 0)
            if not isinstance(sent, int) or isinstance(sent, bool) or sent < 0:
                raise ValueError('发送数量无效')
            results.append({'target': str(result.get('target', '未知好友'))[:128], 'status': result['status'], 'sent': sent, 'error': str(result.get('error', ''))[:2000]})
        record = {'task_id': str(item.get('taskId', ''))[:128], 'finished_at': item['finishedAt'], 'dry_run': bool(item.get('dryRun')), 'results': results}
        if item.get('messageScoped'):
            record['message_scoped'] = True
        for public, private in [('startedAt', 'started_at'), ('durationSeconds', 'duration_seconds')]:
            if item.get(public) is not None:
                record[private] = item[public]
        records.append(record)
    return records


def identity(row):
    # Timing enrichment must not make an exported record a second copy.
    results = [{k: item.get(k, '' if k in {'error', 'target'} else 0) for k in ('target', 'status', 'sent', 'error')} for item in row.get('results', [])]
    return json.dumps([row.get('task_id', ''), row.get('finished_at'), bool(row.get('dry_run')), bool(row.get('message_scoped')), results], sort_keys=True, ensure_ascii=False)


def merge_records(path: Path, incoming, *, commit=False):
    original = path.read_text(encoding='utf-8') if path.exists() else ''
    current = []
    for line in original.splitlines():
        if not line.strip():
            continue
        row = json.loads(line)  # Fail closed: do not discard damaged old records.
        if not isinstance(row, dict) or not isinstance(row.get('results'), list):
            raise ValueError('原历史文件损坏，请先检查备份')
        current.append(row)
    seen = {identity(row) for row in current}
    added = []
    for row in incoming:
        key = identity(row)
        if key not in seen:
            seen.add(key)
            added.append(row)
    backup = None
    if commit and added:
        path.parent.mkdir(parents=True, exist_ok=True)
        folder = path.parent / 'history-backups'
        folder.mkdir(mode=0o700, exist_ok=True)
        backup = folder / f'{datetime.now():%Y%m%d-%H%M%S}-{uuid.uuid4().hex[:8]}.jsonl'
        if path.exists():
            shutil.copy2(path, backup)
            os.chmod(backup, 0o600)
        temporary = path.with_suffix('.restore.tmp')
        try:
            with temporary.open('w', encoding='utf-8') as output:
                os.chmod(temporary, 0o600)
                for row in sorted(current + added, key=lambda row: row.get('finished_at', '')):
                    output.write(json.dumps(row, ensure_ascii=False) + '\n')
                output.flush()
                os.fsync(output.fileno())
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    return {'existing': len(current), 'added': len(added), 'duplicates': len(incoming) - len(added), 'total': len(current) + len(added)}
