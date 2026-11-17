"""Read bounded, nonsecret Steam account metadata; never opens session/token files."""
from pathlib import Path
import os
import re

MAX_BYTES = 1024 * 1024
MAX_ACCOUNTS = 64
STEAM_BASE = 76561197960265728


def account_id(value):
    value = str(value)
    return value if re.fullmatch(r'[1-9][0-9]{0,9}', value) and int(value) <= 4294967295 else ''


def parse_vdf(text):
    if len(text.encode('utf-8')) > MAX_BYTES:
        raise ValueError('Steam account metadata is too large')
    # KeyValues quoted strings, braces and line comments. Reject ambiguous input.
    tokens, position = [], 0
    pattern = re.compile(r'\s+|//[^\r\n]*|"((?:\\.|[^"\\])*)"|([{}])')
    while position < len(text):
        match = pattern.match(text, position)
        if not match:
            raise ValueError('Steam account metadata is invalid')
        if match.group(1) is not None:
            value = re.sub(r'\\([\\"])', r'\1', match.group(1))
            if len(value) > 4096:
                raise ValueError('Steam account metadata field is too large')
            tokens.append(('text', value))
        elif match.group(2):
            tokens.append((match.group(2), match.group(2)))
        position = match.end()
    cursor = 0
    def block(depth=0, nested=False):
        nonlocal cursor
        if depth > 8:
            raise ValueError('Steam account metadata is too deeply nested')
        result = {}
        while cursor < len(tokens):
            kind, key = tokens[cursor]
            cursor += 1
            if kind == '}' and nested:
                return result
            if kind != 'text' or cursor >= len(tokens):
                raise ValueError('Steam account metadata is invalid')
            kind, value = tokens[cursor]
            cursor += 1
            if kind == '{':
                value = block(depth + 1, True)
            elif kind != 'text':
                raise ValueError('Steam account metadata is invalid')
            key = key.casefold()
            if key in result:
                raise ValueError('Steam account metadata contains duplicate fields')
            result[key] = value
        if nested:
            raise ValueError('Steam account metadata is incomplete')
        return result
    return block()


def decode(text):
    users = parse_vdf(text).get('users')
    if not isinstance(users, dict) or len(users) > MAX_ACCOUNTS:
        raise ValueError('Steam account metadata has an invalid account list')
    result, current, automatic = [], [], []
    for steam64, fields in users.items():
        if not steam64.isdigit() or not isinstance(fields, dict):
            continue
        identifier = account_id(int(steam64) - STEAM_BASE)
        if not identifier:
            continue
        recent = fields.get('mostrecent') == '1'
        remembered = fields.get('rememberpassword') == '1' or fields.get('allowautologin') == '1'
        # Login flags can be stale; selected engine authentication decides usability.
        name = fields.get('personaname') or fields.get('accountname') or 'Steam account ' + identifier
        if not isinstance(name, str):
            name = 'Steam account ' + identifier
        name = ''.join(c for c in name if c.isprintable())[:80].strip() or 'Steam account ' + identifier
        result.append({'id': identifier, 'name': name, 'remembered': remembered})
        if recent:
            current.append(identifier)
        if fields.get('autologin') == '1':
            automatic.append(identifier)
    if len(current) > 1:
        raise ValueError('Steam account metadata has more than one current account')
    if not current:
        if len(automatic) > 1:
            raise ValueError('Steam account metadata has more than one auto-login account')
        current = automatic
    return {'accounts': result, 'current': current[0] if current else '', 'error': ''}


def discover(home=None):
    home = Path(home or Path.home())
    flatpak = home / '.var/app/com.valvesoftware.Steam'
    data = Path(os.environ.get('XDG_DATA_HOME', str(home / '.local/share')))
    snap = Path(os.environ.get('SNAP_USER_DATA', str(home / 'snap'))) / 'steam/common'
    candidates = [flatpak / '.local/share/Steam', flatpak / '.steam/steam', flatpak / '.steam/root',
                  data / 'Steam', home / '.steam/steam', home / '.steam/root', home / '.steam/debian-installation',
                  snap / '.local/share/Steam', snap / '.steam/steam', snap / '.steam/root']
    found, seen = [], set()
    for order, root in enumerate(candidates):
        path = root / 'config/loginusers.vdf'
        try:
            path = path.resolve()
            if path in seen or not (root / 'steamapps').is_dir():
                continue
            seen.add(path)
            stat = path.stat()
            found.append((stat.st_mtime_ns, -order, path, stat.st_size))
        except OSError:
            continue
    if not found:
        return {'accounts': [], 'current': '', 'error': 'Sign in to Steam with a remembered login.'}
    _, _, path, size = max(found)
    try:
        if size > MAX_BYTES:
            raise ValueError('Steam account metadata is too large')
        with path.open('rb') as handle:
            data = handle.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError('Steam account metadata is too large')
        return decode(data.decode('utf-8-sig'))
    except (OSError, UnicodeError, ValueError):
        return {'accounts': [], 'current': '', 'error': 'Steam account information is unavailable; waiting for Steam.'}
