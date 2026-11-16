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

sys.dont_write_bytecode = True
from progress import empty as empty_progress, read_journal, snapshot
import recovery

LIVE = 'deadlock-api-ingest.service'
BACKFILL = 'deadlock-api-ingest-backfill.service'
RUNNING = {'active', 'activating', 'deactivating'}


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

    def read(self):
        try:
            state = json.loads(self.state_path.read_text())
            return {'session': str(state.get('session', ''))[:256], 'paused': state.get('paused') is True}
        except (OSError, ValueError, TypeError):
            return {'session': '', 'paused': False}

    def save(self, state):
        temporary = self.state_path.with_suffix('.tmp')
        temporary.write_text(json.dumps(state))
        temporary.chmod(0o600)
        temporary.replace(self.state_path)

    def finish(self, value, unit, now, deliberate=False):
        invocation = unit.get('invocation', '')
        if not recovery.INVOCATION.fullmatch(invocation) or invocation == value['processed']:
            return
        completion = recovery.read_completion(recovery.completion_path(self.persistent))
        details = None
        if completion and completion.get('corrupt'):
            value.update(corrupt=True, reason='state_store', pending=True)
        elif completion and completion['invocation'] == invocation:
            details = completion.get('details')
        try:
            records = read_journal(invocation)
        except (OSError, ValueError, subprocess.SubprocessError):
            records = []
        if details is not None:
            details = recovery.merge_details(details, recovery.analyse(records))
        recovery.finish(value, unit, now, records, deliberate=deliberate, details=details)

    def stop_backfill(self, value, units, now):
        previous = units.get(BACKFILL, {})
        if previous.get('state') not in RUNNING:
            return units
        systemctl('stop', BACKFILL)
        units = read_units()
        stopped = dict(units.get(BACKFILL, {}))
        stopped['invocation'] = stopped.get('invocation') or previous.get('invocation', '')
        self.finish(value, stopped, now, deliberate=True)
        return units

    def result(self, state, value, units, session, now):
        active_progress = snapshot(units, self.directory)
        if not any(unit.get('state') in RUNNING for unit in units.values()):
            active_progress = value['progress']
        return {'live': units.get(LIVE, {}).get('state', 'unavailable'),
                'backfill': units.get(BACKFILL, {}).get('state', 'unavailable'),
                'paused': state['paused'] and state['session'] == session,
                'memory': sum(unit.get('memory', 0) for unit in units.values()),
                'progress': active_progress,
                'recovery': recovery.view(value, units, session, now)}

    def execute(self, action, session):
        if session and not re.fullmatch(r'[a-zA-Z0-9:,_-]{1,256}', session):
            raise ValueError('Invalid game session')
        if action not in {'sync', 'tick', 'status', 'pause', 'resume', 'enable'}:
            raise ValueError('Unknown action')
        with (self.directory / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            now = int(self.clock())
            state, value, units = self.read(), recovery.read(self.persistent), read_units()
            old_state, old_value = dict(state), json.loads(json.dumps(value))
            completion = recovery.read_completion(recovery.completion_path(self.persistent))
            if completion and completion.get('corrupt'):
                if not value['corrupt']:
                    value['enabled'] = True
                value.update(corrupt=True, reason='state_store', pending=True)
            if action == 'status':
                return self.result(state, value, units, session, now)
            # Idle disabled mode has no recurring process scan. Active mode checks
            # the actual game as well as its window, including CLI enable calls.
            if (session or state['session'] or value['enabled'] or action in {'enable', 'resume'} or
                    any(unit.get('state') in RUNNING for unit in units.values())):
                session = game_session() or session
            backfill = units.get(BACKFILL, {})
            if backfill.get('state') not in RUNNING:
                completion = recovery.read_completion(recovery.completion_path(self.persistent))
                if completion and not completion.get('corrupt') and (not backfill.get('invocation') or completion['invocation'] == backfill.get('invocation')):
                    backfill = completion
                self.finish(value, backfill, now)
            if action == 'pause':
                value['enabled'] = False
                state = {'session': session, 'paused': bool(session)}
                # Save disabled intent before blocking stop commands.
                recovery.save(self.persistent, value)
                self.save(state)
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
                    if value['reason'] in {'auth', 'state_store'} and not value['corrupt']:
                        value.update(reason='', pending=True, next_at=max(now, value['hard_until']))
                    state['paused'] = False
                new_game = bool(session) and session != state['session']
                ended_game = not session and bool(state['session'])
                if new_game:
                    state = {'session': session, 'paused': False}
                    value.update(enabled=True, pending=True)
                if ended_game:
                    state = {'session': '', 'paused': False}
                    if value['enabled'] and value['reason'] not in recovery.BLOCKED:
                        value['next_at'] = max(value['next_at'], value['hard_until'], now + recovery.GRACE)
                        if value['hard_until'] <= now:
                            value['reason'] = 'settling'
                # Persist intent/cooldown before any service start.
                if value != old_value:
                    recovery.save(self.persistent, value)
                if state != old_state:
                    self.save(state)
                if session:
                    units = self.stop_backfill(value, units, now)
                    live = units.get(LIVE, {}).get('state')
                    if not state['paused'] and live not in RUNNING and (new_game or action in {'enable', 'resume'}):
                        if live == 'failed':
                            systemctl('reset-failed', LIVE)
                        systemctl('start', LIVE)
                        units = read_units()
                else:
                    if units.get(LIVE, {}).get('state') in RUNNING:
                        systemctl('stop', LIVE)
                        units = read_units()
                    current = snapshot(units, self.directory)
                    if current.get('issue') == 'state_store' and units.get(BACKFILL, {}).get('state') in RUNNING:
                        units = self.stop_backfill(value, units, now)
                        value.update(reason='state_store', pending=True, next_at=0)
                    eligible = (value['enabled'] and value['pending'] and value['reason'] not in recovery.BLOCKED
                                and now >= max(value['next_at'], value['hard_until'])
                                and units.get(BACKFILL, {}).get('state') not in RUNNING)
                    if eligible:
                        # Close the scan/start gap as far as possible without
                        # changing Steam launch options or starting the game.
                        emerging = game_session()
                        if emerging:
                            state = {'session': emerging, 'paused': False}
                            session = emerging
                            self.save(state)
                            systemctl('start', LIVE)
                            units = read_units()
                        else:
                            try:
                                # Reserve a bounded retry window before launch.
                                # If a start/hook vanishes, never hot-loop starts.
                                value['next_at'] = max(value['hard_until'], now + 300)
                                recovery.save(self.persistent, value)
                                if units.get(BACKFILL, {}).get('state') == 'failed':
                                    systemctl('reset-failed', BACKFILL)
                                systemctl('start', '--no-block', BACKFILL)
                                value['reason'] = ''
                                units = read_units()
                            except RuntimeError:
                                recovery.retry(value, now)
            phase = recovery.view(value, units, session, now)['phase']
            notice = phase if phase in {'cooldown', 'attention', 'caught_up'} else ''
            key = notice + ':' + (value['processed'] or 'corrupt') if notice else ''
            announce = bool(key and key != value['notice'])
            if announce:
                value['notice'] = key
            if state != old_state:
                self.save(state)
            if value != old_value:
                recovery.save(self.persistent, value)
            if state != old_state or value != old_value:
                self.marker.touch(exist_ok=True)
            if announce:
                notify(notice)
            return self.result(state, value, units, session, now)


def main():
    action = sys.argv[1] if len(sys.argv) > 1 else 'status'
    session = sys.argv[2] if len(sys.argv) > 2 else ''
    runtime = os.environ.get('XDG_RUNTIME_DIR', f'/run/user/{os.getuid()}')
    try:
        if action == 'record-finished':
            recovery.record_completion(recovery.completion_path(recovery.state_path()), os.environ, int(time.time()))
            Path(runtime, 'deadlock-api-ingest-bar.status').touch(exist_ok=True)
            return 0
        print(json.dumps(Controller(runtime).execute(action, session)))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({'error': str(exc)[:1024]}))
        return 1


if __name__ == '__main__':
    sys.exit(main())
