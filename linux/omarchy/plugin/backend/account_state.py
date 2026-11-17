"""Account-scoped schedules and conservative migration of older shared state."""
import copy
import json
import re
from pathlib import Path

import accounts
import recovery

MAX_TIME = 4102444800
MAX_RECORDS = 128
REASONS = {'', 'rate_limit', 'transient', 'account_in_use', 'settling', *recovery.BLOCKED}
PASS_COUNTERS = ('completed', 'total', 'uploaded', 'failed', 'history')


def default():
    return {'version': 2, 'enabled': False, 'accounts': {}, 'current': '', 'active': '',
            'global_until': 0, 'legacy_until': 0, 'spacing_until': 0, 'settling_until': 0,
            'processed': '', 'live_processed': '', 'notice': '', 'corrupt': False, 'attention': '', 'live_upload_wait': False, 'metadata_error': '', 'legacy': None}


def number(value, maximum=MAX_TIME):
    return isinstance(value, int) and not isinstance(value, bool) and 0 <= value <= maximum


def clean_record(saved):
    if not isinstance(saved, dict):
        raise ValueError('Invalid account schedule')
    result = recovery.default()
    result['enabled'] = True
    for key in ('pending', 'corrupt'):
        if not isinstance(saved.get(key), bool):
            raise ValueError('Invalid account schedule flag')
        result[key] = saved[key]
    for key, maximum in (('next_at', MAX_TIME), ('hard_until', MAX_TIME), ('failures', 20), ('skipped', 10_000_000)):
        if not number(saved.get(key), maximum):
            raise ValueError('Invalid account schedule number')
        result[key] = saved[key]
    if saved.get('reason') not in REASONS:
        raise ValueError('Invalid account schedule reason')
    result['reason'] = saved['reason']
    processed = saved.get('processed', '')
    if processed and not recovery.INVOCATION.fullmatch(str(processed)):
        raise ValueError('Invalid account invocation')
    result['processed'] = processed
    source = saved.get('progress')
    if not isinstance(source, dict):
        raise ValueError('Invalid account progress')
    clean = recovery.empty_progress('backfill')
    for key in PASS_COUNTERS:
        if source.get(key) is not None and not number(source.get(key), 10_000_000):
            raise ValueError('Invalid account progress number')
        clean[key] = source.get(key)
    if source.get('phase') in {'preparing', 'recovering', 'limited', 'failed', 'complete'}:
        clean['phase'] = source['phase']
    if source.get('updated_at') is not None:
        if not number(source['updated_at']):
            raise ValueError('Invalid progress time')
        clean['updated_at'] = source['updated_at']
    result['progress'] = clean
    return result


def read_json(path, maximum=512 * 1024):
    with path.open('rb') as handle:
        data = handle.read(maximum + 1)
    if len(data) > maximum:
        raise ValueError('Saved state is too large')
    return json.loads(data)


def read(path):
    value = default()
    try:
        saved = read_json(path)
        if isinstance(saved, dict) and saved.get('version') == 1:
            legacy = recovery.read(path)
            value.update(enabled=legacy['enabled'], corrupt=legacy['corrupt'], legacy=legacy)
            return value
        if not isinstance(saved, dict) or saved.get('version') != 2:
            raise ValueError('Invalid schedule version')
        for key in ('enabled', 'corrupt'):
            if not isinstance(saved.get(key), bool):
                raise ValueError('Invalid schedule flag')
            value[key] = saved[key]
        if saved.get('attention', '') not in {'', 'state_store', 'unavailable'}:
            raise ValueError('Invalid shared attention state')
        value['attention'] = saved.get('attention', '')
        if not isinstance(saved.get('live_upload_wait', False), bool):
            raise ValueError('Invalid live upload wait flag')
        value['live_upload_wait'] = saved.get('live_upload_wait', False)
        for key in ('global_until', 'legacy_until', 'spacing_until', 'settling_until'):
            if not number(saved.get(key)):
                raise ValueError('Invalid schedule deadline')
            value[key] = saved[key]
        for key in ('current', 'active'):
            identifier = saved.get(key, '')
            if identifier and not accounts.account_id(identifier):
                raise ValueError('Invalid account identifier')
            value[key] = identifier
        source = saved.get('accounts')
        if not isinstance(source, dict) or len(source) > MAX_RECORDS:
            raise ValueError('Invalid account schedules')
        for identifier, record in source.items():
            if not accounts.account_id(identifier):
                raise ValueError('Invalid account identifier')
            value['accounts'][identifier] = clean_record(record)
        if value['active'] and value['active'] not in value['accounts']:
            raise ValueError('Missing active account schedule')
        processed = saved.get('processed', '')
        if processed and not recovery.INVOCATION.fullmatch(str(processed)):
            raise ValueError('Invalid completion identifier')
        value['processed'] = processed
        live_processed = saved.get('live_processed', '')
        if live_processed and not recovery.INVOCATION.fullmatch(str(live_processed)):
            raise ValueError('Invalid live invocation')
        value['live_processed'] = live_processed
        if isinstance(saved.get('notice'), str) and len(saved['notice']) <= 256:
            value['notice'] = saved['notice']
        # Metadata error is fixed local UI text, not persisted Steam contents.
        if saved.get('metadata_error') in {'Sign in to Steam with a remembered login.', 'Steam account information is unavailable; waiting for Steam.'}:
            value['metadata_error'] = saved['metadata_error']
        if saved.get('legacy') is not None:
            value['legacy'] = clean_record(saved['legacy'])
    except FileNotFoundError:
        pass
    except (OSError, ValueError, TypeError):
        value.update(enabled=True, corrupt=True)
    return value


def save(path, value):
    backup = path.with_name(path.stem + '.v1.json')
    if value.get('version') == 2 and path.exists() and not backup.exists():
        try:
            original = path.read_bytes() if path.stat().st_size <= 16384 else b''
            previous = json.loads(original)
            if isinstance(previous, dict) and previous.get('version') == 1:
                with backup.open('xb') as handle:
                    handle.write(original)
                backup.chmod(0o600)
        except (ValueError, FileExistsError):
            pass
    recovery.save(path, value)


def ensure(value, identifier):
    if identifier not in value['accounts']:
        if len(value['accounts']) >= MAX_RECORDS:
            value['corrupt'] = True
            raise ValueError('Too many saved account schedules; preserve the file for repair')
        value['accounts'][identifier] = recovery.default()
        value['accounts'][identifier]['enabled'] = True
    return value['accounts'][identifier]


def attributed_accounts(records):
    found = set()
    for message, _ in recovery.messages(records):
        match = re.search(r'gc: account ([1-9][0-9]*) was rate-limited by Steam', message)
        if match and accounts.account_id(match.group(1)):
            found.add(match.group(1))
    return found


def migrate(value, now, records):
    legacy = value['legacy']
    if legacy is None:
        return
    value['processed'] = legacy['processed']
    deadline = max(legacy['hard_until'], legacy['next_at'])
    attributed = attributed_accounts(records)
    if attributed and legacy['hard_until'] > 0:
        for identifier in attributed:
            prior = ensure(value, identifier)
            record = copy.deepcopy(legacy)
            record['enabled'] = True
            record['hard_until'] = max(prior['hard_until'], record['hard_until'])
            record['next_at'] = max(record['hard_until'], record['next_at'])
            value['accounts'][identifier] = record
    elif deadline > now:
        # Never guess that a newly selected account caused an older shared throttle.
        value['legacy_until'] = max(value['legacy_until'], deadline)
    if legacy['reason'] == 'state_store' and not legacy['corrupt']:
        value['attention'] = 'state_store'
    value['corrupt'] |= legacy['corrupt']
    value['legacy'] = None


def reconcile(value, metadata):
    value['metadata_error'] = metadata.get('error', '')
    if value['metadata_error']:
        return False
    for item in metadata['accounts']:
        ensure(value, item['id'])
    current = metadata['current']
    changed = current != value['current']
    value['current'] = current
    if changed and current:
        # A login change creates fresh work without erasing that account's cooldown.
        record = ensure(value, current)
        record['pending'] = True
        if record['reason'] == 'auth' and not record['corrupt']:
            record.update(reason='', next_at=max(record['next_at'], record['hard_until']))
    return changed


def eligible(value, identifier, now):
    record = value['accounts'].get(identifier)
    return (value['enabled'] and not value['corrupt'] and not value['attention'] and not value['metadata_error'] and record is not None
            and record['pending'] and not record['corrupt'] and record['reason'] not in recovery.BLOCKED
            and now >= max(value['global_until'], value['legacy_until'], value['spacing_until'],
                           value['settling_until'], record['hard_until'], record['next_at']))


def ordered(value, metadata):
    ids = [item['id'] for item in metadata['accounts']]
    # The current account wins; the others have stable numeric order rather than VDF order.
    return sorted(ids, key=lambda identifier: (identifier != value['current'], int(identifier)))


def choose(value, metadata, now):
    return next((identifier for identifier in ordered(value, metadata) if eligible(value, identifier, now)), '')


def focus(value, metadata, now):
    return value['active'] or choose(value, metadata, now) or value['current'] or next(iter(ordered(value, metadata)), '')


def row_phase(item, current, active, now):
    if active:
        return 'working'
    if item['corrupt']:
        return 'attention'
    if item['reason'] == 'auth':
        return 'auth' if current else 'choose_account'
    if item['reason'] in recovery.BLOCKED:
        return 'attention'
    if not item['pending']:
        return 'caught_up'
    return 'cooldown' if item['hard_until'] > now else 'retry'


def last_pass(progress):
    if progress['mode'] != 'backfill':
        return None
    has_counts = any(progress[key] is not None for key in PASS_COUNTERS)
    if has_counts or (progress['phase'] == 'complete' and progress['updated_at']):
        return {key: progress[key] for key in ('mode', 'phase', *PASS_COUNTERS, 'updated_at')}
    return None


def view(value, metadata, units, session, now):
    identifier = focus(value, metadata, now)
    record = copy.deepcopy(value['accounts'].get(identifier, recovery.default()))
    record['enabled'] = value['enabled']
    record['corrupt'] |= value['corrupt']
    identifiers = ordered(value, metadata)
    known = [value['accounts'].get(i, recovery.default()) for i in identifiers]
    current = value['accounts'].get(value['current']) if value['current'] in identifiers else None
    waiting_for_account = False
    all_done = bool(known) and all(not item['pending'] for item in known)
    candidate = choose(value, metadata, now)
    if value['corrupt']:
        record.update(reason='state_store', pending=True)
    elif value['attention']:
        record.update(reason=value['attention'], pending=True)
    elif value['global_until'] > now:
        record.update(reason='upload_rate_limit', pending=True, hard_until=value['global_until'])
    elif value['legacy_until'] > now:
        record.update(reason='legacy_cooldown', pending=True, hard_until=value['legacy_until'])
    elif value['metadata_error'] or not known:
        record.update(reason='auth' if not known and not value['metadata_error'] else 'account_metadata', pending=True)
    elif all_done:
        record.update(reason='', pending=False)
    elif candidate and not value['active']:
        record = copy.deepcopy(value['accounts'][candidate]); record['enabled'] = value['enabled']
    elif not value['active']:
        deadlines = [(max(item['hard_until'], item['next_at'], value['spacing_until'], value['settling_until']), item)
                     for item in known if item['pending'] and item['reason'] not in recovery.BLOCKED and not item['corrupt']]
        if deadlines:
            deadline, waiting = min(deadlines, key=lambda item: item[0])
            record = copy.deepcopy(waiting); record['enabled'] = value['enabled']; record['next_at'] = deadline
        else:
            pending = [item for item in known if item['pending']]
            # A saved login for another account can be retried when the user selects it in Steam.
            actionable = next((item for item in pending if item is current or item['reason'] != 'auth' or item['corrupt']), None)
            waiting_for_account = (actionable is None and current is not None and
                                   row_phase(current, True, False, now) == 'caught_up')
            record = copy.deepcopy(current if waiting_for_account else actionable or pending[0])
            record['enabled'] = value['enabled']
    result = recovery.view(record, units, session, now)
    if waiting_for_account and result['phase'] == 'caught_up':
        result['phase'] = 'waiting_for_account'
    elif result['phase'] == 'attention' and result['reason'] == 'auth' and current is None:
        result['reason'] = 'choose_account'
    if value['enabled'] and record['reason'] == 'account_metadata' and not session:
        result.update(phase='attention', reason='account_metadata')
    if value['enabled'] and value['global_until'] > now:
        result.update(phase='cooldown', reason='upload_rate_limit', next_at=value['global_until'])
    names = {item['id']: item['name'] for item in metadata['accounts']}
    result.update(current_account=names.get(value['current'], ''), active_account=names.get(value['active'], ''),
                  account_count=len(known), accounts=[])
    for identifier, item in zip(identifiers, known):
        phase = row_phase(item, identifier == value['current'], identifier == value['active'], now)
        result['accounts'].append({'id': identifier, 'name': names.get(identifier, 'Steam account'),
                                   'current': identifier == value['current'], 'phase': phase,
                                   'next_at': max(item['hard_until'], item['next_at']) or None,
                                   'progress': last_pass(item['progress'])})
    return result
