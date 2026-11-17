#!/usr/bin/env python3
"""Short-lived game-priority lifecycle and recovery scheduling commands."""
import fcntl
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid

sys.dont_write_bytecode = True
from progress import empty as empty_progress, read_journal, snapshot
import recovery
import accounts
import account_state

LIVE = 'deadlock-api-ingest.service'
BACKFILL = 'deadlock-api-ingest-backfill.service'
RUNNING = {'active', 'activating', 'deactivating'}
HANDOFF = 21  # Whole-second persistence still guarantees at least 20 real seconds.


def systemctl(*args):
    result = subprocess.run(['/usr/bin/systemctl', '--user', *args],
                            capture_output=True, text=True, timeout=12)
    if result.returncode:
        raise RuntimeError((result.stderr or result.stdout or 'System service request failed').strip()[:1024])
    return result.stdout


def read_units():
    output = systemctl('show', LIVE, BACKFILL,
                      '--property=Id,LoadState,ActiveState,MemoryCurrent,InvocationID,Result,ExecMainCode,ExecMainStatus')
    units = {}
    for block in output.strip().split('\n\n'):
        fields = dict(line.split('=', 1) for line in block.splitlines() if '=' in line)
        name = fields.get('Id', '')
        state = fields.get('ActiveState', 'unavailable')
        if fields.get('LoadState') == 'not-found':
            state = 'unavailable'
        def number(key):
            value = fields.get(key, '0')
            return int(value) if value.isdigit() else 0
        units[name] = {'state': state, 'memory': number('MemoryCurrent'),
                       'invocation': fields.get('InvocationID', ''), 'result': fields.get('Result', ''),
                       'code': number('ExecMainCode'), 'exit_status': number('ExecMainStatus')}
    return units


def game_session():
    """A process guard also catches game startup before its first window appears."""
    found = []
    for entry in Path('/proc').iterdir():
        if not entry.name.isdigit():
            continue
        try:
            if entry.stat().st_uid != os.getuid():
                continue
            name = (entry / 'cmdline').read_bytes().split(b'\0', 1)[0].decode(errors='replace')
            name = name.replace('\\', '/').rsplit('/', 1)[-1].lower()
            if name in {'deadlock', 'deadlock.exe', 'citadel.exe'}:
                found.append(entry.name)
        except OSError:
            pass
    return ','.join(sorted(found))[:256]


def notify(phase):
    messages = {
        'cooldown': 'Steam rate-limited history recovery. Automatic retry is scheduled after a 24-hour cooldown; the bar shows when.',
        'attention': 'Automatic history recovery needs attention. Click the gamepad icon for details. Live sync remains available while playing.',
        'caught_up': 'Available match history has been processed. Automatic recovery will check again after you play.',
    }
    if phase not in messages:
        return
    try:
        subprocess.run(['/usr/bin/notify-send', '--app-name=Deadlock Sync', '--icon=applications-games',
                        'Deadlock Match Backfill', messages[phase]], timeout=3, check=False,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except (OSError, subprocess.SubprocessError):
        pass


class Controller:
    def __init__(self, runtime, persistent=None, clock=None):
        self.runtime = Path(runtime)
        self.directory = self.runtime / 'deadlock-api-ingest-bar'
        self.directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.state_path = self.directory / 'state.json'
        self.marker = self.runtime / 'deadlock-api-ingest-bar.status'
        self.persistent = Path(persistent) if persistent else recovery.state_path()
        self.clock = clock or time.time
        self.request_path = self.persistent.with_name('request.json')
        self.running_path = self.persistent.with_name('running.json')

    def read(self):
        try:
            state = json.loads(self.state_path.read_text())
            return {'session': str(state.get('session', ''))[:256], 'paused': state.get('paused') is True}
        except (OSError, ValueError, TypeError):
            return {'session': '', 'paused': False}

    def save(self, state):
        recovery.save(self.state_path, state)

    def logs(self, invocation):
        if not recovery.INVOCATION.fullmatch(str(invocation)):
            return []
        try:
            return read_journal(invocation)
        except (OSError, ValueError, subprocess.SubprocessError):
            return []

    def bound_account(self, invocation, completion=None):
        if completion and completion.get('invocation') == invocation:
            identifier = completion.get('account_id', '')
            if accounts.account_id(identifier):
                return identifier
        try:
            running = account_state.read_json(self.running_path, 4096)
            if running.get('invocation') == invocation:
                return accounts.account_id(running.get('account_id', ''))
        except (OSError, ValueError, TypeError):
            pass
        return ''

    def endpoint_evidence(self, value, details, now):
        if details.get('endpoint_rate_limited'):
            stamp = details.get('endpoint_limited_at') or now
            value['global_until'] = max(value['global_until'], min(now, stamp) + recovery.DAY + 300)

    def finish(self, value, unit, now, deliberate=False):
        invocation = unit.get('invocation', '')
        if not recovery.INVOCATION.fullmatch(invocation) or invocation == value['processed']:
            return
        completion = recovery.read_completion(recovery.completion_path(self.persistent))
        details = None
        if completion and completion.get('corrupt'):
            value['corrupt'] = True
        elif completion and completion['invocation'] == invocation:
            details = completion.get('details')
        records = self.logs(invocation)
        details = recovery.merge_details(details, recovery.analyse(records)) if details is not None else recovery.analyse(records)
        self.endpoint_evidence(value, details, now)
        identifier = self.bound_account(invocation, completion) or value['active']
        if not identifier:
            # Legacy unscoped runs may have touched several accounts. Only explicit
            # throttle markers can assign their cooldown; never blame the current login.
            limited = account_state.attributed_accounts(records)
            for account in limited:
                record = account_state.ensure(value, account)
                recovery.finish(record, unit, now, records, deliberate=deliberate, details=details)
            if not limited and (details['limited_at'] or details['cause'] == 'rate_limit'):
                stamp = details['limited_at'] or now
                value['legacy_until'] = max(value['legacy_until'], min(now, stamp) + recovery.DAY + 300)
        else:
            record = account_state.ensure(value, identifier)
            recovery.finish(record, unit, now, records, deliberate=deliberate, details=details)
            if deliberate and not details['cause'] and not record['corrupt']:
                # An observed intentional stop completes the crash reservation.
                # The shared handoff gap and game settling gate govern the next pass.
                record.update(next_at=record['hard_until'], reason='rate_limit' if record['hard_until'] > now else '')
            if details['cause'] == 'state_store':
                value['attention'] = 'state_store'
        value['processed'] = invocation
        value['active'] = ''
        self.request_path.unlink(missing_ok=True)
        value['spacing_until'] = max(value['spacing_until'], now + HANDOFF)

    def request_pending(self, value, now):
        try:
            request = account_state.read_json(self.request_path, 4096)
            return (value['active'] and request.get('account_id') == value['active'] and
                    account_state.number(request.get('created_at')) and 0 <= now - request['created_at'] <= 30 and
                    recovery.INVOCATION.fullmatch(str(request.get('token', ''))) is not None)
        except (OSError, ValueError, TypeError):
            return False

    def stop_backfill(self, value, units, now):
        previous = units.get(BACKFILL, {})
        if previous.get('state') not in RUNNING and not value['active']:
            return units
        self.request_path.unlink(missing_ok=True)
        systemctl('stop', BACKFILL)
        units = read_units()
        stopped = dict(units.get(BACKFILL, {}))
        stopped['invocation'] = stopped.get('invocation') or previous.get('invocation', '')
        now = max(now, int(self.clock()))
        self.finish(value, stopped, now, deliberate=True)
        if value['active']:
            record = value['accounts'][value['active']]
            record['pending'] = True
            record['next_at'] = max(record['next_at'], record['hard_until'], now + recovery.GRACE)
            value['active'] = ''
            value['spacing_until'] = max(value['spacing_until'], now + HANDOFF)
        return units

    def result(self, state, value, metadata, units, session, now):
        active_progress = snapshot(units, self.directory)
        if not any(unit.get('state') in RUNNING for unit in units.values()):
            identifier = account_state.focus(value, metadata, now)
            active_progress = value['accounts'].get(identifier, {}).get('progress', empty_progress())
        return {'live': units.get(LIVE, {}).get('state', 'unavailable'),
                'backfill': units.get(BACKFILL, {}).get('state', 'unavailable'),
                'paused': state['paused'] and state['session'] == session,
                'memory': sum(unit.get('memory', 0) for unit in units.values()),
                'progress': active_progress,
                'recovery': account_state.view(value, metadata, units, session, now)}

    def execute(self, action, session):
        if session and not re.fullmatch(r'[a-zA-Z0-9:,_-]{1,256}', session):
            raise ValueError('Invalid game session')
        if action not in {'sync', 'tick', 'status', 'pause', 'resume', 'enable'}:
            raise ValueError('Unknown action')
        with (self.directory / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            now = int(self.clock())
            state, value, units = self.read(), account_state.read(self.persistent), read_units()
            old_state, old_value = dict(state), json.loads(json.dumps(value))
            metadata = accounts.discover()
            if action == 'status':
                return self.result(state, value, metadata, units, session, now)
            completion = recovery.read_completion(recovery.completion_path(self.persistent))
            if completion and completion.get('corrupt'):
                if not value['corrupt']:
                    value['enabled'] = True
                value['corrupt'] = True
            legacy = value.get('legacy')
            if legacy is not None:
                records = self.logs(legacy['processed'])
                self.endpoint_evidence(value, recovery.analyse(records), now)
                account_state.migrate(value, now, records)
            account_state.reconcile(value, metadata)
            if (session or state['session'] or value['enabled'] or action in {'enable', 'resume'} or
                    any(unit.get('state') in RUNNING for unit in units.values())):
                session = game_session() or session
            backfill = units.get(BACKFILL, {})
            if backfill.get('state') not in RUNNING:
                if completion and not completion.get('corrupt') and (not backfill.get('invocation') or completion['invocation'] == backfill.get('invocation')):
                    backfill = completion
                self.finish(value, backfill, now)
                if value['active'] and not self.request_pending(value, now):
                    # A previous worker vanished across crash/boot without a completion.
                    # Its last actual request time is unknown, so reserve a fresh handoff gap.
                    recovery.retry(value['accounts'][value['active']], now)
                    value['active'] = ''
                    value['spacing_until'] = max(value['spacing_until'], now + HANDOFF)
                    self.request_path.unlink(missing_ok=True)
            if action == 'pause':
                value['enabled'] = False
                state = {'session': session, 'paused': bool(session)}
                account_state.save(self.persistent, value)
                self.save(state)
                self.request_path.unlink(missing_ok=True)
                units = self.stop_backfill(value, units, now)
                systemctl('stop', LIVE)
                units = read_units()
                failed = [name for name in (LIVE, BACKFILL) if units.get(name, {}).get('state') == 'failed']
                if failed:
                    systemctl('reset-failed', *failed)
                    units = read_units()
            else:
                if action in {'enable', 'resume'}:
                    value['enabled'] = True
                    if value['attention'] == 'state_store' and not value['corrupt']:
                        value['attention'] = ''
                        for item in value['accounts'].values():
                            if item['reason'] == 'state_store' and not item['corrupt']:
                                item.update(reason='', pending=True, next_at=max(now, item['hard_until']))
                    current = value['accounts'].get(value['current'])
                    if current and current['reason'] in {'auth', 'unavailable'} and not current['corrupt']:
                        current.update(reason='', pending=True, next_at=max(now, current['hard_until']))
                    state['paused'] = False
                new_game = bool(session) and session != state['session']
                ended_game = not session and bool(state['session'])
                if new_game:
                    state = {'session': session, 'paused': False}
                    value['enabled'] = True
                    if value['current']:
                        account_state.ensure(value, value['current'])['pending'] = True
                if ended_game:
                    state = {'session': '', 'paused': False}
                    value['settling_until'] = max(value['settling_until'], now + recovery.GRACE)
                # Consume endpoint evidence from either mode before making decisions.
                for name in (LIVE, BACKFILL):
                    unit = units.get(name, {})
                    if unit.get('state') in RUNNING or (name == LIVE and unit.get('invocation') and unit.get('invocation') != value['live_processed']):
                        details = recovery.analyse(self.logs(unit.get('invocation', '')))
                        self.endpoint_evidence(value, details, now)
                        if name == LIVE and unit.get('state') not in RUNNING:
                            value['live_processed'] = unit.get('invocation', '')
                        if name == BACKFILL and details['cause'] == 'state_store':
                            value['attention'] = 'state_store'
                if state != old_state:
                    self.save(state)
                account_state.save(self.persistent, value) if value != old_value else None
                if value['global_until'] > now:
                    value['live_upload_wait'] = value['live_upload_wait'] or bool(session)
                    units = self.stop_backfill(value, units, now)
                    if units.get(LIVE, {}).get('state') in RUNNING:
                        systemctl('stop', LIVE)
                        units = read_units()
                elif session:
                    units = self.stop_backfill(value, units, now)
                    live = units.get(LIVE, {}).get('state')
                    if not state['paused'] and live not in RUNNING and (new_game or action in {'enable', 'resume'} or value['live_upload_wait']):
                        value['live_upload_wait'] = False
                        if live == 'failed':
                            systemctl('reset-failed', LIVE)
                        systemctl('start', LIVE)
                        units = read_units()
                else:
                    if units.get(LIVE, {}).get('state') in RUNNING:
                        systemctl('stop', LIVE)
                        units = read_units()
                    running = units.get(BACKFILL, {}).get('state') in RUNNING
                    current = value['current']
                    priority_ready = (current and current != value['active'] and account_state.eligible(
                        dict(value, spacing_until=0), current, now))
                    removed = value['active'] and value['active'] not in {item['id'] for item in metadata['accounts']} and not metadata['error']
                    if running and (priority_ready or removed or value['corrupt'] or value['attention'] or value['metadata_error']):
                        units = self.stop_backfill(value, units, now)
                    identifier = account_state.choose(value, metadata, now)
                    if identifier and not value['active'] and units.get(BACKFILL, {}).get('state') not in RUNNING:
                        emerging = game_session()
                        if emerging:
                            state = {'session': emerging, 'paused': False}
                            session = emerging
                            self.save(state)
                            systemctl('start', LIVE)
                            units = read_units()
                        else:
                            record = value['accounts'][identifier]
                            value['active'] = identifier
                            record['next_at'] = max(record['hard_until'], now + 300)
                            value['spacing_until'] = max(value['spacing_until'], now + HANDOFF)
                            account_state.save(self.persistent, value)
                            recovery.save(self.request_path, {'account_id': identifier, 'created_at': now,
                                                            'token': uuid.uuid4().hex})
                            try:
                                if units.get(BACKFILL, {}).get('state') == 'failed':
                                    systemctl('reset-failed', BACKFILL)
                                systemctl('start', '--no-block', BACKFILL)
                                record['reason'] = ''
                                units = read_units()
                            except RuntimeError:
                                self.request_path.unlink(missing_ok=True)
                                value['active'] = ''
                                recovery.retry(record, now)
            phase = account_state.view(value, metadata, units, session, now)['phase']
            notice = phase if phase in {'cooldown', 'attention', 'caught_up'} else ''
            key = notice + ':' + (value['processed'] or 'corrupt') if notice else ''
            announce = bool(key and key != value['notice'])
            if announce:
                value['notice'] = key
            if state != old_state:
                self.save(state)
            if value != old_value:
                account_state.save(self.persistent, value)
            if state != old_state or value != old_value:
                self.marker.touch(exist_ok=True)
            if announce:
                notify(notice)
            return self.result(state, value, metadata, units, session, now)


def take_request(controller, environment, now):
    """Consume one schedule request and bind its account to this unit invocation."""
    invocation = environment.get('INVOCATION_ID', '')
    if not recovery.INVOCATION.fullmatch(invocation):
        raise ValueError('A managed recovery invocation is required')
    with (controller.directory / 'control.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        request = account_state.read_json(controller.request_path, 4096)
        identifier = accounts.account_id(request.get('account_id', ''))
        token = request.get('token', '')
        created = request.get('created_at')
        value = account_state.read(controller.persistent)
        record = value['accounts'].get(identifier)
        if (not identifier or not isinstance(token, str) or not recovery.INVOCATION.fullmatch(token)
                or not account_state.number(created) or not 0 <= now - created <= 30
                or value['active'] != identifier or not value['enabled'] or value['corrupt'] or value['attention'] or record is None
                or record['corrupt'] or record['reason'] in recovery.BLOCKED
                or now < max(value['global_until'], value['legacy_until'], value['settling_until'], record['hard_until'])
                or game_session()):
            raise ValueError('Recovery request is no longer eligible')
        controller.request_path.unlink()
        recovery.save(controller.running_path, {'account_id': identifier, 'invocation': invocation, 'token': token})
        return identifier


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    session = sys.argv[2] if len(sys.argv) > 2 else ''
    runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    try:
        controller = Controller(runtime)
        if action == 'record-finished':
            invocation = os.environ.get('INVOCATION_ID', '')
            identifier = controller.bound_account(invocation)
            recovery.record_completion(recovery.completion_path(controller.persistent), os.environ,
                                       int(time.time()), account_id=identifier)
            controller.marker.touch(exist_ok=True)
            return 0
        if action == 'run-selected':
            executable = Path(session)
            expected = Path.home() / '.local/share/deadlock-api-ingest/deadlock-api-ingest'
            if executable != expected or not executable.is_file():
                raise ValueError('Unexpected recovery executable')
            identifier = take_request(controller, os.environ, int(time.time()))
            os.execv(str(executable), [str(executable), '--own-matches', '--account-id', identifier])
        print(json.dumps(controller.execute(action, session)))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)[:1024]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
