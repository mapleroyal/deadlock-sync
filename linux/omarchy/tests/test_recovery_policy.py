"""Recovery policy checks with fake clocks, local fixtures and no service access."""
import copy
import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
BACKEND = Path(__file__).resolve().parents[1] / 'plugin/backend'
spec = importlib.util.spec_from_file_location('recovery_policy_tested', BACKEND / 'recovery.py')
policy = importlib.util.module_from_spec(spec)
sys.path.insert(0, str(BACKEND))
try:
    spec.loader.exec_module(policy)
finally:
    sys.path.pop(0)

NOW = 2_000_000_000
LIVE = 'deadlock-api-ingest.service'
BACKFILL = 'deadlock-api-ingest-backfill.service'


def unit(serial=1, success=False):
    return {'invocation': f'{serial:032x}', 'state': 'inactive',
            'result': 'success' if success else 'exit-code', 'code': 1,
            'exit_status': 0 if success else 1}


def record(message, stamp=NOW):
    return {'MESSAGE': message, '__REALTIME_TIMESTAMP': str(stamp * 1_000_000)}


class RecoveryPolicyTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='deadlock-policy-')
        self.addCleanup(temporary.cleanup)
        self.path = Path(temporary.name) / 'state/deadlock-api-ingest/recovery.json'
        self.value = policy.default()
        self.value['enabled'] = True
        # Policy-only tests must never inspect a real journal or spawn a process.
        process = patch.object(policy.subprocess, 'run', side_effect=AssertionError('real process'))
        process.start()
        self.addCleanup(process.stop)

    def test_missing_state_defaults_to_disabled_without_writes(self):
        value = policy.read(self.path)
        self.assertFalse(value['enabled'])
        self.assertEqual(value['hard_until'], 0)
        self.assertFalse(self.path.exists())
        self.assertEqual(policy.view(value, {}, '', NOW)['phase'], 'off')

    def test_round_trip_preserves_deadline_and_uses_private_permissions(self):
        self.value.update(hard_until=NOW + policy.DAY, next_at=NOW + policy.DAY, reason='rate_limit')
        policy.save(self.path, self.value)
        loaded = policy.read(self.path)
        self.assertEqual(loaded['hard_until'], NOW + policy.DAY)
        self.assertEqual(loaded['next_at'], NOW + policy.DAY)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.path.parent.stat().st_mode & 0o777, 0o700)
        self.assertFalse(self.path.with_suffix('.tmp').exists())

    def test_invalid_json_or_schema_fails_closed(self):
        self.path.parent.mkdir(parents=True)
        for text in ('{broken', '[]', '{"version":2}', 'x' * 16385):
            with self.subTest(text=text[:20]):
                self.path.write_text(text)
                value = policy.read(self.path)
                self.assertTrue(value['corrupt'])
                self.assertEqual(value['reason'], 'state_store')
                self.assertEqual(policy.view(value, {}, '', NOW)['phase'], 'attention')

    def test_invalid_persisted_deadline_fails_closed_instead_of_erasing_cooldown(self):
        self.path.parent.mkdir(parents=True)
        for key in ('hard_until', 'next_at'):
            for invalid in (-1, True, 'tomorrow', 4_102_444_801):
                with self.subTest(key=key, invalid=invalid):
                    saved = policy.default()
                    saved.update(enabled=True, reason='rate_limit', hard_until=NOW + policy.DAY)
                    saved[key] = invalid
                    self.path.write_text(json.dumps(saved))
                    value = policy.read(self.path)
                    self.assertTrue(value['corrupt'])
                    self.assertEqual(value['reason'], 'state_store')

    def test_invalid_original_is_backed_up_when_block_is_saved(self):
        self.path.parent.mkdir(parents=True)
        original = b'{broken scheduler state'
        self.path.write_bytes(original)
        value = policy.read(self.path)
        policy.save(self.path, value)
        self.assertEqual(self.path.with_suffix('.invalid').read_bytes(), original)
        self.assertEqual(self.path.with_suffix('.invalid').stat().st_mode & 0o777, 0o600)
        self.assertTrue(policy.read(self.path)['corrupt'])

    def test_saved_progress_retains_only_numeric_summary(self):
        self.value['progress'] = {'mode': 'backfill', 'phase': 'limited', 'completed': 35,
                                  'total': 320, 'uploaded': 35, 'updated_at': NOW,
                                  'message': '<img src=https://invalid>', 'account': 'private'}
        policy.save(self.path, self.value)
        loaded = policy.read(self.path)['progress']
        self.assertEqual(loaded['completed'], 35)
        self.assertNotIn('message', loaded)
        self.assertNotIn('account', loaded)

    def test_rate_limit_uses_observed_event_time_and_positive_jitter(self):
        event_time = NOW - 7200
        records = [record('Steam rate limit reached. Run this again on another day.', event_time)]
        policy.finish(self.value, unit(), NOW, records, jitter=lambda low, high: 37)
        self.assertEqual(self.value['hard_until'], event_time + policy.DAY + 37)
        self.assertEqual(self.value['next_at'], self.value['hard_until'])
        self.assertEqual(policy.view(self.value, {}, '', NOW)['phase'], 'cooldown')

    def test_future_log_clock_does_not_extend_cooldown_from_untrusted_future(self):
        records = [record('Steam rate limit reached.', NOW + policy.DAY * 4)]
        policy.finish(self.value, unit(), NOW, records, jitter=lambda low, high: 0)
        self.assertEqual(self.value['hard_until'], NOW + policy.DAY)

    def test_finished_invocation_is_consumed_once(self):
        records = [record('Steam rate limit reached.')]
        self.assertTrue(policy.finish(self.value, unit(), NOW, records, jitter=lambda low, high: 0))
        before = copy.deepcopy(self.value)
        self.assertFalse(policy.finish(self.value, unit(), NOW + 5000, records, jitter=lambda low, high: 300))
        self.assertEqual(self.value, before)

    def test_disable_reenable_and_round_trip_keep_rate_limit_deadline(self):
        policy.finish(self.value, unit(), NOW, [record('Steam rate limit reached.')], jitter=lambda low, high: 0)
        deadline = self.value['hard_until']
        self.value['enabled'] = False
        policy.save(self.path, self.value)
        loaded = policy.read(self.path)
        self.assertEqual(policy.view(loaded, {}, '', NOW + 100)['phase'], 'off')
        loaded['enabled'] = True
        self.assertEqual(loaded['hard_until'], deadline)
        self.assertEqual(policy.view(loaded, {}, '', NOW + 100)['phase'], 'cooldown')

    def test_storage_failure_keeps_any_confirmed_cooldown(self):
        records = [record('Steam rate limit reached.', NOW - 10),
                   record('gc: store unavailable, not tracking salt attempts: denied', NOW)]
        policy.finish(self.value, unit(), NOW, records, jitter=lambda low, high: 0)
        self.assertEqual(self.value['reason'], 'state_store')
        self.assertEqual(self.value['hard_until'], NOW - 10 + policy.DAY)
        self.assertEqual(policy.view(self.value, {}, '', NOW)['phase'], 'attention')

    def test_transient_retry_grows_to_cap_without_immediate_retry(self):
        for serial, delay in enumerate((300, 600, 1200, 2400, 3600, 3600), 1):
            now = NOW + serial * 100
            policy.finish(self.value, unit(serial), now, [], jitter=lambda low, high: 0)
            self.assertEqual(self.value['reason'], 'transient')
            self.assertEqual(self.value['next_at'], now + delay)
            self.assertTrue(self.value['pending'])

    def test_transient_retry_never_shortens_hard_cooldown(self):
        self.value['hard_until'] = NOW + policy.DAY
        policy.finish(self.value, unit(), NOW, [], jitter=lambda low, high: 60)
        self.assertEqual(self.value['next_at'], NOW + policy.DAY)

    def test_account_in_use_uses_longer_deferral(self):
        for serial, delay in enumerate((900, 1800, 3600, 3600), 1):
            policy.finish(self.value, unit(serial), NOW,
                          [record('the Steam account is playing a game')], jitter=lambda low, high: 0)
            self.assertEqual(self.value['reason'], 'account_in_use')
            self.assertEqual(self.value['next_at'], NOW + delay)

    def test_success_with_skipped_old_matches_allows_future_history(self):
        records = [record('gc: account 123 skips 3 match(es) given up on after 3 attempts'),
                   record('Done, all own matches processed.')]
        policy.finish(self.value, unit(success=True), NOW, records, jitter=lambda low, high: 0)
        self.assertFalse(self.value['pending'])
        self.assertEqual(self.value['reason'], '')
        self.assertEqual(self.value['skipped'], 3)
        self.assertEqual(policy.view(self.value, {}, '', NOW)['phase'], 'caught_up')
        self.value['pending'] = True  # A later game session creates new work.
        self.assertEqual(policy.view(self.value, {}, '', NOW + 3600)['phase'], 'retry')

    def test_deliberate_game_preemption_has_grace_not_failure_backoff(self):
        stopped = unit()
        stopped.update(result='signal', code=2, exit_status=15)
        policy.finish(self.value, stopped, NOW, [], deliberate=True, jitter=lambda low, high: 0)
        self.assertEqual(self.value['next_at'], NOW + policy.GRACE)
        self.assertEqual(self.value['failures'], 0)
        self.assertEqual(self.value['reason'], 'settling')

    def test_deliberate_stop_still_preserves_rate_limit_if_seen_before_stop(self):
        policy.finish(self.value, unit(), NOW, [record('Steam rate limit reached.')],
                      deliberate=True, jitter=lambda low, high: 0)
        self.assertEqual(self.value['reason'], 'rate_limit')
        self.assertEqual(self.value['hard_until'], NOW + policy.DAY)

    def test_caught_up_working_off_and_attention_have_no_scheduled_retry_date(self):
        self.value.update(hard_until=NOW - 50, next_at=NOW - 10, pending=False)
        self.assertIsNone(policy.view(self.value, {}, '', NOW)['next_at'])
        self.value['pending'] = True
        self.assertIsNone(policy.view(self.value, {BACKFILL: {'state': 'active'}}, '', NOW)['next_at'])
        self.value['enabled'] = False
        self.assertIsNone(policy.view(self.value, {}, '', NOW)['next_at'])
        self.value.update(enabled=True, reason='auth', hard_until=NOW + 5000)
        self.assertIsNone(policy.view(self.value, {}, '', NOW)['next_at'])

    def test_future_retry_is_visible_during_game_but_expired_deadline_is_not(self):
        self.value.update(next_at=NOW + 1000, pending=True)
        view = policy.view(self.value, {LIVE: {'state': 'active'}}, '123', NOW)
        self.assertEqual(view['phase'], 'game')
        self.assertEqual(view['next_at'], NOW + 1000)
        self.value['next_at'] = NOW - 10
        self.assertIsNone(policy.view(self.value, {}, '123', NOW)['next_at'])

    def test_later_completion_cannot_clear_existing_corruption_block(self):
        self.value.update(corrupt=True, reason='state_store')
        policy.finish(self.value, unit(success=True), NOW, [], jitter=lambda low, high: 0)
        self.assertEqual(self.value['reason'], 'state_store')
        view = policy.view(self.value, {}, '', NOW)
        self.assertEqual(view['phase'], 'attention')
        self.assertTrue(view['repair_required'])

    def completion_fixture(self, reader=None):
        path = policy.completion_path(self.path)
        environment = {'INVOCATION_ID': f'{7:032x}', 'SERVICE_RESULT': 'exit-code',
                       'EXIT_CODE': 'exited', 'EXIT_STATUS': '1'}
        if reader is None:
            reader = lambda invocation: [record('Steam rate limit reached.')]
        self.assertTrue(policy.record_completion(path, environment, NOW, reader=reader))
        return path

    def test_completion_record_is_private_and_captures_known_throttle(self):
        path = self.completion_fixture()
        value = policy.read_completion(path)
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(value['invocation'], f'{7:032x}')
        self.assertEqual(value['details']['cause'], 'rate_limit')
        self.assertEqual(value['details']['limited_at'], NOW)
        self.assertEqual(value['code'], 1)
        self.assertEqual(value['exit_status'], 1)

    def test_completion_missing_is_normal_but_invalid_details_fail_closed(self):
        self.assertIsNone(policy.read_completion(policy.completion_path(self.path)))
        path = self.completion_fixture()
        original = json.loads(path.read_text())
        for key, bad in (('limited_at', -1), ('limited_at', 'tomorrow'),
                         ('at', True), ('skipped', 10_000_001), ('cause', '<img>')):
            value = copy.deepcopy(original)
            value['details'][key] = bad
            path.write_text(json.dumps(value))
            self.assertEqual(policy.read_completion(path), {'corrupt': True})
        value = copy.deepcopy(original)
        value['finished_at'] = True
        path.write_text(json.dumps(value))
        self.assertEqual(policy.read_completion(path), {'corrupt': True})

    def test_completion_hook_keeps_exit_status_if_journal_is_unavailable(self):
        def no_journal(invocation):
            raise OSError('fixture journal unavailable')
        value = policy.read_completion(self.completion_fixture(no_journal))
        self.assertEqual(value['result'], 'exit-code')
        self.assertEqual(value['exit_status'], 1)
        self.assertEqual(value['details']['cause'], '')
        self.assertEqual(value['details']['limited_at'], 0)


if __name__ == '__main__':
    unittest.main()
