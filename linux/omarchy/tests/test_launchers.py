"""Launcher delegates recovery policy; no direct service starts or cooldown bypass."""
import contextlib
import io
import json
from pathlib import Path
import subprocess
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


class LauncherTests(unittest.TestCase):
    def setUp(self):
        self.launcher = types.ModuleType('deadlock_backfill')
        exec(compile((ROOT / 'scripts/deadlock-backfill').read_text(), 'deadlock-backfill', 'exec'), self.launcher.__dict__)

    def launch(self, recovery, live='inactive'):
        output = json.dumps({'recovery': recovery, 'live': live})
        with patch.object(self.launcher, 'notify') as notify, contextlib.redirect_stdout(io.StringIO()), \
                patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 0, output)) as run:
            result = self.launcher.main()
        return result, run.call_args, notify.call_args.args[0]

    def test_enable_uses_controller_without_forcing_a_service_start(self):
        result, call, message = self.launch({'enabled': True, 'phase': 'working'})
        self.assertEqual(result, 0)
        self.assertEqual(call.args[0][-1], 'enable')
        self.assertEqual(call.args[0][1], '-B')
        self.assertIn('backend/control.py', call.args[0][2])
        self.assertIn('top-bar gamepad', message)
        self.assertNotIn('terminal', message)

    def test_running_game_enables_queued_recovery_instead_of_refusing(self):
        result, _, message = self.launch({'enabled': True, 'phase': 'game'}, live='active')
        self.assertEqual(result, 0)
        self.assertIn('Live sync takes priority', message)

    def test_waiting_message_preserves_backend_deadline(self):
        for phase in ('cooldown', 'retry'):
            result, _, message = self.launch({'enabled': True, 'phase': phase, 'next_at': 1794854886})
            self.assertEqual(result, 0)
            self.assertIn('Next attempt:', message)
            self.assertNotIn('started', message)

    def test_attention_does_not_claim_work_started(self):
        _, _, message = self.launch({'enabled': True, 'phase': 'attention'})
        self.assertIn('needs attention', message)

    def test_backend_failure_is_reported_without_success_notification(self):
        with patch.object(subprocess, 'run', return_value=subprocess.CompletedProcess([], 1, '{}')), \
                patch.object(self.launcher, 'notify') as notify:
            with self.assertRaises(RuntimeError):
                self.launcher.main()
        notify.assert_not_called()


if __name__ == '__main__':
    unittest.main()
