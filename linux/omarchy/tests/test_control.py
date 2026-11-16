import importlib.util
import json
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'plugin/backend'))
spec = importlib.util.spec_from_file_location('control', ROOT / 'plugin/backend/control.py')
control = importlib.util.module_from_spec(spec)
spec.loader.exec_module(control)


def record(message, second=1000):
    return {'MESSAGE': message, '__REALTIME_TIMESTAMP': str(second * 1000000)}


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.runtime = self.base / 'runtime'
        self.persistent = self.base / 'state/recovery.json'
        self.now = 10000
        self.serial = 0
        self.calls = []
        self.logs = {}
        self.fail_start = False
        self.states = {name: {'state': 'inactive', 'invocation': '', 'result': '', 'code': 0, 'exit_status': 0}
                       for name in (control.LIVE, control.BACKFILL)}
        self.stop_failures = set()
        for name, side_effect in [('systemctl', self.systemctl), ('game_session', lambda: ''),
                                  ('read_journal', lambda invocation: self.logs.get(invocation, [])),
                                  ('notify', lambda phase: None), ('snapshot', lambda *args: control.empty_progress())]:
            mocked = patch.object(control, name, side_effect=side_effect)
            mocked.start()
            self.addCleanup(mocked.stop)
        self.controller = self.new_controller()

    def new_controller(self, runtime=None):
        return control.Controller(runtime or self.runtime, persistent=self.persistent, clock=lambda: self.now)

    def systemctl(self, *args):
        self.calls.append(args)
        if args[0] == 'show':
            return '\n\n'.join(f"Id={unit}\nLoadState=loaded\nActiveState={s['state']}\nMemoryCurrent=10485760\n"
                               f"InvocationID={s['invocation']}\nResult={s['result']}\nExecMainCode={s['code']}\nExecMainStatus={s['exit_status']}"
                               for unit, s in self.states.items())
        if args[0] == 'start':
            if self.fail_start and control.BACKFILL in args:
                raise RuntimeError('simulated start failure')
            for name in args[1:]:
                if name.startswith('--'):
                    continue
                self.serial += 1
                self.states[name].update(state='activating' if name == control.BACKFILL else 'active',
                                         invocation=f'{self.serial:032x}', result='', code=0, exit_status=0)
        if args[0] == 'stop':
            for name in args[1:]:
                self.states[name].update(state='failed' if name in self.stop_failures else 'inactive',
                                         result='signal', code=2, exit_status=15)
        if args[0] == 'reset-failed':
            for name in args[1:]:
                if self.states[name]['state'] != 'failed':
                    raise RuntimeError('reset unloaded/nonfailed unit')
                self.states[name]['state'] = 'inactive'
        return ''

    def actions(self):
        return [call for call in self.calls if call[0] != 'show']

    def stopped(self, logs, status=0):
        unit = self.states[control.BACKFILL]
        unit.update(state='inactive' if status == 0 else 'failed', result='success' if status == 0 else 'exit-code',
                    code=1, exit_status=status)
        self.logs[unit['invocation']] = logs

    def saved(self):
        return control.recovery.read(self.persistent)

    def test_game_lifecycle_starts_once_and_stops_then_waits_grace(self):
        self.controller.execute('sync', '123')
        self.controller.execute('sync', '123')
        self.assertEqual(self.actions(), [('start', control.LIVE)])
        result = self.controller.execute('sync', '')
        self.assertEqual(result['live'], 'inactive')
        self.assertEqual(result['recovery']['phase'], 'retry')
        self.assertGreaterEqual(result['recovery']['next_at'], self.now + 60)
        self.now += 60
        result = self.controller.execute('tick', '')
        self.assertEqual(result['backfill'], 'activating')
        self.assertIn(('start', '--no-block', control.BACKFILL), self.actions())

    def test_pause_survives_reload_and_new_game_reenables(self):
        self.controller.execute('sync', '123')
        self.controller.execute('pause', '123')
        result = self.new_controller().execute('sync', '123')
        self.assertTrue(result['paused'])
        self.assertFalse(result['recovery']['enabled'])
        self.assertEqual(self.states[control.LIVE]['state'], 'inactive')
        self.assertEqual(self.actions().count(('start', control.LIVE)), 1)
        self.controller.execute('sync', '')
        result = self.controller.execute('sync', '456')
        self.assertTrue(result['recovery']['enabled'])
        self.assertEqual(result['live'], 'active')

    def test_enable_while_game_running_uses_live_only(self):
        with patch.object(control, 'game_session', return_value='333'):
            result = self.controller.execute('enable', '')
        self.assertEqual(result['live'], 'active')
        self.assertEqual(result['recovery']['phase'], 'game')
        self.assertFalse(any(control.BACKFILL in call for call in self.actions()))

    def test_process_guard_preempts_before_window_and_does_not_count_failure(self):
        self.controller.execute('enable', '')
        with patch.object(control, 'game_session', return_value='444'):
            result = self.controller.execute('tick', '')
        self.assertEqual(result['live'], 'active')
        actions = self.actions()
        self.assertLess(actions.index(('stop', control.BACKFILL)), actions.index(('start', control.LIVE)))
        self.assertEqual(self.saved()['failures'], 0)
        self.assertNotIn(self.saved()['reason'], {'transient', 'account_in_use'})

    def test_last_failed_rate_limit_is_consumed_before_initial_enable(self):
        invocation = 'a' * 32
        self.states[control.BACKFILL].update(state='failed', invocation=invocation,
                                           result='exit-code', code=1, exit_status=1)
        self.logs[invocation] = [record('Steam rate limit reached.', 1000)]
        result = self.controller.execute('enable', '')
        deadline = result['recovery']['next_at']
        self.assertEqual(result['recovery']['phase'], 'cooldown')
        self.assertTrue(87400 <= deadline <= 87700)
        self.assertFalse(any(call[0] == 'start' for call in self.actions()))
        self.controller.execute('pause', '')
        result = self.new_controller(self.base / 'after-relogin').execute('enable', '')
        self.assertEqual(result['recovery']['next_at'], deadline)
        self.assertFalse(any(call[0] == 'start' for call in self.actions()))
        self.now = deadline
        self.controller.execute('tick', '')
        self.assertIn(('start', '--no-block', control.BACKFILL), self.actions())

    def test_completed_pass_waits_until_next_game_even_with_skipped_history(self):
        self.controller.execute('enable', '')
        self.stopped([record('gc: account 1 skips 2 match(es) given up on after 3 attempts'),
                      record('Done, all own matches processed.')])
        result = self.controller.execute('tick', '')
        self.assertEqual(result['recovery']['phase'], 'caught_up')
        self.assertEqual(result['recovery']['skipped'], 2)
        self.now += 86400 * 7
        self.controller.execute('tick', '')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 1)
        self.controller.execute('sync', '123')
        self.controller.execute('sync', '')
        self.now += 60
        self.controller.execute('tick', '')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 2)

    def test_transient_failure_retries_with_backoff_without_tight_loop(self):
        self.controller.execute('enable', '')
        self.stopped([record('gc: account 1 failed: Steam GC unavailable: timeout')], 1)
        result = self.controller.execute('tick', '')
        self.assertTrue(self.now + 300 <= result['recovery']['next_at'] <= self.now + 360)
        self.controller.execute('tick', '')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 1)
        self.now = result['recovery']['next_at']
        self.controller.execute('tick', '')
        self.stopped([], 1)
        result = self.controller.execute('tick', '')
        self.assertGreaterEqual(result['recovery']['next_at'], self.now + 600)

    def test_auth_failure_waits_for_explicit_enable(self):
        self.controller.execute('enable', '')
        self.stopped([record('gc: no usable Steam account: remembered session unavailable')], 1)
        result = self.controller.execute('tick', '')
        self.assertEqual(result['recovery']['reason'], 'auth')
        self.assertEqual(result['recovery']['phase'], 'attention')
        self.now += 86400
        self.controller.execute('tick', '')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 1)
        self.controller.execute('enable', '')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 2)

    def test_explicit_bad_credentials_block_but_network_timeout_retries(self):
        for message, phase, reason in (
            ('gc: account 1 failed: Steam GC unavailable: CM login: InvalidPassword', 'attention', 'auth'),
            ('gc: account 1 failed: Steam GC unavailable: CM login timed out', 'retry', 'transient'),
        ):
            with self.subTest(message=message):
                value = control.recovery.default()
                value['enabled'] = True
                control.recovery.save(self.persistent, value)
                self.controller.execute('enable', '')
                self.stopped([record(message)], 1)
                result = self.controller.execute('tick', '')
                self.assertEqual((result['recovery']['phase'], result['recovery']['reason']), (phase, reason))

    def test_another_steam_game_defers_recovery(self):
        self.controller.execute('enable', '')
        self.stopped([record('gc: account 1 failed: the Steam account is playing a game')], 1)
        result = self.controller.execute('tick', '')
        self.assertEqual(result['recovery']['reason'], 'account_in_use')
        self.assertGreaterEqual(result['recovery']['next_at'], self.now + 900)

    def test_corrupt_schedule_fails_closed_even_after_enable(self):
        self.persistent.parent.mkdir(parents=True)
        self.persistent.write_text('invalid schedule')
        result = self.controller.execute('enable', '')
        self.assertEqual(result['recovery']['phase'], 'attention')
        self.assertTrue(self.saved()['corrupt'])
        self.controller.execute('enable', '')
        self.assertFalse(any(call[0] == 'start' for call in self.actions()))
        self.assertEqual(self.persistent.with_suffix('.invalid').read_text(), 'invalid schedule')
        self.controller.execute('sync', '123')
        self.assertIn(('start', control.LIVE), self.actions())

    def test_store_tracking_failure_stops_active_pass(self):
        self.controller.execute('enable', '')
        with patch.object(control, 'snapshot', return_value={'issue': 'state_store'}):
            result = self.controller.execute('tick', '')
        self.assertIn(('stop', control.BACKFILL), self.actions())
        self.assertEqual(result['recovery']['phase'], 'attention')
        self.assertEqual(result['recovery']['reason'], 'state_store')

    def test_start_failure_is_delayed(self):
        self.fail_start = True
        result = self.controller.execute('enable', '')
        self.assertGreaterEqual(result['recovery']['next_at'], self.now + 300)
        count = len(self.actions())
        self.controller.execute('tick', '')
        self.assertEqual(len(self.actions()), count)

    def test_status_is_readonly_and_disabled_tick_does_not_scan_processes(self):
        self.controller.execute('status', '')
        self.assertFalse(self.persistent.exists())
        self.assertFalse(self.controller.marker.exists())
        with patch.object(control, 'game_session', side_effect=AssertionError('idle process scan')):
            self.controller.execute('tick', '')
        self.assertEqual(self.actions(), [])

    def test_pause_resets_only_failed_units(self):
        self.controller.execute('enable', '')
        self.stop_failures.add(control.BACKFILL)
        self.controller.execute('pause', '')
        self.assertIn(('reset-failed', control.BACKFILL), self.actions())
        self.assertFalse(any(call[0] == 'reset-failed' and control.LIVE in call for call in self.actions()))
        self.assertFalse(self.saved()['enabled'])

    def test_completion_record_survives_unit_collection_and_runtime_recreation(self):
        self.controller.execute('enable', '')
        invocation = self.states[control.BACKFILL]['invocation']
        control.recovery.record_completion(control.recovery.completion_path(self.persistent),
            {'INVOCATION_ID': invocation, 'SERVICE_RESULT': 'success', 'EXIT_CODE': 'exited', 'EXIT_STATUS': '0'},
            self.now, reader=lambda _: [record('Done, all own matches processed.')])
        self.states[control.BACKFILL].update(state='inactive', invocation='', result='', code=0, exit_status=0)
        result = self.new_controller(self.base / 'new-runtime').execute('tick', '')
        self.assertEqual(result['recovery']['phase'], 'caught_up')
        self.assertEqual(self.actions().count(('start', '--no-block', control.BACKFILL)), 1)

    def test_late_journal_rate_limit_is_merged_with_completion_record(self):
        self.controller.execute('enable', '')
        invocation = self.states[control.BACKFILL]['invocation']
        control.recovery.record_completion(control.recovery.completion_path(self.persistent),
            {'INVOCATION_ID': invocation, 'SERVICE_RESULT': 'exit-code', 'EXIT_CODE': 'exited', 'EXIT_STATUS': '1'},
            self.now, reader=lambda _: [])
        self.stopped([record('Steam rate limit reached.', self.now)], 1)
        result = self.controller.execute('tick', '')
        self.assertEqual(result['recovery']['phase'], 'cooldown')
        self.assertGreaterEqual(result['recovery']['next_at'], self.now + 86400)

    def test_corrupt_completion_blocks_automatic_retry(self):
        self.persistent.parent.mkdir(parents=True)
        control.recovery.completion_path(self.persistent).write_text('{"details":"invalid"}')
        result = self.controller.execute('enable', '')
        self.assertEqual(result['recovery']['phase'], 'attention')
        self.assertTrue(result['recovery']['repair_required'])
        self.assertFalse(any(call[0] == 'start' for call in self.actions()))
        self.controller.execute('pause', '')
        result = self.controller.execute('tick', '')
        self.assertFalse(result['recovery']['enabled'])

    def test_unchanged_waiting_tick_does_not_rewrite_or_notify_again(self):
        self.states[control.BACKFILL].update(state='failed', invocation='d' * 32, result='exit-code', code=1, exit_status=1)
        self.logs['d' * 32] = [record('Steam rate limit reached.', self.now)]
        with patch.object(control, 'notify') as notify:
            self.controller.execute('enable', '')
            stamp = self.persistent.stat().st_mtime_ns
            self.controller.execute('tick', '')
            self.assertEqual(self.persistent.stat().st_mtime_ns, stamp)
            self.controller.execute('sync', '123')
            self.controller.execute('sync', '')
            self.assertEqual(notify.call_count, 1)

    def test_lock_free_completion_writer_does_not_wait_for_controller_lock(self):
        import fcntl
        with (self.controller.directory / 'control.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            self.assertTrue(control.recovery.record_completion(
                control.recovery.completion_path(self.persistent),
                {'INVOCATION_ID': 'b' * 32, 'SERVICE_RESULT': 'exit-code', 'EXIT_CODE': 'exited', 'EXIT_STATUS': '1'},
                self.now, reader=lambda _: [record('Steam rate limit reached.', self.now)]))
        self.assertEqual(control.recovery.read_completion(control.recovery.completion_path(self.persistent))['details']['cause'], 'rate_limit')

    def test_malformed_session_rejected_before_any_system_request(self):
        with self.assertRaises(ValueError):
            self.controller.execute('sync', '123; touch /tmp/unwanted')
        self.assertEqual(self.calls, [])


if __name__ == '__main__':
    unittest.main()
