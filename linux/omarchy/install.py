#!/usr/bin/env python3
"""Preview, stage or install game-lifetime Deadlock match sync on Omarchy.

Preview is the default and performs no writes/downloads. --stage renders user
files into a new directory without downloading, starting services or changing the
desktop. --apply installs for the invoking desktop user; a new memory helper
requires --memory-helper and a one-time system authentication prompt. Backfill
never starts during installation. A fresh shell engine is required afterwards.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import tempfile
import urllib.request
import uuid

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent
PLUGIN_ID = 'user1.deadlock-ingest'
PLUGIN_DEST = Path('.config/omarchy/plugins') / PLUGIN_ID
PLUGIN_FILES = ('backend/progress.py', 'backend/recovery.py', 'backend/control.py',
                'Model.js', 'i18n/en.json', 'Service.qml', 'BarWidget.qml', 'manifest.json')
APP_DEST = Path('.local/share/deadlock-api-ingest')
OBSOLETE_FILES = (APP_DEST / 'backfill-finished',)
HELPER = Path('/usr/local/libexec/deadlock-api-ingest/deadlock-api-ingest-memgrant')
UNITS = ('deadlock-api-ingest.service', 'deadlock-api-ingest-backfill.service')


def systemd_quote(value):
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise ValueError('Control characters in target path')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%').replace('$', '$$') + '"'


def desktop_quote(value):
    value = str(value)
    if any(ord(c) < 32 for c in value):
        raise ValueError('Control characters in target path')
    value = value.replace('%', '%%')
    for character in ('\\', '"', '`', '$'):
        value = value.replace(character, '\\' + character)
    return '"' + value.replace('\\', '\\\\') + '"'


def merge_shell(config):
    """Keep existing placement; add once after any existing tray clone otherwise."""
    result = json.loads(json.dumps(config))
    try:
        layout = result['bar']['layout']
        right = layout['right']
    except (KeyError, TypeError):
        raise ValueError('Initialize the target Omarchy shell layout before installing')
    if not isinstance(layout, dict) or not isinstance(right, list):
        raise ValueError('Invalid target shell layout')
    found = any(isinstance(entry, dict) and entry.get('id') == PLUGIN_ID
                for section in layout.values() if isinstance(section, list) for entry in section)
    if not found:
        index = next((i + 1 for i, entry in enumerate(right)
                      if isinstance(entry, dict) and str(entry.get('id', '')).endswith('.tray')), 0)
        right.insert(index, {'id': PLUGIN_ID})
    disabled = result.get('disabledPlugins', [])
    if not isinstance(disabled, list):
        raise ValueError('Invalid disabledPlugins setting')
    if PLUGIN_ID in disabled:
        result['disabledPlugins'] = [item for item in disabled if item != PLUGIN_ID]
    return result


def user_files(home, config):
    home = Path(home)
    if not home.is_absolute():
        raise ValueError('Target home must be absolute')
    files = {}
    for relative in PLUGIN_FILES:
        source = ROOT / 'plugin' / relative
        if source.is_symlink():
            raise ValueError('Payload symlinks are not supported')
        files[PLUGIN_DEST / relative] = (source.read_bytes(), 0o644)
    files[Path('.local/bin/deadlock-backfill')] = ((ROOT / 'scripts/deadlock-backfill').read_bytes(), 0o755)
    replacements = {
        '@INGEST_EXEC@': systemd_quote(home / APP_DEST / 'deadlock-api-ingest'),
        '@CONTROL_EXEC@': systemd_quote(home / PLUGIN_DEST / 'backend/control.py'),
        '@BACKFILL_EXEC@': desktop_quote(home / '.local/bin/deadlock-backfill'),
    }
    for name in (*UNITS, 'deadlock-backfill.desktop'):
        text = (ROOT / 'templates' / name).read_text()
        for key, value in replacements.items():
            text = text.replace(key, value)
        relative = (Path('.config/systemd/user') if name.endswith('.service')
                    else Path('.local/share/applications')) / name
        files[relative] = (text.encode(), 0o644)
    files[Path('.config/omarchy/shell.json')] = ((json.dumps(merge_shell(config), indent=2) + '\n').encode(), 0o644)
    return files


def stage(output, files):
    output = Path(output)
    if output.exists():
        raise ValueError('Use a new, nonexistent staging directory')
    for relative, (data, mode) in files.items():
        path = output / 'home' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(mode)


def verified_asset(asset, directory=None):
    """Download or read an exact release asset; reject size or digest mismatch."""
    if directory:
        data = (Path(directory) / asset['name']).read_bytes()
    else:
        request = urllib.request.Request(asset['url'], headers={'User-Agent': 'omarchy-restore-deadlock-sync'})
        with urllib.request.urlopen(request, timeout=60) as response:
            data = response.read(asset['size'] + 1)
    if len(data) != asset['size'] or hashlib.sha256(data).hexdigest() != asset['sha256']:
        raise ValueError('Release verification failed: ' + asset['name'])
    return data


def validate_helper_parents():
    for parent in HELPER.parents:
        if parent.exists():
            stat = parent.stat()
            if parent.is_symlink() or stat.st_uid != 0 or stat.st_gid != 0 or stat.st_mode & 0o022:
                raise ValueError('Unsafe privileged helper parent: ' + str(parent))


def helper_ready(asset):
    try:
        validate_helper_parents()
        stat = HELPER.stat()
        if HELPER.is_symlink() or stat.st_uid != 0 or stat.st_gid != 0 or stat.st_mode & 0o7777 != 0o755:
            return False
        if hashlib.sha256(HELPER.read_bytes()).hexdigest() != asset['sha256']:
            return False
        capabilities = subprocess.check_output(['/usr/bin/getcap', str(HELPER)], text=True).strip()
        return capabilities.endswith(' cap_sys_ptrace=ep')
    except (OSError, ValueError, subprocess.SubprocessError):
        return False


def install_helper(data):
    if HELPER.is_symlink():
        raise ValueError('Refusing a symlink at the privileged helper path')
    validate_helper_parents()
    with tempfile.TemporaryDirectory(prefix='deadlock-memgrant-') as temporary:
        source = Path(temporary) / 'memgrant'
        source.write_bytes(data)
        source.chmod(0o644)
        elevate = 'sudo' if sys.stdin.isatty() and sys.stdout.isatty() else 'pkexec'
        subprocess.run([elevate, '/bin/sh', '-c',
                        '/usr/bin/install -D -o root -g root -m 0755 "$1" "$2" && '
                        '{ /usr/bin/setcap cap_sys_ptrace=ep "$2" || { /usr/bin/rm -f "$2"; exit 1; }; }',
                        'sh', str(source), str(HELPER)], check=True)


def validate_targets(home, files):
    home = Path(home).resolve()
    for relative in (*files, *OBSOLETE_FILES, Path('.local/bin/deadlock-api-ingest'), Path('.local/state/deadlock-api-ingest/restore-backups')):
        if relative.is_absolute() or '..' in relative.parts:
            raise ValueError('Invalid destination path: ' + str(relative))
        target = home / relative
        if not target.parent.resolve().is_relative_to(home):
            raise ValueError('Target parent escapes home: ' + str(target))
        if (relative in files or relative in OBSOLETE_FILES) and (target.is_symlink() or target.exists() and not target.is_file()):
            raise ValueError('Refusing non-regular payload target: ' + str(target))
        if relative.name == 'restore-backups' and (target.is_symlink() or target.exists() and not target.is_dir()):
            raise ValueError('Refusing unsafe backup directory: ' + str(target))
        if relative.name == 'deadlock-api-ingest' and relative.parent == Path('.local/bin') and target.is_dir():
            raise ValueError('Refusing directory at launcher path: ' + str(target))


def apply_files(home, files):
    """Back up replaced payloads outside watched plugin directories."""
    home = Path(home).resolve()
    validate_targets(home, files)
    stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ') + '-' + uuid.uuid4().hex[:8]
    backup = home / '.local/state/deadlock-api-ingest/restore-backups' / stamp
    for relative, (data, mode) in files.items():
        target = home / relative
        if target.exists() and target.read_bytes() == data and target.stat().st_mode & 0o7777 == mode:
            continue
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
        target.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=target.parent, prefix='.' + target.name + '-', delete=False) as output:
            temporary = Path(output.name)
            output.write(data)
        temporary.chmod(mode)
        temporary.replace(target)
    for relative in OBSOLETE_FILES:
        target = home / relative
        if target.exists():
            saved = backup / relative
            saved.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, saved)
            target.unlink()
    link = home / '.local/bin/deadlock-api-ingest'
    wanted = home / APP_DEST / 'deadlock-api-ingest'
    if link.is_symlink() and link.resolve() == wanted:
        return backup
    if link.exists() or link.is_symlink():
        saved = backup / '.local/bin/deadlock-api-ingest'
        saved.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(link, saved, follow_symlinks=False)
        link.unlink()
    link.parent.mkdir(parents=True, exist_ok=True)
    link.symlink_to('../share/deadlock-api-ingest/deadlock-api-ingest')
    return backup


def preflight(home):
    if os.geteuid() == 0 or Path(home).resolve() != Path.home().resolve():
        raise ValueError('--apply must run as the intended desktop user, using their own home')
    if platform.machine() not in ('x86_64', 'amd64'):
        raise ValueError('The pinned upstream Linux release is x86_64 only')
    for command in ('systemctl', 'python3', 'notify-send', 'getcap', 'setcap', 'omarchy'):
        if not shutil.which(command):
            raise ValueError('Missing dependency: ' + command)
    shell = Path(os.environ.get('OMARCHY_PATH', '/usr/share/omarchy')) / 'shell'
    for path in ('Commons/ShellIpc.qml', 'Ui/KeyboardPanel.qml', 'Ui/BarIconButton.qml'):
        if not (shell / path).is_file():
            raise ValueError('Incompatible Omarchy shell: missing ' + path)
    for unit in UNITS:
        result = subprocess.run(['systemctl', '--user', 'show', unit, '-p', 'LoadState,ActiveState'],
                                capture_output=True, text=True)
        state = dict(line.split('=', 1) for line in result.stdout.splitlines() if '=' in line)
        if result.returncode and state.get('LoadState') != 'not-found':
            raise ValueError('Cannot inspect user services: ' + result.stderr.strip())
        if state.get('LoadState') == 'masked':
            raise ValueError('Unmask the intended service before restoring: ' + unit)
        if state.get('ActiveState') in ('active', 'activating', 'deactivating'):
            raise ValueError('Stop Deadlock syncing/backfill before restoring: ' + unit)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--home', type=Path, default=Path.home(), help='Target home; apply is restricted to the invoking user')
    action = parser.add_mutually_exclusive_group()
    action.add_argument('--apply', action='store_true', help='Install verified binaries and user files; does not start backfill')
    action.add_argument('--stage', type=Path, help='Render user files into a new directory only; no binaries or activation')
    parser.add_argument('--memory-helper', action='store_true', help='With --apply, authorize installation of the pinned privileged read-only helper')
    parser.add_argument('--assets-dir', type=Path, help='Read and verify predownloaded release assets instead of downloading')
    args = parser.parse_args(argv)
    try:
        config = json.loads((args.home / '.config/omarchy/shell.json').read_text())
        files = user_files(args.home, config)
        upstream = json.loads((ROOT / 'upstream.json').read_text())
        if args.stage:
            stage(args.stage, files)
            print('Staged user files at', args.stage, '(no binaries or activation)')
            return 0
        if not args.apply:
            print('Preview:', upstream['version'], 'for', args.home)
            for relative in files:
                print('  ', args.home / relative)
            print('  ', args.home / APP_DEST / 'deadlock-api-ingest', '(verified release binary)')
            print('  ', HELPER, '(read-only memory helper, root-owned cap_sys_ptrace)')
            print('Apply with --apply --memory-helper on a new target; then run: omarchy restart shell')
            return 0
        preflight(args.home)
        ingest = verified_asset(upstream['assets']['ingest'], args.assets_dir)
        files[APP_DEST / 'deadlock-api-ingest'] = (ingest, 0o755)
        validate_targets(args.home, files)
        ready = helper_ready(upstream['assets']['memoryHelper'])
        if not ready and not args.memory_helper:
            raise ValueError('Pinned memory helper is missing; rerun with --apply --memory-helper')
        if not ready:
            helper = verified_asset(upstream['assets']['memoryHelper'], args.assets_dir)
            install_helper(helper)
            if not helper_ready(upstream['assets']['memoryHelper']):
                raise ValueError('Memory helper installation did not verify')
        # Remove legacy login activation but never start either job here.
        subprocess.run(['systemctl', '--user', 'disable', *UNITS], check=False, stdout=subprocess.DEVNULL)
        backup = apply_files(args.home, files)
        subprocess.run(['systemctl', '--user', 'daemon-reload'], check=True)
        if shutil.which('update-desktop-database'):
            subprocess.run(['update-desktop-database', str(args.home / '.local/share/applications')], check=True)
        print('Installed', upstream['version'], '; changed-file backups:', backup)
        print('Run omarchy restart shell for a fresh engine. Enable automatic recovery with deadlock-backfill.')
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        parser.error(str(error))


if __name__ == '__main__':
    raise SystemExit(main())
