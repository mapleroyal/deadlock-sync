"""Portable rendering and installation checks, confined to temporary homes."""
import contextlib
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('deadlock_sync_install', ROOT / 'install.py')
install = importlib.util.module_from_spec(spec)
spec.loader.exec_module(install)


class InstallTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='deadlock-install-test-')
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.home = self.base / 'different user % $'
        self.home.mkdir()
        self.config = {'bar': {'layout': {'left': [{'id': 'omarchy.workspaces'}], 'center': [],
                                        'right': [{'id': 'custom.tray', 'settings': {'keep': True}}, {'id': 'omarchy.clock'}]}},
                       'disabledPlugins': ['keep.disabled', install.PLUGIN_ID], 'other': {'preserve': 42}}
        self.shell = self.home / '.config/omarchy/shell.json'
        self.shell.parent.mkdir(parents=True)
        self.shell.write_text(json.dumps(self.config))

    def test_merge_preserves_custom_tray_unrelated_settings_and_existing_position(self):
        original = json.dumps(self.config)
        merged = install.merge_shell(self.config)
        self.assertEqual(json.dumps(self.config), original)
        self.assertEqual([x['id'] for x in merged['bar']['layout']['right']],
                         ['custom.tray', install.PLUGIN_ID, 'omarchy.clock'])
        self.assertEqual(merged['other'], self.config['other'])
        self.assertEqual(merged['bar']['layout']['right'][0], self.config['bar']['layout']['right'][0])
        self.assertEqual(merged['disabledPlugins'], ['keep.disabled'])
        merged['bar']['layout']['left'].append(merged['bar']['layout']['right'].pop(1))
        self.assertEqual(install.merge_shell(merged), merged)

    def test_render_has_portable_paths_static_units_and_only_runtime_plugin_files(self):
        files = install.user_files(self.home, self.config)
        self.assertEqual({str(p.relative_to(install.PLUGIN_DEST)) for p in files if p.is_relative_to(install.PLUGIN_DEST)},
                         set(install.PLUGIN_FILES))
        live = files[Path('.config/systemd/user') / install.UNITS[0]][0].decode()
        backfill = files[Path('.config/systemd/user') / install.UNITS[1]][0].decode()
        self.assertIn('different user %% $$', live)
        self.assertIn(' --no-gc\n', live)
        self.assertNotIn('--own-matches', live)
        self.assertIn(' --own-matches\n', backfill)
        self.assertNotIn('--no-gc', backfill)
        self.assertNotIn('backfill-finished', backfill)
        for text in (live, backfill):
            self.assertNotIn('[Install]', text)
            self.assertNotIn('/home/user1/', text)
            for option in ('NoNewPrivileges=', 'PrivateTmp=', 'PrivateUsers='):
                self.assertNotIn(option, text)
        desktop = files[Path('.local/share/applications/deadlock-backfill.desktop')][0].decode()
        self.assertIn('different user %% \\\\$', desktop)
        self.assertEqual(files[Path('.local/bin/deadlock-backfill')][1], 0o755)

    def test_preview_does_not_write_download_or_run_processes(self):
        before = self.shell.read_bytes()
        with patch.object(install, 'verified_asset', side_effect=AssertionError('download')), \
                patch.object(install.subprocess, 'run', side_effect=AssertionError('process')), \
                contextlib.redirect_stdout(io.StringIO()) as output:
            self.assertEqual(install.main(['--home', str(self.home)]), 0)
        self.assertIn('Preview:', output.getvalue())
        self.assertEqual(self.shell.read_bytes(), before)
        self.assertFalse((self.home / '.local').exists())

    def test_stage_is_isolated_and_refuses_existing_directory(self):
        output = self.base / 'staged'
        files = install.user_files(self.home, self.config)
        install.stage(output, files)
        self.assertEqual((output / 'home/.local/bin/deadlock-backfill').stat().st_mode & 0o777, 0o755)
        self.assertFalse((self.home / '.local').exists())
        with self.assertRaises(ValueError):
            install.stage(output, files)

    def test_apply_backs_up_changes_preserves_state_and_is_idempotent(self):
        old = self.shell.read_bytes()
        private = self.home / '.local/share/deadlock-api-ingest/privateSteam'
        private.parent.mkdir(parents=True)
        private.write_text('preserve existing local state')
        obsolete = self.home / install.APP_DEST / 'backfill-finished'
        obsolete.write_text('old one-shot notification')
        files = install.user_files(self.home, self.config)
        files[install.APP_DEST / 'deadlock-api-ingest'] = (b'test binary', 0o755)
        backup = install.apply_files(self.home, files)
        self.assertEqual((backup / '.config/omarchy/shell.json').read_bytes(), old)
        self.assertEqual(private.read_text(), 'preserve existing local state')
        self.assertFalse(obsolete.exists())
        self.assertEqual((backup / install.APP_DEST / 'backfill-finished').read_text(), 'old one-shot notification')
        link = self.home / '.local/bin/deadlock-api-ingest'
        self.assertEqual(link.read_bytes(), b'test binary')
        stamp = self.shell.stat().st_mtime_ns
        no_changes = install.apply_files(self.home, files)
        self.assertFalse(no_changes.exists())
        self.assertEqual(self.shell.stat().st_mtime_ns, stamp)

    def test_unsafe_later_target_is_rejected_before_any_write(self):
        safe = Path('.local/bin/deadlock-backfill')
        unsafe = Path('.config/unsafe')
        (self.home / unsafe).symlink_to(self.base / 'outside')
        with self.assertRaises(ValueError):
            install.apply_files(self.home, {safe: (b'new', 0o755), unsafe: (b'bad', 0o644)})
        self.assertFalse((self.home / safe).exists())
        self.assertFalse((self.base / 'outside').exists())

    def test_parent_escape_is_rejected(self):
        outside = self.base / 'outside'
        outside.mkdir()
        (self.home / '.local').symlink_to(outside, target_is_directory=True)
        with self.assertRaises(ValueError):
            install.apply_files(self.home, {Path('.local/bin/deadlock-backfill'): (b'bad', 0o755)})
        self.assertEqual(list(outside.iterdir()), [])

    def test_asset_size_and_hash_verification(self):
        asset = {'name': 'asset', 'size': 4, 'sha256': hashlib.sha256(b'good').hexdigest()}
        (self.base / 'asset').write_bytes(b'good')
        self.assertEqual(install.verified_asset(asset, self.base), b'good')
        for bad in (b'evil', b'good extra'):
            (self.base / 'asset').write_bytes(bad)
            with self.assertRaises(ValueError):
                install.verified_asset(asset, self.base)

    def test_preflight_accepts_fresh_units_and_rejects_active_or_masked_units(self):
        shell = self.base / 'omarchy/shell'
        for name in ('Commons/ShellIpc.qml', 'Ui/KeyboardPanel.qml', 'Ui/BarIconButton.qml'):
            path = shell / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.touch()
        with patch.object(install.os, 'geteuid', return_value=1000), \
                patch.object(install.Path, 'home', return_value=self.home), \
                patch.object(install.platform, 'machine', return_value='x86_64'), \
                patch.object(install.shutil, 'which', return_value='/usr/bin/command'), \
                patch.dict(install.os.environ, {'OMARCHY_PATH': str(shell.parent)}), \
                patch.object(install.subprocess, 'run') as run:
            run.return_value = subprocess.CompletedProcess([], 1, 'LoadState=not-found\nActiveState=inactive\n', '')
            install.preflight(self.home)
            for state in ('LoadState=loaded\nActiveState=active\n', 'LoadState=masked\nActiveState=inactive\n'):
                run.return_value = subprocess.CompletedProcess([], 0, state, '')
                with self.assertRaises(ValueError):
                    install.preflight(self.home)


if __name__ == '__main__':
    unittest.main()
