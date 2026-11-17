"""Synthetic Steam metadata and account schedule checks; no credentials or network."""
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'plugin/backend'))
import accounts
import account_state
import recovery


def vdf(current=1, names=None):
    names = names or {1: 'First', 2: 'Second'}
    return '"users" { ' + ' '.join('"' + str(accounts.STEAM_BASE + identifier) + '" { "PersonaName" "' + name +
        '" "MostRecent" "' + ('1' if identifier == current else '0') + '" }' for identifier, name in names.items()) + ' }'


class AccountMetadataTests(unittest.TestCase):
    def test_current_identity_and_all_saved_records_without_login_flag_assumptions(self):
        metadata = accounts.decode(vdf(2))
        self.assertEqual(metadata['current'], '2')
        self.assertEqual([item['id'] for item in metadata['accounts']], ['1', '2'])
        self.assertEqual(metadata['accounts'][0]['name'], 'First')
        self.assertNotIn('refresh_token', repr(metadata))

    def test_unique_auto_login_fallback_and_most_recent_precedence(self):
        text = vdf(2).replace('"MostRecent"', '"AutoLogin"')
        self.assertEqual(accounts.decode(text)['current'], '2')
        text = text.replace('"AutoLogin" "0"', '"AutoLogin" "0" "MostRecent" "1"')
        self.assertEqual(accounts.decode(text)['current'], '1')
        text = vdf(2).replace('"MostRecent"', '"AllowAutoLogin"')
        self.assertEqual(accounts.decode(text)['current'], '')

    def test_names_are_bounded_plain_strings_and_invalid_ids_do_not_become_accounts(self):
        name = '<img src=https://invalid>' + 'x' * 100
        metadata = accounts.decode(vdf(1, {1: name}))
        self.assertEqual(len(metadata['accounts'][0]['name']), 80)
        self.assertTrue(metadata['accounts'][0]['name'].startswith('<img'))
        self.assertEqual(accounts.decode('"users" { "0" { "MostRecent" "1" } }')['accounts'], [])
        for invalid in ('0', '-1', '4294967296', '1; rm', '01', True):
            self.assertFalse(accounts.account_id(invalid))

    def test_malformed_ambiguous_or_oversized_metadata_is_rejected(self):
        for text in ('"users" {', '"users" {} "users" {}', '"users" { nope }',
                     vdf().replace('"MostRecent" "0"', '"MostRecent" "1"'), 'x' * (accounts.MAX_BYTES + 1)):
            with self.subTest(text=text[:40]), self.assertRaises(ValueError):
                accounts.decode(text)

    def test_primary_install_uses_latest_metadata_and_reads_no_token_file(self):
        with tempfile.TemporaryDirectory() as temporary:
            home = Path(temporary)
            paths = [home / '.local/share/Steam', home / '.var/app/com.valvesoftware.Steam/.local/share/Steam']
            for index, root in enumerate(paths):
                (root / 'steamapps').mkdir(parents=True)
                (root / 'config').mkdir()
                path = root / 'config/loginusers.vdf'
                path.write_text(vdf(index + 1))
                os.utime(path, (100 + index, 100 + index))
                (root / 'config/local.vdf').write_bytes(b'NOT TO BE READ')
            with patch.dict(os.environ, {'XDG_DATA_HOME': str(home / '.local/share'), 'SNAP_USER_DATA': str(home / 'snap')}):
                metadata = accounts.discover(home)
            self.assertEqual(metadata['current'], '2')


class AccountPolicyTests(unittest.TestCase):
    def test_current_first_then_others_while_current_is_rate_limited(self):
        value = account_state.default(); value['enabled'] = True
        metadata = accounts.decode(vdf(2))
        account_state.reconcile(value, metadata)
        self.assertEqual(account_state.choose(value, metadata, 100), '2')
        value['accounts']['2'].update(hard_until=1000, next_at=1000, reason='rate_limit')
        self.assertEqual(account_state.choose(value, metadata, 100), '1')
        self.assertEqual(account_state.choose(value, metadata, 1000), '2')

    def test_switch_marks_new_work_and_allows_one_auth_retry_without_erasing_cooldown(self):
        value = account_state.default(); value['enabled'] = True
        account_state.reconcile(value, accounts.decode(vdf(1)))
        target = value['accounts']['2']
        target.update(reason='auth', next_at=0, hard_until=1000, pending=False)
        account_state.reconcile(value, accounts.decode(vdf(2)))
        self.assertEqual(target['reason'], '')
        self.assertTrue(target['pending'])
        self.assertEqual(target['hard_until'], 1000)
        target['reason'] = 'auth'
        self.assertFalse(account_state.reconcile(value, accounts.decode(vdf(2))))
        self.assertEqual(target['reason'], 'auth')

    def test_legacy_cooldown_is_attributed_only_to_explicit_original_account(self):
        value = account_state.default()
        value['legacy'] = recovery.default()
        value['legacy'].update(enabled=True, hard_until=1000, next_at=1000, reason='rate_limit', processed='a' * 32)
        account_state.reconcile(value, accounts.decode(vdf(2)))
        account_state.migrate(value, 100, [{'MESSAGE': 'gc: account 1 was rate-limited by Steam'}])
        self.assertEqual(value['accounts']['1']['hard_until'], 1000)
        self.assertEqual(value['accounts']['2']['hard_until'], 0)
        self.assertEqual(value['legacy_until'], 0)

    def test_ambiguous_legacy_cooldown_retained_without_blame_on_current_account(self):
        value = account_state.default(); value['enabled'] = True
        value['legacy'] = recovery.default()
        value['legacy'].update(hard_until=1000, next_at=1000, reason='rate_limit')
        metadata = accounts.decode(vdf(2))
        account_state.reconcile(value, metadata)
        account_state.migrate(value, 100, [{'MESSAGE': 'Steam rate limit reached.'}])
        self.assertEqual(value['legacy_until'], 1000)
        self.assertEqual(value['accounts']['2']['hard_until'], 0)
        self.assertEqual(account_state.choose(value, metadata, 999), '')
        self.assertEqual(account_state.choose(value, metadata, 1000), '2')

    def test_roundtrip_and_corrupt_account_deadline_fail_closed(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'recovery.json'
            value = account_state.default()
            account_state.reconcile(value, accounts.decode(vdf()))
            value['accounts']['1']['hard_until'] = 2000
            account_state.save(path, value)
            self.assertEqual(account_state.read(path)['accounts']['1']['hard_until'], 2000)
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            value['accounts']['1']['hard_until'] = 'tomorrow'
            path.write_text(json.dumps(value))
            self.assertTrue(account_state.read(path)['corrupt'])

    def test_first_v2_save_keeps_private_original_v1_backup(self):
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'recovery.json'
            legacy = recovery.default()
            legacy.update(hard_until=2000, next_at=2000, reason='rate_limit')
            recovery.save(path, legacy)
            original = path.read_bytes()
            value = account_state.read(path)
            account_state.migrate(value, 100, [])
            account_state.save(path, value)
            backup = path.with_name('recovery.v1.json')
            self.assertEqual(backup.read_bytes(), original)
            self.assertEqual(backup.stat().st_mode & 0o777, 0o600)
            account_state.save(path, value)
            self.assertEqual(backup.read_bytes(), original)

    def test_waiting_for_another_login_requires_a_known_caught_up_current_account(self):
        value = account_state.default(); value['enabled'] = True
        metadata = accounts.decode(vdf(1, {1: 'First', 2: 'Second', 3: 'Third'}))
        account_state.reconcile(value, metadata)
        value['accounts']['1']['pending'] = False
        value['accounts']['2']['reason'] = value['accounts']['3']['reason'] = 'auth'
        def view(session=''):
            return account_state.view(value, metadata, {}, session, 100)

        self.assertEqual(view()['phase'], 'waiting_for_account')
        self.assertIsNone(view()['next_at'])
        self.assertEqual([(row['name'], row['phase']) for row in view()['accounts']],
                         [('First', 'caught_up'), ('Second', 'choose_account'), ('Third', 'choose_account')])
        self.assertEqual(view('game')['phase'], 'game')
        value['accounts']['3']['reason'] = 'unavailable'
        self.assertEqual((view()['phase'], view()['reason']), ('attention', 'unavailable'))
        value['accounts']['3']['reason'] = 'auth'
        value['attention'] = 'state_store'
        self.assertEqual((view()['phase'], view()['reason']), ('attention', 'state_store'))
        value['attention'] = ''
        value['accounts']['1'].update(pending=True, reason='auth')
        self.assertEqual((view()['phase'], view()['reason'], view()['accounts'][0]['phase']), ('attention', 'auth', 'auth'))
        metadata['current'] = ''
        account_state.reconcile(value, metadata)
        self.assertEqual((view()['phase'], view()['reason'], view()['current_account']), ('attention', 'choose_account', ''))

    def test_global_endpoint_blocks_all_accounts_and_displays_during_game(self):
        value = account_state.default(); value['enabled'] = True
        metadata = accounts.decode(vdf())
        account_state.reconcile(value, metadata)
        value['global_until'] = 1000
        self.assertEqual(account_state.choose(value, metadata, 100), '')
        view = account_state.view(value, metadata, {}, 'game', 100)
        self.assertEqual((view['phase'], view['reason']), ('cooldown', 'upload_rate_limit'))


if __name__ == '__main__':
    unittest.main()
