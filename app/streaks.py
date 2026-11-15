"""Page baseline plus local confirmed-success days (not an official streak)."""
from datetime import datetime
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

SOURCE = 'chat-list-dom-v1'


def valid_days(value):
    return type(value) is int and 1 <= value <= 36500


TRACKER = 'streak-progress.json'


def load_progress(folder):
    import json
    try:
        data = json.loads((folder / TRACKER).read_text())
    except FileNotFoundError:
        return {'baselines': {}, 'successDays': {}}
    if not isinstance(data, dict) or not isinstance(data.get('baselines'), dict) or not isinstance(data.get('successDays'), dict):
        raise ValueError('本地火花累计文件格式不正确')
    return data


def baseline_for(cache, name, progress, now):
    candidate = progress.get('baselines', {}).get(name)
    if candidate:
        try:
            stamp = datetime.fromisoformat(candidate['at'])
            if not valid_days(candidate.get('days')) or stamp.tzinfo is None or (stamp - now).total_seconds() > 5:
                candidate = None
        except (ValueError, TypeError, KeyError):
            candidate = None
    rows = cache.get('contacts', []) if isinstance(cache, dict) and not cache.get('error') else []
    matches = [row for row in rows if isinstance(row, dict) and row.get('name') == name] if isinstance(rows, list) else []
    if len(matches) == 1:
        row = matches[0]
        if row.get('selectable') and row.get('streakSource') == SOURCE and valid_days(row.get('streakDays')):
            fresh = {'days': row['streakDays'], 'at': cache.get('syncedAt')}
            try:
                stamp = datetime.fromisoformat(fresh['at'])
                if stamp.tzinfo is not None and (stamp - now).total_seconds() <= 5:
                    if not candidate or stamp > datetime.fromisoformat(candidate['at']):
                        candidate = fresh
            except (ValueError, TypeError, KeyError):
                pass
    return candidate


def snapshot(cache, names, now=None, timezone='Asia/Shanghai', progress=None):
    progress = progress or {'baselines': {}, 'successDays': {}}
    output = {name: {'status': 'unknown', 'days': None, 'observedAt': None} for name in names}
    try:
        zone = ZoneInfo(timezone)
        now = (now or datetime.now().astimezone()).astimezone(zone)
    except (ValueError, ZoneInfoNotFoundError):
        return output
    for name in names:
        baseline = baseline_for(cache, name, progress, now)
        if not baseline:
            continue
        try:
            observed = datetime.fromisoformat(baseline['at']).astimezone(zone)
            successes = {datetime.fromisoformat(day).date() for day in progress.get('successDays', {}).get(name, [])}
            counted = {day for day in successes if observed.date() < day <= now.date()}
            days = baseline['days'] + len(counted)
            output[name] = {'status': 'local' if counted or observed.date() != now.date() else 'observed', 'days': days,
                            'observedAt': observed.isoformat(), 'countedDays': len(counted), 'lastSuccessDate': max(counted).isoformat() if counted else None}
        except (ValueError, TypeError, KeyError):
            continue
    return output


def record_success(folder, results, dry_run, now=None):
    if dry_run:
        return
    from app.contacts import write_cache
    import json
    now = now or datetime.now(ZoneInfo("Asia/Shanghai"))
    progress = load_progress(folder)
    try:
        cache = json.loads((folder / 'chat-list.json').read_text())
    except (FileNotFoundError, ValueError):
        cache = {}
    changed = False
    for result in results:
        if result.status != 'success' or result.sent <= 0:
            continue
        name = result.target
        baseline = baseline_for(cache, name, progress, now)
        if baseline:
            progress['baselines'][name] = baseline
        dates = set(progress['successDays'].get(name, []))
        dates.add(now.date().isoformat())
        progress['successDays'][name] = sorted(dates)
        changed = True
    if changed:
        write_cache(folder / TRACKER, progress)


def rename_progress(folder, old, new, now=None):
    if old == new:
        return
    from app.contacts import write_cache
    import json
    progress = load_progress(folder)
    try:
        cache = json.loads((folder / 'chat-list.json').read_text())
    except (FileNotFoundError, ValueError):
        cache = {}
    baseline = baseline_for(cache, old, progress, now or datetime.now().astimezone())
    if baseline:
        progress['baselines'][new] = baseline
    progress['baselines'].pop(old, None)
    if old in progress['successDays']:
        progress['successDays'][new] = sorted(set(progress['successDays'].get(new, []) + progress['successDays'].pop(old)))
    if baseline or new in progress['successDays']:
        write_cache(folder / TRACKER, progress)


def view_progress(folder):
    try:
        return load_progress(folder)
    except (ValueError, OSError):
        return {'baselines': {}, 'successDays': {}}


def remember_baselines(folder, cache):
    from app.contacts import write_cache
    progress = load_progress(folder)
    rows = cache.get('contacts', []) if isinstance(cache, dict) else []
    now = datetime.now(ZoneInfo('Asia/Shanghai'))
    changed = False
    for row in rows if isinstance(rows, list) else []:
        if not isinstance(row, dict) or not isinstance(row.get('name'), str):
            continue
        baseline = baseline_for(cache, row['name'], progress, now)
        if baseline and progress['baselines'].get(row['name']) != baseline:
            progress['baselines'][row['name']] = baseline
            changed = True
    if changed:
        write_cache(folder / TRACKER, progress)
