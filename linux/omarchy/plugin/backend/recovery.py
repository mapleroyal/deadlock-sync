"""Persistent, clock-injected recovery policy; no background process or network IO."""
import json
import os
from pathlib import Path
import random
import re
import time
import subprocess

from progress import ANSI, COUNT_LIMIT, empty as empty_progress, parse_records, read_journal

DAY = 24 * 60 * 60
GRACE = 60
BLOCKED = {'auth', 'state_store', 'unavailable'}
INVOCATION = re.compile(r'[a-f0-9]{32}')
PRIORITY = {'': 0, 'transient': 1, 'account_in_use': 2, 'auth': 3,
            'unavailable': 4, 'rate_limit': 5, 'state_store': 6}


def default():
    return {'version': 1, 'enabled': False, 'pending': True, 'next_at': 0,
            'hard_until': 0, 'reason': '', 'failures': 0, 'processed': '',
            'skipped': 0, 'progress': empty_progress(), 'notice': '', 'corrupt': False}


def state_path():
    return Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'deadlock-api-ingest/recovery.json'


def read(path):
    value = default()
    def invalid():
        value.update(enabled=True, pending=True, reason='state_store', corrupt=True)
        return value
    try:
        if path.stat().st_size > 16384:
            return invalid()
        saved = json.loads(path.read_text())
        if not isinstance(saved, dict) or saved.get('version') != 1:
            return invalid()
        value['corrupt'] = saved.get('corrupt') is True
        for key in ('enabled', 'pending'):
            value[key] = saved.get(key) is True
            if not isinstance(saved.get(key), bool):
                value['corrupt'] = True
        for key, limit in (('next_at', 4102444800), ('hard_until', 4102444800),
                           ('failures', 20), ('skipped', COUNT_LIMIT)):
            number = saved.get(key, 0)
            if key in {'next_at', 'hard_until'} and key not in saved:
                value['corrupt'] = True
            if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= limit:
                value[key] = number
            else:
                value['corrupt'] = True
        if saved.get('reason') in {'', 'rate_limit', 'transient', 'account_in_use', 'settling', *BLOCKED}:
            value['reason'] = saved['reason']
        else:
            value['corrupt'] = True
        if INVOCATION.fullmatch(str(saved.get('processed', ''))):
            value['processed'] = saved['processed']
        elif saved.get('processed', '') != '':
            value['corrupt'] = True
        if isinstance(saved.get('progress'), dict):
            # UI validates these numbers again; never retain arbitrary log fields.
            source = saved['progress']
            clean = empty_progress('backfill')
            for key in ('completed', 'total', 'uploaded', 'failed', 'history'):
                number = source.get(key)
                if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= COUNT_LIMIT:
                    clean[key] = number
            if source.get('phase') in {'preparing', 'recovering', 'limited', 'failed', 'complete'}:
                clean['phase'] = source['phase']
            stamp = source.get('updated_at')
            if isinstance(stamp, int) and 0 < stamp <= 4102444800:
                clean['updated_at'] = stamp
            value['progress'] = clean
        notice = saved.get('notice', '')
        if isinstance(notice, str) and (notice == '' or re.fullmatch(r'(cooldown|attention|caught_up):([a-f0-9]{32}|corrupt)', notice)):
            value['notice'] = notice
    except FileNotFoundError:
        return value
    except (OSError, ValueError, TypeError):
        return invalid()
    if value['corrupt']:
        value['reason'] = 'state_store'
    return value


def save(path, value):
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    if value.get('corrupt') and path.exists():
        backup = path.with_suffix('.invalid')
        if not backup.exists():
            backup.write_bytes(path.read_bytes())
            backup.chmod(0o600)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)


def messages(records):
    for entry in records:
        if not isinstance(entry, dict):
            continue
        message = entry.get('MESSAGE')
        if isinstance(message, list) and len(message) <= 4096:
            try:
                message = bytes(message).decode('utf-8', 'replace')
            except (TypeError, ValueError):
                continue
        if not isinstance(message, str) or len(message) > 4096:
            continue
        stamp = str(entry.get('__REALTIME_TIMESTAMP', ''))
        stamp = int(stamp) // 1000000 if stamp.isdigit() else 0
        yield ANSI.sub('', message), stamp


def analyse(records):
    """Classify only known upstream messages; expose no account/match IDs or text."""
    cause, timestamp, skipped, limited_at, endpoint_limited_at = '', 0, 0, 0, 0
    endpoint_rate_limited = False
    for message, stamp in messages(records):
        current = ''
        if re.search(r'(?:HTTP|status|StatusCode|status_code)[^\n]{0,80}\b429\b', message, re.IGNORECASE):
            endpoint_limited_at = max(endpoint_limited_at, stamp)
            endpoint_rate_limited = True
        if ('gc: store unavailable, not tracking salt attempts:' in message or
                'GC store error' in message):
            current = 'state_store'
        elif ('was rate-limited by Steam' in message or 'Steam rate limit reached.' in message):
            current = 'rate_limit'
        elif ('gc: no usable Steam account' in message or
              'Steam session unavailable:' in message or 'Steam logon failed' in message or
              any(token in message.lower() for token in ('invalidpassword', 'invalid password', 'accessdenied',
                                                         'invalid refresh token', 'expired refresh token'))):
            current = 'auth'
        elif ('match history is private' in message or 'history is private' in message or
              'invalid permission' in message.lower()):
            current = 'unavailable'
        elif ('the Steam account is playing a game' in message or
              'Deadlock was launched' in message or 'Deadlock is running, close it' in message):
            current = 'account_in_use'
        match = re.search(r'gc: account \d+ skips (\d+) match\(es\) given up on after \d+ attempts', message)
        if match:
            skipped = min(COUNT_LIMIT, skipped + int(match.group(1)))
        if current == 'rate_limit':
            limited_at = max(limited_at, stamp)
        if current and PRIORITY[current] >= PRIORITY[cause]:
            cause, timestamp = current, stamp
    return {'cause': cause, 'at': timestamp, 'limited_at': limited_at, 'endpoint_limited_at': endpoint_limited_at, 'endpoint_rate_limited': endpoint_rate_limited, 'skipped': skipped,
            'progress': parse_records(records, 'backfill')}


def merge_details(stored, fresh):
    """Re-read after journal flush without weakening any known safety evidence."""
    result = dict(fresh if PRIORITY[fresh['cause']] >= PRIORITY[stored['cause']] else stored)
    result['limited_at'] = max(stored['limited_at'], fresh['limited_at'])
    result['endpoint_limited_at'] = max(stored.get('endpoint_limited_at', 0), fresh.get('endpoint_limited_at', 0))
    result['endpoint_rate_limited'] = stored.get('endpoint_rate_limited', False) or fresh.get('endpoint_rate_limited', False)
    result['skipped'] = max(stored['skipped'], fresh['skipped'])
    result['progress'] = (fresh['progress'] if (fresh['progress'].get('updated_at') or 0) >=
                          (stored['progress'].get('updated_at') or 0) else stored['progress'])
    return result


def retry(value, now, reason='transient', jitter=None):
    jitter = jitter or random.SystemRandom().randint
    value['pending'] = True
    value['failures'] = min(20, value['failures'] + 1)
    base = 900 if reason == 'account_in_use' else 300
    delay = min(3600, base * 2 ** min(value['failures'] - 1, 4))
    value['reason'] = reason
    value['next_at'] = max(value['hard_until'], now + delay + jitter(0, 60))


def finish(value, unit, now, records, *, deliberate=False, jitter=None, details=None):
    """Consume a completed invocation once, including pre-existing failed runs."""
    invocation = unit.get('invocation', '')
    if not INVOCATION.fullmatch(invocation) or invocation == value['processed']:
        return False
    jitter = jitter or random.SystemRandom().randint
    details = details if details is not None else analyse(records)
    value['processed'] = invocation
    value['progress'] = details['progress']
    value['skipped'] = details['skipped']
    cause = details['cause']
    if details['limited_at'] or cause == 'rate_limit':
        at = min(now, details['limited_at']) if details['limited_at'] > 0 else now
        value['hard_until'] = max(value['hard_until'], at + DAY + jitter(0, 300))
    if cause == 'rate_limit':
        value.update(pending=True, reason='rate_limit', next_at=value['hard_until'], failures=0)
    elif cause in BLOCKED:
        value.update(pending=True, reason=cause, next_at=0)
    elif deliberate:
        value['pending'] = True
        value['next_at'] = max(value['next_at'], value['hard_until'], now + GRACE)
        if value['reason'] not in BLOCKED and value['hard_until'] <= now:
            value['reason'] = 'settling'
    elif cause == 'account_in_use':
        retry(value, now, cause, jitter)
    elif unit.get('result') == 'success' and unit.get('code') == 1 and unit.get('exit_status') == 0:
        # Exhausted old matches stay skipped upstream; a later game may add
        # new recoverable history, so do not block all future recovery.
        value.update(pending=False, reason='', next_at=0, failures=0)
    else:
        retry(value, now, 'transient', jitter)
    if value['corrupt']:
        value.update(pending=True, reason='state_store', next_at=0)
    return True


def view(value, units, session, now):
    live = units.get('deadlock-api-ingest.service', {}).get('state')
    backfill = units.get('deadlock-api-ingest-backfill.service', {}).get('state')
    phase = 'off'
    if value['enabled']:
        if session or live in {'active', 'activating', 'deactivating'}:
            phase = 'game'
        elif backfill in {'active', 'activating', 'deactivating'}:
            phase = 'working'
        elif value['reason'] in BLOCKED:
            phase = 'attention'
        elif not value['pending']:
            phase = 'caught_up'
        elif value['hard_until'] > now:
            phase = 'cooldown'
        else:
            phase = 'retry'
    next_at = max(value['next_at'], value['hard_until']) or None
    if phase not in {'cooldown', 'retry', 'game'} or (phase == 'game' and (next_at or 0) <= now):
        next_at = None
    return {'enabled': value['enabled'], 'phase': phase,
            'next_at': next_at,
            'reason': value['reason'], 'skipped': value['skipped'], 'repair_required': value['corrupt']}


def completion_path(path):
    return path.with_name('completion.json')


def read_completion(path):
    """Missing is normal; malformed completion fails closed rather than retrying."""
    try:
        if path.stat().st_size > 16384:
            return {'corrupt': True}
        value = json.loads(path.read_text())
        if not isinstance(value, dict) or not INVOCATION.fullmatch(str(value.get('invocation', ''))):
            return {'corrupt': True}
        if value.get('result') not in {'success', 'exit-code', 'signal', 'core-dump', 'timeout',
                                        'watchdog', 'start-limit-hit', 'resources', 'oom-kill'}:
            return {'corrupt': True}
        for key, maximum in (('code', 3), ('exit_status', 255), ('finished_at', 4102444800)):
            number = value.get(key)
            if not isinstance(number, int) or isinstance(number, bool) or not 0 <= number <= maximum:
                return {'corrupt': True}
        details = value.get('details')
        if not isinstance(details, dict) or details.get('cause') not in {'', 'rate_limit', 'transient', 'account_in_use', *BLOCKED}:
            return {'corrupt': True}
        clean = analyse([])
        clean['cause'] = details['cause']
        for key, limit in (('at', 4102444800), ('limited_at', 4102444800), ('endpoint_limited_at', 4102444800), ('skipped', COUNT_LIMIT)):
            number = details.get(key, 0) if key == 'endpoint_limited_at' else details.get(key)
            if not isinstance(number, int) or isinstance(number, bool) or not 0 <= number <= limit:
                return {'corrupt': True}
            clean[key] = number
        if not isinstance(details.get('endpoint_rate_limited', False), bool):
            return {'corrupt': True}
        clean['endpoint_rate_limited'] = details.get('endpoint_rate_limited', False)
        progress = details.get('progress')
        if not isinstance(progress, dict):
            return {'corrupt': True}
        for key in ('completed', 'total', 'uploaded', 'failed', 'history'):
            number = progress.get(key)
            if isinstance(number, int) and not isinstance(number, bool) and 0 <= number <= COUNT_LIMIT:
                clean['progress'][key] = number
        if progress.get('phase') in {'preparing', 'recovering', 'limited', 'failed', 'complete'}:
            clean['progress']['phase'] = progress['phase']
        stamp = progress.get('updated_at')
        if isinstance(stamp, int) and not isinstance(stamp, bool) and 0 < stamp <= 4102444800:
            clean['progress']['updated_at'] = stamp
        value['details'] = clean
        identifier = value.get('account_id', '')
        if identifier and (not re.fullmatch(r'[1-9][0-9]{0,9}', str(identifier)) or int(identifier) > 4294967295):
            return {'corrupt': True}
        value['account_id'] = identifier
        return value
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError):
        return {'corrupt': True}


def record_completion(path, environment, now, reader=read_journal, account_id=''):
    """Lock-free unit hook, safe when the scheduler waits for systemctl stop."""
    invocation = environment.get('INVOCATION_ID', '')
    if not INVOCATION.fullmatch(invocation):
        return False
    code = {'exited': 1, 'killed': 2, 'dumped': 3}.get(environment.get('EXIT_CODE'), 0)
    status = environment.get('EXIT_STATUS', '')
    value = {'invocation': invocation, 'state': 'inactive',
             'result': environment.get('SERVICE_RESULT', 'resources'), 'code': code,
             'exit_status': int(status) if status.isdigit() else 1, 'finished_at': now, 'account_id': account_id}
    try:
        value['details'] = analyse(reader(invocation))
    except (OSError, ValueError, subprocess.SubprocessError):
        # This hook must not block shutdown or lose the exit result if journald
        # is unavailable. Store only fixed fallback fields, never the error.
        value['details'] = analyse([])
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    temporary = path.with_name(path.name + '.' + str(os.getpid()) + '.tmp')
    temporary.write_text(json.dumps(value))
    temporary.chmod(0o600)
    temporary.replace(path)
    return True
